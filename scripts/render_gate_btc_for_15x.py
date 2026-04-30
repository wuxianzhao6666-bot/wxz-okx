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
    "btc_for_15x.json",
)
DEFAULT_SVG_OUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "gate_tenfold",
    "charts",
    "summary_svgs",
    "btc_for_15x.svg",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze BTC 1H change during Gate 15x+ hit timestamps.",
    )
    parser.add_argument("--input", default=DEFAULT_GATE_INPUT, help="Gate result.json path")
    parser.add_argument("--json-out", default=DEFAULT_JSON_OUT, help="Output JSON path")
    parser.add_argument("--svg-out", default=DEFAULT_SVG_OUT, help="Output SVG path")
    parser.add_argument("--threshold", type=float, default=15.0, help="Minimum hit multiple")
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


def collect_15x_events(results: dict[str, Any], threshold: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for inst_id, summary in results.items():
        if not isinstance(summary, dict) or "error" in summary:
            continue
        for event in summary.get("events", []):
            multiple = float(event.get("multiple", 0.0))
            if multiple < threshold:
                continue
            events.append(
                {
                    "inst_id": inst_id,
                    "time_cn": event.get("time_cn", ""),
                    "hit_ts": int(event.get("hit_ts", 0)),
                    "multiple": multiple,
                },
            )
    events.sort(key=lambda item: item["hit_ts"])
    return events


def fetch_btc_candles(host: str, inst_id: str, hit_timestamps: list[int]) -> dict[int, dict[str, float]]:
    seen: dict[int, dict[str, float]] = {}
    for hit_ts in hit_timestamps:
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
            seen[ts] = {
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "change_percent": ((close_price - open_price) / open_price * 100) if open_price else 0.0,
                "amplitude_percent": ((high_price - low_price) / open_price * 100) if open_price else 0.0,
            }
    return seen


def build_output_rows(events: list[dict[str, Any]], btc_candles: dict[int, dict[str, float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        candle = btc_candles.get(event["hit_ts"])
        if candle is None:
            continue
        rows.append(
            {
                **event,
                "btc_change_percent": candle["change_percent"],
                "btc_amplitude_percent": candle["amplitude_percent"],
                "btc_open": candle["open"],
                "btc_high": candle["high"],
                "btc_low": candle["low"],
                "btc_close": candle["close"],
            },
        )
    return rows


def render_svg(rows: list[dict[str, Any]], threshold: float, output_path: str) -> None:
    total = len(rows)
    positive = sum(1 for row in rows if row["btc_change_percent"] > 0)
    negative = sum(1 for row in rows if row["btc_change_percent"] < 0)
    flat = sum(1 for row in rows if row["btc_change_percent"] == 0)
    avg_change = sum(row["btc_change_percent"] for row in rows) / total if total else None
    avg_amp = sum(row["btc_amplitude_percent"] for row in rows) / total if total else None
    top_up = sorted(rows, key=lambda item: item["btc_change_percent"], reverse=True)[:10]
    top_down = sorted(rows, key=lambda item: item["btc_change_percent"])[:10]

    width = 1800
    height = 1900
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f8fa"/>',
        f'<text x="48" y="58" font-size="34" font-weight="700" fill="#111111">Gate {threshold:g}倍以上命中时 BTC 涨跌幅分析</text>',
        '<text x="48" y="94" font-size="18" fill="#555555">使用 OKX BTC-USDT-SWAP 1H K线，对齐 Gate 15倍以上命中时间点</text>',
    ]
    cards = [
        ("15倍以上事件", str(total), "#8e44ad"),
        ("BTC上涨", str(positive), "#27ae60"),
        ("BTC下跌", str(negative), "#e74c3c"),
        ("BTC持平", str(flat), "#7f8c8d"),
        ("BTC平均涨跌", "--" if avg_change is None else f"{avg_change:.2f}%", "#2d3436"),
        ("BTC平均振幅", "--" if avg_amp is None else f"{avg_amp:.2f}%", "#f39c12"),
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

    def render_panel(title: str, panel_y: int, rows_subset: list[dict[str, Any]], color: str) -> None:
        panel_x = 48
        panel_w = width - 96
        panel_h = 760
        svg_lines.extend(
            [
                f'<rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
                f'<text x="{panel_x + 24}" y="{panel_y + 36}" font-size="26" font-weight="700" fill="#111111">{xml_escape(title)}</text>',
            ],
        )
        if not rows_subset:
            svg_lines.append(
                f'<text x="{panel_x + 24}" y="{panel_y + 120}" font-size="18" fill="#666666">暂无数据</text>',
            )
            return
        max_abs = max(abs(row["btc_change_percent"]) for row in rows_subset) or 1.0
        row_y = panel_y + 110
        row_gap = 60
        bar_x = panel_x + 700
        bar_w = 260
        for index, row in enumerate(rows_subset):
            y = row_y + index * row_gap
            value = row["btc_change_percent"]
            fill_w = bar_w * (abs(value) / max_abs if max_abs else 0.0)
            label = f"{row['inst_id']} {row['time_cn']}"
            detail = f"BTC {value:+.2f}% | 振幅 {row['btc_amplitude_percent']:.2f}% | 倍数 {row['multiple']:.2f}x"
            svg_lines.append(
                f'<text x="{panel_x + 24}" y="{y + 16}" font-size="15" font-weight="600" fill="#111111">{index + 1}. {xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="16" rx="8" fill="#eef1f5"/>',
            )
            svg_lines.append(
                f'<rect x="{bar_x}" y="{y}" width="{fill_w:.2f}" height="16" rx="8" fill="{color}"/>',
            )
            svg_lines.append(
                f'<text x="{bar_x + bar_w + 16}" y="{y + 14}" font-size="14" fill="#333333">{value:+.2f}%</text>',
            )
            svg_lines.append(
                f'<text x="{panel_x + 44}" y="{y + 36}" font-size="13" fill="#666666">{xml_escape(detail)}</text>',
            )

    render_panel("BTC 同时段涨幅 Top 10", 290, top_up, "#27ae60")
    render_panel("BTC 同时段跌幅 Top 10", 1080, top_down, "#e74c3c")

    svg_lines.append("</svg>")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write("\n".join(svg_lines))


def main() -> int:
    args = parse_args()
    results = load_gate_results(args.input)
    events = collect_15x_events(results, args.threshold)
    btc_candles = fetch_btc_candles(
        args.okx_host,
        args.btc_inst_id,
        [event["hit_ts"] for event in events],
    )
    rows = build_output_rows(events, btc_candles)

    os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)

    render_svg(rows, args.threshold, args.svg_out)
    print(args.json_out)
    print(args.svg_out)
    print(f"matched_events={len(rows)} total_events={len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
