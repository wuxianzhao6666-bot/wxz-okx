#!/usr/bin/env python3
import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EXPORTS = [
    (
        PROJECT_ROOT / "analysis_outputs" / "okx_tenfold" / "result.json",
        PROJECT_ROOT / "analysis_outputs" / "okx_tenfold" / "next_candle_export.csv",
        "1H",
    ),
    (
        PROJECT_ROOT / "analysis_outputs" / "okx_tenfold_4h" / "result.json",
        PROJECT_ROOT / "analysis_outputs" / "okx_tenfold_4h" / "next_candle_export.csv",
        "4H",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the first candle after each tenfold hit into CSV.",
    )
    parser.add_argument(
        "--input",
        help="Path to a single result.json file. If omitted, exports both default 1H and 4H files.",
    )
    parser.add_argument(
        "--output",
        help="Path to output CSV when using --input.",
    )
    parser.add_argument(
        "--bar",
        default="",
        help="Bar label to write into the CSV when using --input, e.g. 1H or 4H.",
    )
    return parser.parse_args()


def load_results(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected JSON shape in {path}")
    return payload


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stringify(value: Any, digits: int = 6) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return str(value)


def threshold_price_for_event(event: dict[str, Any], threshold: float = 10.0) -> float | None:
    threshold_price = as_float(event.get("threshold_price"))
    if threshold_price is not None:
        return threshold_price

    hit_ohlc = event.get("hit_ohlc")
    previous_amplitude_percent = as_float(event.get("previous_amplitude_percent"))
    if (
        isinstance(hit_ohlc, list)
        and len(hit_ohlc) >= 3
        and previous_amplitude_percent is not None
    ):
        hit_open = as_float(hit_ohlc[0]) or 0.0
        hit_low = as_float(hit_ohlc[2]) or 0.0
        return hit_low + hit_open * (previous_amplitude_percent / 100.0) * threshold
    return None


def extract_rows(results: dict[str, Any], bar: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for inst_id, summary in results.items():
        if not isinstance(summary, dict) or "error" in summary:
            continue
        for event in summary.get("events", []):
            hit_ohlc = event.get("hit_ohlc") if isinstance(event.get("hit_ohlc"), list) else None
            next_ohlc = (
                event.get("next_candle_ohlc")
                if isinstance(event.get("next_candle_ohlc"), list)
                else None
            )
            threshold_price = threshold_price_for_event(event)

            hit_open = as_float(hit_ohlc[0]) if hit_ohlc and len(hit_ohlc) >= 1 else None
            hit_high = as_float(hit_ohlc[1]) if hit_ohlc and len(hit_ohlc) >= 2 else None
            hit_low = as_float(hit_ohlc[2]) if hit_ohlc and len(hit_ohlc) >= 3 else None
            hit_close = as_float(hit_ohlc[3]) if hit_ohlc and len(hit_ohlc) >= 4 else None

            next_open = as_float(next_ohlc[0]) if next_ohlc and len(next_ohlc) >= 1 else None
            next_high = as_float(next_ohlc[1]) if next_ohlc and len(next_ohlc) >= 2 else None
            next_low = as_float(next_ohlc[2]) if next_ohlc and len(next_ohlc) >= 3 else None
            next_close = as_float(next_ohlc[3]) if next_ohlc and len(next_ohlc) >= 4 else None

            data_completeness = "full_ohlc" if next_ohlc else "partial_only"

            rows.append(
                {
                    "bar": bar,
                    "inst_id": inst_id,
                    "hit_time_cn": stringify(event.get("time_cn"), 0),
                    "hit_ts": stringify(event.get("hit_ts"), 0),
                    "multiple": stringify(as_float(event.get("multiple"))),
                    "previous_amplitude_percent": stringify(
                        as_float(event.get("previous_amplitude_percent")),
                    ),
                    "previous_change_percent": stringify(
                        as_float(event.get("previous_change_percent")),
                    ),
                    "volume_multiple": stringify(as_float(event.get("volume_multiple"))),
                    "threshold_price": stringify(threshold_price),
                    "hit_open": stringify(hit_open),
                    "hit_high": stringify(hit_high),
                    "hit_low": stringify(hit_low),
                    "hit_close": stringify(hit_close),
                    "next_candle_time_cn": stringify(event.get("next_candle_time_cn"), 0),
                    "next_candle_change_percent": stringify(
                        as_float(event.get("next_candle_change_percent")),
                    ),
                    "next_open": stringify(next_open),
                    "next_high": stringify(next_high),
                    "next_low": stringify(next_low),
                    "next_close": stringify(next_close),
                    "next_high_vs_threshold_percent": stringify(
                        as_float(event.get("next_candle_high_vs_threshold_percent")),
                    ),
                    "next_high_vs_threshold_multiple": stringify(
                        as_float(event.get("next_candle_high_vs_threshold_multiple")),
                    ),
                    "next_low_vs_threshold_percent": stringify(
                        as_float(event.get("next_candle_low_vs_threshold_percent")),
                    ),
                    "next_low_vs_threshold_multiple": stringify(
                        as_float(event.get("next_candle_low_vs_threshold_multiple")),
                    ),
                    "next_close_vs_threshold_percent": stringify(
                        as_float(event.get("next_candle_close_vs_threshold_percent")),
                    ),
                    "next_close_vs_threshold_multiple": stringify(
                        as_float(event.get("next_candle_close_vs_threshold_multiple")),
                    ),
                    "next_low_below_threshold": stringify(
                        event.get("next_candle_low_below_threshold"),
                    ),
                    "next_close_below_threshold": stringify(
                        event.get("next_candle_close_below_threshold"),
                    ),
                    "data_completeness": data_completeness,
                },
            )
    rows.sort(key=lambda item: (item["inst_id"], item["hit_ts"]))
    return rows


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    fieldnames = [
        "bar",
        "inst_id",
        "hit_time_cn",
        "hit_ts",
        "multiple",
        "previous_amplitude_percent",
        "previous_change_percent",
        "volume_multiple",
        "threshold_price",
        "hit_open",
        "hit_high",
        "hit_low",
        "hit_close",
        "next_candle_time_cn",
        "next_candle_change_percent",
        "next_open",
        "next_high",
        "next_low",
        "next_close",
        "next_high_vs_threshold_percent",
        "next_high_vs_threshold_multiple",
        "next_low_vs_threshold_percent",
        "next_low_vs_threshold_multiple",
        "next_close_vs_threshold_percent",
        "next_close_vs_threshold_multiple",
        "next_low_below_threshold",
        "next_close_below_threshold",
        "data_completeness",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_one(input_path: Path, output_path: Path, bar: str) -> None:
    results = load_results(input_path)
    rows = extract_rows(results, bar)
    write_csv(rows, output_path)
    full_count = sum(1 for row in rows if row["data_completeness"] == "full_ohlc")
    print(
        f"{output_path} | rows={len(rows)} | full_ohlc={full_count} | partial_only={len(rows) - full_count}",
    )


def main() -> int:
    args = parse_args()
    if args.input:
        if not args.output:
            raise SystemExit("--output is required when --input is provided")
        input_path = Path(args.input)
        output_path = Path(args.output)
        bar = args.bar or input_path.parent.name
        export_one(input_path, output_path, bar)
        return 0

    for input_path, output_path, bar in DEFAULT_EXPORTS:
        if input_path.exists():
            export_one(input_path, output_path, bar)
        else:
            print(f"skip missing input: {input_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
