"""体检规则引擎 —— bambu-doctor 的核心价值。

每条规则检查一类**真实会踩的坑**，全部来自实战：

  R1 断链继承        profile 继承的父级在系统里不存在（来自没装的机型/插件）
  R2 跨机型继承      profile 继承的是别的机型的预设（封闭机箱 vs 开放机的参数不同）
  R3 温度偏离        喷嘴温度偏离本机型官方值过多（换了机型没换 profile 的典型症状）
  R4 参数落空        filament 参数为 nil → 静默回落到机器级设置，与预期不符
  R5 重复档          多份参数几乎相同的 profile（同一套参数的两次尝试）
  R6 继承链深度      信息项：参数是从哪几层叠出来的（调参时最容易搞错的地方）

每条 Finding 都带 severity、涉及的 profile 名、具体数值和建议动作。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .profiles import ORIGIN_SYSTEM, ORIGIN_USER, ProfileIndex, is_unset, normalize

# 严重度排序：error 排最前
LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}

# 官方预设命名形如 "Bambu PETG Basic @BBL A1"，@BBL 后面整段就是机型标识
_MODEL_SUFFIX_RE = re.compile(r"@BBL\s+(.+)$")
# 机器预设名形如 "Bambu Lab A1 0.4 nozzle" / "Bambu Lab A1 mini 0.4 nozzle"
_MACHINE_PRESET_RE = re.compile(r"Bambu Lab\s+(.+?)\s+\d")
# 机型标识后面可能跟喷嘴规格（"A1 0.2 nozzle"），比对前要剥掉
_NOZZLE_SUFFIX_RE = re.compile(r"\s+\d+(?:\.\d+)?\s*nozzle.*$")


@dataclass
class Finding:
    rule: str
    level: str          # error / warning / info
    title: str
    detail: str
    profile: str = ""
    hint: str = ""

    def __str__(self) -> str:
        where = f" [{self.profile}]" if self.profile else ""
        return f"{self.level.upper():<7} {self.rule}{where} — {self.title}\n        {self.detail}"


# ------------------------------------------------------------------ 工具函数

def machine_of_name(name: str) -> str:
    """
    从 profile 名里提取机型标识，提取不到返回空串。

    必须取 @BBL 后的**完整尾部**再剥喷嘴后缀。只取首个字母数字串会把
    "A1 mini"、"A1M" 都截成 "A1"，导致机型比对错误——把 A1 mini 的预设
    当成 A1 的，进而漏报或错报跨机型继承。
    """
    match = _MODEL_SUFFIX_RE.search(name)
    if match:
        model = _NOZZLE_SUFFIX_RE.sub("", match.group(1).strip()).strip()
        if model:
            return model
    match = _MACHINE_PRESET_RE.search(name)
    if match:
        return match.group(1).strip()
    return ""


def machine_matches(name: str, target: str) -> bool:
    """判断预设名是否属于目标机型（精确比对：A1 不会命中 A1 mini / A1M）。"""
    if not target:
        return False
    return machine_of_name(name) == target


def detect_target_machine(index: ProfileIndex) -> str:
    """从用户自己的机器 profile 推断目标机型。"""
    for profile in index.profiles(origin=ORIGIN_USER, kind="machine"):
        values, _ = index.resolve(profile.name)
        model = normalize(values.get("printer_model"))
        if model and model != "nil":
            match = re.search(r"Bambu Lab\s+(.+)", model)
            if match:
                return match.group(1).strip()
    # 退一步：从官方机器预设名里猜
    for profile in index.profiles(origin=ORIGIN_SYSTEM, kind="machine"):
        machine = machine_of_name(profile.name)
        if machine:
            return machine
    return ""


def _num(value) -> float | None:
    """从 Bambu 的值里取数值，取不到返回 None。"""
    text = normalize(value)
    if text == "nil":
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ------------------------------------------------------------------ R1 断链

def check_broken_chain(index: ProfileIndex) -> list[Finding]:
    """继承链上引用了系统中不存在的 profile。"""
    findings: list[Finding] = []
    for profile in index.profiles(origin=ORIGIN_USER):
        for link in index.chain(profile.name)[1:]:
            if index.get(link) is None:
                findings.append(
                    Finding(
                        rule="R1",
                        level="warning",
                        profile=profile.name,
                        title=f"继承链断裂：找不到父级「{link}」",
                        detail=(
                            f"这个 profile 继承自「{link}」，但它不在本机 Bambu Studio 里。"
                            "通常是从别的机器/别的版本拷过来的 profile，"
                            "或依赖一个你没装的机型预设。"
                        ),
                        hint="链断在这里，下游所有参数解析结果都不可信——建议改继承一个本机存在的预设。",
                    )
                )
                break  # 一条链只报一次
    return findings


# ------------------------------------------------------------------ R2 跨机型继承

def check_cross_machine_inheritance(index: ProfileIndex, target: str) -> list[Finding]:
    """profile 继承的预设属于别的机型。"""
    if not target:
        return []
    findings: list[Finding] = []
    for profile in index.profiles(origin=ORIGIN_USER):
        if profile.kind == "machine":
            # 机器 profile 本来就继承本机型预设，不检查
            continue
        for link in index.chain(profile.name)[1:]:
            other = machine_of_name(link)
            if other and other != target:
                findings.append(
                    Finding(
                        rule="R2",
                        level="warning",
                        profile=profile.name,
                        title=f"跨机型继承：继承的是「{other}」的预设，你用的是「{target}」",
                        detail=(
                            f"继承链：{' ← '.join(index.chain(profile.name))}。"
                            f"不同机型的官方预设是按各自硬件调的——封闭机箱与开放机的"
                            f"腔温、风扇、最高温度都不一样，直接套用会带来偏差。"
                        ),
                        hint=f"考虑改成继承「{target}」对应的预设，或显式覆盖掉差异项。",
                    )
                )
                break
    return findings


# ------------------------------------------------------------------ R3 温度偏离

# 温度容差（摄氏度）。超过这个差值才报。
# 设 5 而不是 10：FDM 里 5℃ 就有可感知差异，而"从别的机型继承来的温度"
# 典型偏差正好是 10℃（封闭机箱机型的 PETG 比开放机高 10℃），
# 容差设 10 会把这类真问题卡在边界上漏掉。
TEMP_TOLERANCE = 5.0


def _official_baseline(index: ProfileIndex, filament_type: str, target: str):
    """找本机型下、同料类型的官方预设，作为对照基线。"""
    best = None
    for profile in index.profiles(origin=ORIGIN_SYSTEM, kind="filament"):
        if target and not machine_matches(profile.name, target):
            continue
        values, _ = index.resolve(profile.name)
        # 必须 normalize 后再比：Bambu 的值形态不统一，同一字段在有的层是
        # 字符串、在有的层是列表（实测会被解析成 ['PETG']），直接比字符串必失败
        if normalize(values.get("filament_type")) == filament_type:
            if "Basic" in profile.name:
                return profile  # 基础料的参数最干净，优先
            best = best or profile
    return best


def check_temperature_deviation(index: ProfileIndex, target: str) -> list[Finding]:
    """喷嘴温度与官方值的偏离。"""
    findings: list[Finding] = []
    for profile in index.profiles(origin=ORIGIN_USER, kind="filament"):
        values, _ = index.resolve(profile.name)
        filament_type = normalize(values.get("filament_type"))
        if not filament_type or filament_type == "nil":
            continue

        baseline = _official_baseline(index, filament_type, target)
        if baseline is None:
            continue
        base_values, _ = index.resolve(baseline.name)

        mine = _num(values.get("nozzle_temperature"))
        theirs = _num(base_values.get("nozzle_temperature"))
        if mine is None or theirs is None:
            continue

        delta = mine - theirs
        if abs(delta) <= TEMP_TOLERANCE:
            continue

        direction = "高" if delta > 0 else "低"
        findings.append(
            Finding(
                rule="R3",
                level="warning",
                profile=profile.name,
                title=f"喷嘴温度 {mine:.0f}℃ 比本机型官方值 {theirs:.0f}℃ {direction} {abs(delta):.0f}℃",
                detail=(
                    f"对照基线：{baseline.name}（同为 {filament_type}）。"
                    + (
                        "温度偏高会让料更稀、拉丝显著加剧、也更容易渗料。"
                        if delta > 0
                        else "温度偏低会导致层间结合不足、挤出阻力变大，甚至堵头。"
                    )
                    + " 若这是从别的机型拷来的 profile，温度值可能还是那台机器的设定。"
                ),
                hint=f"要贴近本机型的话，把喷嘴温度调到 {theirs:.0f}℃ 左右再逐次试。",
            )
        )
    return findings


# ------------------------------------------------------------------ R4 参数落空

def check_unset_fallback(index: ProfileIndex) -> list[Finding]:
    """
    filament 层的回抽参数为 nil 时会静默回落到机器级设置。

    Bambu Studio 的参数优先级：filament 里有值 → 用 filament 的；
    filament 里是 nil → 回落到 machine 的 retraction_length。
    多数人不知道这一点，会以为用的是耗材档里的值。
    """
    findings: list[Finding] = []

    machine_retraction = None
    for profile in index.profiles(origin=ORIGIN_USER, kind="machine"):
        values, _ = index.resolve(profile.name)
        machine_retraction = _num(values.get("retraction_length"))
        if machine_retraction is not None:
            break

    if machine_retraction is None:
        return []

    for profile in index.profiles(origin=ORIGIN_USER, kind="filament"):
        values, _ = index.resolve(profile.name)
        if not is_unset(values.get("filament_retraction_length")):
            continue
        findings.append(
            Finding(
                rule="R4",
                level="info",
                profile=profile.name,
                title=f"回抽长度未设，实际用的是机器级的 {machine_retraction:.1f}mm",
                detail=(
                    "这份耗材 profile 的 filament_retraction_length 是 nil，"
                    "整条继承链上也没人设过，所以实际生效的是机器 profile 里的回抽值。"
                    "不同料对回抽的容忍度不同，混用容易出细拉丝或粗短疙瘩。"
                ),
                hint="若这是有意的就忽略；否则在这个耗材档里显式设一个回抽长度。",
            )
        )
    return findings


# ------------------------------------------------------------------ R5 重复档

DUP_THRESHOLD = 0.9   # 参数重合比例
DUP_MIN_KEYS = 5      # 至少这么多共同键才算


def check_duplicates(index: ProfileIndex) -> list[Finding]:
    """
    多份参数几乎相同的 profile。

    只查 process / machine，**不查 filament**：按颜色或品牌建多个耗材 profile
    是正常用法，它们从同一份官方预设继承、参数自然接近，报出来全是噪音。
    真正值得提醒的重复是工艺/机器档——同一套参数的两次尝试。
    """
    findings: list[Finding] = []
    for kind in ("process", "machine"):
        profiles = index.profiles(origin=ORIGIN_USER, kind=kind)
        for i, left in enumerate(profiles):
            for right in profiles[i + 1:]:
                left_v, _ = index.resolve(left.name)
                right_v, _ = index.resolve(right.name)
                shared = set(left_v) & set(right_v)
                shared = {k for k in shared if not k.startswith("filament_settings_id")}
                if len(shared) < DUP_MIN_KEYS:
                    continue
                same = sum(1 for k in shared if normalize(left_v[k]) == normalize(right_v[k]))
                ratio = same / len(shared)
                if ratio < DUP_THRESHOLD:
                    continue
                diffs = [k for k in sorted(shared) if normalize(left_v[k]) != normalize(right_v[k])]
                findings.append(
                    Finding(
                        rule="R5",
                        level="info",
                        profile=f"{left.name} / {right.name}",
                        title=f"两份 profile 参数重合度 {ratio:.0%}",
                        detail=(
                            f"「{left.name}」和「{right.name}」有 {same}/{len(shared)} 个参数完全相同。"
                            + (f" 差异项：{', '.join(diffs[:5])}。" if diffs else " 没有差异项。")
                        ),
                        hint="同一套参数的多次尝试通常留一份就够，避免以后改错对象。",
                    )
                )
    return findings


# ------------------------------------------------------------------ R6 继承链

def check_chain_depth(index: ProfileIndex) -> list[Finding]:
    """信息项：参数是从哪几层叠出来的。"""
    findings: list[Finding] = []
    for profile in index.profiles(origin=ORIGIN_USER):
        chain = index.chain(profile.name)
        if len(chain) < 3:
            continue
        _, layers = index.resolve(profile.name)
        counts: dict[str, int] = {}
        for layer in layers.values():
            counts[layer] = counts.get(layer, 0) + 1
        breakdown = "，".join(
            f"{name} 贡献 {counts.get(name, 0)} 项"
            for name in chain
            if counts.get(name, 0)
        )
        findings.append(
            Finding(
                rule="R6",
                level="info",
                profile=profile.name,
                title=f"继承链 {len(chain)} 层",
                detail=f"{' ← '.join(chain)}\n        参数来源：{breakdown}",
                hint="你写在最上层的值才会覆盖下面；下面几层里躺着的默认值同样生效。",
            )
        )
    return findings


# ------------------------------------------------------------------ 入口

ALL_CHECKS = (
    check_broken_chain,
    check_cross_machine_inheritance,
    check_temperature_deviation,
    check_unset_fallback,
    check_duplicates,
    check_chain_depth,
)


def run_checks(index: ProfileIndex, verbose: bool = False) -> list[Finding]:
    """
    跑全部规则，按严重度排序返回。

    verbose=False 时跳过 R6（继承链信息）：它给每个 profile 都产一条，
    数量远超真问题，会把报告淹掉。
    """
    target = detect_target_machine(index)
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        if check is check_chain_depth and not verbose:
            continue
        if check in (check_cross_machine_inheritance, check_temperature_deviation):
            findings.extend(check(index, target))
        else:
            findings.extend(check(index))
    return sorted(findings, key=lambda f: (LEVEL_ORDER.get(f.level, 9), f.rule, f.profile))


def count_by_level(findings: list[Finding]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        counts[finding.level] = counts.get(finding.level, 0) + 1
    return counts
