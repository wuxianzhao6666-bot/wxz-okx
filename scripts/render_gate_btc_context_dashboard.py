#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "gate_tenfold",
    "result.json",
)
DEFAULT_OUTPUT = os.path.join(
    PROJECT_ROOT,
    "analysis_outputs",
    "gate_tenfold",
    "charts",
    "summary_svgs",
    "btc_context_dashboard.svg",
)
CN_TZ = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render BTC context dashboard for Gate tenfold hit timestamps.",
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to Gate result.json")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Path to output SVG")
    parser.add_argument("--host", default="fx-api.gateio.ws", help="Gate API host")
    parser.add_argument("--contract", default="BTC_USDT", help="BTC contract name")
    return parser.parse_args()


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def run_curl(url: str) -> Any:
    last_error: str | None = None
    for attempt in range(3):
        cmd = [
            "curl",
            "-L",
            "--connect-timeout",
            "15",
            "--max-time",
            "45",
            "-s",
            "-H",
            "User-Agent: Mozilla/5.0",
            "-H",
            "Accept: application/json",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        stderr = result.stderr.strip() or "empty response"
        last_error = f"curl failed ({result.returncode}) for {url}: {stderr}"
        if attempt < 2:
            time.sleep(1.0)
    raise RuntimeError(last_error or f"curl failed for {url}")


def candidate_hosts(preferred_host: str) -> list[str]:
    hosts = [preferred_host]
    for host in ("fx-api.gateio.ws", "api.gateio.ws"):
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


def run_curl_with_fallback(url: str, preferred_host: str) -> Any:
    last_error: Exception | None = None
    for host in candidate_hosts(preferred_host):
        candidate_url = replace_host(url, host)
        try:
            return run_curl(candidate_url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if last_error is None:
        raise RuntimeError(f"Request failed without an explicit error: {url}")
    raise last_error


def load_results(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected result.json structure: {path}")
    return payload


def collect_hit_events(results: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for inst_id, summary in results.items():
        if not isinstance(summary, dict) or "error" in summary:
            continue
        for event in summary.get("events", []):
            events.append(
                {
                    "inst_id": inst_id,
                    "time_cn": event.get("time_cn", ""),
                    "hit_ts": int(event.get("hit_ts", 0)),
                    "multiple": float(event.get("multiple", 0.0)),
                },
            )
    events.sort(key=lambda item: item["hit_ts"])
    return events


def fetch_btc_candles(host: str, contract: str, start_ts: int, end_ts: int) -> dict[int, dict[str, float]]:
    candles: dict[int, dict[str, float]] = {}
    chunk_seconds = 30 * 24 * 60 * 60
    cursor = start_ts
    while cursor <= end_ts:
        chunk_end = min(end_ts, cursor + chunk_seconds)
        url = (
            f"https://{host}/api/v4/futures/usdt/candlesticks"
            f"?contract={contract}&interval=1h&from={cursor}&to={chunk_end}"
        )
        payload = run_curl_with_fallback(url, host)
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Gate candle response")
        for row in payload:
            if not isinstance(row, dict):
                continue
            try:
                ts_ms = int(float(str(row.get("t")))) * 1000
                open_price = float(str(row.get("o", 0) or 0))
                high_price = float(str(row.get("h", 0) or 0))
                low_price = float(str(row.get("l", 0) or 0))
                close_price = float(str(row.get("c", 0) or 0))
            except (TypeError, ValueError):
                continue
            change_percent = ((close_price - open_price) / open_price * 100) if open_price else 0.0
            amplitude_percent = ((high_price - low_price) / open_price * 100) if open_price else 0.0
            candles[ts_ms] = {
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "change_percent": change_percent,
                "amplitude_percent": amplitude_percent,
            }
        cursor = chunk_end + 3600
    return candles


def format_ts(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def render_dashboard(
    output_path: str,
    contract: str,
    hit_events: list[dict[str, Any]],
    matched_rows: list[dict[str, Any]],
) -> None:
    total = len(hit_events)
    matched = len(matched_rows)
    positive = sum(1 for row in matched_rows if row["btc_change_percent"] > 0)
    negative = sum(1 for row in matched_rows if row["btc_change_percent"] < 0)
    flat = sum(1 for row in matched_rows if row["btc_change_percent"] == 0)
    avg_change = (
        sum(row["btc_change_percent"] for row in matched_rows) / matched if matched else None
    )
    avg_amplitude = (
        sum(row["btc_amplitude_percent"] for row in matched_rows) / matched if matched else None
    )

    direction_buckets = Counter()
    for row in matched_rows:
        pct = row["btc_change_percent"]
        if pct >= 3:
            direction_buckets["上涨>=3%"] += 1
        elif pct >= 1:
            direction_buckets["上涨1-3%"] += 1
        elif pct > 0:
            direction_buckets["上涨0-1%"] += 1
        elif pct <= -3:
            direction_buckets["下跌>=3%"] += 1
        elif pct <= -1:
            direction_buckets["下跌1-3%"] += 1
        elif pct < 0:
            direction_buckets["下跌0-1%"] += 1
        else:
            direction_buckets["持平"] += 1

    top_up = sorted(matched_rows, key=lambda item: item["btc_change_percent"], reverse=True)[:10]
    top_down = sorted(matched_rows, key=lambda item: item["btc_change_percent"])[:10]

    width = 1800
    height = 2400
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f8fa"/>',
        f'<text x="48" y="58" font-size="34" font-weight="700" fill="#111111">Gate 十倍命中时点的 BTC 上涨情况</text>',
        f'<text x="48" y="94" font-size="18" fill="#555555">对齐 {xml_escape(contract)} 在每个 Gate 十倍命中小时的涨跌幅、振幅和方向分布</text>',
    ]

    cards = [
        ("十倍命中总数", str(total), "#8e44ad"),
        ("成功对齐BTC", str(matched), "#2980b9"),
        ("BTC上涨次数", str(positive), "#27ae60"),
        ("BTC下跌次数", str(negative), "#e74c3c"),
        ("BTC平均涨跌", "--" if avg_change is None else f"{avg_change:.2f}%", "#2d3436"),
        ("BTC平均振幅", "--" if avg_amplitude is None else f"{avg_amplitude:.2f}%", "#f39c12"),
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
    panel_h = 620
    svg_lines.extend(
        [
            f'<rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
            f'<text x="{panel_x + 24}" y="{panel_y + 36}" font-size="26" font-weight="700" fill="#111111">BTC 涨跌方向分布</text>',
            f'<text x="{panel_x + 24}" y="{panel_y + 66}" font-size="16" fill="#666666">统计十倍币命中当小时，BTC 的涨跌幅属于哪个区间</text>',
        ],
    )
    order = ["上涨>=3%", "上涨1-3%", "上涨0-1%", "持平", "下跌0-1%", "下跌1-3%", "下跌>=3%"]
    max_count = max((direction_buckets.get(label, 0) for label in order), default=1) or 1
    row_y = panel_y + 110
    row_gap = 60
    bar_x = panel_x + 260
    bar_w = 650
    for index, label in enumerate(order):
        count = direction_buckets.get(label, 0)
        ratio = count / matched * 100 if matched else 0.0
        fill_w = bar_w * (count / max_count if max_count else 0.0)
        y = row_y + index * row_gap
        svg_lines.append(
            f'<text x="{panel_x + 24}" y="{y + 16}" font-size="18" font-weight="600" fill="#111111">{xml_escape(label)}</text>',
        )
        svg_lines.append(
            f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="18" rx="9" fill="#eef1f5"/>',
        )
        svg_lines.append(
            f'<rect x="{bar_x}" y="{y}" width="{fill_w:.2f}" height="18" rx="9" fill="#3498db"/>',
        )
        svg_lines.append(
            f'<text x="{bar_x + bar_w + 18}" y="{y + 15}" font-size="16" fill="#333333">{count} 次 | {ratio:.2f}%</text>',
        )

    def render_rank_panel(title: str, rows: list[dict[str, Any]], start_y: int, color: str) -> None:
        svg_lines.extend(
            [
                f'<rect x="{panel_x}" y="{start_y}" width="{panel_w}" height="620" rx="20" fill="#ffffff" stroke="#ebedf0"/>',
                f'<text x="{panel_x + 24}" y="{start_y + 36}" font-size="26" font-weight="700" fill="#111111">{xml_escape(title)}</text>',
            ],
        )
        if not rows:
            svg_lines.append(
                f'<text x="{panel_x + 24}" y="{start_y + 120}" font-size="18" fill="#666666">暂无数据</text>',
            )
            return
        max_abs = max(abs(row["btc_change_percent"]) for row in rows) or 1.0
        row_y = start_y + 110
        row_gap = 48
        bar_x = panel_x + 620
        bar_w = 260
        for index, row in enumerate(rows):
            y = row_y + index * row_gap
            value = row["btc_change_percent"]
            fill_w = bar_w * (abs(value) / max_abs if max_abs else 0.0)
            label = f"{row['inst_id']} {row['event_time_cn']}"
            detail = (
                f"BTC {value:+.2f}% | 振幅 {row['btc_amplitude_percent']:.2f}% | "
                f"十倍币 {row['multiple']:.2f}x"
            )
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

    render_rank_panel("BTC 同时段涨幅 Top 10", top_up, 950, "#27ae60")
    render_rank_panel("BTC 同时段跌幅 Top 10", top_down, 1600, "#e74c3c")

    svg_lines.append("</svg>")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write("\n".join(svg_lines))


def main() -> int:
    args = parse_args()
    results = load_results(args.input)
    hit_events = collect_hit_events(results)
    if not hit_events:
        raise SystemExit("No tenfold hit events found in input.")

    min_ts = min(item["hit_ts"] for item in hit_events) // 1000 - 3600
    max_ts = max(item["hit_ts"] for item in hit_events) // 1000 + 3600
    btc_candles = fetch_btc_candles(args.host, args.contract, min_ts, max_ts)

    matched_rows: list[dict[str, Any]] = []
    for event in hit_events:
        candle = btc_candles.get(event["hit_ts"])
        if candle is None:
            continue
        matched_rows.append(
            {
                "inst_id": event["inst_id"],
                "event_time_cn": event["time_cn"],
                "multiple": event["multiple"],
                "btc_change_percent": candle["change_percent"],
                "btc_amplitude_percent": candle["amplitude_percent"],
                "btc_open": candle["open"],
                "btc_close": candle["close"],
            },
        )

    render_dashboard(args.output, args.contract, hit_events, matched_rows)
    print(args.output)
    print(f"matched_events={len(matched_rows)} total_events={len(hit_events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
