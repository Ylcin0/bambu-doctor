"""命令行入口。

    bambu-doctor                # 体检（默认）
    bambu-doctor diagnose       # ★ 诊断：从症状查到该改哪个参数
    bambu-doctor symptoms       # 列出所有已知症状
    bambu-doctor check          # 体检 profile 里的参数隐患
    bambu-doctor extract        # 生成 profile 档案（markdown）
    bambu-doctor report         # 打印调参真值表
    bambu-doctor export         # 导出 JSON

所有命令都支持 `--studio-dir`（Bambu Studio 配置目录）和
`--config`（规则豁免配置文件，见 README）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .checks import count_by_level, detect_target_machine, run_checks
from .diagnose import diagnose, render_text
from .discovery import DiscoveryError, load_layout
from .knowledge import KnowledgeBase, KnowledgeError
from .profiles import ProfileIndex
from .report import (
    render_console,
    render_findings,
    render_tuning_table,
    to_json,
    write_extractables,
)
from .rules import ConfigError, apply_ignore, load_config
from .plan import GoalBook, PlanError, build_plan, print_goal_list, render_plan
from .web import serve as serve_web

DEFAULT_OUT_DIR = "bambu-doctor-out"


def build_parser() -> argparse.ArgumentParser:
    # --studio-dir / --config 在顶层和子命令下都要能用：`check --config x` 是
    # 很自然的写法，但 argparse 默认只认写在子命令前面的全局选项。
    # 子命令侧用 SUPPRESS 当默认值——这样写在子命令后面时不会把顶层给的值
    # 覆盖成 None（普通 default 会覆盖）。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--studio-dir",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Bambu Studio 配置目录（默认按平台自动探测）",
    )
    common.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="规则豁免配置文件（默认找当前目录/用户主目录的 .bambu-doctor.toml）",
    )

    parser = argparse.ArgumentParser(
        prog="bambu-doctor",
        description="Bambu Lab 打印问题顾问：从症状查到该改哪个参数。",
        epilog="全部本地分析，不联网、不上传任何数据。",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"bambu-doctor {__version__}")

    sub = parser.add_subparsers(dest="command")

    # ---- diagnose：核心命令 -------------------------------------------------
    p_diag = sub.add_parser(
        "diagnose", parents=[common],
        help="★ 诊断：描述现象，查出该改哪个参数",
    )
    p_diag.add_argument(
        "text", nargs="?",
        help='用一句话描述现象，如 "细拉丝，薄件也有"',
    )
    p_diag.add_argument(
        "--symptom", metavar="ID",
        help="直接指定症状 id（先用 symptoms 命令看有哪些）",
    )
    p_diag.add_argument(
        "--profile", metavar="NAME",
        help="指定用于诊断的耗材 profile（默认用第一份）",
    )
    p_diag.add_argument(
        "--process", metavar="NAME",
        help="指定工艺 profile（层高/填充/速度读它；默认用第一份）",
    )
    p_diag.add_argument(
        "--brief", action="store_true",
        help="精简输出（不显示「已排除」项的依据）",
    )

    sub.add_parser("symptoms", parents=[common], help="列出所有已知症状")

    # ---- plan：打印前给方案 -------------------------------------------------
    p_plan = sub.add_parser(
        "plan", parents=[common],
        help="★ 给方案：说个目标（如「透明」），告诉你参数该怎么配",
    )
    p_plan.add_argument("goal", nargs="?", help="目标关键词，如「透明」")
    p_plan.add_argument("--profile", metavar="NAME", help="按哪份耗材档算（默认第一份）")
    p_plan.add_argument("--process", metavar="NAME", help="按哪份工艺档算（默认第一份）")
    p_plan.add_argument("--hide-ok", action="store_true", help="不列已经对的项")

    # ---- serve：本地网页界面 ------------------------------------------------
    p_serve = sub.add_parser(
        "serve", parents=[common], help="★ 打开网页界面（浏览器里点症状看结果）"
    )
    p_serve.add_argument(
        "--host", default="127.0.0.1",
        help="监听地址（默认只本机能开；--host 0.0.0.0 让同一 Wi-Fi 下的手机也能访问）",
    )
    p_serve.add_argument("--port", type=int, default=8000, help="端口（默认 8000）")
    p_serve.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    # ---- check：profile 体检 ------------------------------------------------
    p_check = sub.add_parser(
        "check", parents=[common], help="体检：找出 profile 里的参数隐患（默认）"
    )
    p_check.add_argument("-o", "--out", metavar="FILE", help="把报告写成 markdown 文件")
    p_check.add_argument(
        "--strict", action="store_true", help="有 warning 或 error 时返回非 0 退出码"
    )
    p_check.add_argument("--json", action="store_true", help="以 JSON 输出")
    p_check.add_argument(
        "--verbose",
        action="store_true",
        help="附带 R6 继承链信息（每个 profile 一条，默认隐藏以免淹没真问题）",
    )
    p_check.add_argument(
        "--show-ignored",
        action="store_true",
        help="列出被配置豁免的发现（默认只报数量）",
    )

    # ---- extract / report / export -----------------------------------------
    p_extract = sub.add_parser(
        "extract", parents=[common], help="生成 profile 档案（markdown）"
    )
    p_extract.add_argument(
        "-o", "--out", metavar="DIR", default=DEFAULT_OUT_DIR,
        help=f"输出目录（默认 {DEFAULT_OUT_DIR}/）",
    )

    p_report = sub.add_parser(
        "report", parents=[common], help="打印调参真值表（你的值 vs 官方基线）"
    )
    p_report.add_argument("-o", "--out", metavar="FILE", help="写成 markdown 文件")

    p_export = sub.add_parser("export", parents=[common], help="导出结构化 JSON")
    p_export.add_argument(
        "-o", "--out", metavar="FILE", default="-", help="输出文件（默认 - 即标准输出）"
    )

    return parser


def load_index(args: argparse.Namespace) -> ProfileIndex:
    try:
        layout = load_layout(getattr(args, "studio_dir", None))
    except DiscoveryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    index = ProfileIndex.build(layout)
    if not index.user:
        print(
            "提示：没有找到任何自定义 profile（user 目录是空的）。"
            "先去 Bambu Studio 里存一个自己的 profile 再来。",
            file=sys.stderr,
        )
    return index


def load_knowledge() -> KnowledgeBase:
    try:
        return KnowledgeBase.load()
    except KnowledgeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _load_rule_config(args: argparse.Namespace):
    try:
        return load_config(getattr(args, "config", None))
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc


# ------------------------------------------------------------------ 诊断

def print_symptom_list(kb: KnowledgeBase) -> None:
    print("已知症状：\n")
    for symptom in sorted(kb.symptoms.values(), key=lambda s: s.id):
        print(f"  {symptom.id}")
        print(f"    {symptom.name} —— {symptom.description}")
        if symptom.keywords:
            print(f"    可以这样说：{'、'.join(symptom.keywords[:8])}")
        print()


def cmd_diagnose(args: argparse.Namespace) -> int:
    kb = load_knowledge()
    index = load_index(args)

    # 定症状：--symptom 优先，其次文字匹配
    if args.symptom:
        if kb.get_symptom(args.symptom) is None:
            print(f"错误：未知症状「{args.symptom}」\n", file=sys.stderr)
            print_symptom_list(kb)
            return 2
        symptom_id = args.symptom
    elif args.text:
        hits = kb.match_symptoms(args.text)
        if not hits:
            print(f"没从「{args.text}」里认出已知症状。\n", file=sys.stderr)
            print_symptom_list(kb)
            return 2
        symptom_id = hits[0][0].id
        if len(hits) > 1:
            others = "、".join(s.name for s, _ in hits[1:4])
            print(f"（匹配到多个症状，按最接近的「{hits[0][0].name}」判断；也可能是：{others}）\n")
    else:
        print_symptom_list(kb)
        print('用法：bambu-doctor diagnose "你的现象描述"')
        print("      或 bambu-doctor diagnose --symptom <id>")
        return 2

    try:
        report = diagnose(
            index, kb, symptom_id,
            profile_name=args.profile,
            process_name=getattr(args, "process", None),
        )
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(render_text(report, show_evidence=not args.brief))

    # 判断成立时退出码非 0，便于脚本判断"有没有发现问题"
    return 1 if report.group("confirmed") else 0


def cmd_symptoms(args: argparse.Namespace) -> int:
    print_symptom_list(load_knowledge())
    return 0


# ------------------------------------------------------------------ 体检

def cmd_check(args: argparse.Namespace) -> int:
    index = load_index(args)
    findings = run_checks(index, verbose=getattr(args, "verbose", False))

    config = _load_rule_config(args)
    findings, ignored = apply_ignore(findings, config)

    if args.json:
        payload = to_json(index, findings, detect_target_machine(index))
        payload["ignored"] = [
            {"rule": f.rule, "profile": f.profile, "title": f.title, "reason": reason}
            for f, reason in ignored
        ]
        payload["config_file"] = str(config.source) if config.source else None
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_console(findings))
        if ignored:
            origin = f"（{config.source}）" if config.source else ""
            print(f"\n另有 {len(ignored)} 条按配置豁免{origin}")
            if getattr(args, "show_ignored", False):
                for finding, reason in ignored:
                    where = finding.profile or "-"
                    print(f"  - {finding.rule} [{where}] {finding.title}　← {reason}")
        if args.out:
            path = Path(args.out)
            path.write_text(render_findings(findings, index), encoding="utf-8")
            print(f"\n完整报告已写入：{path}")

    counts = count_by_level(findings)
    if counts["error"]:
        return 1
    if args.strict and counts["warning"]:
        return 1
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    index = load_index(args)
    written = write_extractables(index, Path(args.out))
    print(f"已生成 {len(written)} 份档案：")
    for path in written:
        print(f"  {path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    index = load_index(args)
    table = render_tuning_table(index)
    if args.out:
        path = Path(args.out)
        path.write_text(table, encoding="utf-8")
        print(f"已写入：{path}")
    else:
        print(table)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    index = load_index(args)
    findings = run_checks(index)
    config = _load_rule_config(args)
    findings, ignored = apply_ignore(findings, config)
    payload = to_json(index, findings, detect_target_machine(index))
    payload["ignored"] = [
        {"rule": f.rule, "profile": f.profile, "title": f.title, "reason": reason}
        for f, reason in ignored
    ]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        path = Path(args.out)
        path.write_text(text, encoding="utf-8")
        print(f"已写入：{path}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        book = GoalBook.load()
    except PlanError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    index = load_index(args)

    goal = None
    if args.goal:
        goal = book.goals.get(args.goal)
        if goal is None:
            hits = book.match(args.goal)
            if not hits:
                print(f"没从「{args.goal}」里认出已知目标。\n", file=sys.stderr)
                print_goal_list(book)
                return 2
            goal = hits[0][0]
            if len(hits) > 1:
                others = "、".join(g.name for g, _ in hits[1:4])
                print(f"（匹配到多个目标，按「{goal.name}」算；也可能是：{others}）\n")
    else:
        print_goal_list(book)
        print("用法：bambu-doctor plan 透明")
        return 2

    try:
        plan = build_plan(
            index, goal,
            profile_name=args.profile,
            process_name=getattr(args, "process", None),
        )
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(render_plan(plan, show_ok=not args.hide_ok))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    index = load_index(args)
    kb = load_knowledge()
    try:
        goals = GoalBook.load()
    except PlanError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    server = serve_web(
        index, kb, goals,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


COMMANDS = {
    "plan": cmd_plan,
    "diagnose": cmd_diagnose,
    "symptoms": cmd_symptoms,
    "serve": cmd_serve,
    "check": cmd_check,
    "extract": cmd_extract,
    "report": cmd_report,
    "export": cmd_export,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # 不带子命令时默认跑体检
    if not args.command:
        args = parser.parse_args((argv or []) + ["check"])
    handler = COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse 已经拦住
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
