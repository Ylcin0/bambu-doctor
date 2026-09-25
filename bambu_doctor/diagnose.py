"""推理引擎：症状 × 根因 × 用户的实际参数。

这是整个项目的核心。它做三件事：

  1. 拿到症状对应的候选根因（来自知识库）
  2. 对每条根因，用**用户自己的参数**验证它是否成立
  3. 分成四组输出：判断 / 需人工查 / 已排除 / 数据不足

不做的事：不让 AI 参与推理。AI 只负责"这是什么现象"，判断由确定性逻辑做，
这样结论可测、可积累、换模型也不影响正确性。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .checks import detect_target_machine, machine_matches, machine_of_name
from .knowledge import Cause, KnowledgeBase, Symptom
from .profiles import ORIGIN_SYSTEM, ORIGIN_USER, ProfileIndex, is_unset, normalize

# 四种判定状态
STATUS_CONFIRMED = "confirmed"        # 工具用你的参数验证成立
STATUS_UNDETERMINED = "undetermined"  # 数据不足，判不了
STATUS_MANUAL = "manual"              # 工具查不了，得你自己动手
STATUS_EXCLUDED = "excluded"          # 验证不成立，可以排除


@dataclass
class Verdict:
    cause: Cause
    status: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class Report:
    symptom: Symptom
    profile_name: str
    target_machine: str
    verdicts: list[Verdict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def group(self, status: str) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == status]


def _num(value) -> float | None:
    text = normalize(value)
    if text == "nil":
        return None
    try:
        return float(text)
    except ValueError:
        return None


class Verifier:
    """用某个 profile 的真实参数，验证一条根因是否成立。"""

    def __init__(self, index: ProfileIndex, profile_name: str, target: str):
        self.index = index
        self.profile_name = profile_name
        self.target = target
        self.values, self.layers = index.resolve(profile_name)
        self.machine_values, self.machine_layers = self._machine_context()
        self.baseline_values, self.baseline_name = self._find_baseline()

    # ------------------------------------------------------------ 参数查找

    def _machine_context(self) -> tuple[dict, dict[str, str]]:
        """
        机器级参数。

        有些参数（回抽长度、擦拭前回抽等）定义在**机器 profile** 里而不是耗材档里。
        诊断时必须一起看，否则会把"机器档里设过的参数"误判成"整条链上都没人设过"。
        """
        for profile in self.index.profiles(origin=ORIGIN_USER, kind="machine"):
            return self.index.resolve(profile.name)
        return {}, {}

    def _lookup(self, param: str) -> tuple[object, str]:
        """
        按实际生效顺序查找参数：耗材档 → 机器档。

        Bambu 的优先级是耗材设置覆盖机器设置；两边都没有才算真正未设置。
        返回 (值, 来自哪一层)。
        """
        value = self.values.get(param)
        if not is_unset(value):
            return value, self.layers.get(param, "")
        value = self.machine_values.get(param)
        if not is_unset(value):
            return value, self.machine_layers.get(param, "")
        return None, ""

    # ------------------------------------------------------------ 基线

    def _find_baseline(self) -> tuple[dict, str]:
        """找本机型、同料类型的官方预设作为对照基线。"""
        filament_type = normalize(self.values.get("filament_type"))
        if not filament_type or filament_type == "nil":
            return {}, ""

        best: tuple[dict, str] | None = None
        for profile in self.index.profiles(origin=ORIGIN_SYSTEM, kind="filament"):
            if self.target and not machine_matches(profile.name, self.target):
                continue
            values, _ = self.index.resolve(profile.name)
            if normalize(values.get("filament_type")) != filament_type:
                continue
            if "Basic" in profile.name:
                return values, profile.name   # 基础料的参数最干净
            best = best or (values, profile.name)
        return best or ({}, "")

    # ------------------------------------------------------------ 验证

    def verify(self, cause: Cause) -> Verdict:
        if not cause.is_auto:
            return Verdict(cause=cause, status=STATUS_MANUAL)

        results = [self._run(chk) for chk in cause.checks]
        # 多条检查可能给出同样的说明（比如同一参数被两条 check 各报一次），去重
        evidence: list[str] = []
        for _, text in results:
            if text not in evidence:
                evidence.append(text)

        if any(ok is True for ok, _ in results):
            return Verdict(cause=cause, status=STATUS_CONFIRMED, evidence=evidence)
        if all(ok is False for ok, _ in results):
            return Verdict(cause=cause, status=STATUS_EXCLUDED, evidence=evidence)
        return Verdict(cause=cause, status=STATUS_UNDETERMINED, evidence=evidence)

    def _run(self, chk: dict) -> tuple[bool | None, str]:
        kind = chk.get("kind", "")
        handler = getattr(self, f"_check_{kind}", None)
        if handler is None:
            return None, f"未知的检查类型：{kind}"
        return handler(chk)

    # ------------------------------------------------------------ 各检查实现

    def _check_param_vs_baseline(self, chk: dict) -> tuple[bool | None, str]:
        param = chk["param"]
        mine = _num(self.values.get(param))
        theirs = _num(self.baseline_values.get(param))
        if mine is None or theirs is None:
            return None, f"{param}：数据不足（你的值或官方基线缺失）"

        delta = float(chk.get("delta", 0))
        op = chk.get("op", "gt")
        if op == "gt":
            ok: bool | None = mine > theirs + delta
        elif op == "lt":
            ok = mine < theirs - delta
        elif op == "ne":
            ok = mine != theirs
        elif op == "eq":
            ok = mine == theirs
        else:
            return None, f"{param}：不支持的比较方式 {op}"

        rel = "高于" if mine > theirs else ("低于" if mine < theirs else "等于")
        gap = abs(mine - theirs)
        tail = f"（{rel}官方值 {gap:g}）" if gap else "（与官方值一致）"
        return ok, f"你的 {param} = {mine:g}，官方基线 = {theirs:g} {tail}"

    def _check_param_range(self, chk: dict) -> tuple[bool | None, str]:
        param = chk["param"]
        raw, _layer = self._lookup(param)
        if is_unset(raw):
            return None, f"{param}：未设置，无法判断"
        val = _num(raw)
        if val is None:
            return None, f"{param}：不是数值（{normalize(raw)}）"
        # 0 常表示"该板型/该功能未启用"，算不上问题
        if val == 0 and chk.get("ignore_zero"):
            return None, f"{param} = 0（视为未启用，跳过）"

        low, high = chk.get("min"), chk.get("max")
        breaches = []
        if low is not None and val < float(low):
            breaches.append(f"低于下限 {float(low):g}")
        if high is not None and val > float(high):
            breaches.append(f"高于上限 {float(high):g}")

        if breaches:
            return True, f"你的 {param} = {val:g}（{'，'.join(breaches)}）"
        return False, f"你的 {param} = {val:g}（在合理区间内）"

    def _check_param_unset(self, chk: dict) -> tuple[bool | None, str]:
        param = chk["param"]
        raw, layer = self._lookup(param)
        if is_unset(raw):
            return True, f"{param} 未设置（耗材档和机器档里都没有）→ 会静默回落到更下层的设置"
        where = f"（来自「{layer}」）" if layer else ""
        return False, f"{param} 已设置：{normalize(raw)}{where}"

    def _check_param_absolute(self, chk: dict) -> tuple[bool | None, str]:
        param = chk["param"]
        raw, _layer = self._lookup(param)
        if is_unset(raw):
            return None, f"{param}：未设置"
        mine = normalize(raw)
        target = str(chk.get("value", ""))
        op = chk.get("op", "eq")
        if op == "eq":
            ok: bool | None = mine == target
        elif op == "ne":
            ok = mine != target
        else:
            return None, f"{param}：不支持的比较方式 {op}"
        return ok, f"你的 {param} = {mine}（{'等于' if ok else '不等于'} {target}）"

    def _check_inherit_cross_machine(self, chk: dict) -> tuple[bool | None, str]:
        chain = self.index.chain(self.profile_name)
        for link in chain[1:]:
            other = machine_of_name(link)
            if other and other != self.target:
                return True, f"继承链里有「{other}」的预设：{link}"
        return False, "继承链里没有其他机型的预设"


# ---------------------------------------------------------------- 入口

def diagnose(
    index: ProfileIndex,
    kb: KnowledgeBase,
    symptom_id: str,
    profile_name: str | None = None,
    answers: dict[str, str] | None = None,
) -> Report:
    symptom = kb.get_symptom(symptom_id)
    if symptom is None:
        known = ", ".join(sorted(kb.symptoms))
        raise ValueError(f"未知症状「{symptom_id}」。已知：{known}")

    target = detect_target_machine(index)

    filaments = index.profiles(origin=ORIGIN_USER, kind="filament")
    if not filaments:
        raise ValueError("没有找到自定义耗材 profile，无法诊断（先把你的耗材档存进 Bambu Studio）")

    if profile_name:
        profile = index.user.get(profile_name)
        if profile is None:
            names = ", ".join(p.name for p in filaments)
            raise ValueError(f"找不到耗材 profile「{profile_name}」。你的耗材档：{names}")
    else:
        profile = filaments[0]

    verifier = Verifier(index, profile.name, target)
    report = Report(symptom=symptom, profile_name=profile.name, target_machine=target)

    for cause_id in symptom.causes_for(answers):
        cause = kb.get_cause(cause_id)
        if cause is None:
            continue
        report.verdicts.append(verifier.verify(cause))

    if len(filaments) > 1:
        report.notes.append(
            f"你有 {len(filaments)} 份耗材档，本次基于「{profile.name}」判断；"
            f"要换一份用 --profile 指定。"
        )
    if not verifier.baseline_name:
        report.notes.append("没找到本机型对应的官方耗材基线，涉及「与官方值对比」的判断会跳过。")

    return report


# ---------------------------------------------------------------- 渲染

_HEADERS = {
    STATUS_CONFIRMED: "【判断】下面这些根因，在你的参数里是成立的",
    STATUS_MANUAL: "【需要你自己查】工具读不到，但你两分钟能确认",
    STATUS_UNDETERMINED: "【数据不足】想判但缺参数",
    STATUS_EXCLUDED: "【已排除】这些解释在你这里不成立，不用试了",
}


def render_text(report: Report, show_evidence: bool = True) -> str:
    lines = [
        f"症状：{report.symptom.name}",
        f"基于耗材档：{report.profile_name}　机型：{report.target_machine or '未识别'}",
        "",
    ]
    if report.symptom.description:
        lines.append(f"（{report.symptom.description}）")
        lines.append("")

    for status in (STATUS_CONFIRMED, STATUS_MANUAL, STATUS_UNDETERMINED, STATUS_EXCLUDED):
        group = report.group(status)
        if not group:
            continue
        lines.append(_HEADERS[status])
        lines.append("")
        for i, verdict in enumerate(group, 1):
            cause = verdict.cause
            lines.append(f"  {i}. {cause.name}")
            if cause.confidence == "low":
                lines.append(f"     ⚠ 置信度较低：这是未充分验证的经验")
            for text in verdict.evidence:
                if show_evidence or status == STATUS_CONFIRMED:
                    lines.append(f"     依据：{text}")
            if status != STATUS_EXCLUDED:
                if cause.mechanism:
                    lines.append(f"     机理：{cause.mechanism}")
                if cause.manual_check:
                    lines.append(f"     怎么查：{cause.manual_check}")
                if cause.action:
                    lines.append(f"     动作：{cause.action}")
                if cause.cost:
                    lines.append(f"     代价：{cause.cost}")
                if cause.verify:
                    lines.append(f"     验证：{cause.verify}")
                if cause.note:
                    lines.append(f"     注意：{cause.note}")
            lines.append("")

    if report.symptom.distinguishing:
        lines.append("【对号入座】这个症状的判别要点")
        for item in report.symptom.distinguishing:
            lines.append(f"  · {item}")
        lines.append("")

    if report.symptom.questions:
        lines.append("【追问】")
        for q in report.symptom.questions:
            lines.append(f"  · {q.get('ask', '')}")
            if q.get("pending"):
                lines.append(f"      → {q['pending']}")
        lines.append("")

    for note in report.notes:
        lines.append(f"注：{note}")

    return "\n".join(lines)
