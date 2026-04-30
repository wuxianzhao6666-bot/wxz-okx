#!/usr/bin/env python3
import argparse
import json
import os
from typing import Any


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "gate_tenfold",
    "previous_volume_summary.json",
)
DEFAULT_OUTPUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "gate_tenfold",
    "charts",
    "summary_svgs",
    "previous_volume_dashboard.svg",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render Gate previous-volume summary dashboard SVG.",
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to JSON input")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Path to output SVG")
    return parser.parse_args()


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def load_rows(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected JSON shape in {path}")
    return [row for row in payload if isinstance(row, dict)]


def render_dashboard(rows: list[dict[str, Any]], output_path: str) -> None:
    bucket_defs = [
        ("0-2k", 0, 2_000),
        ("2k-10k", 2_000, 10_000),
        ("10k-50k", 10_000, 50_000),
        ("50k-100k", 50_000, 100_000),
        ("100k-500k", 100_000, 500_000),
        ("500k以上", 500_000, None),
    ]
    bucket_stats: list[dict[str, Any]] = []
    total = len(rows)
    for label, lower, upper in bucket_defs:
        matched = [
            row
            for row in rows
            if float(row.get("previous_volume", 0.0)) >= lower
            and (upper is None or float(row.get("previous_volume", 0.0)) < upper)
        ]
        matched.sort(key=lambda item: float(item.get("multiple", 0.0)), reverse=True)
        bucket_stats.append(
            {
                "label": label,
                "count": len(matched),
                "ratio": (len(matched) / total * 100) if total else 0.0,
                "top": matched[:3],
            },
        )

    ranked_by_volume = sorted(
        rows,
        key=lambda item: float(item.get("previous_volume", 0.0)),
        reverse=True,
    )[:10]

    width = 1800
    height = 2200
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f8fa"/>',
        '<text x="48" y="58" font-size="34" font-weight="700" fill="#111111">Gate 十倍前成交量总结图</text>',
        '<text x="48" y="94" font-size="18" fill="#555555">统计 Gate 10倍命中事件中，十倍前一根K线成交量的区间分布、占比和排名</text>',
    ]

    cards = [
        ("命中总数", str(total), "#8e44ad"),
        ("最小成交量", "--" if not rows else f"{min(float(row['previous_volume']) for row in rows):.0f}", "#27ae60"),
        ("中位成交量", "--" if not rows else f"{sorted(float(row['previous_volume']) for row in rows)[len(rows)//2]:.0f}", "#2980b9"),
        ("最大成交量", "--" if not rows else f"{max(float(row['previous_volume']) for row in rows):.0f}", "#c0392b"),
    ]
    card_y = 130
    card_w = 360
    card_h = 110
    gap = 24
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

    panel_x = 48
    panel_y = 290
    panel_w = width - 96
    panel_h = 760
    svg_lines.extend(
        [
            f'<rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{panel_x + 24}" y="{panel_y + 36}" font-size="26" font-weight="700" fill="#111111">十倍前成交量区间分布</text>',
            f'<text x="{panel_x + 24}" y="{panel_y + 66}" font-size="16" fill="#666666">按区间统计数量和占比，并列出每个区间内倍数最高的前三个事件</text>',
        ],
    )
    max_count = max((item["count"] for item in bucket_stats), default=1) or 1
    row_y = panel_y + 110
    row_gap = 108
    bar_x = panel_x + 260
    bar_w = 300
    for index, item in enumerate(bucket_stats):
        y = row_y + index * row_gap
        fill_w = bar_w * (item["count"] / max_count if max_count else 0.0)
        svg_lines.append(
            f'<text x="{panel_x + 24}" y="{y + 16}" font-size="20" font-weight="700" fill="#111111">{xml_escape(item["label"])}</text>',
        )
        svg_lines.append(
            f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="18" rx="9" fill="#eef1f5"/>',
        )
        svg_lines.append(
            f'<rect x="{bar_x}" y="{y}" width="{fill_w:.2f}" height="18" rx="9" fill="#16a085"/>',
        )
        svg_lines.append(
            f'<text x="{bar_x + bar_w + 18}" y="{y + 15}" font-size="16" fill="#333333">{item["count"]} 次 | {item["ratio"]:.2f}%</text>',
        )
        top_text = " / ".join(
            f"{entry['inst_id']} {float(entry['multiple']):.2f}x"
            for entry in item["top"]
        ) or "无"
        svg_lines.append(
            f'<text x="{panel_x + 44}" y="{y + 48}" font-size="15" fill="#666666">Top: {xml_escape(top_text)}</text>',
        )

    rank_panel_y = 1090
    rank_panel_h = 980
    svg_lines.extend(
        [
            f'<rect x="{panel_x}" y="{rank_panel_y}" width="{panel_w}" height="{rank_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{panel_x + 24}" y="{rank_panel_y + 36}" font-size="26" font-weight="700" fill="#111111">十倍前成交量 Top 10</text>',
            f'<text x="{panel_x + 24}" y="{rank_panel_y + 66}" font-size="16" fill="#666666">按十倍前一根K线成交量从高到低排序，并显示对应倍数</text>',
        ],
    )
    if ranked_by_volume:
        max_volume = max(float(item["previous_volume"]) for item in ranked_by_volume) or 1.0
        row_y = rank_panel_y + 110
        row_gap = 78
        bar_x = panel_x + 620
        bar_w = 320
        for index, item in enumerate(ranked_by_volume):
            y = row_y + index * row_gap
            value = float(item["previous_volume"])
            fill_w = bar_w * (value / max_volume if max_volume else 0.0)
            label = f"{item['inst_id']} {item['time_cn']}"
            svg_lines.append(
                f'<text x="{panel_x + 24}" y="{y + 16}" font-size="16" font-weight="600" fill="#111111">{index + 1}. {xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="16" rx="8" fill="#eef1f5"/>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{fill_w:.2f}" height="16" rx="8" fill="#8e44ad"/>',
            )
            svg_lines.append(
                f'<text x="{bar_x + bar_w + 16}" y="{y + 14}" font-size="14" fill="#333333">{value:.0f} | 倍数 {float(item["multiple"]):.2f}x</text>',
            )
            detail = (
                f"前振幅 {float(item['previous_amplitude_percent']):.2f}% | "
                f"前涨幅 {float(item['previous_change_percent']):.2f}% | "
                f"量能倍数 {'--' if item.get('volume_multiple') is None else f'{float(item['volume_multiple']):.2f}x'}"
            )
            svg_lines.append(
                f'<text x="{panel_x + 44}" y="{y + 38}" font-size="13" fill="#666666">{xml_escape(detail)}</text>',
            )
    else:
        svg_lines.append(
            f'<text x="{panel_x + 24}" y="{rank_panel_y + 120}" font-size="18" fill="#666666">暂无可展示数据</text>',
        )

    svg_lines.append("</svg>")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write("\n".join(svg_lines))


def main() -> int:
    args = parse_args()
    rows = load_rows(args.input)
    render_dashboard(rows, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
