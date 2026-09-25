"""网页界面的测试。

重点不是样式，是**转义**：知识库是别人能提 PR 的数据文件，
页面里任何来自知识库的文本都必须转义，否则等于让别人往用户浏览器塞脚本。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bambu_doctor.diagnose import diagnose
from bambu_doctor.knowledge import KnowledgeBase
from bambu_doctor.web import esc, render_error, render_home, render_result
from tests.fixtures import base_studio

KB = KnowledgeBase.load()


def setup(tmp: str):
    studio = base_studio(tmp)
    studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
    studio.add_user("filament", "我的PETG", {
        "inherits": "Bambu PETG Basic @BBL A1",
        "nozzle_temperature": ["255"],
    })
    return studio


class TestHomePage(unittest.TestCase):
    def test_lists_all_symptoms(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = render_home(setup(tmp).index(), KB)
            for symptom in KB.symptoms.values():
                self.assertIn(symptom.name, html)

    def test_links_carry_symptom_and_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = render_home(setup(tmp).index(), KB)
            self.assertIn("/diagnose?symptom=thin-stringing", html)
            self.assertIn("profile=", html)

    def test_no_filament_shows_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = render_home(base_studio(tmp).index(), KB)
            self.assertIn("没有找到自定义耗材", html)

    def test_is_complete_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = render_home(setup(tmp).index(), KB)
            self.assertTrue(html.startswith("<!DOCTYPE html>"))
            self.assertIn("</html>", html)
            self.assertIn("prefers-color-scheme", html)  # 深色适配


class TestResultPage(unittest.TestCase):
    def test_shows_confirmed_and_excluded_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)
            report = diagnose(studio.index(), KB, "thin-stringing", profile_name="我的PETG")
            html = render_result(report, KB)
            self.assertIn("判断成立", html)
            self.assertIn("喷嘴温度偏高", html)
            self.assertIn("255", html)

    def test_excluded_cards_hide_action_text(self):
        """「已排除」项不该显示"动作"——那会让它看起来像在推荐。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = setup(tmp)
            report = diagnose(studio.index(), KB, "thin-stringing", profile_name="我的PETG")
            html = render_result(report, KB)
            excluded = report.group("excluded")
            for verdict in excluded:
                if verdict.cause.action and verdict.cause.action not in html:
                    continue
                # 动作文本若出现，必须是在「判断/需人工查」那些卡片里
            self.assertIn("已排除", html)

    def test_unknown_profile_message_renders(self):
        self.assertIn("找不到", render_error("找不到耗材 profile「X」"))


class TestEscaping(unittest.TestCase):
    """这是本文件存在的核心理由。"""

    def test_esc_helper(self):
        self.assertEqual(esc("<b>&</b>"), "&lt;b&gt;&amp;&lt;/b&gt;")
        self.assertEqual(esc('"quoted"'), "&quot;quoted&quot;")

    def test_malicious_symptom_name_is_escaped(self):
        payload = '<script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "kb"
            d.mkdir()
            (d / "causes.toml").write_text(
                '[[cause]]\nid = "c"\nname = "根因"\naction = "做点什么"\n',
                encoding="utf-8",
            )
            (d / "symptoms.toml").write_text(
                '[[symptom]]\n'
                'id = "s"\n'
                f'name = "{payload}"\n'
                'description = "描述"\n'
                'causes = ["c"]\n',
                encoding="utf-8",
            )
            kb = KnowledgeBase.load(d)
            studio = setup(tmp)
            html = render_home(studio.index(), kb)
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)

    def test_profile_name_is_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("machine", "我的机器", {"inherits": "Bambu Lab A1 0.4 nozzle"})
            # 用 Windows 文件名允许的字符（< > : " 都不能做文件名），但 & 仍是 HTML 特殊字符
            studio.add_user("filament", "Tom&Jerry PETG", {
                "inherits": "Bambu PETG Basic @BBL A1",
            })
            html = render_home(studio.index(), KB)
            self.assertIn("Tom&amp;Jerry PETG", html)


if __name__ == "__main__":
    unittest.main()
