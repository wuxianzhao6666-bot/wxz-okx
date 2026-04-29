#!/usr/bin/env python3
import argparse
import os
from typing import Any

from analyze_okx_tenfold import compact_ts_label
from analyze_okx_tenfold import format_ts
from analyze_okx_tenfold import run_curl_with_fallback
from analyze_okx_tenfold import slugify_inst_id


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "okx_tenfold",
    "result.json",
)
DEFAULT_OUTPUT_DIR = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "okx_tenfold",
    "charts",
    "tenfold_15m_svgs",
)
FIFTEEN_MIN_MS = 15 * 60 * 1000
ONE_HOUR_MS = 60 * 60 * 1000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate 15m candle charts for scanned 10x hit events and mark "
            "the 15m candle where the 10x price is first reached."
        ),
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help="Path to the existing result.json file.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to save the 15m SVG charts.",
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
        help="Multiple threshold used by the source scan. Default: 10",
    )
    parser.add_argument(
        "--before-count",
        type=int,
        default=8,
        help="How many 15m candles to show before the target candle. Default: 8",
    )
    parser.add_argument(
        "--after-count",
        type=int,
        default=16,
        help="How many 15m candles to show after the target candle. Default: 16",
    )
    return parser.parse_args()


def load_results(path: str) -> dict[str, Any]:
    import json

    with open(path, encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected result.json structure: {path}")
    return payload


def fetch_15m_window(
    host: str,
    inst_id: str,
    hit_ts: int,
    before_count: int,
    after_count: int,
) -> list[dict[str, float | int]]:
    total_needed = before_count + after_count + 1
    boundary_ts = hit_ts + (after_count + 1) * FIFTEEN_MIN_MS
    limit = min(max(total_needed + 8, total_needed), 300)
    url = (
        f"https://{host}/api/v5/market/history-candles"
        f"?instId={inst_id}&bar=15m&limit={limit}&after={boundary_ts}"
    )
    payload = run_curl_with_fallback(url, host)
    rows = []
    for row in payload.get("data", []):
        if str(row[8]) != "1":
            continue
        rows.append(
            {
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            },
        )
    rows.sort(key=lambda item: int(item["ts"]))
    window_start = hit_ts - before_count * FIFTEEN_MIN_MS
    window_end = hit_ts + after_count * FIFTEEN_MIN_MS
    filtered = [
        row
        for row in rows
        if window_start <= int(row["ts"]) <= window_end
    ]
    if len(filtered) >= total_needed:
        return filtered
    return rows


def threshold_price_for_event(event: dict[str, Any], threshold: float) -> float:
    threshold_price = event.get("threshold_price")
    if threshold_price is not None:
        return float(threshold_price)
    hit_ohlc = event["hit_ohlc"]
    previous_amp_percent = float(event["previous_amplitude_percent"])
    return float(hit_ohlc[2]) + (float(hit_ohlc[0]) * (previous_amp_percent / 100.0) * threshold)


def select_target_15m_candle(
    candles: list[dict[str, float | int]],
    hit_ts: int,
    threshold_price: float,
) -> int | None:
    hour_rows = [
        index
        for index, candle in enumerate(candles)
        if hit_ts <= int(candle["ts"]) < hit_ts + ONE_HOUR_MS
    ]
    for index in hour_rows:
        if float(candles[index]["high"]) >= threshold_price:
            return index
    for index, candle in enumerate(candles):
        if int(candle["ts"]) == hit_ts:
            return index
    return hour_rows[0] if hour_rows else None


def render_chart(
    output_path: str,
    inst_id: str,
    event: dict[str, Any],
    candles: list[dict[str, float | int]],
    target_index: int,
    threshold_price: float,
) -> None:
    if not candles:
        raise RuntimeError(f"No 15m candles available for {inst_id} {event['time_cn']}")

    prices = [value for candle in candles for value in (float(candle["low"]), float(candle["high"]))]
    min_price = min(min(prices), threshold_price)
    max_price = max(max(prices), threshold_price)
    if max_price <= min_price:
        max_price = min_price + 1e-8

    width = 1500
    height = 860
    margin_left = 90
    margin_right = 48
    margin_top = 80
    margin_bottom = 130
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    candle_step = plot_width / max(len(candles), 1)
    candle_width = max(candle_step * 0.56, 6)

    def y_of(price: float) -> float:
        ratio_y = (price - min_price) / (max_price - min_price)
        return margin_top + plot_height - ratio_y * plot_height

    def x_of(index: int) -> float:
        return margin_left + candle_step * index + candle_step / 2

    target_x = x_of(target_index)
    threshold_y = y_of(threshold_price)
    target_candle = candles[target_index]

    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fcfcfc"/>',
        f'<text x="{margin_left}" y="38" font-size="28" font-weight="700" fill="#111111">{inst_id} 15m - 10倍命中定位图</text>',
        f'<text x="{margin_left}" y="68" font-size="16" fill="#555555">1H命中时间: {event["time_cn"]} | 10倍价: {threshold_price:.6f} | 命中倍数: {float(event["multiple"]):.4f}x</text>',
    ]

    for grid_index in range(6):
        gy = margin_top + plot_height * grid_index / 5
        price = max_price - (max_price - min_price) * grid_index / 5
        svg_lines.append(
            f'<line x1="{margin_left}" y1="{gy:.2f}" x2="{width - margin_right}" y2="{gy:.2f}" stroke="#d9d9d9" stroke-dasharray="4 4" stroke-width="1"/>',
        )
        svg_lines.append(
            f'<text x="12" y="{gy + 4:.2f}" font-size="12" fill="#555555">{price:.6f}</text>',
        )

    svg_lines.append(
        f'<rect x="{target_x - candle_step / 2:.2f}" y="{margin_top}" width="{candle_step:.2f}" height="{plot_height:.2f}" fill="#f1c40f" opacity="0.16"/>',
    )
    svg_lines.append(
        f'<line x1="{target_x:.2f}" y1="{margin_top}" x2="{target_x:.2f}" y2="{height - margin_bottom}" stroke="#f39c12" stroke-width="2" stroke-dasharray="6 4"/>',
    )
    svg_lines.append(
        f'<line x1="{margin_left}" y1="{threshold_y:.2f}" x2="{width - margin_right}" y2="{threshold_y:.2f}" stroke="#8e44ad" stroke-width="2" stroke-dasharray="8 4"/>',
    )
    svg_lines.append(
        f'<text x="{width - margin_right - 8}" y="{threshold_y - 8:.2f}" font-size="13" fill="#8e44ad" text-anchor="end">10倍价 {threshold_price:.6f}</text>',
    )

    for index, candle in enumerate(candles):
        color = "#2ecc71" if float(candle["close"]) >= float(candle["open"]) else "#e74c3c"
        x = x_of(index)
        y_high = y_of(float(candle["high"]))
        y_low = y_of(float(candle["low"]))
        y_open = y_of(float(candle["open"]))
        y_close = y_of(float(candle["close"]))
        body_top = min(y_open, y_close)
        body_height = max(abs(y_close - y_open), 1.2)
        svg_lines.append(
            f'<line x1="{x:.2f}" y1="{y_high:.2f}" x2="{x:.2f}" y2="{y_low:.2f}" stroke="{color}" stroke-width="2"/>',
        )
        svg_lines.append(
            f'<rect x="{x - candle_width / 2:.2f}" y="{body_top:.2f}" width="{candle_width:.2f}" height="{body_height:.2f}" fill="{color}" stroke="{color}" stroke-width="1"/>',
        )
        label = format_ts(int(candle["ts"]))[5:16]
        svg_lines.append(
            f'<text x="{x:.2f}" y="{height - 58}" font-size="11" fill="#555555" text-anchor="middle">{label[:5]}</text>',
        )
        svg_lines.append(
            f'<text x="{x:.2f}" y="{height - 42}" font-size="11" fill="#555555" text-anchor="middle">{label[6:]}</text>',
        )

    note_x = min(target_x + 70, width - 360)
    note_y = max(y_of(float(target_candle["high"])) - 90, margin_top + 24)
    svg_lines.append(
        f'<line x1="{target_x:.2f}" y1="{y_of(float(target_candle["high"])):.2f}" x2="{note_x:.2f}" y2="{note_y:.2f}" stroke="#f39c12" stroke-width="2"/>',
    )
    svg_lines.append(
        f'<rect x="{note_x:.2f}" y="{note_y - 52:.2f}" width="280" height="90" rx="10" fill="#fff8dc" stroke="#f39c12"/>',
    )
    svg_lines.append(
        f'<text x="{note_x + 12:.2f}" y="{note_y - 28:.2f}" font-size="13" fill="#111111">15m定位时间 {format_ts(int(target_candle["ts"]))}</text>',
    )
    svg_lines.append(
        f'<text x="{note_x + 12:.2f}" y="{note_y - 8:.2f}" font-size="13" fill="#111111">该15m最高价 {float(target_candle["high"]):.6f}</text>',
    )
    svg_lines.append(
        f'<text x="{note_x + 12:.2f}" y="{note_y + 12:.2f}" font-size="13" fill="#111111">10倍价横线已标出</text>',
    )
    svg_lines.append(
        f'<text x="{note_x + 12:.2f}" y="{note_y + 32:.2f}" font-size="13" fill="#111111">竖线高亮首次触达10倍价的15m</text>',
    )

    svg_lines.append("</svg>")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write("\n".join(svg_lines))


def main() -> int:
    args = parse_args()
    results = load_results(args.input)
    os.makedirs(args.output_dir, exist_ok=True)
    generated_paths: list[str] = []

    for inst_id, summary in results.items():
        if not isinstance(summary, dict) or "error" in summary:
            continue
        events = summary.get("events", [])
        if not events:
            continue
        for event_index, event in enumerate(events, start=1):
            hit_ts = int(event["hit_ts"])
            threshold_price = threshold_price_for_event(event, args.threshold)
            candles = fetch_15m_window(
                host=args.host,
                inst_id=inst_id,
                hit_ts=hit_ts,
                before_count=args.before_count,
                after_count=args.after_count,
            )
            target_index = select_target_15m_candle(candles, hit_ts, threshold_price)
            if target_index is None:
                continue
            file_name = (
                f"{slugify_inst_id(inst_id)}__TENFOLD_15M__"
                f"{compact_ts_label(hit_ts)}__event_{event_index}.svg"
            )
            output_path = os.path.join(args.output_dir, file_name)
            render_chart(
                output_path=output_path,
                inst_id=inst_id,
                event=event,
                candles=candles,
                target_index=target_index,
                threshold_price=threshold_price,
            )
            generated_paths.append(output_path)

    print(f"15m命中定位图已生成: {len(generated_paths)} 张")
    for path in generated_paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
