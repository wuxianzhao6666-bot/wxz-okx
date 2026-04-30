#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
from typing import Any


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GATE_INPUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "gate_tenfold",
    "result.json",
)
DEFAULT_JSON_OUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "gate_tenfold",
    "btc_next_low_breakdown.json",
)
DEFAULT_SVG_OUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "gate_tenfold",
    "charts",
    "summary_svgs",
    "btc_next_low_breakdown.svg",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render BTC-vs-next-low breakdown for Gate tenfold hits.",
    )
    parser.add_argument("--input", default=DEFAULT_GATE_INPUT, help="Gate result.json path")
    parser.add_argument("--json-out", default=DEFAULT_JSON_OUT, help="Output JSON path")
    parser.add_argument("--svg-out", default=DEFAULT_SVG_OUT, help="Output SVG path")
    parser.add_argument("--okx-host", default="app.okx.com", help="OKX host")
    parser.add_argument("--btc-inst-id", default="BTC-USDT-SWAP", help="BTC instrument id")
    return parser.parse_args()


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def run_curl(url: str) -> dict[str, Any]:
    cmd = [
        "curl",
        "-L",
        "--max-time",
        "20",
        "-s",
        "-H",
        "User-Agent: Mozilla/5.0",
        "-H",
        "Accept: application/json",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"curl failed ({result.returncode}) for {url}: {result.stderr.strip()}",
        )
    if not result.stdout.strip():
        raise RuntimeError(f"empty response for {url}")
    payload = json.loads(result.stdout)
    code = payload.get("code")
    if code is not None and code != "0":
        raise RuntimeError(f"OKX returned error for {url}: {payload}")
    return payload


def candidate_hosts(preferred_host: str) -> list[str]:
    hosts = [preferred_host]
    for host in ("app.okx.com", "my.okx.com"):
        if host not in hosts:
            hosts.append(host)
    return hosts


def replace_host(url: str, new_host: str) -> str:
    prefix = "https://"
    if not url.startswith(prefix):
        return url
    rest = url[len(prefix):]
    _, _, path = rest.partition("/")
    return f"{prefix}{new_host}/{path}"


def run_curl_with_fallback(url: str, preferred_host: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for host in candidate_hosts(preferred_host):
        candidate_url = replace_host(url, host)
        for attempt in range(3):
            try:
                return run_curl(candidate_url)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < 2:
                    time.sleep(0.6)
    if last_error is None:
        raise RuntimeError(f"Request failed without an explicit error: {url}")
    raise last_error


def load_gate_results(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected gate result structure: {path}")
    return payload


def collect_events(results: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for inst_id, summary in results.items():
        if not isinstance(summary, dict) or "error" in summary:
            continue
        for event in summary.get("events", []):
            rows.append(
                {
                    "inst_id": inst_id,
                    "time_cn": event.get("time_cn", ""),
                    "hit_ts": int(event.get("hit_ts", 0)),
                    "multiple": float(event.get("multiple", 0.0)),
                    "next_candle_low_below_threshold": event.get("next_candle_low_below_threshold"),
                    "next_candle_low_vs_threshold_percent": event.get("next_candle_low_vs_threshold_percent"),
                },
            )
    rows.sort(key=lambda item: item["hit_ts"])
    return rows


def fetch_okx_btc_candles(
    host: str,
    inst_id: str,
    hit_timestamps: list[int],
) -> dict[int, dict[str, float]]:
    candles: dict[int, dict[str, float]] = {}
    for hit_ts in sorted(set(hit_timestamps)):
        # Pull a narrow window around the hit time instead of walking the full
        # BTC history; this is much faster and more reliable on flaky networks.
        boundary_ts = hit_ts + 3 * 3600 * 1000
        url = (
            f"https://{host}/api/v5/market/history-candles"
            f"?instId={inst_id}&bar=1H&limit=6&after={boundary_ts}"
        )
        payload = run_curl_with_fallback(url, host)
        for row in payload.get("data", []):
            if str(row[8]) != "1":
                continue
            ts = int(row[0])
            open_price = float(row[1])
            high_price = float(row[2])
            low_price = float(row[3])
            close_price = float(row[4])
            candles[ts] = {
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "change_percent": ((close_price - open_price) / open_price * 100) if open_price else 0.0,
                "amplitude_percent": ((high_price - low_price) / open_price * 100) if open_price else 0.0,
            }
    return candles


def join_rows(events: list[dict[str, Any]], btc_candles: dict[int, dict[str, float]]) -> list[dict[str, Any]]:
    joined: list[dict[str, Any]] = []
    for event in events:
        candle = btc_candles.get(event["hit_ts"])
        if candle is None:
            continue
        joined.append(
            {
                **event,
                "btc_change_percent": candle["change_percent"],
                "btc_amplitude_percent": candle["amplitude_percent"],
            },
        )
    return joined


def render_svg(rows: list[dict[str, Any]], output_path: str) -> None:
    total = len(rows)
    btc_up_rows = [row for row in rows if row["btc_change_percent"] > 0]
    btc_down_rows = [row for row in rows if row["btc_change_percent"] < 0]
    btc_flat_rows = [row for row in rows if row["btc_change_percent"] == 0]
    low_below_rows = [row for row in rows if row["next_candle_low_below_threshold"] is True]
    low_not_below_rows = [row for row in rows if row["next_candle_low_below_threshold"] is False]

    up_below = sum(1 for row in btc_up_rows if row["next_candle_low_below_threshold"] is True)
    down_below = sum(1 for row in btc_down_rows if row["next_candle_low_below_threshold"] is True)
    flat_below = sum(1 for row in btc_flat_rows if row["next_candle_low_below_threshold"] is True)

    up_below_ratio = up_below / len(btc_up_rows) * 100 if btc_up_rows else None
    down_below_ratio = down_below / len(btc_down_rows) * 100 if btc_down_rows else None
    stay_above_ratio = len(low_not_below_rows) / total * 100 if total else None

    width = 1800
    height = 1650
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f8fa"/>',
        '<text x="48" y="58" font-size="34" font-weight="700" fill="#111111">BTC 涨跌 vs 十倍后一根最低点</text>',
        '<text x="48" y="94" font-size="18" fill="#555555">统计 Gate 10倍命中中，BTC 当小时涨跌与后一根最低点是否跌破10倍价的关系</text>',
    ]

    cards = [
        ("10倍命中总数", str(total), "#8e44ad"),
        ("BTC上涨样本", str(len(btc_up_rows)), "#27ae60"),
        ("BTC下跌样本", str(len(btc_down_rows)), "#e74c3c"),
        ("BTC平盘样本", str(len(btc_flat_rows)), "#7f8c8d"),
        ("后一根最低点<10倍价", str(len(low_below_rows)), "#c0392b"),
        ("后一根最低点>=10倍价", str(len(low_not_below_rows)), "#2980b9"),
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
            f'<text x="{x + 20}" y="{card_y + 84}" font-size="32" font-weight="700" fill="{color}">{xml_escape(value)}</text>',
        )

    panel_x = 48
    panel_y = 290
    panel_w = width - 96
    panel_h = 520
    svg_lines.extend(
        [
            f'<rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{panel_x + 24}" y="{panel_y + 36}" font-size="26" font-weight="700" fill="#111111">关键占比</text>',
            f'<text x="{panel_x + 24}" y="{panel_y + 66}" font-size="16" fill="#666666">这里把你关心的三类情况直接单独拎出来统计</text>',
        ],
    )
    stat_cards = [
        (
            "BTC上涨时，后一根最低点跌破10倍价",
            "--" if up_below_ratio is None else f"{up_below_ratio:.2f}%",
            f"{up_below}/{len(btc_up_rows)}" if btc_up_rows else "数据不足",
            "#27ae60",
        ),
        (
            "BTC下跌时，后一根最低点跌破10倍价",
            "--" if down_below_ratio is None else f"{down_below_ratio:.2f}%",
            f"{down_below}/{len(btc_down_rows)}" if btc_down_rows else "数据不足",
            "#e74c3c",
        ),
        (
            "后一根最低点仍在10倍价以上",
            "--" if stay_above_ratio is None else f"{stay_above_ratio:.2f}%",
            f"{len(low_not_below_rows)}/{total}" if total else "数据不足",
            "#2980b9",
        ),
    ]
    stat_y = panel_y + 110
    stat_w = 540
    stat_gap = 24
    for index, (label, value, detail, color) in enumerate(stat_cards):
        x = panel_x + 24 + index * (stat_w + stat_gap)
        svg_lines.append(
            f'<rect x="{x}" y="{stat_y}" width="{stat_w}" height="180" rx="16" fill="#fafbfc" stroke="#edf0f2"/>',
        )
        svg_lines.append(
            f'<text x="{x + 18}" y="{stat_y + 42}" font-size="20" fill="#666666">{xml_escape(label)}</text>',
        )
        svg_lines.append(
            f'<text x="{x + 18}" y="{stat_y + 92}" font-size="34" font-weight="700" fill="{color}">{xml_escape(value)}</text>',
        )
        svg_lines.append(
            f'<text x="{x + 18}" y="{stat_y + 132}" font-size="18" fill="#555555">{xml_escape(detail)}</text>',
        )

    second_panel_y = 850
    second_panel_h = 700
    svg_lines.extend(
        [
            f'<rect x="{panel_x}" y="{second_panel_y}" width="{panel_w}" height="{second_panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{panel_x + 24}" y="{second_panel_y + 36}" font-size="26" font-weight="700" fill="#111111">BTC方向分布与跌破情况</text>',
            f'<text x="{panel_x + 24}" y="{second_panel_y + 66}" font-size="16" fill="#666666">看 BTC 上涨、下跌、平盘 时，各自有多少事件导致后一根最低点跌破10倍价</text>',
        ],
    )
    rows_for_bars = [
        ("BTC上涨", len(btc_up_rows), up_below, "#27ae60"),
        ("BTC下跌", len(btc_down_rows), down_below, "#e74c3c"),
        ("BTC平盘", len(btc_flat_rows), flat_below, "#7f8c8d"),
    ]
    max_total = max((item[1] for item in rows_for_bars), default=1) or 1
    row_y = second_panel_y + 120
    row_gap = 140
    bar_x = panel_x + 260
    bar_w = 700
    for label, total_count, below_count, color in rows_for_bars:
        ratio = below_count / total_count * 100 if total_count else 0.0
        fill_w = bar_w * (total_count / max_total if max_total else 0.0)
        below_w = fill_w * (below_count / total_count) if total_count else 0.0
        svg_lines.append(
            f'<text x="{panel_x + 24}" y="{row_y + 16}" font-size="22" font-weight="700" fill="#111111">{xml_escape(label)}</text>',
        )
        svg_lines.append(
            f'<rect x="{bar_x}" y="{row_y}" width="{bar_w}" height="24" rx="12" fill="#eef1f5"/>',
        )
        svg_lines.append(
            f'<rect x="{bar_x}" y="{row_y}" width="{fill_w:.2f}" height="24" rx="12" fill="{color}" opacity="0.35"/>',
        )
        svg_lines.append(
            f'<rect x="{bar_x}" y="{row_y}" width="{below_w:.2f}" height="24" rx="12" fill="{color}"/>',
        )
        svg_lines.append(
            f'<text x="{bar_x + bar_w + 18}" y="{row_y + 20}" font-size="18" fill="#333333">跌破 {below_count}/{total_count} | {ratio:.2f}%</text>',
        )
        row_y += row_gap

    svg_lines.append("</svg>")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write("\n".join(svg_lines))


def main() -> int:
    args = parse_args()
    results = load_gate_results(args.input)
    events = collect_events(results)
    btc_candles = fetch_okx_btc_candles(
        args.okx_host,
        args.btc_inst_id,
        [event["hit_ts"] for event in events],
    )
    rows = join_rows(events, btc_candles)

    os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)

    render_svg(rows, args.svg_out)
    print(args.json_out)
    print(args.svg_out)
    print(f"matched_events={len(rows)} total_events={len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
