"""目标处方：从「我想要什么效果」反推参数该设成多少。

和 diagnose.py 是同一套知识的两个方向：
    diagnose   症状 → 为什么      （打坏了，查原因）
    plan       目标 → 该怎么配    （打之前，给方案）

两个方向共用同一批事实——为诊断写的每条机理，反过来就是一条处方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .checks import detect_target_machine
from .diagnose import Verifier, _num
from .profiles import ORIGIN_USER, ProfileIndex, is_unset, normalize

GOALS_FILE = Path(__file__).parent / "knowledge" / "goals.toml"

LAYER_LABELS = {"process": "工艺档", "filament": "耗材档", "machine": "机器档"}

# 常用参数的中文名（表里没有的就用原键名，宁可显示英文也别编错）
PARAM_LABELS = {
    "layer_height": "层高",
    "line_width": "线宽",
    "wall_loops": "墙层数",
    "top_shell_layers": "顶壳层数",
    "bottom_shell_layers": "底壳层数",
    "sparse_infill_density": "填充密度",
    "sparse_infill_pattern": "填充图案",
    "sparse_infill_direction": "填充方向",
    "outer_wall_speed": "外墙速度",
    "inner_wall_speed": "内墙速度",
    "sparse_infill_speed": "填充速度",
    "initial_layer_speed": "首层速度",
    "fan_max_speed": "冷却风扇上限",
    "fan_min_speed": "冷却风扇下限",
    "filament_flow_ratio": "流量比",
    "nozzle_temperature": "喷嘴温度",
}


class PlanError(Exception):
    """处方文件读不了或格式不对。"""


# ------------------------------------------------------------------ 数据结构

@dataclass
class Setting:
    param: str
    layer: str
    target: str
    why: str = ""
    source: str = "experience"
    priority: int = 2
    note: str = ""

    @property
    def label(self) -> str:
        return PARAM_LABELS.get(self.param, self.param)

    @property
    def mark(self) -> str:
        return {1: "必改", 2: "建议"}.get(self.priority, "可选")


@dataclass
class Hint:
    text: str
    step: str = ""
    priority: int = 2


@dataclass
class Goal:
    id: str
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    source_note: str = ""
    settings: list[Setting] = field(default_factory=list)
    hints: list[Hint] = field(default_factory=list)


@dataclass
class Change:
    setting: Setting
    current: str
    status: str           # ok / change
    layer_name: str = ""
    index: int = 0        # 在 goals.toml 里的原始顺序（输出按它排）


@dataclass
class Plan:
    goal: Goal
    profile_name: str
    process_name: str
    machine: str
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def group(self, status: str) -> list[Change]:
        return [c for c in self.changes if c.status == status]

    def by_layer(self, status: str) -> dict[str, list[Change]]:
        out: dict[str, list[Change]] = {}
        for change in self.group(status):
            items = out.setdefault(change.setting.layer, [])
            items.append(change)
        for items in out.values():
            # 按 goals.toml 里的书写顺序——那是按"先改什么、后改什么"排的，
            # 比按参数名排更符合实际动手顺序
            items.sort(key=lambda c: c.index)
        return out


# ------------------------------------------------------------------ 加载

def _load_toml(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"读不到处方文件：{path}") from exc
    try:
        import tomllib
        return tomllib.loads(text)
    except ModuleNotFoundError:
        try:
            import tomli
            return tomli.loads(text)
        except ModuleNotFoundError as exc:
            raise PlanError("需要 Python 3.11+，或者装上 tomli") from exc
    except Exception as exc:
        raise PlanError(f"处方文件解析失败：{path}\n  {exc}") from exc


class GoalBook:
    """goals.toml 的内存表示。"""

    def __init__(self, goals: dict[str, Goal]):
        self.goals = goals

    @classmethod
    def load(cls, path: Path | None = None) -> "GoalBook":
        data = _load_toml(path or GOALS_FILE)
        goals: dict[str, Goal] = {}
        for raw in data.get("goal", []):
            gid = str(raw.get("id", "")).strip()
            if not gid:
                raise PlanError("有 goal 没写 id")
            if gid in goals:
                raise PlanError(f"goal id 重复：{gid}")
            settings = [
                Setting(
                    param=str(s.get("param", "")),
                    layer=str(s.get("layer", "process")),
                    target=str(s.get("target", "")),
                    why=str(s.get("why", "")),
                    source=str(s.get("source", "experience")),
                    priority=int(s.get("priority", 2)),
                    note=str(s.get("note", "")),
                )
                for s in raw.get("setting", [])
            ]
            for setting in settings:
                if not setting.param:
                    raise PlanError(f"goal「{gid}」里有个 setting 没写 param")
                if setting.layer not in LAYER_LABELS:
                    raise PlanError(
                        f"goal「{gid}」的 {setting.param} 写了未知的 layer：{setting.layer}"
                    )
            goals[gid] = Goal(
                id=gid,
                name=str(raw.get("name", gid)),
                description=str(raw.get("description", "")),
                keywords=[str(k) for k in raw.get("keywords", [])],
                source_note=str(raw.get("source_note", "")),
                settings=settings,
                hints=[
                    Hint(
                        text=str(h.get("text", "")),
                        step=str(h.get("step", "")),
                        priority=int(h.get("priority", 2)),
                    )
                    for h in raw.get("hint", [])
                ],
            )
        if not goals:
            raise PlanError("goals.toml 里一个目标都没有")
        return cls(goals)

    def match(self, text: str) -> list[tuple[Goal, int]]:
        hits = []
        for goal in self.goals.values():
            count = sum(1 for kw in goal.keywords if kw.lower() in text.lower())
            if count:
                hits.append((goal, count))
        return sorted(hits, key=lambda pair: -pair[1])


# ------------------------------------------------------------------ 生成

def _fmt(raw) -> str:
    return "（没设）" if is_unset(raw) else normalize(raw)


def _compare(raw, target: str) -> str:
    if is_unset(raw):
        return "change"
    current = normalize(raw)
    mine, want = _num(current), _num(target)
    if mine is not None and want is not None:
        return "ok" if abs(mine - want) < 1e-6 else "change"
    return "ok" if current.strip().lower() == target.strip().lower() else "change"


def build_plan(index: ProfileIndex, goal: Goal, profile_name: str | None = None,
               process_name: str | None = None) -> Plan:
    """拿用户的真实参数和目标值逐项对比，生成"该改什么"的清单。"""
    machine = detect_target_machine(index)
    filaments = index.profiles(origin=ORIGIN_USER, kind="filament")
    if not filaments:
        raise ValueError("没有找到自定义耗材 profile，没法给方案（先把耗材档存进 Bambu Studio）")

    if profile_name:
        profile = index.user.get(profile_name)
        if profile is None:
            names = "、".join(p.name for p in filaments)
            raise ValueError(f"找不到耗材 profile「{profile_name}」。你的耗材档：{names}")
    else:
        profile = filaments[0]

    processes = index.profiles(origin=ORIGIN_USER, kind="process")
    if process_name and index.user.get(process_name) is None:
        names = "、".join(p.name for p in processes) or "（一份都没有）"
        raise ValueError(f"找不到工艺 profile「{process_name}」。你的工艺档：{names}")
    used_process = process_name or (processes[0].name if processes else "")

    verifier = Verifier(index, profile.name, machine, process_name=process_name)
    plan = Plan(goal=goal, profile_name=profile.name, process_name=used_process,
                machine=machine)

    for order, setting in enumerate(goal.settings):
        raw, layer_name = verifier._lookup(setting.param)
        plan.changes.append(
            Change(
                setting=setting,
                current=_fmt(raw),
                status=_compare(raw, setting.target),
                layer_name=layer_name,
                index=order,
            )
        )

    if len(filaments) > 1:
        plan.notes.append(
            f"耗材档你有 {len(filaments)} 份，这次按「{profile.name}」算；要换用 --profile 指定。"
        )
    if len(processes) > 1:
        plan.notes.append(
            f"工艺档你有 {len(processes)} 份，这次按「{used_process}」算；要换用 --process 指定。"
        )
    if not processes:
        plan.notes.append("没有找到自定义工艺档，层高/填充/速度这类项没法对比。")

    return plan


# ------------------------------------------------------------------ 渲染

def render_plan(plan: Plan, show_ok: bool = True) -> str:
    lines = [
        f"目标：{plan.goal.name}",
        f"基于：耗材档「{plan.profile_name}」"
        f" + 工艺档「{plan.process_name or '（无）'}」"
        f" + 机型 {plan.machine or '未识别'}",
        "",
    ]
    if plan.goal.description:
        lines += [f"（{plan.goal.description}）", ""]

    changes = plan.group("change")
    lines += [f"【要改的 {len(changes)} 项】", ""]

    if not changes:
        lines += ["  已经全部到位了，直接打就行。", ""]
    for layer, items in plan.by_layer("change").items():
        lines.append(f"  ── {LAYER_LABELS.get(layer, layer)} ──")
        for i, change in enumerate(items, 1):
            setting = change.setting
            lines.append(f"  {i}. {setting.label}　[{setting.mark}]")
            lines.append(f"     现在 {change.current}　→　改成 {setting.target}")
            if setting.why:
                lines.append(f"     为什么：{setting.why}")
            if setting.note:
                lines.append(f"     注意：{setting.note}")
        lines.append("")

    ok = plan.group("ok")
    if ok and show_ok:
        lines.append(f"【已经对的 {len(ok)} 项】")
        for change in ok:
            lines.append(f"  · {change.setting.label} {change.current}")
        lines.append("")

    if plan.goal.hints:
        lines.append("【不管参数怎么调，这几步也不能省】")
        lines.append("")
        for i, hint in enumerate(
            sorted(plan.goal.hints, key=lambda h: h.priority), 1
        ):
            lines.append(f"  {i}. {hint.text}")
            if hint.step:
                lines.append(f"     → {hint.step}")
            lines.append("")

    if plan.goal.source_note:
        lines.append(f"依据：{plan.goal.source_note}")
    for note in plan.notes:
        lines.append(f"注：{note}")

    return "\n".join(lines)


def print_goal_list(book: GoalBook) -> None:
    print("已知目标：\n")
    for goal in sorted(book.goals.values(), key=lambda g: g.id):
        print(f"  {goal.id}")
        print(f"    {goal.name} —— {goal.description}")
        if goal.keywords:
            print(f"    可以说：{'、'.join(goal.keywords[:8])}")
        print()
