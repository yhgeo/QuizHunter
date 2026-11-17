"""把三个体育理论 docx 题库转成 QuizHunter JSON，并检测易混/冲突题。

用法：
    python tools/docx_to_bank.py                     # 转换 + 冲突报告
    python tools/docx_to_bank.py --src <目录>        # 指定 docx 目录

输出：
    question_banks/体育理论-单选.json / 多选 / 判断
    tools/conflict_report.txt   （题干高度相似但答案不同的题对）

解析规则：
  * 题块以「正确答案为X」为界，向前回溯收集题干与 A-E 选项；
  * 单选题干可能带「单选题 」前缀，去掉；
  * 判断题选项恒为 A 正确 / B 错误，答案即 A/B；
  * 多选题答案如「B、C」→ "BC"。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = Path(r"D:/AI-ANSWER-OCR/题库转换")

ANS_RE = re.compile(r"正确答案为\s*([A-EＡ-Ｅ、,，\s]+)")
OPT_RE = re.compile(r"^\s*([A-Ea-eＡ-Ｅ])[\s、.：:]+(.+)$")
TYPE_PREFIX_RE = re.compile(r"^\s*(单选题|多选题|判断题|不定项)\s*")
SKIP_PREFIXES = ("【答案】", "【解析】", "正确答案", "无", "答案")


def norm_letters(text: str) -> str:
    up = text.upper().translate(str.maketrans("ＡＢＣＤＥ", "ABCDE"))
    return "".join(re.findall(r"[A-E]", up))


def parse_docx(path: Path, qtype: str):
    """按题块切分：题干行 + A-E 选项行 + 一个「正确答案为X」收尾。

    每个「答案为」行标志一道题的结束；此前累积的题干与选项立即组成一条，
    答案赋给本题而不是上一题，避免答案串位。
    """
    paras = [p.text.strip() for p in Document(str(path)).paragraphs]
    entries = []
    stem_lines: List[str] = []
    options: Dict[str, str] = {}

    def finish(answer: str):
        nonlocal stem_lines, options
        stem = " ".join(l for l in stem_lines if l).strip()
        stem = TYPE_PREFIX_RE.sub("", stem).strip()
        stem = re.sub(r"\s+", " ", stem)
        if stem and options and answer:
            # 题型词拼进题干：OCR 常把「单选题/多选题/判断题」一起识别出来，
            # 保留它可让题型成为额外的判别维度（单选/多选题干相同时尤其关键）。
            parts = [f"{qtype}题 {stem}"]
            for k in sorted(options):
                parts.append(f"{k}. {options[k]}")
            entries.append({"question": "\n".join(parts), "answer": answer,
                            "qtype": qtype, "aliases": []})
        stem_lines, options = [], {}

    for line in paras:
        if not line:
            continue
        m = ANS_RE.search(line)
        if m:
            letters = norm_letters(m.group(1))
            if letters:
                finish(letters)     # 答案属于当前题块，立即结算
            continue
        # 跳过标记行/解析行（但保留以字母开头的选项）
        if line.startswith(("【答案】", "【解析】", "正确答案")) or line in ("无",):
            continue
        om = OPT_RE.match(line)
        if om:
            options[om.group(1).upper()] = om.group(2).strip()
        else:
            stem_lines.append(line)

    return entries


def normalize(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in t if ch.isalnum())


def detect_conflicts(entries):
    """题干（题干首行）相似但答案不同的题对。"""
    from difflib import SequenceMatcher
    stems = [(i, normalize(e["question"].split("\n")[0])) for i, e in enumerate(entries)]
    conflicts = []
    for a in range(len(stems)):
        ia, sa = stems[a]
        if len(sa) < 12:
            continue
        for b in range(a + 1, len(stems)):
            ib, sb = stems[b]
            if len(sb) < 12:
                continue
            if entries[ia]["answer"] == entries[ib]["answer"]:
                continue
            if SequenceMatcher(None, sa, sb, autojunk=False).ratio() >= 0.82:
                conflicts.append((ia, ib, entries[ia], entries[ib]))
    return conflicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    args = ap.parse_args()
    src = Path(args.src)

    files = {
        "单选": src / "体育理论测试题库（单选）.docx",
        "多选": src / "体育理论测试题库（多选）.docx",
        "判断": src / "体育理论测试题库（判断）.docx",
    }
    all_entries = {}
    for qtype, path in files.items():
        if not path.exists():
            print(f"跳过 {path}（不存在）")
            continue
        entries = parse_docx(path, qtype)
        all_entries[qtype] = entries
        out = ROOT / "question_banks" / f"体育理论-{qtype}.json"
        out.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"{qtype}: {len(entries)} 题 → {out.name}")

    # 全库冲突检测（含跨题型）
    flat = []
    for qtype, entries in all_entries.items():
        for e in entries:
            flat.append(e)
    conflicts = detect_conflicts(flat)
    report = ROOT / "tools" / "conflict_report.txt"
    lines = [f"易混题对（题干相似度>=0.82 且答案不同）：{len(conflicts)} 对", ""]
    for _ia, _ib, a, b in conflicts[:200]:
        lines.append(f"[{a['qtype']}] {a['question'][:90]}")
        lines.append(f"   → 答案 {a['answer']}   <<< 冲突 >>>   {b['qtype']} 答案 {b['answer']}")
        lines.append(f"[{b['qtype']}] {b['question'][:90]}")
        lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n冲突题对 {len(conflicts)} 个 → {report.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
