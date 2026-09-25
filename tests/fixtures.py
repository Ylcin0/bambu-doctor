"""构造一个假的 Bambu Studio 配置目录，供测试使用。

目录结构与真实的一致：

    <root>/
        user/<账号>/  {machine,filament,process}/*.json
        system/BBL/   {machine,filament,process}/**/*.json
"""

from __future__ import annotations

import json
from pathlib import Path


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


class FakeStudio:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def add_system(self, kind: str, name: str, data: dict | None = None) -> "FakeStudio":
        payload = {"name": name, "from": "system"}
        payload.update(data or {})
        write_json(self.root / "system" / "BBL" / kind / f"{name}.json", payload)
        return self

    def add_system_vendor(
        self, vendor: str, kind: str, name: str, data: dict | None = None
    ) -> "FakeStudio":
        """第三方耗材预设按厂商放子目录里（真实布局就是这样）。"""
        payload = {"name": name, "from": "system"}
        payload.update(data or {})
        write_json(self.root / "system" / "BBL" / kind / vendor / f"{name}.json", payload)
        return self

    def add_system_template(self, machine: str, field: str, text: str) -> "FakeStudio":
        """官方默认 G-code 模板文件。"""
        name = f"{machine} template {field}"
        write_json(
            self.root / "system" / "BBL" / "machine" / f"{name}.json",
            {"name": name, "instantiation": "true", field: text},
        )
        return self

    def add_user(
        self, kind: str, name: str, data: dict | None = None, account: str = "123456"
    ) -> "FakeStudio":
        payload = {"name": name, "from": "User"}
        payload.update(data or {})
        write_json(self.root / "user" / account / kind / f"{name}.json", payload)
        return self

    def index(self):
        from bambu_doctor.discovery import load_layout
        from bambu_doctor.profiles import ProfileIndex

        return ProfileIndex.build(load_layout(self.root))


def base_studio(root: str | Path) -> FakeStudio:
    """一套最小可用的假配置：A1 机器 + PETG/PLA 官方预设。"""
    studio = FakeStudio(root)

    # 机器预设链
    studio.add_system("machine", "fdm_machine_common", {"retraction_length": ["0.4"]})
    studio.add_system(
        "machine",
        "Bambu Lab A1 0.4 nozzle",
        {
            "inherits": "fdm_machine_common",
            "printer_model": "Bambu Lab A1",
            "nozzle_diameter": ["0.4"],
            "printable_area": ["0x0", "256x0", "256x256", "0x256"],
            "printable_height": "256",
            "auxiliary_fan": "0",
        },
    )

    # 耗材预设链（注意 filament_type 用列表形式——真实数据就是这样）
    studio.add_system("filament", "fdm_filament_common", {"filament_diameter": ["1.75"]})
    studio.add_system(
        "filament",
        "fdm_filament_pet",
        {"inherits": "fdm_filament_common", "filament_type": "PETG"},
    )
    studio.add_system(
        "filament",
        "Bambu PETG Basic @BBL A1",
        {
            "inherits": "fdm_filament_pet",
            "filament_type": ["PETG"],
            "nozzle_temperature": ["245"],
            "nozzle_temperature_initial_layer": ["245"],
            "filament_retraction_length": ["0.4"],
        },
    )
    studio.add_system_vendor(
        "SUNLU",
        "filament",
        "SUNLU PETG @BBL X1C",
        {
            "inherits": "fdm_filament_pet",
            "filament_type": ["PETG"],
            "nozzle_temperature": ["250"],
            "filament_retraction_length": ["0.4"],
        },
    )

    # 工艺预设（参数给足，供 R5 重复档检测凑够共同键数）
    studio.add_system(
        "process",
        "fdm_process_common",
        {
            "layer_height": "0.2",
            "initial_layer_print_height": "0.2",
            "line_width": "0.42",
            "outer_wall_line_width": "0.42",
            "inner_wall_line_width": "0.45",
            "travel_speed": ["700"],
        },
    )
    studio.add_system(
        "process",
        "0.20mm Standard @BBL A1",
        {
            "inherits": "fdm_process_common",
            "outer_wall_speed": ["200"],
            "inner_wall_speed": ["300"],
        },
    )
    studio.add_system(
        "process",
        "0.20mm Standard @BBL X1C",
        {
            "inherits": "fdm_process_common",
            "outer_wall_speed": ["200"],
            "inner_wall_speed": ["300"],
            "travel_speed": ["500"],
        },
    )
    return studio
