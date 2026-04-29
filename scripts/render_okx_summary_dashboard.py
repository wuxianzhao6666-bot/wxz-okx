#!/usr/bin/env python3
import argparse
import json
import math
import os
from datetime import datetime
from typing import Any


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "okx_tenfold",
    "result.json",
)
DEFAULT_OUTPUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "okx_tenfold",
    "charts",
    "summary_svgs",
    "summary_dashboard.svg",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a single OKX summary dashboard SVG from result.json.",
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help="Path to result.json",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Path to output SVG",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        help="Multiple threshold used in the scan. Default: 10",
    )
    return parser.parse_args()


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def load_results(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected JSON shape in {path}")
    return payload


def build_event_rows(results: dict[str, Any], threshold: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for inst_id, summary in results.items():
        if not isinstance(summary, dict) or "error" in summary:
            continue
        for event in summary.get("events", []):
            hit_ohlc = event.get("hit_ohlc") or [0, 0, 0, 0]
            threshold_price = event.get("threshold_price")
            if threshold_price is None:
                threshold_price = hit_ohlc[2] + (
                    hit_ohlc[0] * (float(event["previous_amplitude_percent"]) / 100.0) * threshold
                )
            close_vs_threshold_percent = (
                (hit_ohlc[3] - threshold_price) / threshold_price * 100
                if threshold_price
                else None
            )
            next_close = None
            next_close_below_threshold = None
            next_low = None
            next_low_below_threshold = None
            next_high = None
            next_high_above_threshold = None
            next_high_vs_threshold_percent = None
            next_ohlc = event.get("next_candle_ohlc")
            if threshold_price and next_ohlc:
                next_high = float(next_ohlc[1])
                next_low = float(next_ohlc[2])
                next_close = float(next_ohlc[3])
                next_high_above_threshold = next_high > threshold_price
                next_high_vs_threshold_percent = (next_high - threshold_price) / threshold_price * 100
                next_close_below_threshold = next_close < threshold_price
                next_low_below_threshold = next_low < threshold_price
            else:
                next_high_vs_threshold_percent = event.get("next_candle_high_vs_threshold_percent")
                if threshold_price and next_high_vs_threshold_percent is not None:
                    next_high_vs_threshold_percent = float(next_high_vs_threshold_percent)
                    next_high_above_threshold = next_high_vs_threshold_percent > 0
                next_change = event.get("next_candle_change_percent")
                if threshold_price and next_change is not None and hit_ohlc:
                    next_open_assumed = hit_ohlc[3]
                    next_close = next_open_assumed * (1 + float(next_change) / 100.0)
                    next_close_below_threshold = next_close < threshold_price
            rows.append(
                {
                    "inst_id": inst_id,
                    "time_cn": event.get("time_cn", ""),
                    "multiple": float(event.get("multiple", 0.0)),
                    "previous_amplitude_percent": float(event.get("previous_amplitude_percent", 0.0)),
                    "previous_change_percent": float(event.get("previous_change_percent", 0.0)),
                    "volume_multiple": (
                        None
                        if event.get("volume_multiple") is None
                        else float(event["volume_multiple"])
                    ),
                    "close_vs_threshold_percent": close_vs_threshold_percent,
                    "close_above_threshold": (
                        None
                        if close_vs_threshold_percent is None
                        else close_vs_threshold_percent >= 0
                    ),
                    "next_close": next_close,
                    "next_close_below_threshold": next_close_below_threshold,
                    "next_low": next_low,
                    "next_low_below_threshold": next_low_below_threshold,
                    "next_high": next_high,
                    "next_high_above_threshold": next_high_above_threshold,
                    "next_high_vs_threshold_percent": next_high_vs_threshold_percent,
                },
            )
    return rows


def render_dashboard(
    results: dict[str, Any],
    output_path: str,
    threshold: float,
) -> None:
    event_rows = build_event_rows(results, threshold)
    ok_summaries = [
        (inst_id, summary)
        for inst_id, summary in results.items()
        if isinstance(summary, dict) and "error" not in summary
    ]
    error_count = len(results) - len(ok_summaries)
    hit_symbol_count = sum(1 for _, summary in ok_summaries if summary.get("hit_count", 0) > 0)
    hit_event_count = len(event_rows)
    close_above_count = sum(1 for row in event_rows if row["close_above_threshold"] is True)
    close_below_count = sum(1 for row in event_rows if row["close_above_threshold"] is False)
    close_above_then_next_close_below = sum(
        1
        for row in event_rows
        if row["close_above_threshold"] is True and row["next_close_below_threshold"] is True
    )
    close_above_then_next_close_not_below = sum(
        1
        for row in event_rows
        if row["close_above_threshold"] is True and row["next_close_below_threshold"] is False
    )
    close_above_then_next_close_unknown = sum(
        1
        for row in event_rows
        if row["close_above_threshold"] is True and row["next_close_below_threshold"] is None
    )
    next_high_known_rows = [
        row for row in event_rows if row["next_high_above_threshold"] is not None
    ]
    next_high_above_rows = [
        row for row in next_high_known_rows if row["next_high_above_threshold"] is True
    ]
    next_high_below_rows = [
        row for row in next_high_known_rows if row["next_high_above_threshold"] is False
    ]
    next_high_unknown_count = len(event_rows) - len(next_high_known_rows)
    next_high_above_count = len(next_high_above_rows)
    next_high_below_count = len(next_high_below_rows)
    next_high_above_avg_percent = (
        sum(float(row["next_high_vs_threshold_percent"]) for row in next_high_above_rows)
        / next_high_above_count
        if next_high_above_count
        else None
    )
    next_high_above_max_percent = max(
        (float(row["next_high_vs_threshold_percent"]) for row in next_high_above_rows),
        default=None,
    )

    current_year = datetime.now().year
    target_years = [current_year, current_year - 1, current_year - 2]
    yearly_monthly: dict[int, list[int]] = {year: [0] * 12 for year in target_years}
    for row in event_rows:
        time_cn = row.get("time_cn", "")
        if len(time_cn) < 7:
            continue
        try:
            year = int(time_cn[0:4])
            month = int(time_cn[5:7])
        except ValueError:
            continue
        if year in yearly_monthly and 1 <= month <= 12:
            yearly_monthly[year][month - 1] += 1

    highest_top10: list[dict[str, Any]] = []
    for inst_id, summary in ok_summaries:
        highest = summary.get("highest_candidate")
        if not highest:
            continue
        highest_top10.append(
            {
                "inst_id": inst_id,
                "multiple": float(highest.get("multiple", 0.0)),
                "hit_count": int(summary.get("hit_count", 0)),
                "previous_amplitude_percent": float(
                    highest.get("previous_amplitude_percent", 0.0),
                ),
                "previous_change_percent": float(
                    highest.get("previous_change_percent", 0.0),
                ),
                "volume_multiple": (
                    None
                    if highest.get("volume_multiple") is None
                    else float(highest["volume_multiple"])
                ),
            },
        )
    highest_top10.sort(key=lambda item: item["multiple"], reverse=True)
    highest_top10 = highest_top10[:10]

    volume_top10 = [row for row in event_rows if row["volume_multiple"] is not None]
    volume_top10.sort(key=lambda item: float(item["volume_multiple"]), reverse=True)
    volume_top10 = volume_top10[:10]

    amplitude_top10 = list(event_rows)
    amplitude_top10.sort(
        key=lambda item: float(item["previous_amplitude_percent"]),
        reverse=True,
    )
    amplitude_top10 = amplitude_top10[:10]

    width = 1800
    height = 4460
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f8fa"/>',
        '<text x="48" y="58" font-size="34" font-weight="700" fill="#111111">OKX 10倍总结图</text>',
        f'<text x="48" y="94" font-size="18" fill="#555555">基于 result.json 生成；展示最高倍数、十倍前振幅、成交量倍数、10倍收盘位置统计</text>',
    ]

    cards = [
        ("总币数", str(len(results)), "#2d3436"),
        ("命中币种", str(hit_symbol_count), "#c0392b"),
        ("命中次数", str(hit_event_count), "#8e44ad"),
        ("错误数", str(error_count), "#7f8c8d"),
        ("收盘高于10倍价", str(close_above_count), "#27ae60"),
        ("收盘低于10倍价", str(close_below_count), "#e74c3c"),
    ]
    card_y = 130
    card_w = 260
    card_h = 110
    gap = 20
    for index, (label, value, color) in enumerate(cards):
        x = 48 + index * (card_w + gap)
        svg_lines.append(
            f'<rect x="{x}" y="{card_y}" width="{card_w}" height="{card_h}" rx="18" fill="#ffffff" stroke="#ebedf0"/>',
        )
        svg_lines.append(
            f'<text x="{x + 20}" y="{card_y + 42}" font-size="18" fill="#666666">{xml_escape(label)}</text>',
        )
        svg_lines.append(
            f'<text x="{x + 20}" y="{card_y + 84}" font-size="34" font-weight="700" fill="{color}">{xml_escape(value)}</text>',
        )

    panel_top = 290
    left_panel_x = 48
    panel_w = 820
    panel_h = 520
    right_panel_x = 930

    # Top 10 highest multiple panel
    svg_lines.extend(
        [
            f'<rect x="{left_panel_x}" y="{panel_top}" width="{panel_w}" height="{panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{left_panel_x + 24}" y="{panel_top + 36}" font-size="26" font-weight="700" fill="#111111">最高倍数 Top 10（含成交量倍数）</text>',
        ],
    )
    if highest_top10:
        max_multiple = max(item["multiple"] for item in highest_top10) or 1.0
        row_y = panel_top + 88
        for index, item in enumerate(highest_top10):
            y = row_y + index * 44
            bar_x = left_panel_x + 250
            bar_w = 250
            fill_w = bar_w * (item["multiple"] / max_multiple)
            volume_label = (
                "成交量 --"
                if item["volume_multiple"] is None
                else f"成交量 {item['volume_multiple']:.2f}x"
            )
            amplitude_label = f"前振幅 {item['previous_amplitude_percent']:.2f}%"
            change_label = f"前涨跌 {item['previous_change_percent']:.2f}%"
            svg_lines.append(
                f'<text x="{left_panel_x + 24}" y="{y + 16}" font-size="16" font-weight="600" fill="#111111">{index + 1}. {xml_escape(item["inst_id"])}</text>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="16" rx="8" fill="#eef1f5"/>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{fill_w:.2f}" height="16" rx="8" fill="#f39c12"/>',
            )
            svg_lines.append(
                f'<text x="{bar_x + bar_w + 16}" y="{y + 14}" font-size="14" fill="#333333">最高 {item["multiple"]:.2f}x | 命中 {item["hit_count"]} 次</text>',
            )
            svg_lines.append(
                f'<text x="{left_panel_x + 44}" y="{y + 33}" font-size="13" fill="#666666">{xml_escape(amplitude_label)} | {xml_escape(change_label)} | {xml_escape(volume_label)}</text>',
            )
    else:
        svg_lines.append(
            f'<text x="{left_panel_x + 24}" y="{panel_top + 100}" font-size="18" fill="#666666">暂无可展示数据</text>',
        )

    # Close above/below threshold panel
    svg_lines.extend(
        [
            f'<rect x="{right_panel_x}" y="{panel_top}" width="{panel_w}" height="{panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{right_panel_x + 24}" y="{panel_top + 36}" font-size="26" font-weight="700" fill="#111111">10倍命中收盘价位置</text>',
            f'<text x="{right_panel_x + 24}" y="{panel_top + 66}" font-size="16" fill="#666666">统计所有10倍命中，收盘价在10倍价之上还是之下</text>',
        ],
    )
    pie_total = close_above_count + close_below_count
    pie_cx = right_panel_x + 250
    pie_cy = panel_top + 280
    pie_radius = 145
    if pie_total > 0:
        slices = [
            ("高于10倍价", close_above_count, "#27ae60"),
            ("低于10倍价", close_below_count, "#e74c3c"),
        ]
        start_angle = -90.0
        for _, value, color in slices:
            if value <= 0:
                continue
            sweep = 360.0 * value / pie_total
            end_angle = start_angle + sweep
            x1 = pie_cx + pie_radius * math.cos(math.radians(start_angle))
            y1 = pie_cy + pie_radius * math.sin(math.radians(start_angle))
            x2 = pie_cx + pie_radius * math.cos(math.radians(end_angle))
            y2 = pie_cy + pie_radius * math.sin(math.radians(end_angle))
            large_arc = 1 if sweep > 180 else 0
            svg_lines.append(
                "<path "
                f'd="M {pie_cx:.2f} {pie_cy:.2f} L {x1:.2f} {y1:.2f} '
                f'A {pie_radius} {pie_radius} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" '
                f'fill="{color}" opacity="0.94"/>',
            )
            start_angle = end_angle
        svg_lines.append(
            f'<circle cx="{pie_cx}" cy="{pie_cy}" r="72" fill="#ffffff"/>',
        )
        svg_lines.append(
            f'<text x="{pie_cx}" y="{pie_cy - 4}" font-size="34" font-weight="700" fill="#111111" text-anchor="middle">{pie_total}</text>',
        )
        svg_lines.append(
            f'<text x="{pie_cx}" y="{pie_cy + 24}" font-size="16" fill="#666666" text-anchor="middle">命中总数</text>',
        )
        legend_x = right_panel_x + 520
        legend_y = panel_top + 220
        for index, (label, value, color) in enumerate(slices):
            percent = value / pie_total * 100 if pie_total else 0
            y = legend_y + index * 90
            svg_lines.append(
                f'<rect x="{legend_x}" y="{y - 16}" width="26" height="26" rx="6" fill="{color}"/>',
            )
            svg_lines.append(
                f'<text x="{legend_x + 40}" y="{y}" font-size="22" font-weight="600" fill="#111111">{xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<text x="{legend_x + 40}" y="{y + 28}" font-size="17" fill="#555555">{value} 次，占比 {percent:.2f}%</text>',
            )
    else:
        svg_lines.append(
            f'<text x="{right_panel_x + 24}" y="{panel_top + 120}" font-size="18" fill="#666666">暂无10倍命中数据</text>',
        )

    lower_panel_top = 850
    lower_panel_h = 760

    # Volume top10 panel
    svg_lines.extend(
        [
            f'<rect x="{left_panel_x}" y="{lower_panel_top}" width="{panel_w}" height="{lower_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{left_panel_x + 24}" y="{lower_panel_top + 36}" font-size="26" font-weight="700" fill="#111111">成交量倍数 Top 10</text>',
            f'<text x="{left_panel_x + 24}" y="{lower_panel_top + 66}" font-size="16" fill="#666666">按10倍命中事件的成交量倍数排序</text>',
        ],
    )
    if volume_top10:
        max_vol = max(float(item["volume_multiple"]) for item in volume_top10) or 1.0
        row_y = lower_panel_top + 100
        for index, item in enumerate(volume_top10):
            y = row_y + index * 58
            bar_x = left_panel_x + 360
            bar_w = 220
            fill_w = bar_w * (float(item["volume_multiple"]) / max_vol)
            label = f'{item["inst_id"]} {item["time_cn"]}'
            amp_text = f"前振幅 {item['previous_amplitude_percent']:.2f}%"
            change_text = f"前涨跌 {item['previous_change_percent']:.2f}%"
            svg_lines.append(
                f'<text x="{left_panel_x + 24}" y="{y + 16}" font-size="15" font-weight="600" fill="#111111">{index + 1}. {xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="16" rx="8" fill="#eef1f5"/>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{fill_w:.2f}" height="16" rx="8" fill="#8e44ad"/>',
            )
            svg_lines.append(
                f'<text x="{bar_x + bar_w + 16}" y="{y + 14}" font-size="14" fill="#333333">{float(item["volume_multiple"]):.2f}x | 倍数 {item["multiple"]:.2f}x</text>',
            )
            svg_lines.append(
                f'<text x="{left_panel_x + 44}" y="{y + 36}" font-size="13" fill="#666666">{xml_escape(amp_text)} | {xml_escape(change_text)}</text>',
            )
    else:
        svg_lines.append(
            f'<text x="{left_panel_x + 24}" y="{lower_panel_top + 120}" font-size="18" fill="#666666">暂无成交量倍数数据</text>',
        )

    # Event list panel for close-vs-threshold details
    svg_lines.extend(
        [
            f'<rect x="{right_panel_x}" y="{lower_panel_top}" width="{panel_w}" height="{lower_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{right_panel_x + 24}" y="{lower_panel_top + 36}" font-size="26" font-weight="700" fill="#111111">10倍命中收盘价相对10倍价 Top 10</text>',
            f'<text x="{right_panel_x + 24}" y="{lower_panel_top + 66}" font-size="16" fill="#666666">按收盘相对10倍价的偏离涨跌幅从高到低排序</text>',
        ],
    )
    close_ranked = [row for row in event_rows if row["close_vs_threshold_percent"] is not None]
    close_ranked.sort(key=lambda item: float(item["close_vs_threshold_percent"]), reverse=True)
    close_ranked = close_ranked[:10]
    close_ranked_low_abs = [
        row for row in event_rows if row["close_vs_threshold_percent"] is not None
    ]
    close_ranked_low_abs.sort(key=lambda item: float(item["close_vs_threshold_percent"]))
    close_ranked_low_abs = close_ranked_low_abs[:10]
    if close_ranked:
        row_y = lower_panel_top + 104
        for index, item in enumerate(close_ranked):
            y = row_y + index * 58
            pct = float(item["close_vs_threshold_percent"])
            color = "#27ae60" if pct >= 0 else "#e74c3c"
            label = f'{item["inst_id"]} {item["time_cn"]}'
            svg_lines.append(
                f'<text x="{right_panel_x + 24}" y="{y + 16}" font-size="15" font-weight="600" fill="#111111">{index + 1}. {xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<text x="{right_panel_x + 560}" y="{y + 16}" font-size="16" font-weight="600" fill="{color}">{pct:.2f}%</text>',
            )
            state = "收盘高于10倍价" if pct >= 0 else "收盘低于10倍价"
            vol = item["volume_multiple"]
            vol_text = "--" if vol is None else f"{float(vol):.2f}x"
            amp_text = f"前振幅 {item['previous_amplitude_percent']:.2f}%"
            change_text = f"前涨跌 {item['previous_change_percent']:.2f}%"
            svg_lines.append(
                f'<text x="{right_panel_x + 44}" y="{y + 36}" font-size="13" fill="#666666">{xml_escape(state)} | {xml_escape(amp_text)} | {xml_escape(change_text)} | 成交量 {xml_escape(vol_text)}</text>',
            )
    else:
        svg_lines.append(
            f'<text x="{right_panel_x + 24}" y="{lower_panel_top + 120}" font-size="18" fill="#666666">暂无可计算数据</text>',
        )

    third_panel_top = 1650
    third_panel_h = 560

    # next close below threshold panel
    svg_lines.extend(
        [
            f'<rect x="{left_panel_x}" y="{third_panel_top}" width="{panel_w}" height="{third_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{left_panel_x + 24}" y="{third_panel_top + 36}" font-size="26" font-weight="700" fill="#111111">收盘高于10倍价后，第二根收盘是否跌回10倍价下</text>',
            f'<text x="{left_panel_x + 24}" y="{third_panel_top + 66}" font-size="16" fill="#666666">基于当前 result.json，用命中后第二根涨跌幅推算第二根收盘价</text>',
        ],
    )
    next_close_total = (
        close_above_then_next_close_below
        + close_above_then_next_close_not_below
        + close_above_then_next_close_unknown
    )
    pie_cx = left_panel_x + 250
    pie_cy = third_panel_top + 280
    pie_radius = 145
    slices = [
        ("第二根收盘低于10倍价", close_above_then_next_close_below, "#e74c3c"),
        ("第二根收盘仍高于10倍价", close_above_then_next_close_not_below, "#27ae60"),
        ("无法判断", close_above_then_next_close_unknown, "#95a5a6"),
    ]
    non_zero = [(label, value, color) for label, value, color in slices if value > 0]
    if next_close_total > 0:
        if len(non_zero) == 1:
            _, _, color = non_zero[0]
            svg_lines.append(
                f'<circle cx="{pie_cx}" cy="{pie_cy}" r="{pie_radius}" fill="{color}" opacity="0.94"/>',
            )
        else:
            start_angle = -90.0
            for _, value, color in non_zero:
                sweep = 360.0 * value / next_close_total
                end_angle = start_angle + sweep
                x1 = pie_cx + pie_radius * math.cos(math.radians(start_angle))
                y1 = pie_cy + pie_radius * math.sin(math.radians(start_angle))
                x2 = pie_cx + pie_radius * math.cos(math.radians(end_angle))
                y2 = pie_cy + pie_radius * math.sin(math.radians(end_angle))
                large_arc = 1 if sweep > 180 else 0
                svg_lines.append(
                    "<path "
                    f'd="M {pie_cx:.2f} {pie_cy:.2f} L {x1:.2f} {y1:.2f} '
                    f'A {pie_radius} {pie_radius} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" '
                    f'fill="{color}" opacity="0.94"/>',
                )
                start_angle = end_angle
        svg_lines.append(f'<circle cx="{pie_cx}" cy="{pie_cy}" r="72" fill="#ffffff"/>')
        svg_lines.append(
            f'<text x="{pie_cx}" y="{pie_cy - 4}" font-size="34" font-weight="700" fill="#111111" text-anchor="middle">{next_close_total}</text>',
        )
        svg_lines.append(
            f'<text x="{pie_cx}" y="{pie_cy + 24}" font-size="16" fill="#666666" text-anchor="middle">高于10倍价样本</text>',
        )
        legend_x = left_panel_x + 520
        legend_y = third_panel_top + 190
        for index, (label, value, color) in enumerate(slices):
            percent = value / next_close_total * 100 if next_close_total else 0
            y = legend_y + index * 90
            svg_lines.append(
                f'<rect x="{legend_x}" y="{y - 16}" width="26" height="26" rx="6" fill="{color}"/>',
            )
            svg_lines.append(
                f'<text x="{legend_x + 40}" y="{y}" font-size="20" font-weight="600" fill="#111111">{xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<text x="{legend_x + 40}" y="{y + 28}" font-size="16" fill="#555555">{value} 次，占比 {percent:.2f}%</text>',
            )
    else:
        svg_lines.append(
            f'<text x="{left_panel_x + 24}" y="{third_panel_top + 120}" font-size="18" fill="#666666">暂无符合条件的数据</text>',
        )

    sixth_panel_top = 3920
    sixth_panel_h = 420
    svg_lines.extend(
        [
            f'<rect x="{left_panel_x}" y="{sixth_panel_top}" width="{width - 96}" height="{sixth_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{left_panel_x + 24}" y="{sixth_panel_top + 36}" font-size="26" font-weight="700" fill="#111111">总体第二根最高价是否高于10倍价</text>',
            f'<text x="{left_panel_x + 24}" y="{sixth_panel_top + 66}" font-size="16" fill="#666666">统计命中后第二根K线最高价是否站上10倍价，以及高出10倍价多少</text>',
        ],
    )
    summary_box_y = sixth_panel_top + 104
    summary_box_w = 280
    summary_gap = 24
    second_high_cards = [
        ("第二根高于10倍", str(next_high_above_count), "#27ae60"),
        ("第二根未高于10倍", str(next_high_below_count), "#e74c3c"),
        ("数据不足", str(next_high_unknown_count), "#95a5a6"),
        ("平均高出", "--" if next_high_above_avg_percent is None else f"{next_high_above_avg_percent:.2f}%", "#8e44ad"),
        ("最高高出", "--" if next_high_above_max_percent is None else f"{next_high_above_max_percent:.2f}%", "#f39c12"),
    ]
    for index, (label, value, color) in enumerate(second_high_cards):
        x = left_panel_x + 24 + index * (summary_box_w + summary_gap)
        svg_lines.append(
            f'<rect x="{x}" y="{summary_box_y}" width="{summary_box_w}" height="120" rx="16" fill="#fafbfc" stroke="#edf0f2"/>',
        )
        svg_lines.append(
            f'<text x="{x + 18}" y="{summary_box_y + 42}" font-size="18" fill="#666666">{xml_escape(label)}</text>',
        )
        svg_lines.append(
            f'<text x="{x + 18}" y="{summary_box_y + 82}" font-size="32" font-weight="700" fill="{color}">{xml_escape(value)}</text>',
        )
    known_total = next_high_above_count + next_high_below_count
    if known_total > 0:
        ratio = next_high_above_count / known_total * 100
        note = f"在可判断的 {known_total} 次里，第二根最高价高于10倍价 {next_high_above_count} 次，占比 {ratio:.2f}%"
    else:
        note = "当前 result.json 不含足够的第二根最高价数据，无法给出准确占比"
    if next_high_unknown_count > 0:
        note += f"；另有 {next_high_unknown_count} 次因缺少第二根 OHLC/最高价字段未纳入该统计"
    svg_lines.append(
        f'<text x="{left_panel_x + 24}" y="{sixth_panel_top + 280}" font-size="18" fill="#333333">{xml_escape(note)}</text>',
    )

    # three-year monthly summary panel
    svg_lines.extend(
        [
            f'<rect x="{right_panel_x}" y="{third_panel_top}" width="{panel_w}" height="{third_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{right_panel_x + 24}" y="{third_panel_top + 36}" font-size="26" font-weight="700" fill="#111111">最近三年命中统计（{target_years[2]} - {target_years[0]}）</text>',
            f'<text x="{right_panel_x + 24}" y="{third_panel_top + 66}" font-size="16" fill="#666666">展示 {target_years[0]}、{target_years[1]}、{target_years[2]} 每个月的命中数量</text>',
        ],
    )
    start_y = third_panel_top + 110
    month_labels = [f"{month}月" for month in range(1, 13)]
    for month_index, month_label in enumerate(month_labels):
        x = right_panel_x + 170 + month_index * 48
        svg_lines.append(
            f'<text x="{x}" y="{start_y}" font-size="14" fill="#666666" text-anchor="middle">{month_label}</text>',
        )
    year_colors = {
        target_years[0]: "#e74c3c",
        target_years[1]: "#3498db",
        target_years[2]: "#27ae60",
    }
    max_month_hits = max((max(counts) for counts in yearly_monthly.values()), default=1)
    for row_index, year in enumerate(target_years):
        y = start_y + 52 + row_index * 96
        year_total = sum(yearly_monthly[year])
        svg_lines.append(
            f'<text x="{right_panel_x + 24}" y="{y}" font-size="22" font-weight="700" fill="{year_colors[year]}">{year}</text>',
        )
        svg_lines.append(
            f'<text x="{right_panel_x + 92}" y="{y}" font-size="16" fill="#666666">全年 {year_total} 次</text>',
        )
        for month_index, count in enumerate(yearly_monthly[year]):
            x = right_panel_x + 150 + month_index * 48
            bar_h = 44 * (count / max_month_hits if max_month_hits else 0)
            bar_y = y + 26 - bar_h
            svg_lines.append(
                f'<rect x="{x - 14}" y="{y - 18}" width="28" height="48" rx="8" fill="#f1f3f5"/>',
            )
            svg_lines.append(
                f'<rect x="{x - 14}" y="{bar_y:.2f}" width="28" height="{bar_h:.2f}" rx="8" fill="{year_colors[year]}"/>',
            )
            svg_lines.append(
                f'<text x="{x}" y="{y + 50}" font-size="13" fill="#444444" text-anchor="middle">{count}</text>',
            )

    summary_text_y = third_panel_top + 440
    for offset, year in enumerate(target_years):
        counts = yearly_monthly[year]
        month_rank = sorted(
            [(idx + 1, count) for idx, count in enumerate(counts)],
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        month_text = " / ".join(f"{month}月 {count}" for month, count in month_rank if count > 0) or "无命中"
        svg_lines.append(
            f'<text x="{right_panel_x + 24}" y="{summary_text_y + offset * 30}" font-size="16" fill="#333333">{year} Top月份: {xml_escape(month_text)}</text>',
        )

    fourth_panel_top = 2240
    fourth_panel_h = 980
    svg_lines.extend(
        [
            f'<rect x="{left_panel_x}" y="{fourth_panel_top}" width="{width - 96}" height="{fourth_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{left_panel_x + 24}" y="{fourth_panel_top + 36}" font-size="26" font-weight="700" fill="#111111">十倍前振幅排行榜 Top 10（同时显示对应倍数）</text>',
            f'<text x="{left_panel_x + 24}" y="{fourth_panel_top + 66}" font-size="16" fill="#666666">按命中事件中十倍前一根K线的振幅排序</text>',
        ],
    )
    if amplitude_top10:
        max_amp = max(float(item["previous_amplitude_percent"]) for item in amplitude_top10) or 1.0
        row_y = fourth_panel_top + 110
        for index, item in enumerate(amplitude_top10):
            y = row_y + index * 78
            bar_x = left_panel_x + 520
            bar_w = 320
            fill_w = bar_w * (float(item["previous_amplitude_percent"]) / max_amp)
            label = f'{item["inst_id"]} {item["time_cn"]}'
            vol_text = "--" if item["volume_multiple"] is None else f'{float(item["volume_multiple"]):.2f}x'
            change_text = f"前涨跌 {item['previous_change_percent']:.2f}%"
            svg_lines.append(
                f'<text x="{left_panel_x + 24}" y="{y + 16}" font-size="16" font-weight="600" fill="#111111">{index + 1}. {xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="16" rx="8" fill="#eef1f5"/>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{fill_w:.2f}" height="16" rx="8" fill="#16a085"/>',
            )
            svg_lines.append(
                f'<text x="{bar_x + bar_w + 16}" y="{y + 14}" font-size="14" fill="#333333">{item["previous_amplitude_percent"]:.2f}% | 倍数 {item["multiple"]:.2f}x</text>',
            )
            svg_lines.append(
                f'<text x="{left_panel_x + 44}" y="{y + 38}" font-size="13" fill="#666666">{xml_escape(change_text)} | 成交量 {xml_escape(vol_text)}</text>',
            )
    else:
        svg_lines.append(
            f'<text x="{left_panel_x + 24}" y="{fourth_panel_top + 120}" font-size="18" fill="#666666">暂无振幅排行数据</text>',
        )

    fifth_panel_top = 3260
    fifth_panel_h = 650
    svg_lines.extend(
        [
            f'<rect x="{left_panel_x}" y="{fifth_panel_top}" width="{width - 96}" height="{fifth_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{left_panel_x + 24}" y="{fifth_panel_top + 36}" font-size="26" font-weight="700" fill="#111111">收盘相对10倍价偏离幅度排行榜 Top 10（从低到高）</text>',
            f'<text x="{left_panel_x + 24}" y="{fifth_panel_top + 66}" font-size="16" fill="#666666">按收盘相对10倍价的偏离涨跌幅从低到高排序</text>',
        ],
    )
    if close_ranked_low_abs:
        max_abs_close = max(
            abs(float(item["close_vs_threshold_percent"])) for item in close_ranked_low_abs
        ) or 1.0
        row_y = fifth_panel_top + 110
        for index, item in enumerate(close_ranked_low_abs):
            y = row_y + index * 52
            pct = float(item["close_vs_threshold_percent"])
            abs_pct = abs(pct)
            color = "#27ae60" if pct >= 0 else "#e74c3c"
            bar_x = left_panel_x + 560
            bar_w = 220
            fill_w = bar_w * (abs_pct / max_abs_close if max_abs_close else 0.0)
            label = f'{item["inst_id"]} {item["time_cn"]}'
            state = "收盘高于10倍价" if pct >= 0 else "收盘低于10倍价"
            vol = item["volume_multiple"]
            vol_text = "--" if vol is None else f"{float(vol):.2f}x"
            amp_text = f"前振幅 {item['previous_amplitude_percent']:.2f}%"
            change_text = f"前涨跌 {item['previous_change_percent']:.2f}%"
            svg_lines.append(
                f'<text x="{left_panel_x + 24}" y="{y + 16}" font-size="15" font-weight="600" fill="#111111">{index + 1}. {xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="16" rx="8" fill="#eef1f5"/>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{fill_w:.2f}" height="16" rx="8" fill="{color}"/>',
            )
            svg_lines.append(
                f'<text x="{bar_x + bar_w + 16}" y="{y + 14}" font-size="14" fill="#333333">偏离 {pct:.2f}%</text>',
            )
            svg_lines.append(
                f'<text x="{left_panel_x + 44}" y="{y + 36}" font-size="13" fill="#666666">{xml_escape(state)} | {xml_escape(amp_text)} | {xml_escape(change_text)} | 成交量 {xml_escape(vol_text)}</text>',
            )
    else:
        svg_lines.append(
            f'<text x="{left_panel_x + 24}" y="{fifth_panel_top + 120}" font-size="18" fill="#666666">暂无偏离幅度排行数据</text>',
        )

    svg_lines.append("</svg>")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write("\n".join(svg_lines))


def main() -> int:
    args = parse_args()
    results = load_results(args.input)
    render_dashboard(results, args.output, args.threshold)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
