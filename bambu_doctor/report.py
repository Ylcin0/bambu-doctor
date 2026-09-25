"""输出层：把解析结果渲染成 markdown / JSON / 终端文本。"""

from __future__ import annotations

import datetime as dt
import difflib
import json
from pathlib import Path

from .checks import Finding, count_by_level
from .profiles import (
    ORIGIN_SYSTEM,
    ORIGIN_USER,
    Profile,
    ProfileIndex,
    normalize,
    normalize_full,
)

# ------------------------------------------------------------------ 参数标签

MACHINE_KEYS: list[tuple[str, str]] = [
    ("printer_model", "机型"),
    ("printer_variant", "变体"),
    ("nozzle_diameter", "喷嘴直径"),
    ("printable_area", "可打印区域 XY（四角坐标）"),
    ("printable_height", "最大打印高度 Z"),
    ("machine_max_acceleration_x", "X 最大加速度"),
    ("machine_max_acceleration_y", "Y 最大加速度"),
    ("machine_max_acceleration_z", "Z 最大加速度"),
    ("machine_max_acceleration_extruding", "挤出最大加速度"),
    ("machine_max_acceleration_travel", "空驶最大加速度"),
    ("machine_max_speed_x", "X 最大速度"),
    ("machine_max_speed_y", "Y 最大速度"),
    ("machine_max_speed_z", "Z 最大速度"),
    ("machine_max_jerk_x", "X Jerk"),
    ("machine_max_jerk_y", "Y Jerk"),
    ("retraction_length", "机器回抽长度"),
    ("retraction_speed", "机器回抽速度"),
    ("deretraction_speed", "回填速度"),
    ("retract_before_wipe", "擦拭前回抽"),
    ("retraction_minimum_travel", "最小回抽移动距离"),
    ("z_hop", "Z 抬升"),
    ("auxiliary_fan", "辅助风扇"),
    ("support_chamber_temp_control", "腔体加热控制"),
    ("default_filament_profile", "默认耗材预设"),
    ("default_print_profile", "默认工艺预设"),
]

FILAMENT_KEYS: list[tuple[str, str]] = [
    ("filament_type", "耗材类型"),
    ("nozzle_temperature", "喷嘴温度"),
    ("nozzle_temperature_initial_layer", "首层喷嘴温度"),
    ("nozzle_temperature_range_low", "喷嘴温度下限"),
    ("nozzle_temperature_range_high", "喷嘴温度上限"),
    ("hot_plate_temp", "热床温度"),
    ("hot_plate_temp_initial_layer", "首层热床温度"),
    ("cool_plate_temp", "低温板温度"),
    ("cool_plate_temp_initial_layer", "低温板首层温度"),
    ("textured_plate_temp", "纹理 PEI 板温度"),
    ("textured_plate_temp_initial_layer", "纹理 PEI 板首层温度"),
    ("eng_plate_temp", "工程板温度"),
    ("supertack_plate_temp", "Supertack 板温度"),
    ("filament_flow_ratio", "流量比"),
    ("filament_shrink", "收缩率补偿"),
    ("filament_retraction_length", "回抽长度"),
    ("filament_retraction_speed", "回抽速度"),
    ("filament_z_hop_types", "Z-hop 类型"),
    ("filament_max_volumetric_speed", "最大体积流量"),
    ("filament_density", "密度"),
    ("filament_cost", "单价"),
    ("filament_diameter", "线径"),
    ("chamber_temperature", "腔体温度"),
    ("filament_notes", "备注"),
]

PROCESS_KEYS: list[tuple[str, str]] = [
    ("layer_height", "层高"),
    ("initial_layer_print_height", "首层层高"),
    ("line_width", "默认线宽"),
    ("outer_wall_line_width", "外壁线宽"),
    ("inner_wall_line_width", "内壁线宽"),
    ("internal_solid_infill_line_width", "实心填充线宽"),
    ("outer_wall_speed", "外壁速度"),
    ("inner_wall_speed", "内壁速度"),
    ("initial_layer_speed", "首层速度"),
    ("initial_layer_infill_speed", "首层填充速度"),
    ("default_acceleration", "默认加速度"),
    ("outer_wall_acceleration", "外壁加速度"),
    ("inner_wall_acceleration", "内壁加速度"),
    ("initial_layer_acceleration", "首层加速度"),
    ("travel_speed", "空驶速度"),
    ("bridge_flow", "桥接流量"),
    ("bridge_speed", "桥接速度"),
    ("max_bridge_length", "最大桥接长度"),
    ("overhang_2_4_speed", "悬垂 25% 速度"),
    ("overhang_3_4_speed", "悬垂 50% 速度"),
    ("overhang_4_4_speed", "悬垂 75% 速度"),
    ("overhang_totally_speed", "全悬垂速度"),
    ("enable_support", "启用支撑"),
    ("support_threshold_angle", "支撑阈值角度"),
    ("support_interface_spacing", "支撑界面间距"),
    ("support_interface_top_layers", "支撑界面顶层数"),
    ("support_object_xy_distance", "支撑-物件 XY 距离"),
    ("support_interface_pattern", "支撑界面图案"),
    ("reduce_crossing_wall", "避免跨越外墙"),
    ("precise_outer_wall", "精确外壁"),
    ("seam_position", "接缝位置"),
    ("seam_slope_type", "斜接缝类型"),
    ("prime_tower_infill_gap", "擦料塔填充间隙"),
    ("prime_tower_max_speed", "擦料塔最大速度"),
    ("fan_max_speed", "风扇最大转速"),
    ("fan_min_speed", "风扇最小转速"),
    ("skeleton_infill_density", "骨架填充密度"),
    ("raft_first_layer_density", "raft 首层密度"),
    ("resolution", "分辨率"),
    ("infill_combination", "填充组合"),
    ("max_travel_detour_distance", "最大空驶绕行距离"),
    ("min_bead_width", "最小挤出宽度"),
]

KEYS_BY_KIND = {"machine": MACHINE_KEYS, "filament": FILAMENT_KEYS, "process": PROCESS_KEYS}

# 角点类列表必须全列，只取首位会得到 "0x0" 这种误导值
FULL_LIST_KEYS = frozenset({"printable_area", "bed_exclude_area"})

KIND_TITLE = {"machine": "机器档案", "filament": "耗材档案", "process": "工艺档案"}


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _header(title: str, note: str, source: str) -> str:
    return (
        f"# {title}\n\n"
        f"> 自动生成，勿手改。生成时间 {_stamp()}　数据源：`{source}`\n"
        f"> 重新生成：`bambu-doctor extract`\n\n"
        f"{note}\n\n"
    )


# ------------------------------------------------------------------ profile 档案

def render_profile_section(kind: str, profiles: list[Profile], index: ProfileIndex) -> str:
    keys = KEYS_BY_KIND[kind]
    lines: list[str] = []
    for profile in profiles:
        values, layers = index.resolve(profile.name)
        chain = index.chain(profile.name)
        lines.append(f"### {profile.name}")
        lines.append("")
        lines.append(f"- **来源**：`{profile.path.name}`")
        lines.append(f"- **继承链**：{' ← '.join(chain)}")
        lines.append("")
        lines.append("| 参数 | 值 | 来源层 |")
        lines.append("|---|---|---|")
        found = 0
        for key, label in keys:
            if key not in values:
                continue
            found += 1
            layer = layers.get(key, "?")
            mark = "← 你调的" if layer == profile.name else ""
            shown = (
                normalize_full(values[key]) if key in FULL_LIST_KEYS else normalize(values[key])
            )
            lines.append(f"| {label} | `{shown}` | {layer} {mark} |")
        if not found:
            lines.append("| （该 profile 未涉及这些参数） | | |")
        lines.append("")
    return "\n".join(lines)


def render_extractables(index: ProfileIndex) -> dict[str, str]:
    """生成各类档案的 markdown 全文，键是文件名。"""
    out: dict[str, str] = {}
    for kind in ("machine", "filament", "process"):
        profiles = index.profiles(origin=ORIGIN_USER, kind=kind)
        note = {
            "machine": "你自定义的机器 profile（含被改写的 G-code 所在的那份）。",
            "filament": "你所有自定义耗材 profile。`← 你调的` 标记的键 = 与你继承的那份预设不同。",
            "process": "你所有自定义工艺 profile。",
        }[kind]
        body = render_profile_section(kind, profiles, index)
        if not body:
            body = "（没有自定义 profile）\n"
        out[f"{kind}.md"] = _header(
            KIND_TITLE[kind], note, f"Bambu Studio 的 user/*/{kind}"
        ) + body
    out["tuning-reference.md"] = _header(
        "调参真值表",
        "调参时直接查这张表：左列是你的值，右括号里是本机型官方基线。**粗体** = 你改过的。",
        "Bambu Studio 的 user/*/filament + system/BBL/filament",
    ) + render_tuning_table(index)
    out["gcode.md"] = _header(
        "自定义 G-code",
        "机器 profile 里被改写过的开始/结束 G-code，以及与官方默认的差异。★ = 你注释里标出的改动点。",
        "Bambu Studio 的 user/*/machine + system/BBL/machine 模板",
    ) + render_gcode(index)
    return out


# ------------------------------------------------------------------ 调参真值表

def render_tuning_table(index: ProfileIndex) -> str:
    profiles = index.profiles(origin=ORIGIN_USER, kind="filament")
    if not profiles:
        return "（没有自定义耗材 profile）\n"

    rows = []
    for profile in profiles:
        values, _ = index.resolve(profile.name)
        baseline_name = ""
        baseline_values: dict = {}
        for link in index.chain(profile.name)[1:]:
            candidate = index.get(link)
            if candidate is not None and candidate.origin == ORIGIN_SYSTEM:
                baseline_name = link
                baseline_values, _ = index.resolve(link)
                break

        def pair(key: str) -> str:
            mine = normalize(values[key]) if key in values else "—"
            theirs = normalize(baseline_values[key]) if key in baseline_values else "—"
            if mine != "—" and theirs != "—" and mine != theirs:
                return f"**{mine}**（官方 {theirs}）"
            return mine

        rows.append(
            (
                profile.name,
                baseline_name or "（无官方对应）",
                pair("filament_flow_ratio"),
                pair("filament_shrink"),
                pair("nozzle_temperature"),
                pair("nozzle_temperature_initial_layer"),
                pair("filament_retraction_length"),
                pair("filament_max_volumetric_speed"),
                pair("hot_plate_temp"),
                pair("textured_plate_temp"),
                pair("cool_plate_temp"),
            )
        )

    header = (
        "| 耗材 | 官方基线 | 流量比 | 收缩率 | 喷嘴温 | 首层喷嘴温 | 回抽长度 "
        "| 最大体积流量 | 热床 | 纹理板 | 低温板 |"
    )
    sep = "|" + "---|" * 11
    lines = [header, sep]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    lines += [
        "",
        "> 打印板分三种，看对应那列：低温板（CryoGrip 等）、纹理板（Textured PEI）、热床（通用）。",
        "> `nil` 表示整条继承链上没人设过这个键——它会回落到机器级设置，不等于 0。",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ G-code

def render_gcode(index: ProfileIndex) -> str:
    out: list[str] = []
    for profile in index.profiles(origin=ORIGIN_USER, kind="machine"):
        chain = index.chain(profile.name)
        found_any = False
        for field, label in (
            ("machine_start_gcode", "开始 G-code"),
            ("machine_end_gcode", "结束 G-code"),
        ):
            mine = profile.data.get(field)
            if not isinstance(mine, str) or not mine.strip():
                continue
            found_any = True

            # 官方基线：机型专属模板优先（那才是被复制改造的那份），继承链兜底
            theirs, source = "", ""
            if len(chain) > 1:
                template = index.templates.get((chain[1], field), "")
                if template:
                    theirs, source = template, f"官方模板 · {chain[1]}"
            if not theirs:
                for link in chain[1:]:
                    candidate = index.get(link)
                    if candidate and isinstance(candidate.data.get(field), str):
                        theirs, source = candidate.data[field], f"继承链 · {link}"
                        break

            mine_lines = mine.splitlines()
            stars = [line.strip() for line in mine_lines if "★" in line]

            out.append(f"## {profile.name} — {label}")
            out.append("")
            if theirs:
                diff = difflib.unified_diff(
                    theirs.splitlines(), mine_lines, lineterm="", n=0
                )
                changed = [
                    line
                    for line in diff
                    if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
                ]
                out.append(f"- 官方基线（{source}）：{len(theirs.splitlines())} 行")
                out.append(f"- 你的版本：{len(mine_lines)} 行")
                out.append(f"- 差异：**{len(changed)} 行**")
            else:
                out.append("- 官方基线：**未找到**（该字段只存在于你的自定义档里）")
                out.append(f"- 你的版本：{len(mine_lines)} 行")
            if stars:
                out.append(f"- ★ 你标注的改动点（{len(stars)} 处）：")
                for star in stars:
                    out.append(f"  - `{star}`")
            out.append("")
            out.append("<details><summary>展开全文</summary>")
            out.append("")
            out.append("```gcode")
            out.extend(mine_lines)
            out.append("```")
            out.append("")
            out.append("</details>")
            out.append("")
        if not found_any:
            out.append(f"## {profile.name}")
            out.append("")
            out.append("（未改写 G-code，沿用官方默认）")
            out.append("")
    return "\n".join(out) if out else "（没有自定义机器 profile）\n"


# ------------------------------------------------------------------ 体检报告

LEVEL_LABEL = {"error": "严重", "warning": "警告", "info": "提示"}
LEVEL_ICON = {"error": "🔴", "warning": "🟠", "info": "🔵"}


def render_findings(findings: list[Finding], index: ProfileIndex) -> str:
    from .checks import detect_target_machine

    target = detect_target_machine(index)
    counts = count_by_level(findings)
    lines = [
        "# bambu-doctor 体检报告",
        "",
        f"> 生成时间 {_stamp()}　检测机型：**{target or '未能识别'}**",
        "",
        f"**{counts['error']} 个严重 · {counts['warning']} 个警告 · {counts['info']} 个提示**",
        "",
    ]
    if not findings:
        lines.append("没有发现问题。")
        return "\n".join(lines)

    for level in ("error", "warning", "info"):
        group = [f for f in findings if f.level == level]
        if not group:
            continue
        lines.append(f"## {LEVEL_ICON[level]} {LEVEL_LABEL[level]}（{len(group)}）")
        lines.append("")
        for finding in group:
            lines.append(f"### {finding.rule} · {finding.title}")
            lines.append("")
            if finding.profile:
                lines.append(f"- **profile**：`{finding.profile}`")
            lines.append(f"- **说明**：{finding.detail}")
            if finding.hint:
                lines.append(f"- **建议**：{finding.hint}")
            lines.append("")
    return "\n".join(lines)


def render_console(findings: list[Finding]) -> str:
    """终端输出，带颜色（无依赖，直接用 ANSI）。"""
    colors = {"error": "\033[31m", "warning": "\033[33m", "info": "\033[36m"}
    reset = "\033[0m"
    counts = count_by_level(findings)
    lines = []
    if not findings:
        lines.append("没有发现问题。")
        return "\n".join(lines)
    for finding in findings:
        color = colors.get(finding.level, "")
        where = f" [{finding.profile}]" if finding.profile else ""
        lines.append(f"{color}{finding.level.upper():<7}{reset} {finding.rule}{where}")
        lines.append(f"        {finding.title}")
        if finding.hint:
            lines.append(f"        → {finding.hint}")
    lines.append("")
    lines.append(
        f"合计：{counts['error']} 严重 / {counts['warning']} 警告 / {counts['info']} 提示"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ JSON

def to_json(index: ProfileIndex, findings: list[Finding], target: str) -> dict:
    profiles = []
    for profile in index.profiles(origin=ORIGIN_USER):
        values, layers = index.resolve(profile.name)
        profiles.append(
            {
                "name": profile.name,
                "kind": profile.kind,
                "path": str(profile.path),
                "chain": index.chain(profile.name),
                "overrides": profile.override_keys,
                "values": {k: normalize(v) for k, v in values.items()},
                "layers": layers,
            }
        )
    return {
        "tool": "bambu-doctor",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "target_machine": target,
        "profiles": profiles,
        "findings": [
            {
                "rule": f.rule,
                "level": f.level,
                "profile": f.profile,
                "title": f.title,
                "detail": f.detail,
                "hint": f.hint,
            }
            for f in findings
        ],
        "summary": count_by_level(findings),
    }


def write_extractables(index: ProfileIndex, out_dir: Path) -> list[Path]:
    """把档案写到目录，返回写出的文件列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, text in render_extractables(index).items():
        path = out_dir / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written
