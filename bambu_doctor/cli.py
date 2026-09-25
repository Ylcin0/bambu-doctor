"""命令行入口。

    bambu-doctor                # 体检（默认）
    bambu-doctor check          # 同上，--strict 时有 error 则退出码 1
    bambu-doctor extract        # 生成 profile 档案（markdown）
    bambu-doctor report         # 打印调参真值表
    bambu-doctor export         # 导出 JSON（供其他工具消费）

所有命令都支持 `--studio-dir <路径>` 手动指定 Bambu Studio 配置目录。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .checks import count_by_level, detect_target_machine, run_checks
from .discovery import DiscoveryError, load_layout
from .profiles import ProfileIndex
from .report import (
    render_console,
    render_findings,
    render_tuning_table,
    to_json,
    write_extractables,
)

DEFAULT_OUT_DIR = "bambu-doctor-out"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bambu-doctor",
        description="体检你的 Bambu Studio profile：解析继承链、找出参数隐患、生成调参对照表。",
        epilog="全部本地分析，不联网、不上传任何数据。",
    )
    parser.add_argument("--version", action="version", version=f"bambu-doctor {__version__}")
    parser.add_argument(
        "--studio-dir",
        metavar="PATH",
        help="Bambu Studio 配置目录（默认按平台自动探测）",
    )

    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser("check", help="体检：找出 profile 里的参数隐患（默认）")
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

    p_extract = sub.add_parser("extract", help="生成 profile 档案（markdown）")
    p_extract.add_argument(
        "-o", "--out", metavar="DIR", default=DEFAULT_OUT_DIR, help=f"输出目录（默认 {DEFAULT_OUT_DIR}/）"
    )

    p_report = sub.add_parser("report", help="打印调参真值表（你的值 vs 官方基线）")
    p_report.add_argument("-o", "--out", metavar="FILE", help="写成 markdown 文件")

    p_export = sub.add_parser("export", help="导出结构化 JSON")
    p_export.add_argument(
        "-o", "--out", metavar="FILE", default="-", help="输出文件（默认 - 即标准输出）"
    )

    return parser


def load_index(args: argparse.Namespace) -> ProfileIndex:
    try:
        layout = load_layout(args.studio_dir)
    except DiscoveryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    index = ProfileIndex.build(layout)
    if not index.user:
        print(
            "提示：没有找到任何自定义 profile（user 目录是空的）。"
            "体检结果会是空的——先去 Bambu Studio 里存一个自己的 profile 再来。",
            file=sys.stderr,
        )
    return index


def cmd_check(args: argparse.Namespace) -> int:
    index = load_index(args)
    findings = run_checks(index, verbose=getattr(args, "verbose", False))

    if args.json:
        payload = to_json(index, findings, detect_target_machine(index))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_console(findings))
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
    payload = to_json(index, findings, detect_target_machine(index))
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        path = Path(args.out)
        path.write_text(text, encoding="utf-8")
        print(f"已写入：{path}")
    return 0


COMMANDS = {
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
