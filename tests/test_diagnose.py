"""诊断推理的测试。

其中两个用例是**回归测试**，对应开发中真实踩到的 bug：
  - test_machine_level_param_is_found：擦拭前回抽定义在机器 profile 里，
    早期只看耗材档，把它误判成"整条链上都没人设过"
  - test_range_min_semantics：min/max 语义曾写反，把"低于下限"写成了"高于上限"
"""

from __future__ import annotations

import tempfile
import unittest

from bambu_doctor.diagnose import (
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    STATUS_MANUAL,
    STATUS_UNDETERMINED,
    Verifier,
    diagnose,
)
from bambu_doctor.knowledge import KnowledgeBase
from tests.fixtures import base_studio


def setup(tmp: str, extra_machine: dict | None = None, extra_filament: dict | None = None):
    """一套最小诊断环境：A1 + PETG 官方基线 + 一份自定义耗材档。"""
    studio = base_studio(tmp)
    machine = {"inherits": "Bambu Lab A1 0.4 nozzle"}
    machine.update(extra_machine or {})
    studio.add_user("machine", "我的机器", machine)
    filament = {"inherits": "Bambu PETG Basic @BBL A1"}
    filament.update(extra_filament or {})
    studio.add_user("filament", "我的PETG", filament)
    return studio


def verdict_for(studio, cause_id: str):
    index = studio.index()
    verifier = Verifier(index, "我的PETG", "A1")
    kb = KnowledgeBase.load()
    return verifier.verify(kb.get_cause(cause_id))


class TestTemperatureChecks(unittest.TestCase):
    def test_too_hot_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp, extra_filament={"nozzle_temperature": ["255"]})
            verdict = verdict_for(studio, "temp-high")
            self.assertEqual(verdict.status, STATUS_CONFIRMED)
            self.assertIn("255", verdict.evidence[0])
            self.assertIn("245", verdict.evidence[0])

    def test_at_baseline_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)  # 不覆盖 → 继承官方 245
            self.assertEqual(verdict_for(studio, "temp-high").status, STATUS_EXCLUDED)

    def test_within_tolerance_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 官方 245，设 249 → 差 4，未超过容差 5
            studio = setup(tmp, extra_filament={"nozzle_temperature": ["249"]})
            self.assertEqual(verdict_for(studio, "temp-high").status, STATUS_EXCLUDED)

    def test_too_cold_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp, extra_filament={"nozzle_temperature": ["230"]})
            self.assertEqual(verdict_for(studio, "temp-low").status, STATUS_CONFIRMED)


class TestRetractionChecks(unittest.TestCase):
    def test_range_min_semantics(self):
        """回归：min 表示"低于它就算问题"。0.4 < 0.8 → 成立。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp, extra_filament={"filament_retraction_length": ["0.4"]})
            verdict = verdict_for(studio, "retract-low")
            self.assertEqual(verdict.status, STATUS_CONFIRMED)
            self.assertIn("低于下限", verdict.evidence[0])

    def test_value_inside_range_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp, extra_filament={"filament_retraction_length": ["1.0"]})
            self.assertEqual(verdict_for(studio, "retract-low").status, STATUS_EXCLUDED)

    def test_high_retraction_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp, extra_filament={"filament_retraction_length": ["1.8"]})
            self.assertEqual(verdict_for(studio, "retract-high").status, STATUS_CONFIRMED)


class TestMachineLevelParams(unittest.TestCase):
    def test_machine_level_param_is_found(self):
        """
        回归测试：retract_before_wipe 定义在**机器 profile** 里。
        早期实现只查耗材档，把它误判成"未设置"，于是误报"擦拭前回抽未开"。
        """
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp, extra_machine={"retract_before_wipe": ["50%"]})
            verdict = verdict_for(studio, "wipe-missing")
            self.assertEqual(verdict.status, STATUS_EXCLUDED)
            self.assertIn("我的机器", " ".join(verdict.evidence))  # 应说明来自哪一层
            self.assertIn("50%", " ".join(verdict.evidence))

    def test_reports_unset_when_nowhere_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)  # 耗材档和机器档都没设
            verdict = verdict_for(studio, "wipe-missing")
            self.assertEqual(verdict.status, STATUS_CONFIRMED)

    def test_filament_overrides_machine(self):
        """耗材档的值应优先于机器档（Bambu 的参数优先级）。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(
                tmp,
                extra_machine={"retract_before_wipe": ["50%"]},
                extra_filament={"retract_before_wipe": ["0%"]},
            )
            # 耗材档显式设 0% → 就该报出来
            self.assertEqual(verdict_for(studio, "wipe-missing").status, STATUS_CONFIRMED)


class TestIgnoreZero(unittest.TestCase):
    def test_zero_is_skipped(self):
        """值为 0 表示该板型未启用，不该报"床温过低"。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp, extra_filament={"cool_plate_temp": ["0"]})
            verdict = verdict_for(studio, "bed-temp-low")
            self.assertIn(verdict.status, (STATUS_EXCLUDED, STATUS_UNDETERMINED))
            self.assertNotIn("低于下限", " ".join(verdict.evidence))

    def test_genuinely_low_temp_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp, extra_filament={"cool_plate_temp": ["40"]})
            verdict = verdict_for(studio, "bed-temp-low")
            self.assertEqual(verdict.status, STATUS_CONFIRMED)


class TestCrossMachineInherit(unittest.TestCase):
    def test_detects_x1c_inheritance(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)
            studio.add_user("filament", "拷来的", {"inherits": "SUNLU PETG @BBL X1C"})
            index = studio.index()
            verifier = Verifier(index, "拷来的", "A1")
            verdict = verifier.verify(KnowledgeBase.load().get_cause("cross-machine-inherit"))
            self.assertEqual(verdict.status, STATUS_CONFIRMED)
            self.assertIn("X1C", verdict.evidence[0])

    def test_clean_inheritance_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                verdict_for(setup(tmp), "cross-machine-inherit").status, STATUS_EXCLUDED
            )


class TestManualCauses(unittest.TestCase):
    def test_manual_cause_reported_as_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)
            kb = KnowledgeBase.load()
            verdict = Verifier(studio.index(), "我的PETG", "A1").verify(
                kb.get_cause("wet-filament")
            )
            self.assertEqual(verdict.status, STATUS_MANUAL)
            self.assertTrue(verdict.cause.manual_check, "manual 根因必须给出自查方法")


class TestEndToEnd(unittest.TestCase):
    def test_diagnose_thin_stringing(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(
                tmp,
                extra_filament={"nozzle_temperature": ["255"]},
            )
            report = diagnose(
                studio.index(), KnowledgeBase.load(), "thin-stringing",
                profile_name="我的PETG",
            )
            confirmed = {v.cause.id for v in report.group(STATUS_CONFIRMED)}
            self.assertIn("temp-high", confirmed)
            self.assertEqual(report.profile_name, "我的PETG")
            self.assertEqual(report.target_machine, "A1")

    def test_unknown_symptom_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)
            with self.assertRaises(ValueError) as ctx:
                diagnose(studio.index(), KnowledgeBase.load(), "not-a-symptom")
            self.assertIn("未知症状", str(ctx.exception))

    def test_unknown_profile_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)
            with self.assertRaises(ValueError) as ctx:
                diagnose(
                    studio.index(), KnowledgeBase.load(), "thin-stringing",
                    profile_name="不存在的档",
                )
            self.assertIn("找不到", str(ctx.exception))

    def test_no_user_filament_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)   # 只有官方预设，没有用户档
            with self.assertRaises(ValueError) as ctx:
                diagnose(studio.index(), KnowledgeBase.load(), "thin-stringing")
            self.assertIn("没有找到自定义耗材", str(ctx.exception))

    def test_notes_mention_multiple_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)
            studio.add_user("filament", "第二份", {"inherits": "Bambu PETG Basic @BBL A1"})
            report = diagnose(studio.index(), KnowledgeBase.load(), "thin-stringing")
            self.assertTrue(report.notes)
            self.assertIn("2 份", report.notes[0])


if __name__ == "__main__":
    unittest.main()
