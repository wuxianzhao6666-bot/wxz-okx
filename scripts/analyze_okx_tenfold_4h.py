#!/usr/bin/env python3
import os
import subprocess
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "analyze_okx_tenfold.py")
OUTPUT_ROOT = os.path.join(PROJECT_ROOT, "analysis_outputs", "okx_tenfold_4h")


def has_flag(argv: list[str], flag: str) -> bool:
    return any(arg == flag or arg.startswith(f"{flag}=") for arg in argv)


def main() -> int:
    argv = sys.argv[1:]
    cmd = [sys.executable, BASE_SCRIPT, "--bar", "4H", *argv]

    if not has_flag(argv, "--json-out"):
        cmd.extend(["--json-out", os.path.join(OUTPUT_ROOT, "result.json")])
    if not has_flag(argv, "--csv-out"):
        cmd.extend(["--csv-out", os.path.join(OUTPUT_ROOT, "result.csv")])
    if not has_flag(argv, "--chart-dir"):
        cmd.extend(["--chart-dir", os.path.join(OUTPUT_ROOT, "charts")])
    if not has_flag(argv, "--scanned-file"):
        cmd.extend(["--scanned-file", os.path.join(OUTPUT_ROOT, "scanned_symbols.txt")])
    if not has_flag(argv, "--hit-list-file"):
        cmd.extend(["--hit-list-file", os.path.join(OUTPUT_ROOT, "tenfold_hit_symbols.txt")])
    if not has_flag(argv, "--progress-file"):
        cmd.extend(["--progress-file", os.path.join(OUTPUT_ROOT, "scan_progress.jsonl")])

    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
