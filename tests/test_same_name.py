"""同名档的测试：耗材档和工艺档可以叫同一个名字。

对应一个真实踩到的 bug：ProfileIndex 按**名字**建字典，于是同名的两个档
在加载时就互相覆盖，`resolve()` 还会在合并继承链时再按名字查一遍第一层，
结果工艺档被解析成了耗材档的内容——层高/填充/速度全读成"没设"。

Bambu Studio 里耗材和工艺本来就是两个独立列表，同名完全合法，
所以这不是边角情况。
"""

from __future__ import annotations

import tempfile
import unittest

from bambu_doctor.profiles import ORIGIN_USER, normalize
from tests.fixtures import base_studio

NAME = "透明"


def studio_with_same_name(tmp: str):
    studio = base_studio(tmp)
    studio.add_user("filament", NAME, {
        "inherits": "Bambu PETG Basic @BBL A1",
        "filament_flow_ratio": ["0.98"],
    })
    studio.add_user("process", NAME, {
        "inherits": "0.20mm Standard @BBL A1",
        "layer_height": "0.1",
        "sparse_infill_density": "100%",
    })
    return studio


class TestSameNameDifferentKind(unittest.TestCase):
    def test_both_are_findable(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = studio_with_same_name(tmp).index()
            self.assertIsNotNone(index.find(NAME, "filament"))
            self.assertIsNotNone(index.find(NAME, "process"))

    def test_both_appear_in_profiles_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = studio_with_same_name(tmp).index()
            pairs = [(p.kind, p.name) for p in index.profiles(origin=ORIGIN_USER)]
            self.assertIn(("filament", NAME), pairs)
            self.assertIn(("process", NAME), pairs)

    def test_process_profile_resolves_to_its_own_values(self):
        """核心回归：工艺档的层高必须读到，而不是变成耗材档的内容。"""
        with tempfile.TemporaryDirectory() as tmp:
            index = studio_with_same_name(tmp).index()
            process = index.find(NAME, "process")
            values, _ = index.resolve(process)
            self.assertEqual(values.get("layer_height"), "0.1")
            self.assertEqual(normalize(values.get("sparse_infill_density")), "100%")
            # 工艺档不该带出耗材参数
            self.assertIsNone(values.get("filament_flow_ratio"))

    def test_filament_profile_resolves_to_its_own_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = studio_with_same_name(tmp).index()
            filament = index.find(NAME, "filament")
            values, _ = index.resolve(filament)
            self.assertEqual(normalize(values.get("filament_flow_ratio")), "0.98")
            # 耗材档不该带出工艺参数
            self.assertIsNone(values.get("layer_height"))

    def test_resolve_by_name_still_works(self):
        """传名字的老用法不能坏（只是同名时会有歧义，故新代码用 find）。"""
        with tempfile.TemporaryDirectory() as tmp:
            index = studio_with_same_name(tmp).index()
            values, _ = index.resolve("0.20mm Standard @BBL A1")
            self.assertEqual(values.get("layer_height"), "0.2")

    def test_find_returns_none_for_wrong_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = studio_with_same_name(tmp).index()
            self.assertIsNone(index.find(NAME, "machine"))


class TestChainNotBroken(unittest.TestCase):
    def test_broken_chain_still_lists_missing_parent(self):
        """断链时把不存在的父级名字也列出来——R1 规则靠它报警。"""
        with tempfile.TemporaryDirectory() as tmp:
            studio = base_studio(tmp)
            studio.add_user("filament", "孤儿", {"inherits": "不存在的父级"})
            index = studio.index()
            self.assertEqual(
                index.chain("孤儿"), ["孤儿", "不存在的父级"]
            )

    def test_normal_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = studio_with_same_name(tmp).index()
            chain = index.chain(index.find(NAME, "process"))
            self.assertEqual(chain[0], NAME)
            self.assertEqual(chain[1], "0.20mm Standard @BBL A1")


if __name__ == "__main__":
    unittest.main()
