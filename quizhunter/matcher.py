"""题库匹配：文本规范化 + 全量打分择优 + 极性判别 + LRU 缓存。

相比旧版「四段式短路」的关键修复：
* 旧版 exact/contains 一旦命中就立即 return，导致题库中排在前面的「相似题」
  抢先返回，后面真正更贴切的题目被完全跳过——这就是「意思差不多但答案不同」
  的题会答错、更优匹配被漏掉的根因。
* 新版对所有候选统一打分，取全局最高分；并用「极性词」区分
  「是/不是」「正确/错误」「属于/不属于」这类一字之差、答案相反的题。

评分构成（0~1）：
  base   = max(编辑相似度, 子串覆盖率×0.98, 包含匹配分)
  polarity_penalty：题干极性相反（肯定是↔否定是）时 ×0.35
  最终取 score 最大者；mode 标记来源，score 不再人为钉死 1.0。

两个性能关键点：
* SequenceMatcher 一律 autojunk=False，避免长中文题干高频字被当噪声；
* 子串覆盖率用一次 get_matching_blocks() 求覆盖比例，复杂度 O(n·m)。
"""

from __future__ import annotations

import difflib
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

_MARKER_RE = re.compile(r"【\s*答\s*案\s*】|\[\s*答\s*案\s*\]|（\s*答\s*案\s*）|\(\s*答\s*案\s*\)|答\s*案\s*[:：]")
_OPTION_LINE_RE = re.compile(
    r"^[\s\u3000]*(?:[（(]\s*[A-Da-dＡ-Ｄａ-ｄ]\s*[）)]|[A-Da-dＡ-Ｄａ-ｄ]\s*[\.、．\)）:：])")

# 极性词：题干首行出现这些词，代表题目问的是肯定还是否定方向。
# 一字之差答案相反的题（「下列正确的」↔「下列不正确的」）靠这个区分。
_NEG_WORDS = ("不是", "不属于", "不正确", "不对", "错误", "除外", "不包括",
              "不包含", "不具备", "不能", "不应", "不宜", "无关", "不可以",
              "错误的是", "不正确的", "除了", "不能是", "不会")
_POS_WORDS = ("正确", "属于", "是", "包括", "包含", "具有", "能够", "应该",
              "可以", "有关", "正确的是", "属于的是")


@dataclass(frozen=True)
class Candidate:
    text: str
    normalized: str
    char_set: FrozenSet[str]
    kind: str = "question"
    stem: str = ""            # 题干首行（去选项），用于极性比对


@dataclass
class TopCandidate:
    question: str
    answer: str
    score: float
    mode: str


@dataclass
class MatchResult:
    entry: Optional[Any] = None
    mode: str = "miss"          # exact | contains | substring | similarity | miss | skipped
    score: float = 0.0
    candidate: str = ""
    query_length: int = 0
    elapsed: float = 0.0
    top: List[TopCandidate] = field(default_factory=list)
    polarity_conflict: bool = False   # 是否出现极性相反的高相似题（提示人工核对）

    @property
    def hit(self) -> bool:
        return self.entry is not None

    @property
    def answer(self) -> str:
        return getattr(self.entry, "answer", "") if self.entry else ""

    def label(self) -> str:
        return {
            "exact": "完全一致", "contains": "包含匹配", "substring": "子串相似",
            "similarity": "整体相似", "miss": "未匹配", "skipped": "已跳过",
        }.get(self.mode, self.mode)


# ---------------------------------------------------------------- 规范化
def normalize(text: str) -> str:
    """NFKC 归一 → 去答案标记 → 只保留字母、数字、汉字。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).lower()
    t = _MARKER_RE.sub("", t)
    return "".join(ch for ch in t if ch.isalnum())


def strip_options(text: str) -> str:
    """截掉选项区，只留题干。首行就像选项时保守保留整体。"""
    if not text:
        return ""
    kept: List[str] = []
    for line in str(text).splitlines():
        if _OPTION_LINE_RE.match(line) and kept:
            break
        kept.append(line)
    return "\n".join(kept).strip()


def first_stem(text: str) -> str:
    """题干首行，极性判别的依据。

    OCR 文本常把「多选题 / 单选题 / 判断题」题型标签单独放一行，
    真正题干在下一行。必须跳过标签行，否则极性判别取到标签（极性中性），
    导致「属于↔不属于」这类只差一个字的反义题无法区分。
    """
    lines = [l.strip() for l in strip_options(text).splitlines() if l.strip()]
    for line in lines:
        # 纯题型标签行（可能带序号/冒号）：多选题、单选题、判断题、不定项选择题
        if re.fullmatch(r"[\(（]?\s*(多|单|不?定项|判断)项?选?择?题\s*[\)）:：]?", line):
            continue
        return line
    return lines[0] if lines else ""


def polarity(text: str) -> int:
    """返回题干极性：-1 否定 / 1 肯定 / 0 中性。

    只在题干文本上调用（不含选项），避免判断题选项「A正确 B错误」干扰。
    否定词优先匹配，避免「不是」里的「是」被当成肯定。
    """
    s = normalize(text)
    if not s:
        return 0
    if any(w in s for w in _NEG_WORDS):
        return -1
    if any(w in s for w in _POS_WORDS):
        return 1
    return 0


def build_candidates(question: str, aliases: Sequence[str] = ()) -> List[Candidate]:
    """构建去重后的候选列表（题干 / 去选项题干 / 别名）。"""
    seen: set = set()
    result: List[Candidate] = []
    sources: List[Tuple[str, str]] = [(question or "", "question")]
    stem = strip_options(question or "")
    if stem and stem != (question or "").strip():
        sources.append((stem, "stem"))
    for alias in aliases or []:
        if alias:
            sources.append((str(alias), "alias"))
    for text, kind in sources:
        norm = normalize(text)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(Candidate(text=str(text).strip(), normalized=norm,
                                char_set=frozenset(norm), kind=kind,
                                stem=first_stem(text)))
    return result


# ---------------------------------------------------------------- 相似度
def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    if matcher.real_quick_ratio() < 0.05 or matcher.quick_ratio() < 0.05:
        return 0.0
    return matcher.ratio()


def containment_score(query: str, candidate: str) -> float:
    """较短串被匹配块覆盖的比例，用来容忍 OCR 漏字/多字。"""
    if not query or not candidate:
        return 0.0
    shorter, longer = (query, candidate) if len(query) <= len(candidate) else (candidate, query)
    if shorter in longer:
        return 1.0
    matcher = difflib.SequenceMatcher(None, longer, shorter, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / max(1, len(shorter))


def char_overlap(left: FrozenSet[str], right: FrozenSet[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


# ---------------------------------------------------------------- 匹配器
class BankMatcher:
    """全量打分的题库匹配器。条目/阈值变更后调用 invalidate()。"""

    def __init__(self, entries: Optional[Sequence[Any]] = None,
                 similarity_threshold: float = 0.72,
                 substring_threshold: float = 0.80,
                 min_query_length: int = 8,
                 min_contains_length: int = 8,
                 cache_size: int = 256,
                 polarity_penalty: float = 0.35):
        self.entries: List[Any] = list(entries or [])
        self.similarity_threshold = float(similarity_threshold)
        self.substring_threshold = float(substring_threshold)
        self.min_query_length = max(1, int(min_query_length))
        self.min_contains_length = max(1, int(min_contains_length))
        self.cache_size = max(0, int(cache_size))
        self.polarity_penalty = float(polarity_penalty)
        self._cache: Dict[str, MatchResult] = {}
        self._order: List[str] = []

    def set_entries(self, entries: Sequence[Any]) -> None:
        self.entries = list(entries)
        self.invalidate()

    def configure(self, similarity_threshold: float, substring_threshold: float,
                  min_query_length: int, min_contains_length: int) -> None:
        self.similarity_threshold = float(similarity_threshold)
        self.substring_threshold = float(substring_threshold)
        self.min_query_length = max(1, int(min_query_length))
        self.min_contains_length = max(1, int(min_contains_length))
        self.invalidate()

    def invalidate(self) -> None:
        self._cache.clear()
        self._order.clear()

    def match(self, question_text: str) -> MatchResult:
        started = time.time()
        query = normalize(question_text)
        if not query:
            return MatchResult(mode="skipped", elapsed=time.time() - started)
        if query in self._cache:
            cached = self._cache[query]
            return MatchResult(**{**cached.__dict__, "elapsed": time.time() - started})
        if len(query) < self.min_query_length:
            result = MatchResult(mode="skipped", query_length=len(query),
                                 elapsed=time.time() - started)
            self._remember(query, result)
            return result
        result = self._match_uncached(query, first_stem(question_text))
        result.elapsed = time.time() - started
        self._remember(query, result)
        return result

    def _score_candidate(self, query: str, q_set: FrozenSet[str], q_len: int,
                         q_pol: int, norm: str, c_set: FrozenSet[str],
                         c_stem: str) -> Tuple[float, str, bool]:
        """返回 (score, mode, polarity_conflict)。"""
        # 完全一致：满分
        if norm == query:
            return 1.0, "exact", False
        # 字符集预筛：几乎不相交，后续计算没意义
        if char_overlap(q_set, c_set) < 0.34:
            return 0.0, "", False

        shorter = min(q_len, len(norm))
        length_ratio = shorter / max(q_len, len(norm))
        best_score = 0.0
        best_mode = ""

        # 编辑相似度
        if length_ratio >= 0.5:
            r = similarity(query, norm)
            if r > best_score:
                best_score, best_mode = r, "similarity"
        # 子串覆盖（略打折，容忍 OCR 漏字但不让它压过编辑相似）
        if length_ratio >= 0.2:
            c = containment_score(query, norm) * 0.98
            if c > best_score:
                best_score, best_mode = c, "substring"
        # 包含关系：不再钉死 1.0，按长度比给分，防止长题干包含短题就锁死
        if shorter >= self.min_contains_length and (norm in query or query in norm):
            cv = 0.97 if length_ratio >= 0.9 else (0.72 + 0.25 * length_ratio)
            if cv > best_score:
                best_score, best_mode = cv, "contains"

        # 极性判别：题干肯定↔否定相反则大幅降分，并标记为冲突
        conflict = False
        c_pol = polarity(c_stem)
        if q_pol and c_pol and q_pol != c_pol:
            best_score *= self.polarity_penalty
            conflict = True
        return best_score, best_mode, conflict

    def _match_uncached(self, query: str, query_stem: str) -> MatchResult:
        q_set = frozenset(query)
        q_len = len(query)
        q_pol = polarity(query_stem)

        best_entry = None
        best_score = 0.0
        best_mode = ""
        best_candidate_text = ""
        best_conflict = False
        tops: List[TopCandidate] = []
        high_conflicts: List[TopCandidate] = []

        for entry in self.entries:
            entry_best = 0.0
            entry_mode = ""
            entry_text = ""
            entry_conflict = False
            for candidate in getattr(entry, "candidates", ()):
                if not candidate.normalized:
                    continue
                score, mode, conflict = self._score_candidate(
                    query, q_set, q_len, q_pol,
                    candidate.normalized, candidate.char_set, candidate.stem)
                if score > entry_best:
                    entry_best, entry_mode, entry_text, entry_conflict = (
                        score, mode, candidate.text, conflict)
            # 每条题取自身最优候选，避免同题多候选重复进 top
            if entry_best > 0.5:
                q_text = getattr(entry, "question", "")
                a_text = getattr(entry, "answer", "")
                tops.append(TopCandidate(q_text, a_text, round(entry_best, 4), entry_mode))
                if entry_conflict and entry_best >= self.similarity_threshold:
                    high_conflicts.append(TopCandidate(q_text, a_text,
                                                       round(entry_best, 4), entry_mode))
            if entry_best > best_score:
                best_score, best_entry = entry_best, entry
                best_mode, best_candidate_text, best_conflict = (
                    entry_mode, entry_text, entry_conflict)

        top3 = self._collect_top(tops, None, 0, "")
        conflict_flag = best_conflict or bool(high_conflicts)

        if best_entry is not None and best_score >= self.similarity_threshold:
            # 极性冲突下命中不可靠：降级为 miss，把两个候选都列出来让人核对
            if best_conflict:
                merged = self._collect_top(
                    high_conflicts + tops, best_entry, best_score, best_mode, limit=5)
                return MatchResult(entry=None, mode="miss", score=best_score,
                                   candidate=best_candidate_text, query_length=q_len,
                                   top=merged, polarity_conflict=True)
            mode = "exact" if best_score >= 1.0 else best_mode
            return MatchResult(entry=best_entry, mode=mode, score=round(best_score, 4),
                               candidate=best_candidate_text, query_length=q_len,
                               top=top3, polarity_conflict=conflict_flag)

        return MatchResult(entry=None, mode="miss", score=round(best_score, 4),
                           candidate=best_candidate_text or (top3[0].question if top3 else ""),
                           query_length=q_len, top=top3, polarity_conflict=conflict_flag)

    @staticmethod
    def _collect_top(existing: List[TopCandidate], entry: Optional[Any],
                     score: float, mode: str, limit: int = 3) -> List[TopCandidate]:
        pool = list(existing)
        if entry is not None:
            pool.append(TopCandidate(getattr(entry, "question", ""),
                                     getattr(entry, "answer", ""), score, mode))
        seen, unique = set(), []
        for item in sorted(pool, key=lambda c: c.score, reverse=True):
            key = (item.question, item.answer)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= limit:
                break
        return unique

    def _remember(self, query: str, result: MatchResult) -> None:
        if self.cache_size <= 0:
            return
        self._cache[query] = result
        self._order.append(query)
        while len(self._order) > self.cache_size:
            old = self._order.pop(0)
            self._cache.pop(old, None)
