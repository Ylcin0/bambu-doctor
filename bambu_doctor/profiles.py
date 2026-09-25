"""profile 的索引与继承链解析 —— bambu-doctor 的核心。

为什么必须解析继承链
--------------------
Bambu Studio 的 profile 是**覆盖式**的：一个 profile 只保存与它 `inherits` 的
父级不同的键。实测（Bambu Studio 2.8.0 自带数据）：

    system/BBL/process/0.20mm Standard @BBL A1.json    只有 11 个键
    system/BBL/filament/Bambu PETG Basic @BBL A1.json  只有 21 个键

真实参数值分散在整条继承链上，常见深度 4–5 层，例如：

    我的PETG ← Bambu PETG Basic @BBL A1 ← Bambu PETG Basic @base
             ← fdm_filament_pet ← fdm_filament_common

只读单个 json 会得到一张残缺参数表，进而得出错误结论。本模块负责把链合并平。

另外，官方默认 G-code 不在继承链上，而是单独存在模板文件里：

    system/BBL/machine/Bambu Lab A1 0.4 nozzle template machine_start_gcode.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

KINDS = ("machine", "filament", "process")

ORIGIN_USER = "user"
ORIGIN_SYSTEM = "system"

# 这些字段不是参数，是元数据，展示时会过滤掉
META_KEYS = frozenset(
    {"name", "inherits", "from", "version", "type", "setting_id", "instantiation",
     "include", "filament_settings_id", "print_settings_id", "printer_settings_id"}
)


@dataclass
class Profile:
    name: str
    path: Path
    kind: str          # machine / filament / process
    origin: str        # user / system
    data: dict

    @property
    def inherits(self) -> str:
        value = self.data.get("inherits", "")
        return value if isinstance(value, str) else ""

    @property
    def override_keys(self) -> list[str]:
        """这个 profile 自己写的键（即相对父级改了什么）。"""
        return sorted(k for k in self.data if k not in META_KEYS)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Profile {self.kind} {self.name!r} [{self.origin}]>"


def normalize(value):
    """
    把 Bambu 的值归一化成可展示的字符串。

    Bambu 的值多为列表形式，且用 "nil" 表示"该层未设置"：
        喷嘴温度       ["245"]           → "245"
        流量比         ["0.94", "nil"]   → "0.94"    （多元素是不同挤出机变体）
        角点列表       ["0x0", "256x0", …] → 见 full=True

    对角点列表必须用 normalize_full() —— 只取首位会得到 "0x0" 这种误导值
    （可打印区域就是这么被读错成 0×0 的）。
    """
    if isinstance(value, list):
        items = [x for x in value if x not in ("nil", None, "")]
        for item in items:
            return str(item)
        return "nil"
    if value is None:
        return "nil"
    return str(value)


def normalize_full(value) -> str:
    """归一化并保留列表全部元素（用于 printable_area / bed_exclude_area 这类角点列表）。"""
    if isinstance(value, list):
        items = [x for x in value if x not in ("nil", None, "")]
        return " / ".join(str(x) for x in items) if items else "nil"
    return normalize(value)


def _is_unset(value) -> bool:
    """判断一个值是否等价于「这条链上没人设过这个键」。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value in ("", "nil")
    if isinstance(value, list):
        return all(_is_unset(v) for v in value)
    return False


class ProfileIndex:
    """把 user / system 两个来源的 profile 建成索引，支持按名字解析继承链。"""

    def __init__(self) -> None:
        self.user: dict[str, Profile] = {}
        self.system: dict[str, Profile] = {}
        self.templates: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------ 构建

    @classmethod
    def build(cls, layout) -> "ProfileIndex":
        """从 discovery.load_layout() 的结果构建索引。"""
        index = cls()
        for user_dir in layout.user_dirs:
            index._scan(user_dir, ORIGIN_USER)
        if layout.system_dir:
            index._scan(layout.system_dir, ORIGIN_SYSTEM)
        return index

    def _scan(self, root: Path, origin: str) -> None:
        """
        递归扫描 root 下的所有 profile。

        必须递归：第三方耗材预设按厂商放在子目录里。实测 Bambu Studio 的布局：

            system/BBL/filament/SUNLU/SUNLU PETG @BBL X1C.json
            system/BBL/filament/Polymaker/...
            system/BBL/filament/P1P/...

        只扫一层会漏掉这些，进而把依赖它们的正常继承链误判成「断链」。
        """
        for path in sorted(root.rglob("*.json")):
            kind = self._kind_of(path, root)
            if not kind:
                continue
            # 模板文件（<机器> template machine_start_gcode.json）不是 profile，
            # 单独收起来供 G-code 差异对比用。
            if " template " in path.stem:
                if origin == ORIGIN_SYSTEM:
                    self._add_template(path)
                continue
            self._add(path, kind, origin)

    @staticmethod
    def _kind_of(path: Path, root: Path) -> str:
        """从相对路径里含 machine/filament/process 的那一段判断类型。"""
        try:
            rel = path.relative_to(root)
        except ValueError:
            return ""
        for part in rel.parts:
            if part in KINDS:
                return part
        return ""

    def _add(self, path: Path, kind: str, origin: str) -> None:
        data = self._load_json(path)
        if not data or "name" not in data:
            return
        profile = Profile(
            name=str(data["name"]), path=path, kind=kind, origin=origin, data=data
        )
        # 同名时用户档优先（用户可能复制官方档并改过）
        target = self.user if origin == ORIGIN_USER else self.system
        target.setdefault(profile.name, profile)

    def _add_template(self, path: Path) -> None:
        base, _, field = path.stem.partition(" template ")
        data = self._load_json(path)
        if not data:
            return
        text = data.get(field)
        if isinstance(text, str):
            self.templates[(base, field)] = text

    @staticmethod
    def _load_json(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------ 查询

    def get(self, name: str) -> Profile | None:
        """按名字取 profile，用户档优先。"""
        return self.user.get(name) or self.system.get(name)

    def profiles(self, origin: str | None = None, kind: str | None = None) -> list[Profile]:
        source = self.user if origin == ORIGIN_USER else (
            self.system if origin == ORIGIN_SYSTEM else {**self.system, **self.user}
        )
        items = list(source.values())
        if kind:
            items = [p for p in items if p.kind == kind]
        return sorted(items, key=lambda p: (p.kind, p.name))

    def chain(self, name: str, limit: int = 16) -> list[str]:
        """继承链，从自己到最远祖先。断链处会标注并停止。"""
        chain: list[str] = []
        current = name
        while current and len(chain) < limit:
            if current in chain:
                break
            chain.append(current)
            profile = self.get(current)
            if profile is None:
                break
            current = profile.inherits
        return chain

    def resolve(self, name: str) -> tuple[dict, dict[str, str]]:
        """
        沿继承链合并参数。返回 (values, layers)：

            values[key] = 最终生效的值
            layers[key] = 该键来自哪一层（用来判断"这是不是我调过的"）

        合并规则：父级打底，子级覆盖。写成迭代而非递归，避免深链爆栈。
        """
        chain = self.chain(name)
        values: dict = {}
        layers: dict[str, str] = {}
        # 从最远的祖先往回合并
        for layer_name in reversed(chain):
            profile = self.get(layer_name)
            if profile is None:
                continue
            for key, value in profile.data.items():
                if key in META_KEYS or key == "inherits":
                    continue
                values[key] = value
                layers[key] = layer_name
        return values, layers

    def baseline_source(self, profile: Profile) -> str:
        """查某个键的官方基线在各层里有没有定义（供 G-code 对比用）。"""
        for link in self.chain(profile.name)[1:]:
            candidate = self.get(link)
            if candidate is not None:
                return link
        return ""

    def official_for(self, machine_name: str, kind: str) -> list[Profile]:
        """取某机型在官方预设里的所有 profile（用作对照基线）。"""
        result = []
        for profile in self.profiles(origin=ORIGIN_SYSTEM, kind=kind):
            if machine_name in profile.name:
                result.append(profile)
        return result


def is_unset(value) -> bool:
    """公开的「未设置」判定，供体检规则使用。"""
    return _is_unset(value)
