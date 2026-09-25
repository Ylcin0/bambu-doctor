"""透明件专用规则的测试。

依据：Bambu Lab 官方《透明/半透明 PLA/PETG 耗材打印指南》
https://wiki.bambulab.com/zh/knowledge-sharing/transparent-petg

其中 test_process_profile_is_read 是**回归测试**：透明件的关键参数
（层高、填充、速度）都在工艺档里，而早期实现只读耗材档和机器档，
会把它们全判成"没设置"。
"""

from __future__ import annotations

import tempfile
import unittest

from bambu_doctor.diagnose import (
    STATUS_CONFIRMED,
    STATUS_MANUAL,
    Verifier,
    diagnose,
)
from bambu_doctor.knowledge import KnowledgeBase
from tests.fixtures import base_studio

KB = KnowledgeBase.load()


def transparent_setup(
    tmp,
    *,
    fan=("50", "30"),
    layer_height="0.2",
    line_width="0.42",
    walls="2",
    top_shells="4",
    bottom_shells="3",
    density="15%",
    pattern="grid",
    speed="200",
    flow="0.94752",
    temp="245",
):
    """一份"什么都还没按透明方案改"的环境（数值取自真实的用户档案）。"""
    studio = base_studio(tmp)
    studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
    studio.add_user("filament", "透明", {
        "inherits": "Bambu PETG Basic @BBL A1",
        "fan_max_speed": [fan[0]],
        "fan_min_speed": [fan[1]],
        "filament_flow_ratio": [flow],
        "nozzle_temperature": [temp],
    })
    studio.add_user("process", "透明档", {
        "inherits": "0.20mm Standard @BBL A1",
        "layer_height": layer_height,
        "line_width": line_width,
        "wall_loops": walls,
        "top_shell_layers": top_shells,
        "bottom_shell_layers": bottom_shells,
        "sparse_infill_density": density,
        "sparse_infill_pattern": pattern,
        "outer_wall_speed": [speed],
    })
    return studio


def verdict(studio, cause_id, process: str = "透明档"):
    return Verifier(
        studio.index(), "透明", "A1", process_name=process
    ).verify(KB.get_cause(cause_id))


class TestProcessProfileIsRead(unittest.TestCase):
    """回归：工艺参数必须从工艺档读到，否则透明件的判断全废。"""

    def test_layer_height_comes_from_process_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp, layer_height="0.2")
            result = verdict(studio, "layer-height-large-for-clarity")
            self.assertEqual(result.status, STATUS_CONFIRMED)
            self.assertIn("0.2", result.evidence[0])

    def test_infill_comes_from_process_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp, density="15%")
            self.assertEqual(
                verdict(studio, "infill-not-solid").status, STATUS_CONFIRMED
            )

    def test_missing_process_profile_does_not_crash(self):
        """没有工艺档时应该是"数据不足"，而不是崩或者瞎报。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("filament", "透明", {"inherits": "Bambu PETG Basic @BBL A1"})
            result = Verifier(studio.index(), "透明", "A1").verify(
                KB.get_cause("layer-height-large-for-clarity")
            )
            self.assertNotEqual(result.status, STATUS_CONFIRMED)


class TestPercentParsing(unittest.TestCase):
    """Bambu 把百分比存成带 % 的字符串（如 "15%"）——必须能和数字比。"""

    def test_low_density_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, density="15%"), "infill-not-solid")
            self.assertEqual(result.status, STATUS_CONFIRMED)

    def test_full_density_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, density="100%"), "infill-not-solid")
            self.assertNotEqual(result.status, STATUS_CONFIRMED)


class TestFanRule(unittest.TestCase):
    def test_fan_on_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp), "fan-on-for-clarity")
            self.assertEqual(result.status, STATUS_CONFIRMED)
            self.assertTrue(any("50" in e for e in result.evidence))

    def test_fan_off_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, fan=("0", "0")), "fan-on-for-clarity")
            self.assertNotEqual(result.status, STATUS_CONFIRMED)


class TestOtherRules(unittest.TestCase):
    def test_narrow_line_width_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(
                transparent_setup(tmp, line_width="0.42"), "line-width-narrow-for-clarity"
            )
            self.assertEqual(result.status, STATUS_CONFIRMED)

    def test_many_walls_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, walls="2"), "too-many-walls-for-clarity")
            self.assertEqual(result.status, STATUS_CONFIRMED)

    def test_shells_present_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp), "shells-not-removed")
            self.assertEqual(result.status, STATUS_CONFIRMED)

    def test_wrong_pattern_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, pattern="grid"), "infill-pattern-wrong")
            self.assertEqual(result.status, STATUS_CONFIRMED)

    def test_line_pattern_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, pattern="line"), "infill-pattern-wrong")
            self.assertNotEqual(result.status, STATUS_CONFIRMED)

    def test_fast_speed_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, speed="200"), "speed-too-fast-for-clarity")
            self.assertEqual(result.status, STATUS_CONFIRMED)

    def test_slow_speed_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, speed="20"), "speed-too-fast-for-clarity")
            self.assertNotEqual(result.status, STATUS_CONFIRMED)

    def test_zero_speed_means_use_default_not_slow(self):
        """速度 0 表示「用默认」，不能据此判成"速度合适"。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, speed="0"), "speed-too-fast-for-clarity")
            self.assertNotEqual(result.status, STATUS_CONFIRMED)

    def test_low_flow_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, flow="0.94752"), "flow-ratio-low-for-clarity")
            self.assertEqual(result.status, STATUS_CONFIRMED)

    def test_low_temp_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verdict(transparent_setup(tmp, temp="245"), "nozzle-temp-low-for-clarity")
            self.assertEqual(result.status, STATUS_CONFIRMED)


class TestFullyCompliantSetup(unittest.TestCase):
    """全按官方方案改完之后，判断里不该再有任何一条。"""

    def test_nothing_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(
                tmp,
                fan=("0", "0"),
                layer_height="0.1",
                line_width="0.5",
                walls="1",
                top_shells="0",
                bottom_shells="0",
                density="100%",
                pattern="line",
                speed="20",
                flow="0.98",
                temp="255",
            )
            report = diagnose(
                studio.index(), KB, "cloudy",
                profile_name="透明", process_name="透明档",
            )
            confirmed = [v.cause.name for v in report.group(STATUS_CONFIRMED)]
            self.assertEqual(confirmed, [], f"改到位后不该还报这些：{confirmed}")


class TestEndToEnd(unittest.TestCase):
    def test_cloudy_reports_the_usual_suspects(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp)
            report = diagnose(
                studio.index(), KB, "cloudy",
                profile_name="透明", process_name="透明档",
            )
            confirmed = {v.cause.id for v in report.group(STATUS_CONFIRMED)}
            self.assertIn("fan-on-for-clarity", confirmed)
            self.assertIn("infill-not-solid", confirmed)
            self.assertIn("layer-height-large-for-clarity", confirmed)

    def test_dried_filament_is_still_flagged_as_manual(self):
        """受潮查不了参数——必须老实说"需要你自己确认"，不能跳过。"""
        with tempfile.TemporaryDirectory() as tmp:
            report = diagnose(
                transparent_setup(tmp).index(), KB, "cloudy",
                profile_name="透明", process_name="透明档",
            )
            manual = {v.cause.id for v in report.group(STATUS_MANUAL)}
            self.assertIn("wet-filament", manual)

    def test_notes_mention_multiple_process_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp)
            studio.add_user("process", "另一份工艺", {"inherits": "0.20mm Standard @BBL A1"})
            report = diagnose(
                studio.index(), KB, "cloudy", profile_name="透明"
            )
            self.assertTrue(any("工艺档" in n for n in report.notes))

    def test_keyword_matching_for_transparency(self):
        for text in ("发雾", "不透光", "打了不透明", "白蒙蒙的"):
            hits = KB.match_symptoms(text)
            self.assertTrue(hits, f"「{text}」应该能匹配到症状")
            self.assertEqual(hits[0][0].id, "cloudy", f"「{text}」匹配错了")


if __name__ == "__main__":
    unittest.main()
