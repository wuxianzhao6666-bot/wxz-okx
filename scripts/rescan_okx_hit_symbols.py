#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_SCRIPT = PROJECT_ROOT / "scripts" / "analyze_okx_tenfold.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rescan only the symbols that already hit the tenfold condition.",
    )
    parser.add_argument(
        "--bar",
        default="1H",
        help="Candle interval, e.g. 1H or 4H. Default: 1H",
    )
    parser.add_argument(
        "--host",
        default="app.okx.com",
        help="OKX public API host. Default: app.okx.com",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        help="Multiple threshold. Default: 10",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=250,
        help="Maximum history pages to fetch. Default: 250",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=100,
        help="Per-request candle limit. Default: 100",
    )
    parser.add_argument(
        "--chart-window",
        type=int,
        default=8,
        help="How many candles before and after the target candle to draw. Default: 8",
    )
    parser.add_argument(
        "--source-dir",
        help=(
            "Directory containing tenfold_hit_symbols.txt. "
            "Defaults to analysis_outputs/okx_tenfold or analysis_outputs/okx_tenfold_4h based on --bar."
        ),
    )
    parser.add_argument(
        "--output-dir",
        help=(
            "Directory for the rescanned outputs. "
            "Defaults to analysis_outputs/okx_tenfold_hits_only or okx_tenfold_4h_hits_only."
        ),
    )
    parser.add_argument(
        "--hit-list-file",
        help="Optional explicit path to tenfold_hit_symbols.txt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command without executing it.",
    )
    return parser.parse_args()


def default_dirs_for_bar(bar: str) -> tuple[Path, Path]:
    bar_upper = bar.upper()
    if bar_upper == "4H":
        return (
            PROJECT_ROOT / "analysis_outputs" / "okx_tenfold_4h",
            PROJECT_ROOT / "analysis_outputs" / "okx_tenfold_4h_hits_only",
        )
    return (
        PROJECT_ROOT / "analysis_outputs" / "okx_tenfold",
        PROJECT_ROOT / "analysis_outputs" / "okx_tenfold_hits_only",
    )


def load_symbols(hit_list_file: Path) -> list[str]:
    if not hit_list_file.exists():
        raise FileNotFoundError(f"Hit list file not found: {hit_list_file}")
    symbols = [
        line.strip()
        for line in hit_list_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not symbols:
        raise RuntimeError(f"No symbols found in {hit_list_file}")
    return symbols


def main() -> int:
    args = parse_args()
    source_dir, output_dir = default_dirs_for_bar(args.bar)
    if args.source_dir:
        source_dir = Path(args.source_dir)
    if args.output_dir:
        output_dir = Path(args.output_dir)

    hit_list_file = Path(args.hit_list_file) if args.hit_list_file else source_dir / "tenfold_hit_symbols.txt"
    symbols = load_symbols(hit_list_file)

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(BASE_SCRIPT),
        *symbols,
        "--bar",
        args.bar,
        "--host",
        args.host,
        "--threshold",
        str(args.threshold),
        "--max-pages",
        str(args.max_pages),
        "--page-limit",
        str(args.page_limit),
        "--chart-window",
        str(args.chart_window),
        "--rescan",
        "--json-out",
        str(output_dir / "result.json"),
        "--csv-out",
        str(output_dir / "result.csv"),
        "--chart-dir",
        str(output_dir / "charts"),
        "--scanned-file",
        str(output_dir / "scanned_symbols.txt"),
        "--hit-list-file",
        str(output_dir / "tenfold_hit_symbols.txt"),
        "--progress-file",
        str(output_dir / "scan_progress.jsonl"),
    ]

    print(f"Hit symbols: {len(symbols)}")
    print(f"Source hit list: {hit_list_file}")
    print(f"Output dir: {output_dir}")
    print("Command:")
    print(" ".join(cmd))

    if args.dry_run:
        return 0
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
