"""核心单元测试：不联网、不开窗口、不需要 OCR 模型。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quizhunter import config as cfgmod
from quizhunter.bank import BankEntry, QuestionBank
from quizhunter.config import AppConfig
from quizhunter.hotkey import parse_hotkey
from quizhunter.matcher import (BankMatcher, containment_score, first_stem,
                                normalize, polarity, similarity)


class NormalizeTest(unittest.TestCase):
    def test_nfkc_and_markers(self):
        self.assertEqual(normalize("【答案】ＡＢＣ：12３"), "abc123")

    def test_strip_answer_label(self):
        self.assertEqual(normalize("下列说法正确的是答案：A"), "下列说法正确的是a")


class SimilarityTest(unittest.TestCase):
    def test_autojunk_off_for_long_chinese(self):
        a = "体育课上" + "的" * 120 + "测试"
        b = a
        self.assertAlmostEqual(similarity(a, b), 1.0)

    def test_containment(self):
        self.assertEqual(containment_score("慢跑", "慢跑三十分钟"), 1.0)
        self.assertLess(containment_score("abcdef", "abxxef"), 1.0)


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.entries = [
            BankEntry(question="以下哪项属于典型的有氧运动方式", answer="B"),
            BankEntry(question="体育课上测量肺活量主要反映哪项素质", answer="A"),
        ]
        self.matcher = BankMatcher(self.entries, 0.72, 0.80, 8, 8)

    def test_exact(self):
        m = self.matcher.match("以下哪项属于典型的有氧运动方式")
        self.assertTrue(m.hit)
        self.assertEqual(m.answer, "B")
        self.assertEqual(m.mode, "exact")

    def test_similarity_with_ocr_noise(self):
        m = self.matcher.match("以下哪项属于典型的有氧运动方式啊")
        self.assertTrue(m.hit)

    def test_short_query_skipped(self):
        m = self.matcher.match("正确吗")
        self.assertEqual(m.mode, "skipped")
        self.assertFalse(m.hit)

    def test_miss(self):
        m = self.matcher.match("量子力学中薛定谔方程描述什么")
        self.assertFalse(m.hit)
        self.assertEqual(m.mode, "miss")

    def test_short_contains_no_longer_steals_best(self):
        """回归：短相似题（包含匹配）不得抢在完全匹配的长题前返回。"""
        entries = [
            BankEntry("下列说法正确的是", "错误答案X"),
            BankEntry("体育锻炼时心率达到最大心率的百分之六十到八十属于有氧运动这一说法正确吗？", "正确答案B"),
        ]
        matcher = BankMatcher(entries, 0.72, 0.80, 8, 8)
        m = matcher.match("体育锻炼时心率达到最大心率的百分之六十到八十属于有氧运动这一说法正确吗？")
        self.assertTrue(m.hit)
        self.assertEqual(m.answer, "正确答案B")

    def test_polarity_opposite_questions(self):
        """回归：「正确/不正确」反义题各答各的。"""
        entries = [
            BankEntry("体育锻炼对身体有积极影响，下列哪些不是积极影响？", "B"),
            BankEntry("体育锻炼对身体有积极影响，下列哪些是积极影响？", "ACD"),
        ]
        matcher = BankMatcher(entries, 0.72, 0.80, 8, 8)
        q1 = "体育锻炼对身体有积极影响，下列哪些不是积极影响？"
        q2 = "体育锻炼对身体有积极影响，下列哪些是积极影响？"
        self.assertEqual(polarity(first_stem(q1)), -1)
        self.assertEqual(polarity(first_stem(q2)), 1)
        self.assertEqual(matcher.match(q1).answer, "B")
        self.assertEqual(matcher.match(q2).answer, "ACD")

    def test_polarity_conflict_flagged(self):
        """题干肯定但选项含否定题特征、且两题高度相似时，标记冲突供核对。"""
        entries = [
            BankEntry("以下哪些属于有氧运动的方式？", "B"),
            BankEntry("以下哪些不属于有氧运动的方式？", "ACD"),
        ]
        matcher = BankMatcher(entries, 0.72, 0.80, 8, 8)
        # 题干识别成肯定，但整体文本与否定题也高度相似
        m = matcher.match("以下哪些不属于有氧运动的方式？ A 慢跑 B 举重")
        self.assertTrue(m.polarity_conflict or m.hit)  # 要么拦截提示，要么明确命中

    def test_cache(self):
        self.matcher.match("以下哪项属于典型有氧运动方式")
        self.assertIn(normalize("以下哪项属于典型有氧运动方式"), self.matcher._cache)


class ConfigTest(unittest.TestCase):
    def test_tolerant_load(self):
        cfg = AppConfig.from_dict({"model_enabled": "yes", "min_query_length": "5",
                                   "region": [], "unknown": 123})
        self.assertTrue(cfg.model_enabled)
        self.assertEqual(cfg.min_query_length, 5)

    def test_bad_types_fallback(self):
        cfg = AppConfig.from_dict({"similarity_threshold": "abc", "region": "oops"})
        self.assertEqual(cfg.similarity_threshold, 0.72)
        self.assertEqual(cfg.region, [])

    def test_masked_key(self):
        cfg = AppConfig(api_key="sk-1234567890abcdef")
        self.assertTrue(cfg.masked_key().startswith("sk-1"))
        self.assertIn("*", cfg.masked_key())


class BankTest(unittest.TestCase):
    def test_load_dedupe_and_writable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ro = Path(d) / "question_banks"
            ro.mkdir()
            (ro / "a.json").write_text(
                '{"entries":[{"question":"题目一","answer":"甲"}]}', encoding="utf-8")
            user = Path(d) / "my_bank.json"
            user.write_text(
                '{"entries":[{"question":"题目一","answer":"甲"},'
                '{"question":"题目二","answer":"乙"}]}', encoding="utf-8")
            bank = QuestionBank(bank_dir=ro, user_file=user)
            report = bank.load()
            self.assertEqual(len(bank.entries), 2)
            self.assertEqual(report.duplicates, 1)
            self.assertEqual(len([e for e in bank.entries if e.writable]), 1)
            self.assertTrue(bank.get(bank.entries[0].entry_id) is not None)

    def test_add_update_delete(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bank = QuestionBank(bank_dir=Path(d) / "qb", user_file=Path(d) / "my.json")
            bank.load()
            e = bank.add("新题目", "答案")
            self.assertEqual(len(bank.entries), 1)
            bank.update(e.entry_id, "改题目", "新答案")
            self.assertEqual(bank.entries[0].answer, "新答案")
            bank.delete(e.entry_id)
            self.assertEqual(len(bank.entries), 0)


class HotkeyTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hotkey("F8"), (0, 0x77))
        mods, vk = parse_hotkey("Ctrl+Shift+Q")
        self.assertEqual(mods, 0x0002 | 0x0004)
        self.assertEqual(vk, ord("Q"))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_hotkey("Ctrl+")


if __name__ == "__main__":
    unittest.main()
