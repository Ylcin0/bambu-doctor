"""体检规则的豁免配置。

体检规则是"提醒"，不是"判决"。有些偏离是用户**有意**为之的——例如为了压拉丝
主动把喷嘴温度降 15℃，或者明知跨机型继承却只想要那份 profile 的某个参数。
每次跑都报同一批已知项，会让人不再看报告。

所以提供一个 TOML 配置文件声明"这条我知道"：

    # .bambu-doctor.toml
    [check]
    disable = ["R5"]              # 全局关闭某条规则

    [[ignore]]                    # 针对特定 profile 豁免
    profile = "悬挂参考PETG"
    rules = ["R3"]
    reason = "有意降温压拉丝，SUNLU 料"

    [[ignore]]
    profile = "Bambu PETG White"
    rules = ["R2", "R3"]          # 只写 rules 也会匹配（对所有 profile）

匹配规则：
- `profile` 是子串匹配（R5 的 profile 字段形如 "A / B"，两边都能命中）
- `profile` 与 `rules` 同时给出时要求都满足；只给一个时只看那一个
- 缺省配置文件查找顺序：`--config` 指定 → 当前目录 → 用户主目录
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAMES = (".bambu-doctor.toml", "bambu-doctor.toml")


class ConfigError(RuntimeError):
    """配置文件无法读取。"""


@dataclass
class RuleConfig:
    disable: set[str] = field(default_factory=set)
    ignore: list[dict] = field(default_factory=list)
    source: Path | None = None

    def is_ignored(self, finding) -> str:
        """这条发现是否被豁免？返回原因，没被豁免返回空串。"""
        if finding.rule in self.disable:
            return "配置里全局关闭了这条规则"

        for item in self.ignore:
            if not isinstance(item, dict):
                continue
            profile = str(item.get("profile") or "")
            rules = item.get("rules") or []
            if isinstance(rules, str):
                rules = [rules]

            # 两个条件都为空 = 无效条目，跳过（避免误吞全部结果）
            if not profile and not rules:
                continue
            if profile and profile not in (finding.profile or ""):
                continue
            if rules and finding.rule not in rules:
                continue
            return str(item.get("reason") or "配置里豁免")

        return ""

    @property
    def is_empty(self) -> bool:
        return not self.disable and not self.ignore


def find_config(explicit: str | Path | None = None) -> Path | None:
    """按 --config → 当前目录 → 用户主目录 的顺序找配置文件。"""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"指定的配置文件不存在：{path}")
        return path

    for directory in (Path.cwd(), Path.home()):
        for name in CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def _parse_toml(text: str) -> dict:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli  # 3.9/3.10 用户装这个
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "读取 TOML 配置需要 Python 3.11+，或安装 tomli（pip install tomli）"
            ) from exc
        return tomli.loads(text)
    return tomllib.loads(text)


def load_config(explicit: str | Path | None = None) -> RuleConfig:
    """加载配置。没有配置文件就返回空配置（不影响正常体检）。"""
    path = find_config(explicit)
    if path is None:
        return RuleConfig()

    try:
        data = _parse_toml(path.read_text(encoding="utf-8"))
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"配置文件解析失败：{path}\n  {exc}") from exc

    check = data.get("check") or {}
    if not isinstance(check, dict):
        raise ConfigError(f"配置里的 [check] 段应该是表：{path}")

    disable = check.get("disable") or []
    if isinstance(disable, str):
        disable = [disable]

    ignore = data.get("ignore") or []
    if isinstance(ignore, dict):
        ignore = [ignore]

    return RuleConfig(
        disable={str(r).upper() for r in disable},
        ignore=list(ignore),
        source=path,
    )


def apply_ignore(findings: list, config: RuleConfig):
    """按配置过滤发现项，返回 (保留的, [(被豁免的, 原因)])。"""
    if config.is_empty:
        return list(findings), []

    kept, ignored = [], []
    for finding in findings:
        reason = config.is_ignored(finding)
        if reason:
            ignored.append((finding, reason))
        else:
            kept.append(finding)
    return kept, ignored
