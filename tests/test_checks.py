"""体检规则的测试。"""

from __future__ import annotations

import tempfile
import unittest

from bambu_doctor.checks import (
    count_by_level,
    detect_target_machine,
    machine_matches,
    machine_of_name,
    run_checks,
)
from tests.fixtures import base_studio


def findings_for(studio) -> list:
    return run_checks(studio.index())


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


def by_rule(findings, rule) -> list:
    return [f for f in findings if f.rule == rule]


class TestMachineNameParsing(unittest.TestCase):
    def test_nozzle_suffix_is_stripped(self):
        self.assertEqual(machine_of_name("Bambu PETG Basic @BBL A1 0.2 nozzle"), "A1")

    def test_mini_is_not_truncated_to_a1(self):
        # 只取首个字母数字串会把 "A1 mini" 截成 "A1"，导致机型比对错误
        self.assertEqual(machine_of_name("Bambu PETG Basic @BBL A1 mini"), "A1 mini")

    def test_a1m_is_not_a1(self):
        self.assertEqual(machine_of_name("Bambu PETG Basic @BBL A1M 0.4 nozzle"), "A1M")
        self.assertFalse(machine_matches("Bambu PETG Basic @BBL A1M 0.4 nozzle", "A1"))
        self.assertTrue(machine_matches("Bambu PETG Basic @BBL A1", "A1"))

    def test_machine_preset_name(self):
        self.assertEqual(machine_of_name("Bambu Lab A1 mini 0.4 nozzle"), "A1 mini")


class TestTargetMachineDetection(unittest.TestCase):
    def test_reads_printer_model_from_user_machine_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user(
                "machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"}
            )
            self.assertEqual(detect_target_machine(studio.index()), "A1")


class TestR2CrossMachineInheritance(unittest.TestCase):
    def test_reports_inheriting_another_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            studio.add_user(
                "filament", "拷来的PETG", {"inherits": "SUNLU PETG @BBL X1C"}
            )
            found = by_rule(findings_for(studio), "R2")
            self.assertEqual(len(found), 1)
            self.assertIn("X1C", found[0].title)
            self.assertIn("A1", found[0].title)

    def test_no_false_positive_for_same_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            studio.add_user(
                "filament", "正常的PETG", {"inherits": "Bambu PETG Basic @BBL A1"}
            )
            self.assertEqual(by_rule(findings_for(studio), "R2"), [])


class TestR3TemperatureDeviation(unittest.TestCase):
    def test_reports_too_hot(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            studio.add_user(
                "filament",
                "过热的PETG",
                {"inherits": "Bambu PETG Basic @BBL A1", "nozzle_temperature": ["255"]},
            )
            found = by_rule(findings_for(studio), "R3")
            self.assertEqual(len(found), 1)
            self.assertIn("高 10", found[0].title)

    def test_reports_too_cold(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            studio.add_user(
                "filament",
                "偏冷的PETG",
                {"inherits": "Bambu PETG Basic @BBL A1", "nozzle_temperature": ["230"]},
            )
            found = by_rule(findings_for(studio), "R3")
            self.assertEqual(len(found), 1)
            self.assertIn("低 15", found[0].title)

    def test_tolerance_boundary(self):
        """差 5℃ 不报，差 6℃ 报（容差设 5 是为了不漏掉"跨机型的 10℃ 差"）。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            studio.add_user(
                "filament", "差5度", {"inherits": "Bambu PETG Basic @BBL A1",
                                    "nozzle_temperature": ["250"]},
            )
            self.assertEqual(by_rule(findings_for(studio), "R3"), [])

    def test_no_deviation_when_matching_official(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            studio.add_user(
                "filament", "标准PETG", {"inherits": "Bambu PETG Basic @BBL A1"}
            )
            self.assertEqual(by_rule(findings_for(studio), "R3"), [])

    def test_filament_type_as_list_still_matches_baseline(self):
        """
        回归测试：真实数据里 filament_type 会被解析成 ['PETG']（列表），
        若拿它直接和字符串比就永远匹配不上基线，R3 会静默失效。
        """
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            studio.add_user(
                "filament",
                "列表形态PETG",
                {"inherits": "Bambu PETG Basic @BBL A1", "nozzle_temperature": ["255"]},
            )
            self.assertEqual(len(by_rule(findings_for(studio), "R3")), 1)


class TestR1BrokenChain(unittest.TestCase):
    def test_reports_missing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("filament", "孤儿", {"inherits": "不存在的预设"})
            found = by_rule(findings_for(studio), "R1")
            self.assertEqual(len(found), 1)
            self.assertIn("不存在的预设", found[0].title)

    def test_no_false_positive_for_vendor_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("filament", "sunlu料", {"inherits": "SUNLU PETG @BBL X1C"})
            self.assertEqual(by_rule(findings_for(studio), "R1"), [])


class TestR4UnsetFallback(unittest.TestCase):
    def test_reports_fallback_to_machine_retraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user(
                "machine", "我的机器",
                {"inherits": "Bambu Lab A1 0.4 nozzle", "retraction_length": ["1"]},
            )
            # 这份料没设 filament_retraction_length，整条链上也没有
            studio.add_system(
                "filament", "无回抽料", {"inherits": "fdm_filament_pet",
                                     "nozzle_temperature": ["245"]},
            )
            studio.add_user("filament", "无回抽", {"inherits": "无回抽料"})
            found = by_rule(findings_for(studio), "R4")
            self.assertEqual(len(found), 1)
            self.assertIn("1.0mm", found[0].title)

    def test_silent_when_filament_defines_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user(
                "machine", "我的机器",
                {"inherits": "Bambu Lab A1 0.4 nozzle", "retraction_length": ["1"]},
            )
            studio.add_user(
                "filament", "有回抽", {"inherits": "Bambu PETG Basic @BBL A1"}
            )
            self.assertEqual(by_rule(findings_for(studio), "R4"), [])


class TestR5Duplicates(unittest.TestCase):
    def test_reports_near_identical_process_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user(
                "process", "优化", {"inherits": "0.20mm Standard @BBL A1",
                                   "outer_wall_speed": ["185"]},
            )
            studio.add_user(
                "process", "优化设置", {"inherits": "0.20mm Standard @BBL A1",
                                     "outer_wall_speed": ["185"]},
            )
            found = by_rule(findings_for(studio), "R5")
            self.assertEqual(len(found), 1)
            self.assertIn("100%", found[0].title)

    def test_filament_profiles_are_not_flagged(self):
        """按颜色建多个耗材 profile 是正常用法，参数接近不算重复。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            for name in ("红PETG", "蓝PETG"):
                studio.add_user("filament", name, {"inherits": "Bambu PETG Basic @BBL A1"})
            self.assertEqual(by_rule(findings_for(studio), "R5"), [])


class TestR6ChainDepth(unittest.TestCase):
    def test_hidden_by_default_and_shown_with_verbose(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("filament", "我的PETG", {"inherits": "Bambu PETG Basic @BBL A1"})
            index = studio.index()
            self.assertEqual(by_rule(run_checks(index), "R6"), [])
            self.assertEqual(len(by_rule(run_checks(index, verbose=True), "R6")), 1)


class TestSummary(unittest.TestCase):
    def test_counts_add_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            studio.add_user(
                "filament", "拷来的", {"inherits": "SUNLU PETG @BBL X1C"}
            )
            findings = findings_for(studio)
            counts = count_by_level(findings)
            self.assertEqual(sum(counts.values()), len(findings))
            self.assertEqual(rules(findings) & {"R2"}, {"R2"})

    def test_clean_setup_has_no_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            self.assertEqual(findings_for(studio), [])


if __name__ == "__main__":
    unittest.main()
