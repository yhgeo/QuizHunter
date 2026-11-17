"""安装自检 + 三档速度基准：合成题图跑 OCR + 匹配。

用法：
    python tools/selftest.py              # 默认 balance 档自检
    python tools/selftest.py --benchmark  # 额外对比 fast/balance/accurate 耗时与命中

成功输出 [OK]，退出码 0；失败退出码 1。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont

from quizhunter.bank import BankEntry
from quizhunter.matcher import BankMatcher
from quizhunter.ocr import OcrEngine

QUESTION = "以下哪项属于典型的有氧运动方式"
OPTIONS = ["A. 100 米冲刺", "B. 慢跑 30 分钟", "C. 引体向上", "D. 举重深蹲"]
BANK = [
    (QUESTION, "B"),
    ("体育课上测量肺活量主要反映哪项素质", "A"),
]
FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
         r"C:\Windows\Fonts\simsun.ttc"]


def load_font(size):
    for p in FONTS:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def render(dark=False):
    img = Image.new("RGB", (860, 420), (18, 20, 26) if dark else (250, 250, 252))
    draw = ImageDraw.Draw(img)
    fg = (235, 238, 245) if dark else (24, 26, 32)
    draw.text((40, 40), QUESTION, font=load_font(30), fill=fg)
    y = 120
    for line in OPTIONS:
        draw.text((60, y), line, font=load_font(26), fill=fg)
        y += 62
    return img


def run_mode(mode: str, images, matcher, repeats: int = 3):
    engine = OcrEngine(scale=2.0, mode=mode, use_cls=False)
    ok, reason = engine.warmup()
    if not ok:
        return None, reason
    total = 0.0
    hits = 0
    variants = 0
    for _ in range(repeats):
        for img, _label in images:
            t = time.time()
            r = engine.recognize(img)
            total += time.time() - t
            variants = r.variants_run
            if r.ok and matcher.match(r.text).hit:
                hits += 1
    count = repeats * len(images)
    return {"mode": mode, "avg_ms": total / count * 1000, "hits": hits,
            "count": count, "variants": variants}, ""


def main() -> int:
    images = [(render(False), "浅色底"), (render(True), "深色底")]
    matcher = BankMatcher([BankEntry(q, a) for q, a in BANK], 0.72, 0.80, 8, 8)

    if "--benchmark" in sys.argv:
        print("=== 三档速度基准（合成题图，各跑 6 次取平均）===")
        print(f"{'模式':<10}{'平均耗时':>12}{'变体数':>10}{'命中':>10}")
        results = {}
        for mode in ("fast", "balance", "accurate"):
            data, err = run_mode(mode, images, matcher, repeats=3)
            if err:
                print(f"[FAIL] {mode}: {err}")
                continue
            results[mode] = data
            print(f"{mode:<10}{data['avg_ms']:>9.0f} ms{data['variants']:>10}"
                  f"{str(data['hits']) + '/' + str(data['count']):>10}")
        if "fast" in results and "accurate" in results:
            base = results["accurate"]["avg_ms"]
            fast = results["fast"]["avg_ms"]
            if base > 0:
                print(f"\nfast 相对 accurate 提速 {base / max(fast, 0.01):.1f}×")
        print()

    # ---- 默认自检：balance 档 ----
    engine = OcrEngine(scale=2.0, mode="balance", use_cls=False)
    print("加载 OCR 引擎（balance 档）…")
    t = time.time()
    ok, reason = engine.warmup()
    if not ok:
        print(f"[FAIL] OCR 初始化失败：{reason}")
        return 1
    print(f"[OK] OCR 就绪（{time.time() - t:.2f}s）")

    failures = 0
    for img, label in images:
        r = engine.recognize(img)
        if not r.ok:
            print(f"[FAIL] {label}：未识别到文字")
            failures += 1
            continue
        m = matcher.match(r.text)
        if m.hit:
            print(f"[OK] {label}：变体 {r.variant}（跑 {r.variants_run} 次），"
                  f"置信度 {r.mean_score:.2f}，{r.elapsed * 1000:.0f}ms → 答案 {m.answer}")
        else:
            print(f"[WARN] {label}：识别成功但未匹配（最高 {m.score:.2f}）")
            failures += 1
    if failures:
        print(f"\n{failures} 项未通过，请检查依赖或阈值。")
        return 1
    print("\n自检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
