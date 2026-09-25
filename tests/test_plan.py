"""目标处方（plan）的测试：打印前该配什么参数。"""

from __future__ import annotations

import tempfile
import unittest

from bambu_doctor.plan import GoalBook, PlanError, build_plan, render_plan
from tests.test_transparency import transparent_setup

BOOK = GoalBook.load()


class TestGoalBook(unittest.TestCase):
    def test_loads_goals(self):
        self.assertIn("transparent", BOOK.goals)

    def test_match_by_keyword(self):
        hits = BOOK.match("我想打个透明的件")
        self.assertTrue(hits)
        self.assertEqual(hits[0][0].id, "transparent")

    def test_no_match(self):
        self.assertEqual(BOOK.match("我想吃饭"), [])

    def test_every_setting_says_why(self):
        """每条处方都得说明为什么，否则用户没法判断该不该改。"""
        for goal in BOOK.goals.values():
            for setting in goal.settings:
                self.assertTrue(setting.why, f"{goal.id}/{setting.param} 没有 why")

    def test_no_generic_why(self):
        """「同上」这种写法在分组输出里会指代不明。"""
        for goal in BOOK.goals.values():
            for setting in goal.settings:
                self.assertNotIn("同上", setting.why, f"{goal.id}/{setting.param} 用了「同上」")

    def test_layer_values_are_valid(self):
        for goal in BOOK.goals.values():
            for setting in goal.settings:
                self.assertIn(setting.layer, ("process", "filament", "machine"))

    def test_targets_are_not_empty(self):
        for goal in BOOK.goals.values():
            for setting in goal.settings:
                self.assertTrue(setting.target.strip(), f"{goal.id}/{setting.param} 目标值是空的")

    def test_bad_layer_raises(self):
        import tempfile as tf
        from pathlib import Path
        with tf.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goals.toml"
            path.write_text(
                '[[goal]]\nid = "x"\nname = "X"\n'
                '[[goal.setting]]\nparam = "p"\nlayer = "nonsense"\ntarget = "1"\n',
                encoding="utf-8",
            )
            with self.assertRaises(PlanError):
                GoalBook.load(path)


class TestBuildPlan(unittest.TestCase):
    GOAL = BOOK.goals["transparent"]

    def test_lists_what_needs_changing(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp)
            plan = build_plan(
                studio.index(), self.GOAL, profile_name="透明", process_name="透明档"
            )
            changes = {c.setting.param for c in plan.group("change")}
            self.assertIn("layer_height", changes)
            self.assertIn("fan_max_speed", changes)
            self.assertIn("sparse_infill_density", changes)

    def test_compliant_setup_has_nothing_to_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(
                tmp,
                fan=("0", "0"), layer_height="0.1", line_width="0.5", walls="1",
                top_shells="0", bottom_shells="0", density="100%", pattern="line",
                speed="20", flow="0.98", temp="250",
            )
            plan = build_plan(
                studio.index(), self.GOAL, profile_name="透明", process_name="透明档"
            )
            left = [c.setting.param for c in plan.group("change")]
            self.assertEqual(left, [], f"改到位后不该还要改：{left}")

    def test_target_match_uses_number_not_string(self):
        """「0」和「0%」在语义上应该算一致，别因为字符串不同就报要改。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp, density="100%")
            plan = build_plan(
                studio.index(), self.GOAL, profile_name="透明", process_name="透明档"
            )
            density = next(
                c for c in plan.changes if c.setting.param == "sparse_infill_density"
            )
            self.assertEqual(density.status, "ok")

    def test_unknown_profile_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp)
            with self.assertRaises(ValueError) as ctx:
                build_plan(studio.index(), self.GOAL, profile_name="不存在")
            self.assertIn("找不到", str(ctx.exception))

    def test_unknown_process_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp)
            with self.assertRaises(ValueError) as ctx:
                build_plan(studio.index(), self.GOAL, process_name="不存在")
            self.assertIn("找不到", str(ctx.exception))

    def test_no_filament_raises(self):
        from tests.fixtures import base_studio
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            with self.assertRaises(ValueError):
                build_plan(studio.index(), self.GOAL)

    def test_notes_mention_multiple_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp)
            studio.add_user("filament", "另一份", {"inherits": "Bambu PETG Basic @BBL A1"})
            plan = build_plan(studio.index(), self.GOAL, process_name="透明档")
            self.assertTrue(plan.notes)


class TestRender(unittest.TestCase):
    def test_shows_goal_items_and_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp)
            plan = build_plan(
                studio.index(), BOOK.goals["transparent"],
                profile_name="透明", process_name="透明档",
            )
            text = render_plan(plan)
            self.assertIn("透明件", text)
            self.assertIn("层高", text)
            self.assertIn("改成", text)
            self.assertIn("也不能省", text)
            self.assertIn("烘干", text)

    def test_hide_ok_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = transparent_setup(tmp, density="100%")
            plan = build_plan(
                studio.index(), BOOK.goals["transparent"],
                profile_name="透明", process_name="透明档",
            )
            self.assertNotIn("已经对的", render_plan(plan, show_ok=False))


if __name__ == "__main__":
    unittest.main()
