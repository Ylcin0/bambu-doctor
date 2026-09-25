"""定位 Bambu Studio 的配置目录（跨平台）。

Bambu Studio 把配置放在用户配置区（不是程序安装目录）：

    Windows   %APPDATA%\\BambuStudio
    macOS     ~/Library/Application Support/BambuStudio
    Linux     ~/.config/BambuStudio  （Flatpak: ~/.var/app/com.bambulab.BambuStudio/config/BambuStudio）

目录结构（对本工具重要的部分）：

    <root>/
        user/<账号ID>/{machine,filament,process}/*.json   你的自定义 profile
        system/BBL/{machine,filament,process}/*.json       官方预设（含继承链的根）
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


class DiscoveryError(RuntimeError):
    """找不到 Bambu Studio 配置目录。"""


def candidate_studio_dirs() -> list[Path]:
    """按平台返回 Bambu Studio 配置目录的候选路径（按可能性排序）。"""
    home = Path.home()
    candidates: list[Path] = []

    if sys.platform == "win32":
        for env_var in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env_var)
            if base:
                candidates.append(Path(base) / "BambuStudio")
        # 便携版可能把配置放在程序目录
        candidates.append(Path("C:/BambuStudio/Config"))
    elif sys.platform == "darwin":
        candidates.append(home / "Library" / "Application Support" / "BambuStudio")
    else:
        candidates.append(home / ".config" / "BambuStudio")
        candidates.append(
            home / ".var" / "app" / "com.bambulab.BambuStudio" / "config" / "BambuStudio"
        )

    # 去重且保持顺序
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


@dataclass
class StudioLayout:
    """一个已定位的 Bambu Studio 配置目录布局。"""

    root: Path
    user_dirs: list[Path] = field(default_factory=list)
    system_dir: Path | None = None

    @property
    def is_usable(self) -> bool:
        """至少要有一个 user 目录或 system 目录才算能用。"""
        return bool(self.user_dirs or self.system_dir)

    def describe(self) -> str:
        parts = [f"配置目录: {self.root}"]
        parts.append(f"自定义 profile 目录: {len(self.user_dirs)} 个")
        for d in self.user_dirs:
            parts.append(f"  - {d}")
        parts.append(
            f"官方预设目录: {self.system_dir}" if self.system_dir else "官方预设目录: 未找到"
        )
        return "\n".join(parts)


def load_layout(explicit_root: str | Path | None = None) -> StudioLayout:
    """
    定位并检查 Bambu Studio 配置目录。

    explicit_root 由命令行 --studio-dir 传入；不传则按平台默认路径探测。
    """
    if explicit_root:
        roots = [Path(explicit_root).expanduser()]
    else:
        roots = candidate_studio_dirs()

    problems: list[str] = []
    for root in roots:
        if not root.is_dir():
            problems.append(f"{root} 不存在")
            continue

        user_dirs: list[Path] = []
        user_root = root / "user"
        if user_root.is_dir():
            # 实测 user/ 下通常是账号目录（Bambu 账号数字 ID）；
            # 也见过只有 user/default 的情形。两者都收。
            for child in sorted(user_root.iterdir()):
                if child.is_dir() and any(
                    (child / kind).is_dir() for kind in ("machine", "filament", "process")
                ):
                    user_dirs.append(child)

        system_dir = root / "system" / "BBL"
        layout = StudioLayout(
            root=root,
            user_dirs=user_dirs,
            system_dir=system_dir if system_dir.is_dir() else None,
        )
        if layout.is_usable:
            return layout
        problems.append(f"{root} 存在但没有 profile 子目录")

    detail = "\n  ".join(problems) if problems else "（没有候选路径）"
    raise DiscoveryError(
        "找不到 Bambu Studio 的配置目录。\n"
        f"已尝试：\n  {detail}\n"
        "请确认 Bambu Studio 装过并至少启动过一次，或用 --studio-dir 手动指定。"
    )
