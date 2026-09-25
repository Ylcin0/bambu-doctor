"""profile 索引与继承链解析的测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bambu_doctor.discovery import load_layout
from bambu_doctor.profiles import (
    ORIGIN_SYSTEM,
    ORIGIN_USER,
    ProfileIndex,
    is_unset,
    normalize,
    normalize_full,
)
from tests.fixtures import FakeStudio, base_studio


class TestNormalize(unittest.TestCase):
    def test_single_element_list(self):
        self.assertEqual(normalize(["245"]), "245")

    def test_multi_element_takes_first_non_nil(self):
        # Bambu 用多元素列表表示不同挤出机变体，"nil" 表示该变体未设
        self.assertEqual(normalize(["0.94", "nil"]), "0.94")

    def test_all_nil(self):
        self.assertEqual(normalize(["nil"]), "nil")
        self.assertEqual(normalize(None), "nil")

    def test_plain_string(self):
        self.assertEqual(normalize("PETG"), "PETG")

    def test_full_list_keeps_every_element(self):
        # 角点列表只取首位会得到 "0x0" 这种误导值
        corners = ["0x0", "256x0", "256x256", "0x256"]
        self.assertEqual(normalize(corners), "0x0")
        self.assertEqual(normalize_full(corners), "0x0 / 256x0 / 256x256 / 0x256")


class TestIsUnset(unittest.TestCase):
    def test_unset_variants(self):
        for value in (None, "", "nil", ["nil"], ["nil", "nil"]):
            self.assertTrue(is_unset(value), f"{value!r} 应判为未设置")

    def test_set_values(self):
        for value in ("0.4", ["0.4"], ["0.4", "nil"], 0):
            self.assertFalse(is_unset(value), f"{value!r} 应判为已设置")


class TestInheritanceChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = FakeStudio(self.tmp.name)
        self.studio.add_system("filament", "root_level", {"filament_diameter": ["1.75"]})
        self.studio.add_system(
            "filament", "mid_level", {"inherits": "root_level", "filament_type": "PETG"}
        )
        self.studio.add_user(
            "filament", "mine", {"inherits": "mid_level", "nozzle_temperature": ["255"]}
        )
        self.index = self.studio.index()

    def tearDown(self):
        self.tmp.cleanup()

    def test_chain_order_is_self_to_ancestor(self):
        self.assertEqual(
            self.index.chain("mine"), ["mine", "mid_level", "root_level"]
        )

    def test_resolve_merges_whole_chain(self):
        # 这是本工具存在的理由：只读自己那个 json 会得到残缺参数表
        values, _ = self.index.resolve("mine")
        self.assertEqual(normalize(values["filament_diameter"]), "1.75")  # 来自最远祖先
        self.assertEqual(normalize(values["filament_type"]), "PETG")       # 来自中间层
        self.assertEqual(normalize(values["nozzle_temperature"]), "255")   # 来自自己

    def test_child_overrides_parent(self):
        self.studio.add_system(
            "filament", "override_test", {"nozzle_temperature": ["200"]}
        )
        self.studio.add_user(
            "filament", "child", {"inherits": "override_test", "nozzle_temperature": ["220"]}
        )
        index = self.studio.index()
        values, _ = index.resolve("child")
        self.assertEqual(normalize(values["nozzle_temperature"]), "220")

    def test_layers_record_where_each_key_came_from(self):
        _, layers = self.index.resolve("mine")
        self.assertEqual(layers["filament_diameter"], "root_level")
        self.assertEqual(layers["nozzle_temperature"], "mine")

    def test_broken_chain_stops_cleanly(self):
        self.studio.add_user("filament", "orphan", {"inherits": "does_not_exist"})
        index = self.studio.index()
        self.assertEqual(index.chain("orphan"), ["orphan", "does_not_exist"])
        values, _ = index.resolve("orphan")
        self.assertEqual(values, {})

    def test_cycle_does_not_hang(self):
        self.studio.add_system("filament", "a", {"inherits": "b"})
        self.studio.add_system("filament", "b", {"inherits": "a"})
        self.studio.add_user("filament", "cyclic", {"inherits": "a"})
        index = self.studio.index()
        self.assertLessEqual(len(index.chain("cyclic")), 4)


class TestVendorSubdirectories(unittest.TestCase):
    def test_third_party_presets_are_found(self):
        """
        第三方耗材预设按厂商放在子目录里（真实布局：filament/SUNLU/…）。
        只扫一层会漏掉它们，并把依赖它们的继承链误判成断链。
        """
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user(
                "filament", "my_sunlu", {"inherits": "SUNLU PETG @BBL X1C"}
            )
            index = studio.index()
            self.assertIsNotNone(index.get("SUNLU PETG @BBL X1C"))
            chain = index.chain("my_sunlu")
            self.assertIn("SUNLU PETG @BBL X1C", chain)
            values, _ = index.resolve("my_sunlu")
            self.assertEqual(normalize(values["nozzle_temperature"]), "250")


class TestMachineTemplateFiles(unittest.TestCase):
    def test_template_is_not_a_profile_but_is_indexed(self):
        """
        模板文件（<机器> template machine_start_gcode.json）不是 profile，
        但官方默认 G-code 存在里面，要单独收好供差异对比。
        """
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_system_template("Bambu Lab A1 0.4 nozzle", "machine_start_gcode", "G28\n")
            index = studio.index()
            self.assertIsNone(index.get("Bambu Lab A1 0.4 nozzle template machine_start_gcode"))
            self.assertEqual(
                index.templates[("Bambu Lab A1 0.4 nozzle", "machine_start_gcode")], "G28\n"
            )


class TestDiscovery(unittest.TestCase):
    def test_user_account_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("filament", "x", {})
            layout = load_layout(tmp)
            self.assertEqual(len(layout.user_dirs), 1)
            self.assertEqual(layout.user_dirs[0].name, "123456")

    def test_missing_directory_raises(self):
        from bambu_doctor.discovery import DiscoveryError

        with self.assertRaises(DiscoveryError):
            load_layout(Path("D:/definitely/not/here"))


if __name__ == "__main__":
    unittest.main()
