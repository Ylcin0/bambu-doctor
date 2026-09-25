"""知识库加载与自洽性的测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bambu_doctor.knowledge import KnowledgeBase, KnowledgeError


class TestLoadRealKnowledge(unittest.TestCase):
    """真实知识库必须始终自洽——别人提 PR 写错引用时这里会挂。"""

    @classmethod
    def setUpClass(cls):
        cls.kb = KnowledgeBase.load()

    def test_loads(self):
        self.assertGreaterEqual(len(self.kb.symptoms), 4)
        self.assertGreaterEqual(len(self.kb.causes), 10)

    def test_validate_passes(self):
        # load() 内部已调用 validate()，这里再显式跑一遍
        self.kb.validate()

    def test_every_auto_cause_has_checks(self):
        for cause in self.kb.causes.values():
            if cause.verifiable == "auto":
                self.assertTrue(cause.checks, f"{cause.id} 标了 auto 但没有 checks")

    def test_symptom_cause_references_exist(self):
        for symptom in self.kb.symptoms.values():
            for cid in symptom.causes:
                self.assertIn(cid, self.kb.causes, f"{symptom.id} 引用了不存在的 {cid}")
            for branch in symptom.branches:
                for option in branch.options:
                    for cid in option.causes:
                        self.assertIn(cid, self.kb.causes, f"{symptom.id} 分支引用了不存在的 {cid}")

    def test_all_causes_have_action(self):
        """每条根因都得给出动作，否则诊断输出等于没结论。"""
        for cause in self.kb.causes.values():
            self.assertTrue(cause.action, f"{cause.id} 没有 action")

    def test_manual_causes_have_check_method(self):
        """标了 manual 的必须告诉用户怎么自己查，否则就是甩锅。"""
        for cause in self.kb.causes.values():
            if cause.verifiable == "manual":
                self.assertTrue(
                    cause.manual_check, f"{cause.id} 是 manual 但没给 manual_check"
                )


class TestSymptomMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = KnowledgeBase.load()

    def test_matches_by_keyword(self):
        hits = self.kb.match_symptoms("打出来有细拉丝")
        self.assertTrue(hits)
        self.assertEqual(hits[0][0].id, "thin-stringing")

    def test_matches_spaghetti(self):
        hits = self.kb.match_symptoms("打印失败了，一堆乱丝缠在喷头上")
        self.assertTrue(hits)
        self.assertEqual(hits[0][0].id, "spaghetti")

    def test_no_match_returns_empty(self):
        self.assertEqual(self.kb.match_symptoms("我今天想吃火锅"), [])

    def test_more_hits_rank_higher(self):
        # 同时命中"拉丝"和"细丝"的应排在只命中一个的前面
        hits = self.kb.match_symptoms("细丝、拉丝")
        self.assertEqual(hits[0][0].id, "thin-stringing")


class TestBranchCauses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = KnowledgeBase.load()

    def test_branch_answer_selects_causes(self):
        symptom = self.kb.get_symptom("spaghetti")
        question = symptom.branches[0].question
        option = symptom.branches[0].options[1]  # 中段才开始乱
        picked = symptom.causes_for({question: option.answer})
        self.assertIn("hotend-partial-clog", picked)
        # 首层问题不该出现在"中段才开始乱"的候选里
        self.assertNotIn("first-layer-z-high", picked)

    def test_no_answers_falls_back_to_default(self):
        symptom = self.kb.get_symptom("spaghetti")
        self.assertEqual(symptom.causes_for(), symptom.causes)

    def test_unmatched_answer_falls_back(self):
        symptom = self.kb.get_symptom("spaghetti")
        self.assertEqual(
            symptom.causes_for({symptom.branches[0].question: "瞎编的答案"}),
            symptom.causes,
        )


class TestBadKnowledge(unittest.TestCase):
    def _write(self, tmp: str, symptoms: str, causes: str) -> Path:
        d = Path(tmp)
        (d / "symptoms.toml").write_text(symptoms, encoding="utf-8")
        (d / "causes.toml").write_text(causes, encoding="utf-8")
        return d

    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KnowledgeError):
                KnowledgeBase.load(Path(tmp))

    def test_dangling_reference_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._write(
                tmp,
                '[[symptom]]\nid = "s"\nname = "症状"\ncauses = ["does-not-exist"]\n',
                '[[cause]]\nid = "c"\nname = "根因"\naction = "做点什么"\n',
            )
            with self.assertRaises(KnowledgeError) as ctx:
                KnowledgeBase.load(d)
            self.assertIn("does-not-exist", str(ctx.exception))

    def test_auto_without_checks_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._write(
                tmp,
                '[[symptom]]\nid = "s"\nname = "症状"\ncauses = ["c"]\n',
                '[[cause]]\nid = "c"\nname = "根因"\nverifiable = "auto"\naction = "做点什么"\n',
            )
            with self.assertRaises(KnowledgeError) as ctx:
                KnowledgeBase.load(d)
            self.assertIn("没有 checks", str(ctx.exception))

    def test_malformed_toml_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._write(tmp, '[[symptom]\nid = "broken"\n', "")
            with self.assertRaises(KnowledgeError):
                KnowledgeBase.load(d)


if __name__ == "__main__":
    unittest.main()
