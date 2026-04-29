#!/usr/bin/env python3
import argparse
import csv
import json
import math
import os
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


CN_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "analysis_outputs", "okx_tenfold")
DEFAULT_SCANNED_FILE = os.path.join(DEFAULT_OUTPUT_DIR, "scanned_symbols.txt")
DEFAULT_HIT_LIST_FILE = os.path.join(DEFAULT_OUTPUT_DIR, "tenfold_hit_symbols.txt")
DEFAULT_PROGRESS_FILE = os.path.join(DEFAULT_OUTPUT_DIR, "scan_progress.jsonl")
NINEFOLD_CHART_THRESHOLD = 9.0


@dataclass(frozen=True)
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def change_ratio(self) -> float:
        if self.open == 0:
            return 0.0
        return (self.close - self.open) / self.open

    @property
    def change_percent(self) -> float:
        return self.change_ratio * 100

    @property
    def amplitude_ratio(self) -> float:
        if self.open == 0:
            return 0.0
        return (self.high - self.low) / self.open

    @property
    def amplitude_percent(self) -> float:
        return self.amplitude_ratio * 100


@dataclass(frozen=True)
class InstrumentRecord:
    inst_id: str
    base_ccy: str
    quote_ccy: str
    settle_ccy: str
    state: str
    list_time_ms: int


@dataclass(frozen=True)
class TickerRecord:
    inst_id: str
    last_price: float
    today_change_percent: float
    change_percent_24h: float
    volume_24h_quote: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze OKX candle history for symbols that meet the project's "
            "tenfold condition."
        ),
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="Symbols like APE, ZBT, RAVE or full instIds like APE-USDT-SWAP.",
    )
    parser.add_argument(
        "--host",
        default="app.okx.com",
        help="OKX public API host. Default: app.okx.com",
    )
    parser.add_argument(
        "--bar",
        default="1H",
        help="Candle interval. Default: 1H",
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
        "--json-out",
        help=(
            "Optional path to save the analysis result as JSON. "
            "Default: analysis_outputs/okx_tenfold/result.json"
        ),
    )
    parser.add_argument(
        "--csv-out",
        help=(
            "Optional path to save the flattened analysis result as CSV. "
            "Default: analysis_outputs/okx_tenfold/result.csv"
        ),
    )
    parser.add_argument(
        "--chart-dir",
        help=(
            "Optional base directory to save charts. "
            "The script will create subfolders for tenfold hits and highest samples. "
            "Default: analysis_outputs/okx_tenfold/charts"
        ),
    )
    parser.add_argument(
        "--chart-window",
        type=int,
        default=8,
        help="How many candles before and after the target candle to draw. Default: 8",
    )
    parser.add_argument(
        "--okx-market",
        choices=("spot", "futures"),
        default="futures",
        help="Category market type. Default: futures",
    )
    parser.add_argument(
        "--okx-category",
        choices=(
            "hot-crypto",
            "new-crypto",
            "gainers-losers",
            "losers-gainers",
            "crypto-market-cap",
            "crypto-volume",
        ),
        help="Analyze symbols from an OKX ranking category instead of passing symbols manually.",
    )
    parser.add_argument(
        "--top-n",
        default="30",
        help=(
            "When category mode is used, analyze the top N symbols from that category. "
            "Use a number or 'all'. Default: 30"
        ),
    )
    parser.add_argument(
        "--scanned-file",
        default=DEFAULT_SCANNED_FILE,
        help=(
            "Path to the scanned-symbol list file. "
            "Already listed symbols will be skipped. "
            "Default: analysis_outputs/okx_tenfold/scanned_symbols.txt"
        ),
    )
    parser.add_argument(
        "--hit-list-file",
        default=DEFAULT_HIT_LIST_FILE,
        help=(
            "Path to save the symbols that actually hit the tenfold condition. "
            "Default: analysis_outputs/okx_tenfold/tenfold_hit_symbols.txt"
        ),
    )
    parser.add_argument(
        "--progress-file",
        default=DEFAULT_PROGRESS_FILE,
        help=(
            "Path to save per-symbol scan progress with full candle data as JSONL. "
            "Default: analysis_outputs/okx_tenfold/scan_progress.jsonl"
        ),
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help="Ignore scanned_symbols.txt and rescan all selected symbols.",
    )
    parser.add_argument(
        "--all-futures",
        action="store_true",
        help="Analyze all live OKX USDT swap symbols.",
    )
    parser.add_argument(
        "--all-spot",
        action="store_true",
        help="Analyze all live OKX USDT spot symbols.",
    )
    return parser.parse_args()


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper().replace("/", "-").replace("_", "-")
    if value.endswith("-SWAP"):
        return value
    for suffix in ("USDT", "USDC", "USD"):
        if value.endswith(f"-{suffix}"):
            return f"{value}-SWAP"
        if (
            value.endswith(suffix)
            and "-" not in value
            and len(value) - len(suffix) >= 2
        ):
            base = value[: -len(suffix)]
            return f"{base}-{suffix}-SWAP"
    if "-" in value:
        return f"{value}-SWAP"
    return f"{value}-USDT-SWAP"


def run_curl(url: str) -> dict[str, Any]:
    cmd = [
        "curl",
        "-L",
        "--max-time",
        "15",
        "-s",
        "-H",
        "User-Agent: Mozilla/5.0",
        "-H",
        "Accept: application/json",
        url,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
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
        # switch host after retries on current host
    if last_error is None:
        raise RuntimeError(f"Request failed without an explicit error: {url}")
    raise last_error


def fetch_candles(
    host: str,
    inst_id: str,
    bar: str,
    page_limit: int,
    max_pages: int,
) -> list[Candle]:
    base_url = f"https://{host}/api/v5/market/history-candles"
    seen: set[str] = set()
    rows: list[list[Any]] = []
    after: str | None = None

    for _ in range(max_pages):
        url = f"{base_url}?instId={inst_id}&bar={bar}&limit={page_limit}"
        if after is not None:
            url += f"&after={after}"

        payload = run_curl_with_fallback(url, host)
        data = payload.get("data", [])
        if not data:
            break

        before_count = len(rows)
        for row in data:
            ts = str(row[0])
            if ts not in seen:
                seen.add(ts)
                rows.append(row)
        if len(rows) == before_count:
            break
        after = str(data[-1][0])

    candles = [
        Candle(
            ts=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in rows
        if str(row[8]) == "1"
    ]
    candles.sort(key=lambda item: item.ts)
    return candles


def fetch_instruments(host: str, market: str) -> list[InstrumentRecord]:
    inst_type = "SPOT" if market == "spot" else "SWAP"
    url = f"https://{host}/api/v5/public/instruments?instType={inst_type}"
    payload = run_curl_with_fallback(url, host)
    records: list[InstrumentRecord] = []
    for row in payload.get("data", []):
        if not isinstance(row, dict):
            continue
        inst_id = str(row.get("instId", ""))
        if not inst_id:
            continue
        record = InstrumentRecord(
            inst_id=inst_id,
            base_ccy=str(row.get("baseCcy", "")),
            quote_ccy=str(row.get("quoteCcy", "")),
            settle_ccy=str(row.get("settleCcy", "")),
            state=str(row.get("state", "")),
            list_time_ms=int(str(row.get("listTime", "0") or "0")),
        )
        records.append(record)
    return records


def fetch_tickers(host: str, market: str) -> list[TickerRecord]:
    inst_type = "SPOT" if market == "spot" else "SWAP"
    url = f"https://{host}/api/v5/market/tickers?instType={inst_type}"
    payload = run_curl_with_fallback(url, host)
    records: list[TickerRecord] = []
    for row in payload.get("data", []):
        if not isinstance(row, dict):
            continue
        inst_id = str(row.get("instId", ""))
        if not inst_id:
            continue
        last = float(str(row.get("last", "0") or "0"))
        open24h = float(str(row.get("open24h", "0") or "0"))
        sod_utc8 = float(str(row.get("sodUtc8", "0") or "0"))
        volume_24h_quote = float(str(row.get("volCcy24h", "0") or "0"))
        change24h = (
            (last - open24h) / open24h * 100 if open24h else float("-inf")
        )
        today_change = (
            (last - sod_utc8) / sod_utc8 * 100 if sod_utc8 else float("-inf")
        )
        records.append(
            TickerRecord(
                inst_id=inst_id,
                last_price=last,
                today_change_percent=today_change,
                change_percent_24h=change24h,
                volume_24h_quote=volume_24h_quote,
            ),
        )
    return records


def select_symbols_from_category(
    host: str,
    market: str,
    category: str,
    top_n: int | None,
) -> list[str]:
    instruments = fetch_instruments(host, market)
    tickers = fetch_tickers(host, market)

    instrument_map = {record.inst_id: record for record in instruments}
    ticker_map = {record.inst_id: record for record in tickers}

    def is_live_usdt(record: InstrumentRecord) -> bool:
        if record.state.lower() != "live":
            return False
        if market == "futures":
            return record.settle_ccy.upper() == "USDT"
        return record.quote_ccy.upper() == "USDT"

    candidates = [record for record in instruments if is_live_usdt(record)]

    if category == "new-crypto":
        ranked = sorted(candidates, key=lambda item: item.list_time_ms, reverse=True)
        return [item.inst_id for item in ranked[:top_n]] if top_n is not None else [item.inst_id for item in ranked]

    if category == "gainers-losers":
        ranked = sorted(
            (
                item
                for item in candidates
                if item.inst_id in ticker_map
                and ticker_map[item.inst_id].today_change_percent != float("-inf")
            ),
            key=lambda item: ticker_map[item.inst_id].today_change_percent,
            reverse=True,
        )
        return [item.inst_id for item in ranked[:top_n]] if top_n is not None else [item.inst_id for item in ranked]

    if category == "losers-gainers":
        ranked = sorted(
            (
                item
                for item in candidates
                if item.inst_id in ticker_map
                and ticker_map[item.inst_id].today_change_percent != float("-inf")
            ),
            key=lambda item: ticker_map[item.inst_id].today_change_percent,
        )
        return [item.inst_id for item in ranked[:top_n]] if top_n is not None else [item.inst_id for item in ranked]

    if category == "crypto-volume":
        ranked = sorted(
            (
                item
                for item in candidates
                if item.inst_id in ticker_map
            ),
            key=lambda item: ticker_map[item.inst_id].volume_24h_quote,
            reverse=True,
        )
        return [item.inst_id for item in ranked[:top_n]] if top_n is not None else [item.inst_id for item in ranked]

    if category in {"hot-crypto", "crypto-market-cap"}:
        ranked = sorted(
            (
                item
                for item in candidates
                if item.inst_id in ticker_map
            ),
            key=lambda item: (
                ticker_map[item.inst_id].volume_24h_quote,
                ticker_map[item.inst_id].today_change_percent,
            ),
            reverse=True,
        )
        return [item.inst_id for item in ranked[:top_n]] if top_n is not None else [item.inst_id for item in ranked]

    raise RuntimeError(f"Unsupported category: {category}")


def select_all_symbols(host: str, market: str) -> list[str]:
    instruments = fetch_instruments(host, market)

    def is_live_usdt(record: InstrumentRecord) -> bool:
        if record.state.lower() != "live":
            return False
        if market == "futures":
            return record.settle_ccy.upper() == "USDT"
        return record.quote_ccy.upper() == "USDT"

    ranked = sorted(
        (record.inst_id for record in instruments if is_live_usdt(record)),
    )
    return ranked


def format_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(CN_TZ).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


def compact_ts_label(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(CN_TZ).strftime(
        "%Y%m%d_%H%M%S",
    )


def format_duration(seconds: float) -> str:
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes > 0:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def slugify_inst_id(inst_id: str) -> str:
    return inst_id.replace("/", "_")


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def load_scanned_symbols(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    scanned: set[str] = set()
    with open(path, encoding="utf-8") as file:
        for line in file:
            value = line.strip()
            if value:
                scanned.add(value)
    return scanned


def append_scanned_symbol(path: str, inst_id: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        file.write(f"{inst_id}\n")


def write_hit_list(path: str, hit_symbols: list[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        for inst_id in hit_symbols:
            file.write(f"{inst_id}\n")


def analyze_symbol(candles: list[Candle], threshold: float) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    ninefold_events: list[dict[str, Any]] = []
    highest_candidate: dict[str, Any] | None = None

    for index in range(1, len(candles)):
        previous = candles[index - 1]
        latest = candles[index]

        if previous.amplitude_ratio <= 0:
            continue
        if previous.change_percent <= 1:
            continue
        if not latest.is_bullish:
            continue

        multiple = latest.amplitude_ratio / previous.amplitude_ratio
        volume_multiple = (
            latest.volume / previous.volume if previous.volume > 0 else None
        )
        threshold_price = latest.low + (latest.open * previous.amplitude_ratio * threshold)
        hit_close_vs_threshold_percent = (
            (latest.close - threshold_price) / threshold_price * 100
            if threshold_price > 0
            else None
        )
        next_candle = candles[index + 1] if index + 1 < len(candles) else None
        next_candle_ohlc = (
            None
            if next_candle is None
            else [next_candle.open, next_candle.high, next_candle.low, next_candle.close]
        )
        next_candle_high_vs_threshold_percent = (
            None
            if next_candle is None or threshold_price <= 0
            else (next_candle.high - threshold_price) / threshold_price * 100
        )
        next_candle_high_vs_threshold_multiple = (
            None
            if next_candle is None or threshold_price <= 0
            else next_candle.high / threshold_price
        )
        next_candle_low_vs_threshold_percent = (
            None
            if next_candle is None or threshold_price <= 0
            else (next_candle.low - threshold_price) / threshold_price * 100
        )
        next_candle_low_vs_threshold_multiple = (
            None
            if next_candle is None or threshold_price <= 0
            else next_candle.low / threshold_price
        )
        next_candle_close_vs_threshold_percent = (
            None
            if next_candle is None or threshold_price <= 0
            else (next_candle.close - threshold_price) / threshold_price * 100
        )
        next_candle_close_vs_threshold_multiple = (
            None
            if next_candle is None or threshold_price <= 0
            else next_candle.close / threshold_price
        )
        candidate = {
            "time_cn": format_ts(latest.ts),
            "hit_ts": latest.ts,
            "multiple": multiple,
            "previous_amplitude_percent": previous.amplitude_percent,
            "previous_change_percent": previous.change_percent,
            "previous_volume": previous.volume,
            "hit_amplitude_percent": latest.amplitude_percent,
            "hit_change_percent": latest.change_percent,
            "hit_volume": latest.volume,
            "volume_multiple": volume_multiple,
            "threshold_price": threshold_price,
            "hit_close_vs_threshold_percent": hit_close_vs_threshold_percent,
            "hit_close_above_threshold": latest.close >= threshold_price,
            "hit_ohlc": [latest.open, latest.high, latest.low, latest.close],
            "next_candle_change_percent": (
                next_candle.change_percent if next_candle is not None else None
            ),
            "next_candle_time_cn": (
                format_ts(next_candle.ts) if next_candle is not None else None
            ),
            "next_candle_ohlc": next_candle_ohlc,
            "next_candle_high_vs_threshold_percent": next_candle_high_vs_threshold_percent,
            "next_candle_high_vs_threshold_multiple": next_candle_high_vs_threshold_multiple,
            "next_candle_low_vs_threshold_percent": next_candle_low_vs_threshold_percent,
            "next_candle_low_vs_threshold_multiple": next_candle_low_vs_threshold_multiple,
            "next_candle_close_vs_threshold_percent": next_candle_close_vs_threshold_percent,
            "next_candle_close_vs_threshold_multiple": next_candle_close_vs_threshold_multiple,
            "next_candle_low_below_threshold": (
                None
                if next_candle is None
                else next_candle.low < threshold_price
            ),
            "next_candle_close_below_threshold": (
                None
                if next_candle is None
                else next_candle.close < threshold_price
            ),
        }

        if highest_candidate is None or candidate["multiple"] > highest_candidate["multiple"]:
            highest_candidate = candidate

        if NINEFOLD_CHART_THRESHOLD <= multiple < threshold:
            ninefold_events.append(candidate)

        if multiple >= threshold:
            events.append(candidate)

    highest_hit = max(events, key=lambda item: item["multiple"], default=None)
    first_hit = min(events, key=lambda item: item["hit_ts"], default=None)

    return {
        "confirmed_candle_count": len(candles),
        "hit_count": len(events),
        "highest_candidate": highest_candidate,
        "first_hit": first_hit,
        "highest_hit": highest_hit,
        "ninefold_hit_count": len(ninefold_events),
        "ninefold_events": ninefold_events,
        "events": events,
        "candles": candles,
    }


def print_report(inst_id: str, summary: dict[str, Any], threshold: float) -> None:
    print(inst_id)
    print(f"- 确认K线数量: {summary['confirmed_candle_count']}")
    print(f"- {threshold:g}倍命中次数: {summary['hit_count']}")

    highest_candidate = summary["highest_candidate"]
    if highest_candidate is None:
        print("- 没有可用于比较的有效样本")
        print()
        return

    print(
        f"- 最高到: {highest_candidate['multiple']:.4f} 倍"
        f"（{highest_candidate['time_cn']}）",
    )

    if summary["hit_count"] == 0:
        print(f"- 结论: 历史上未达到 {threshold:g} 倍条件")
        print(
            f"- 当时十倍前振幅: {highest_candidate['previous_amplitude_percent']:.4f}%",
        )
        print(
            f"- 当时前一根涨幅: {highest_candidate['previous_change_percent']:.4f}%",
        )
        print(
            f"- 当时这一根振幅: {highest_candidate['hit_amplitude_percent']:.4f}%",
        )
        print(
            f"- 当时这一根涨跌幅: {highest_candidate['hit_change_percent']:.4f}%",
        )
        if highest_candidate["hit_close_vs_threshold_percent"] is not None:
            print(
                "- 收盘相对10倍价: "
                f"{highest_candidate['hit_close_vs_threshold_percent']:.4f}%"
                f"（{'高于' if highest_candidate['hit_close_above_threshold'] else '低于'}10倍价）",
            )
        if highest_candidate["volume_multiple"] is None:
            print("- 当时成交量倍数: 无法计算（前一根成交量为0）")
        else:
            print(
                "- 当时成交量倍数: "
                f"{highest_candidate['hit_volume']:.4f} / "
                f"{highest_candidate['previous_volume']:.4f} = "
                f"{highest_candidate['volume_multiple']:.4f}x",
            )
        if highest_candidate["next_candle_change_percent"] is not None:
            print(
                "- 该根后的第二根涨跌幅: "
                f"{highest_candidate['next_candle_change_percent']:.4f}%"
                f"（{highest_candidate['next_candle_time_cn']}）",
            )
        print()
        return

    first_hit = summary["first_hit"]
    highest_hit = summary["highest_hit"]
    print(f"- 首次达到{threshold:g}倍时间: {first_hit['time_cn']}")
    print(f"- 命中中的最高倍数: {highest_hit['multiple']:.4f} 倍")
    print("- 命中明细:")

    for index, event in enumerate(summary["events"], start=1):
        print(
            f"  {index}. 时间: {event['time_cn']} | 倍数: {event['multiple']:.4f}x",
        )
        print(f"     - 十倍前振幅: {event['previous_amplitude_percent']:.4f}%")
        print(f"     - 十倍前涨幅: {event['previous_change_percent']:.4f}%")
        print(f"     - 命中K线振幅: {event['hit_amplitude_percent']:.4f}%")
        print(f"     - 命中K线涨跌幅: {event['hit_change_percent']:.4f}%")
        if event["hit_close_vs_threshold_percent"] is not None:
            print(
                "     - 收盘相对10倍价: "
                f"{event['hit_close_vs_threshold_percent']:.4f}%"
                f"（{'高于' if event['hit_close_above_threshold'] else '低于'}10倍价）",
            )
        if event["volume_multiple"] is None:
            print("     - 成交量倍数: 无法计算（前一根成交量为0）")
        else:
            print(
                "     - 成交量倍数: "
                f"{event['hit_volume']:.4f} / "
                f"{event['previous_volume']:.4f} = "
                f"{event['volume_multiple']:.4f}x",
            )
        if event["next_candle_change_percent"] is None:
            print("     - 命中后第二根涨跌幅: 无后续确认K线")
        else:
            print(
                "     - 命中后第二根涨跌幅: "
                f"{event['next_candle_change_percent']:.4f}%"
                f"（{event['next_candle_time_cn']}）",
            )
        if event["next_candle_high_vs_threshold_percent"] is not None:
            print(
                "     - 第二根最高价相对10倍价: "
                f"{event['next_candle_high_vs_threshold_multiple']:.4f}x "
                f"({event['next_candle_high_vs_threshold_percent']:.4f}%)",
            )
    print()


def write_csv(path: str, results: dict[str, Any], threshold: float) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "inst_id",
                "status",
                "confirmed_candle_count",
                "hit_count",
                "highest_multiple",
                "highest_multiple_time_cn",
                f"first_hit_time_cn_{threshold:g}x",
                "event_index",
                "event_time_cn",
                "event_multiple",
                "previous_amplitude_percent",
                "previous_change_percent",
                "previous_volume",
                "hit_amplitude_percent",
                "hit_change_percent",
                "hit_volume",
                "volume_multiple",
                "threshold_price",
                "hit_close_vs_threshold_percent",
                "hit_close_above_threshold",
                "next_candle_change_percent",
                "next_candle_time_cn",
                "next_candle_close_vs_threshold_multiple",
                "next_candle_close_vs_threshold_percent",
                "next_candle_close_below_threshold",
                "next_candle_low_vs_threshold_multiple",
                "next_candle_low_vs_threshold_percent",
                "next_candle_low_below_threshold",
                "next_candle_high_vs_threshold_multiple",
                "next_candle_high_vs_threshold_percent",
            ],
        )

        for inst_id, summary in results.items():
            if "error" in summary:
                writer.writerow([inst_id, f"error: {summary['error']}"])
                continue

            highest = summary.get("highest_candidate")
            events = summary.get("events", [])
            common = [
                inst_id,
                "ok",
                summary["confirmed_candle_count"],
                summary["hit_count"],
                "" if highest is None else f"{highest['multiple']:.6f}",
                "" if highest is None else highest["time_cn"],
                "" if summary["first_hit"] is None else summary["first_hit"]["time_cn"],
            ]

            if not events:
                writer.writerow(common + ["", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""])
                continue

            for index, event in enumerate(events, start=1):
                writer.writerow(
                    common
                    + [
                        index,
                        event["time_cn"],
                        f"{event['multiple']:.6f}",
                        f"{event['previous_amplitude_percent']:.6f}",
                        f"{event['previous_change_percent']:.6f}",
                        f"{event['previous_volume']:.6f}",
                        f"{event['hit_amplitude_percent']:.6f}",
                        f"{event['hit_change_percent']:.6f}",
                        f"{event['hit_volume']:.6f}",
                        (
                            ""
                            if event["volume_multiple"] is None
                            else f"{event['volume_multiple']:.6f}"
                        ),
                        f"{event['threshold_price']:.6f}",
                        (
                            ""
                            if event["hit_close_vs_threshold_percent"] is None
                            else f"{event['hit_close_vs_threshold_percent']:.6f}"
                        ),
                        str(event["hit_close_above_threshold"]).lower(),
                        (
                            ""
                            if event["next_candle_change_percent"] is None
                            else f"{event['next_candle_change_percent']:.6f}"
                        ),
                        event["next_candle_time_cn"] or "",
                        (
                            ""
                            if event["next_candle_close_vs_threshold_multiple"] is None
                            else f"{event['next_candle_close_vs_threshold_multiple']:.6f}"
                        ),
                        (
                            ""
                            if event["next_candle_close_vs_threshold_percent"] is None
                            else f"{event['next_candle_close_vs_threshold_percent']:.6f}"
                        ),
                        (
                            ""
                            if event["next_candle_close_below_threshold"] is None
                            else str(event["next_candle_close_below_threshold"]).lower()
                        ),
                        (
                            ""
                            if event["next_candle_low_vs_threshold_multiple"] is None
                            else f"{event['next_candle_low_vs_threshold_multiple']:.6f}"
                        ),
                        (
                            ""
                            if event["next_candle_low_vs_threshold_percent"] is None
                            else f"{event['next_candle_low_vs_threshold_percent']:.6f}"
                        ),
                        (
                            ""
                            if event["next_candle_low_below_threshold"] is None
                            else str(event["next_candle_low_below_threshold"]).lower()
                        ),
                        (
                            ""
                            if event["next_candle_high_vs_threshold_multiple"] is None
                            else f"{event['next_candle_high_vs_threshold_multiple']:.6f}"
                        ),
                        (
                            ""
                            if event["next_candle_high_vs_threshold_percent"] is None
                            else f"{event['next_candle_high_vs_threshold_percent']:.6f}"
                        ),
                    ],
                )


def write_json(path: str, results: dict[str, Any]) -> None:
    json_ready = {}
    for inst_id, summary in results.items():
        if "error" in summary:
            json_ready[inst_id] = summary
            continue
        json_ready[inst_id] = {
            key: value
            for key, value in summary.items()
            if key != "candles"
        }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(json_ready, file, ensure_ascii=False, indent=2)


def build_progress_report_text(
    inst_id: str,
    summary: dict[str, Any],
    threshold: float,
) -> str:
    lines = [
        inst_id,
        f"- 确认K线数量: {summary['confirmed_candle_count']}",
        f"- {threshold:g}倍命中次数: {summary['hit_count']}",
    ]

    highest_candidate = summary["highest_candidate"]
    if highest_candidate is None:
        lines.append("- 没有可用于比较的有效样本")
        return "\n".join(lines)

    lines.append(
        f"- 最高到: {highest_candidate['multiple']:.4f} 倍"
        f"（{highest_candidate['time_cn']}）",
    )

    if summary["hit_count"] == 0:
        lines.append(f"- 结论: 历史上未达到 {threshold:g} 倍条件")
        lines.append(
            f"- 当时十倍前振幅: {highest_candidate['previous_amplitude_percent']:.4f}%",
        )
        lines.append(
            f"- 当时十倍前涨幅: {highest_candidate['previous_change_percent']:.4f}%",
        )
        lines.append(
            f"- 命中K线振幅: {highest_candidate['hit_amplitude_percent']:.4f}%",
        )
        lines.append(
            f"- 命中K线涨跌幅: {highest_candidate['hit_change_percent']:.4f}%",
        )
        if highest_candidate["volume_multiple"] is None:
            lines.append("- 成交量倍数: 无法计算（前一根成交量为0）")
        else:
            lines.append(
                "- 成交量倍数: "
                f"{highest_candidate['hit_volume']:.4f} / "
                f"{highest_candidate['previous_volume']:.4f} = "
                f"{highest_candidate['volume_multiple']:.4f}x",
            )
        if highest_candidate["next_candle_change_percent"] is not None:
            lines.append(
                "- 命中后第二根涨跌幅: "
                f"{highest_candidate['next_candle_change_percent']:.4f}%"
                f"（{highest_candidate['next_candle_time_cn']}）",
            )
        return "\n".join(lines)

    first_hit = summary["first_hit"]
    highest_hit = summary["highest_hit"]
    lines.append(f"- 首次达到{threshold:g}倍时间: {first_hit['time_cn']}")
    lines.append(f"- 命中中的最高倍数: {highest_hit['multiple']:.4f} 倍")
    lines.append("- 命中明细:")

    for index, event in enumerate(summary["events"], start=1):
        lines.append(
            f"  {index}. 时间: {event['time_cn']} | 倍数: {event['multiple']:.4f}x",
        )
        lines.append(f"     - 十倍前振幅: {event['previous_amplitude_percent']:.4f}%")
        lines.append(f"     - 十倍前涨幅: {event['previous_change_percent']:.4f}%")
        lines.append(f"     - 命中K线振幅: {event['hit_amplitude_percent']:.4f}%")
        lines.append(f"     - 命中K线涨跌幅: {event['hit_change_percent']:.4f}%")
        if event["volume_multiple"] is None:
            lines.append("     - 成交量倍数: 无法计算（前一根成交量为0）")
        else:
            lines.append(
                "     - 成交量倍数: "
                f"{event['hit_volume']:.4f} / "
                f"{event['previous_volume']:.4f} = "
                f"{event['volume_multiple']:.4f}x",
            )
        if event["next_candle_change_percent"] is None:
            lines.append("     - 命中后第二根涨跌幅: 无后续确认K线")
        else:
            lines.append(
                "     - 命中后第二根涨跌幅: "
                f"{event['next_candle_change_percent']:.4f}%"
                f"（{event['next_candle_time_cn']}）",
            )
        if event.get("next_candle_close_vs_threshold_percent") is not None:
            lines.append(
                "     - 第二根收盘相对10倍价: "
                f"{event['next_candle_close_vs_threshold_percent']:.4f}%"
                f"（{'低于' if event.get('next_candle_close_below_threshold') else '高于或等于'}10倍价）",
            )
        if event.get("next_candle_low_vs_threshold_percent") is not None:
            lines.append(
                "     - 第二根最低价相对10倍价: "
                f"{event['next_candle_low_vs_threshold_percent']:.4f}%"
                f"（{'低于' if event.get('next_candle_low_below_threshold') else '高于或等于'}10倍价）",
            )
    return "\n".join(lines)


def serialize_progress_summary(
    inst_id: str,
    summary: dict[str, Any],
    threshold: float,
    status: str,
    scan_index: int,
    total_targets: int,
    elapsed_seconds: float,
    generated_svg_count: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "inst_id": inst_id,
        "status": status,
        "scan_index": scan_index,
        "total_targets": total_targets,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "generated_svg_count": generated_svg_count,
    }
    if "error" in summary:
        payload["error"] = summary["error"]
        return payload

    payload.update(
        {
            "confirmed_candle_count": summary["confirmed_candle_count"],
            "hit_count": summary["hit_count"],
            "ninefold_hit_count": summary.get("ninefold_hit_count", 0),
            "highest_candidate": summary["highest_candidate"],
            "first_hit": summary["first_hit"],
            "highest_hit": summary["highest_hit"],
            "events": summary["events"],
            "report_text": build_progress_report_text(inst_id, summary, threshold),
        },
    )
    return payload


def init_progress_file(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8"):
        pass


def append_progress_record(
    path: str,
    inst_id: str,
    summary: dict[str, Any],
    threshold: float,
    status: str,
    scan_index: int,
    total_targets: int,
    elapsed_seconds: float,
    generated_svg_count: int = 0,
) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = serialize_progress_summary(
        inst_id=inst_id,
        summary=summary,
        threshold=threshold,
        status=status,
        scan_index=scan_index,
        total_targets=total_targets,
        elapsed_seconds=elapsed_seconds,
        generated_svg_count=generated_svg_count,
    )
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False))
        file.write("\n")


def render_charts(
    chart_dir: str,
    results: dict[str, Any],
    threshold: float,
    chart_window: int,
    bar_label: str,
) -> list[str]:
    ninefold_chart_dir = os.path.join(chart_dir, "ninefold_hit_svgs")
    hit_chart_dir = os.path.join(chart_dir, "tenfold_hit_svgs")
    os.makedirs(ninefold_chart_dir, exist_ok=True)
    os.makedirs(hit_chart_dir, exist_ok=True)
    saved_paths: list[str] = []

    for inst_id, summary in results.items():
        if "error" in summary:
            continue

        candles: list[Candle] = summary["candles"]
        by_ts = {candle.ts: index for index, candle in enumerate(candles)}

        render_sets: list[tuple[list[dict[str, Any]], str, str]] = []
        ninefold_targets = summary.get("ninefold_events", [])
        if ninefold_targets:
            render_sets.append((ninefold_targets, "9x_hit", ninefold_chart_dir))

        tenfold_targets = summary["events"]
        if tenfold_targets:
            render_sets.append((tenfold_targets, f"{threshold:g}x_hit", hit_chart_dir))

        if not render_sets:
            continue

        for targets, label_prefix, target_dir in render_sets:
            for index, target in enumerate(targets, start=1):
                ts = target["hit_ts"]
                if ts not in by_ts:
                    continue

                hit_index = by_ts[ts]
                start = max(0, hit_index - chart_window)
                end = min(len(candles), hit_index + chart_window + 1)
                window = candles[start:end]
                local_hit_index = hit_index - start
                prev_index = max(local_hit_index - 1, 0)

                hit_candle = candles[hit_index]
                previous_candle = candles[hit_index - 1] if hit_index > 0 else hit_candle
                prev_amp = previous_candle.amplitude_percent
                prev_chg = previous_candle.change_percent
                hit_amp = hit_candle.amplitude_percent
                ratio = target["multiple"]
                prices = [value for candle in window for value in (candle.low, candle.high)]
                min_price = min(prices)
                max_price = max(prices)
                if max_price <= min_price:
                    max_price = min_price + 1e-8

                width = 1400
                height = 760
                margin_left = 90
                margin_right = 40
                margin_top = 70
                margin_bottom = 110
                plot_width = width - margin_left - margin_right
                plot_height = height - margin_top - margin_bottom
                candle_step = plot_width / max(len(window), 1)
                candle_width = max(candle_step * 0.58, 6)

                def y_of(price: float) -> float:
                    ratio_y = (price - min_price) / (max_price - min_price)
                    return margin_top + plot_height - ratio_y * plot_height

                def x_of(candle_index: int) -> float:
                    return margin_left + candle_step * candle_index + candle_step / 2

                title = "9倍命中"
                if label_prefix == "9x_hit":
                    title = "9倍命中"
                else:
                    title = "10倍命中"

                svg_lines = [
                    f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                    '<rect width="100%" height="100%" fill="#fcfcfc"/>',
                    f'<text x="{margin_left}" y="36" font-size="26" font-weight="700" fill="#111111">{inst_id} {bar_label} - {title}</text>',
                ]

                # grid
                for grid_index in range(6):
                    gy = margin_top + plot_height * grid_index / 5
                    price = max_price - (max_price - min_price) * grid_index / 5
                    svg_lines.append(
                        f'<line x1="{margin_left}" y1="{gy:.2f}" x2="{width - margin_right}" y2="{gy:.2f}" stroke="#d9d9d9" stroke-dasharray="4 4" stroke-width="1"/>',
                    )
                    svg_lines.append(
                        f'<text x="12" y="{gy + 4:.2f}" font-size="12" fill="#555555">{price:.6f}</text>',
                    )

                # highlight zones
                hit_center = x_of(local_hit_index)
                svg_lines.append(
                    f'<rect x="{hit_center - candle_step / 2:.2f}" y="{margin_top}" width="{candle_step:.2f}" height="{plot_height:.2f}" fill="#f1c40f" opacity="0.16"/>',
                )
                if local_hit_index > 0:
                    prev_center = x_of(prev_index)
                    svg_lines.append(
                        f'<rect x="{prev_center - candle_step / 2:.2f}" y="{margin_top}" width="{candle_step:.2f}" height="{plot_height:.2f}" fill="#3498db" opacity="0.10"/>',
                    )

                # candles
                for candle_index, candle in enumerate(window):
                    color = "#2ecc71" if candle.close >= candle.open else "#e74c3c"
                    x = x_of(candle_index)
                    y_high = y_of(candle.high)
                    y_low = y_of(candle.low)
                    y_open = y_of(candle.open)
                    y_close = y_of(candle.close)
                    body_top = min(y_open, y_close)
                    body_height = max(abs(y_close - y_open), 1.2)
                    svg_lines.append(
                        f'<line x1="{x:.2f}" y1="{y_high:.2f}" x2="{x:.2f}" y2="{y_low:.2f}" stroke="{color}" stroke-width="2"/>',
                    )
                    svg_lines.append(
                        f'<rect x="{x - candle_width / 2:.2f}" y="{body_top:.2f}" width="{candle_width:.2f}" height="{body_height:.2f}" fill="{color}" stroke="{color}" stroke-width="1"/>',
                    )

                    label = format_ts(candle.ts)[5:16]
                    svg_lines.append(
                        f'<text x="{x:.2f}" y="{height - 56}" font-size="11" fill="#555555" text-anchor="middle">{label[:5]}</text>',
                    )
                    svg_lines.append(
                        f'<text x="{x:.2f}" y="{height - 40}" font-size="11" fill="#555555" text-anchor="middle">{label[6:]}</text>',
                    )

                # annotations
                hit_text_x = min(hit_center + 80, width - 320)
                hit_text_y = max(y_of(hit_candle.high) - 70, margin_top + 20)
                svg_lines.append(
                    f'<line x1="{hit_center:.2f}" y1="{y_of(hit_candle.high):.2f}" x2="{hit_text_x:.2f}" y2="{hit_text_y:.2f}" stroke="#f1c40f" stroke-width="2"/>',
                )
                svg_lines.append(
                    f'<rect x="{hit_text_x:.2f}" y="{hit_text_y - 48:.2f}" width="220" height="72" rx="8" fill="#fff8dc" stroke="#f1c40f"/>',
                )
                svg_lines.append(
                    f'<text x="{hit_text_x + 10:.2f}" y="{hit_text_y - 24:.2f}" font-size="12" fill="#111111">目标K线 {format_ts(hit_candle.ts)[:-3]}</text>',
                )
                svg_lines.append(
                    f'<text x="{hit_text_x + 10:.2f}" y="{hit_text_y - 6:.2f}" font-size="12" fill="#111111">振幅 {hit_amp:.2f}%</text>',
                )
                svg_lines.append(
                    f'<text x="{hit_text_x + 10:.2f}" y="{hit_text_y + 12:.2f}" font-size="12" fill="#111111">倍数 {ratio:.2f}x</text>',
                )

                if local_hit_index > 0:
                    prev_center = x_of(prev_index)
                    prev_text_x = max(prev_center - 250, margin_left + 10)
                    prev_text_y = max(y_of(previous_candle.high) - 40, margin_top + 30)
                    svg_lines.append(
                        f'<line x1="{prev_center:.2f}" y1="{y_of(previous_candle.high):.2f}" x2="{prev_text_x + 220:.2f}" y2="{prev_text_y:.2f}" stroke="#3498db" stroke-width="2"/>',
                    )
                    svg_lines.append(
                        f'<rect x="{prev_text_x:.2f}" y="{prev_text_y - 36:.2f}" width="220" height="58" rx="8" fill="#eef6ff" stroke="#3498db"/>',
                    )
                    svg_lines.append(
                        f'<text x="{prev_text_x + 10:.2f}" y="{prev_text_y - 14:.2f}" font-size="12" fill="#111111">前一根涨幅 {prev_chg:.2f}%</text>',
                    )
                    svg_lines.append(
                        f'<text x="{prev_text_x + 10:.2f}" y="{prev_text_y + 4:.2f}" font-size="12" fill="#111111">前一根振幅 {prev_amp:.2f}%</text>',
                    )

                svg_lines.append("</svg>")

                time_label = compact_ts_label(hit_candle.ts)
                multiple_label = f"{target['multiple']:.2f}x".replace(".", "_")
                if label_prefix == "9x_hit":
                    filename = (
                        f"{slugify_inst_id(inst_id)}__NINEFOLD_HIT__"
                        f"{time_label}__{multiple_label}__event_{index}.svg"
                    )
                else:
                    filename = (
                        f"{slugify_inst_id(inst_id)}__TENFOLD_HIT__"
                        f"{time_label}__{multiple_label}__event_{index}.svg"
                    )
                output_path = os.path.join(target_dir, filename)
                with open(output_path, "w", encoding="utf-8") as file:
                    file.write("\n".join(svg_lines))
                saved_paths.append(output_path)

    return saved_paths


def clear_chart_svgs(chart_dir: str, subdirs: tuple[str, ...]) -> None:
    for subdir in subdirs:
        target_dir = os.path.join(chart_dir, subdir)
        if not os.path.isdir(target_dir):
            continue
        for filename in os.listdir(target_dir):
            if filename.endswith(".svg"):
                os.remove(os.path.join(target_dir, filename))


def backup_chart_svgs(chart_dir: str, subdirs: tuple[str, ...]) -> str | None:
    backup_root: str | None = None
    timestamp = datetime.now(tz=CN_TZ).strftime("%Y%m%d_%H%M%S")

    for subdir in subdirs:
        target_dir = os.path.join(chart_dir, subdir)
        if not os.path.isdir(target_dir):
            continue

        svg_names = [
            filename
            for filename in os.listdir(target_dir)
            if filename.endswith(".svg")
        ]
        if not svg_names:
            continue

        if backup_root is None:
            backup_root = os.path.join(chart_dir, "_backup_runs", timestamp)

        backup_subdir = os.path.join(backup_root, subdir)
        os.makedirs(backup_subdir, exist_ok=True)
        for filename in svg_names:
            shutil.move(
                os.path.join(target_dir, filename),
                os.path.join(backup_subdir, filename),
            )

    return backup_root


def clear_existing_symbol_charts(chart_dir: str, inst_id: str) -> None:
    symbol_prefix = f"{slugify_inst_id(inst_id)}__"
    for subdir in ("ninefold_hit_svgs", "tenfold_hit_svgs", "highest_sample_svgs"):
        target_dir = os.path.join(chart_dir, subdir)
        if not os.path.isdir(target_dir):
            continue
        for filename in os.listdir(target_dir):
            if filename.startswith(symbol_prefix) and filename.endswith(".svg"):
                os.remove(os.path.join(target_dir, filename))


def render_symbol_charts(
    chart_dir: str,
    inst_id: str,
    summary: dict[str, Any],
    threshold: float,
    chart_window: int,
    bar_label: str,
) -> list[str]:
    clear_existing_symbol_charts(chart_dir, inst_id)
    return render_charts(
        chart_dir=chart_dir,
        results={inst_id: summary},
        threshold=threshold,
        chart_window=chart_window,
        bar_label=bar_label,
    )


def render_summary_charts(
    chart_dir: str,
    results: dict[str, Any],
    threshold: float,
) -> list[str]:
    summary_dir = os.path.join(chart_dir, "summary_svgs")
    os.makedirs(summary_dir, exist_ok=True)
    saved_paths: list[str] = []

    error_count = 0
    hit_count = 0
    no_hit_count = 0
    hit_event_count = 0
    top_candidates: list[tuple[str, float, int, float | None]] = []
    tenfold_events: list[dict[str, Any]] = []
    bucket_labels = ["<2x", "2-4x", "4-6x", "6-8x", "8-10x", ">=10x"]
    bucket_counts = [0 for _ in bucket_labels]

    for inst_id, summary in results.items():
        if "error" in summary:
            error_count += 1
            continue

        highest = summary.get("highest_candidate")
        if highest is not None:
            multiple = float(highest["multiple"])
            top_candidates.append(
                (
                    inst_id,
                    multiple,
                    int(summary["hit_count"]),
                    (
                        None
                        if highest.get("volume_multiple") is None
                        else float(highest["volume_multiple"])
                    ),
                ),
            )
            if multiple < 2:
                bucket_counts[0] += 1
            elif multiple < 4:
                bucket_counts[1] += 1
            elif multiple < 6:
                bucket_counts[2] += 1
            elif multiple < 8:
                bucket_counts[3] += 1
            elif multiple < threshold:
                bucket_counts[4] += 1
            else:
                bucket_counts[5] += 1

        if summary["hit_count"] > 0:
            hit_count += 1
            hit_event_count += int(summary["hit_count"])
            for event in summary.get("events", []):
                threshold_price = event.get("threshold_price")
                if threshold_price is None:
                    hit_ohlc = event.get("hit_ohlc")
                    prev_amp_percent = event.get("previous_amplitude_percent")
                    if hit_ohlc and prev_amp_percent is not None:
                        threshold_price = hit_ohlc[2] + (
                            hit_ohlc[0] * (prev_amp_percent / 100.0) * threshold
                        )
                close_vs_threshold = event.get("hit_close_vs_threshold_percent")
                if close_vs_threshold is None and threshold_price:
                    close_vs_threshold = (
                        (event["hit_ohlc"][3] - threshold_price) / threshold_price * 100
                    )
                close_above_threshold = event.get("hit_close_above_threshold")
                if close_above_threshold is None and close_vs_threshold is not None:
                    close_above_threshold = close_vs_threshold >= 0
                tenfold_events.append(
                    {
                        "inst_id": inst_id,
                        "time_cn": event.get("time_cn"),
                        "multiple": float(event.get("multiple", 0.0)),
                        "volume_multiple": (
                            None
                            if event.get("volume_multiple") is None
                            else float(event["volume_multiple"])
                        ),
                        "threshold_price": threshold_price,
                        "close_vs_threshold_percent": close_vs_threshold,
                        "close_above_threshold": close_above_threshold,
                        "next_candle_high_vs_threshold_percent": event.get(
                            "next_candle_high_vs_threshold_percent",
                        ),
                        "next_candle_high_vs_threshold_multiple": event.get(
                            "next_candle_high_vs_threshold_multiple",
                        ),
                    },
                )
        else:
            no_hit_count += 1

    total_count = error_count + hit_count + no_hit_count
    close_above_count = sum(
        1 for event in tenfold_events if event.get("close_above_threshold") is True
    )
    close_below_count = sum(
        1 for event in tenfold_events if event.get("close_above_threshold") is False
    )

    def render_message_svg(output_path: str, title: str, message: str) -> None:
        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="420" viewBox="0 0 1200 420">',
            '<rect width="100%" height="100%" fill="#fcfcfc"/>',
            f'<text x="48" y="58" font-size="30" font-weight="700" fill="#111111">{xml_escape(title)}</text>',
            f'<text x="48" y="130" font-size="24" fill="#666666">{xml_escape(message)}</text>',
            "</svg>",
        ]
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(lines))

    def render_pie_svg(output_path: str) -> None:
        slices = [
            ("命中", hit_count, "#e74c3c"),
            ("未命中", no_hit_count, "#3498db"),
            ("失败", error_count, "#95a5a6"),
        ]
        width = 1200
        height = 760
        cx = 320
        cy = 360
        radius = 210
        inner_radius = 98

        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fcfcfc"/>',
            f'<text x="48" y="58" font-size="30" font-weight="700" fill="#111111">{threshold:g}倍扫描结果占比</text>',
            f'<text x="48" y="96" font-size="18" fill="#555555">总币数: {total_count} | 命中币种: {hit_count} | 命中次数: {hit_event_count}</text>',
        ]

        non_zero = [(label, value, color) for label, value, color in slices if value > 0]
        if not non_zero:
            non_zero = [("无数据", 1, "#dcdcdc")]

        if len(non_zero) == 1:
            _, _, color = non_zero[0]
            svg_lines.append(
                f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}" opacity="0.92"/>',
            )
        else:
            start_angle = -90.0
            for _, value, color in non_zero:
                sweep = 360.0 * value / max(total_count, 1)
                end_angle = start_angle + sweep

                x1 = cx + radius * math.cos(math.radians(start_angle))
                y1 = cy + radius * math.sin(math.radians(start_angle))
                x2 = cx + radius * math.cos(math.radians(end_angle))
                y2 = cy + radius * math.sin(math.radians(end_angle))
                large_arc = 1 if sweep > 180 else 0
                svg_lines.append(
                    "<path "
                    f'd="M {cx:.2f} {cy:.2f} L {x1:.2f} {y1:.2f} '
                    f'A {radius} {radius} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" '
                    f'fill="{color}" opacity="0.92"/>',
                )
                start_angle = end_angle

        svg_lines.append(
            f'<circle cx="{cx}" cy="{cy}" r="{inner_radius}" fill="#fcfcfc"/>',
        )
        svg_lines.append(
            f'<text x="{cx}" y="{cy - 6}" font-size="36" font-weight="700" fill="#111111" text-anchor="middle">{total_count}</text>',
        )
        svg_lines.append(
            f'<text x="{cx}" y="{cy + 28}" font-size="18" fill="#666666" text-anchor="middle">扫描总数</text>',
        )

        legend_x = 690
        legend_y = 220
        legend_gap = 92
        for index, (label, value, color) in enumerate(slices):
            percent = (value / total_count * 100) if total_count else 0.0
            y = legend_y + index * legend_gap
            svg_lines.append(
                f'<rect x="{legend_x}" y="{y - 18}" width="28" height="28" rx="6" fill="{color}"/>',
            )
            svg_lines.append(
                f'<text x="{legend_x + 44}" y="{y}" font-size="24" font-weight="600" fill="#111111">{label}</text>',
            )
            svg_lines.append(
                f'<text x="{legend_x + 44}" y="{y + 30}" font-size="18" fill="#555555">{value} 个币，占比 {percent:.2f}%</text>',
            )

        svg_lines.append("</svg>")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(svg_lines))

    def render_top_multiple_bar_svg(output_path: str) -> None:
        ranked = sorted(top_candidates, key=lambda item: item[1], reverse=True)[:10]
        if not ranked:
            render_message_svg(output_path, "最高倍数 Top 10", "当前没有可展示的数据。")
            return
        width = 1400
        height = max(520, 150 + len(ranked) * 58)
        left = 260
        right = 120
        top = 120
        row_gap = 52
        bar_height = 30
        plot_width = width - left - right
        max_multiple = max((item[1] for item in ranked), default=1.0)

        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fcfcfc"/>',
            '<text x="48" y="58" font-size="30" font-weight="700" fill="#111111">最高倍数 Top 10</text>',
            f'<text x="48" y="96" font-size="18" fill="#555555">按每个币历史最高候选倍数排序；同时显示该最高样本对应的成交量倍数</text>',
        ]

        for index, (inst_id, multiple, event_count, volume_multiple) in enumerate(ranked):
            y = top + index * row_gap
            bar_width = plot_width * (multiple / max_multiple if max_multiple else 0.0)
            fill = "#e74c3c" if multiple >= threshold else "#f39c12"
            volume_label = (
                "成交量 无法计算"
                if volume_multiple is None
                else f"成交量 {volume_multiple:.2f}x"
            )
            svg_lines.append(
                f'<text x="{left - 20}" y="{y + 22}" font-size="19" fill="#111111" text-anchor="end">{xml_escape(inst_id)}</text>',
            )
            svg_lines.append(
                f'<rect x="{left}" y="{y}" width="{plot_width:.2f}" height="{bar_height}" rx="8" fill="#efefef"/>',
            )
            svg_lines.append(
                f'<rect x="{left}" y="{y}" width="{bar_width:.2f}" height="{bar_height}" rx="8" fill="{fill}"/>',
            )
            svg_lines.append(
                f'<text x="{left + bar_width + 12:.2f}" y="{y + 22}" font-size="18" fill="#333333">{multiple:.2f}x | 命中 {event_count} 次 | {volume_label}</text>',
            )

        svg_lines.append("</svg>")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(svg_lines))

    def render_distribution_bar_svg(output_path: str) -> None:
        width = 1200
        height = 760
        left = 120
        right = 70
        top = 110
        bottom = 110
        plot_width = width - left - right
        plot_height = height - top - bottom
        max_count = max(bucket_counts, default=1)
        bar_width = plot_width / max(len(bucket_labels), 1) * 0.62
        step = plot_width / max(len(bucket_labels), 1)
        colors = ["#95a5a6", "#5dade2", "#48c9b0", "#f5b041", "#eb984e", "#e74c3c"]

        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fcfcfc"/>',
            '<text x="48" y="58" font-size="30" font-weight="700" fill="#111111">最高倍数分布</text>',
            f'<text x="48" y="96" font-size="18" fill="#555555">统计每个币历史最高候选倍数落在哪个区间</text>',
        ]

        for grid_index in range(6):
            y = top + plot_height * grid_index / 5
            value = round(max_count - max_count * grid_index / 5)
            svg_lines.append(
                f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" stroke="#dddddd" stroke-dasharray="4 4"/>',
            )
            svg_lines.append(
                f'<text x="{left - 18}" y="{y + 6:.2f}" font-size="14" fill="#666666" text-anchor="end">{value}</text>',
            )

        for index, label in enumerate(bucket_labels):
            count = bucket_counts[index]
            bar_height = plot_height * (count / max_count if max_count else 0.0)
            x = left + index * step + (step - bar_width) / 2
            y = top + plot_height - bar_height
            svg_lines.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" rx="10" fill="{colors[index]}"/>',
            )
            svg_lines.append(
                f'<text x="{x + bar_width / 2:.2f}" y="{y - 12:.2f}" font-size="18" fill="#222222" text-anchor="middle">{count}</text>',
            )
            svg_lines.append(
                f'<text x="{x + bar_width / 2:.2f}" y="{height - 44}" font-size="18" fill="#555555" text-anchor="middle">{label}</text>',
            )

        svg_lines.append("</svg>")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(svg_lines))

    def render_close_vs_threshold_pie(output_path: str) -> None:
        slices = [
            ("收盘高于10倍价", close_above_count, "#2ecc71"),
            ("收盘低于10倍价", close_below_count, "#e74c3c"),
        ]
        total_hits = close_above_count + close_below_count
        width = 1200
        height = 760
        cx = 320
        cy = 360
        radius = 210
        inner_radius = 98
        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fcfcfc"/>',
            f'<text x="48" y="58" font-size="30" font-weight="700" fill="#111111">{threshold:g}倍命中收盘价位置统计</text>',
            f'<text x="48" y="96" font-size="18" fill="#555555">统计所有{threshold:g}倍命中事件的收盘价是在10倍价之上还是之下</text>',
        ]
        non_zero = [(label, value, color) for label, value, color in slices if value > 0]
        if not non_zero:
            non_zero = [("无数据", 1, "#dcdcdc")]
        if len(non_zero) == 1:
            _, _, color = non_zero[0]
            svg_lines.append(
                f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}" opacity="0.92"/>',
            )
        else:
            start_angle = -90.0
            for _, value, color in non_zero:
                sweep = 360.0 * value / max(total_hits, 1)
                end_angle = start_angle + sweep
                x1 = cx + radius * math.cos(math.radians(start_angle))
                y1 = cy + radius * math.sin(math.radians(start_angle))
                x2 = cx + radius * math.cos(math.radians(end_angle))
                y2 = cy + radius * math.sin(math.radians(end_angle))
                large_arc = 1 if sweep > 180 else 0
                svg_lines.append(
                    "<path "
                    f'd="M {cx:.2f} {cy:.2f} L {x1:.2f} {y1:.2f} '
                    f'A {radius} {radius} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" '
                    f'fill="{color}" opacity="0.92"/>',
                )
                start_angle = end_angle
        svg_lines.append(
            f'<circle cx="{cx}" cy="{cy}" r="{inner_radius}" fill="#fcfcfc"/>',
        )
        svg_lines.append(
            f'<text x="{cx}" y="{cy - 6}" font-size="36" font-weight="700" fill="#111111" text-anchor="middle">{total_hits}</text>',
        )
        svg_lines.append(
            f'<text x="{cx}" y="{cy + 28}" font-size="18" fill="#666666" text-anchor="middle">10倍命中总数</text>',
        )
        legend_x = 690
        legend_y = 240
        legend_gap = 110
        for index, (label, value, color) in enumerate(slices):
            percent = (value / total_hits * 100) if total_hits else 0.0
            y = legend_y + index * legend_gap
            svg_lines.append(
                f'<rect x="{legend_x}" y="{y - 18}" width="28" height="28" rx="6" fill="{color}"/>',
            )
            svg_lines.append(
                f'<text x="{legend_x + 44}" y="{y}" font-size="24" font-weight="600" fill="#111111">{label}</text>',
            )
            svg_lines.append(
                f'<text x="{legend_x + 44}" y="{y + 30}" font-size="18" fill="#555555">{value} 次，占比 {percent:.2f}%</text>',
            )
        svg_lines.append("</svg>")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(svg_lines))

    def render_event_volume_top10(output_path: str) -> None:
        ranked = [
            event for event in tenfold_events if event.get("volume_multiple") is not None
        ]
        ranked.sort(key=lambda item: float(item["volume_multiple"]), reverse=True)
        ranked = ranked[:10]
        if not ranked:
            render_message_svg(output_path, "成交量倍数 Top 10", "当前没有可展示的成交量倍数数据。")
            return
        width = 1500
        height = max(520, 160 + len(ranked) * 58)
        left = 330
        right = 120
        top = 130
        row_gap = 52
        bar_height = 30
        plot_width = width - left - right
        max_value = max(float(item["volume_multiple"]) for item in ranked)
        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fcfcfc"/>',
            '<text x="48" y="58" font-size="30" font-weight="700" fill="#111111">成交量倍数 Top 10</text>',
            f'<text x="48" y="96" font-size="18" fill="#555555">按{threshold:g}倍命中事件的成交量倍数排序</text>',
        ]
        for index, item in enumerate(ranked):
            y = top + index * row_gap
            value = float(item["volume_multiple"])
            bar_width = plot_width * (value / max_value if max_value else 0.0)
            label = f"{item['inst_id']} {item['time_cn']}"
            svg_lines.append(
                f'<text x="{left - 18}" y="{y + 22}" font-size="18" fill="#111111" text-anchor="end">{xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<rect x="{left}" y="{y}" width="{plot_width:.2f}" height="{bar_height}" rx="8" fill="#efefef"/>',
            )
            svg_lines.append(
                f'<rect x="{left}" y="{y}" width="{bar_width:.2f}" height="{bar_height}" rx="8" fill="#9b59b6"/>',
            )
            svg_lines.append(
                f'<text x="{left + bar_width + 12:.2f}" y="{y + 22}" font-size="17" fill="#333333">{value:.2f}x | 倍数 {item["multiple"]:.2f}x</text>',
            )
        svg_lines.append("</svg>")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(svg_lines))

    def render_event_volume_compare(output_path: str) -> None:
        ranked = [
            event for event in tenfold_events if event.get("volume_multiple") is not None
        ]
        ranked.sort(key=lambda item: float(item["volume_multiple"]), reverse=True)
        if not ranked:
            render_message_svg(output_path, "10倍命中成交量倍数对比", "当前没有可展示的成交量倍数数据。")
            return
        width = 1600
        height = max(560, 160 + len(ranked) * 34)
        left = 380
        right = 120
        top = 130
        row_gap = 30
        bar_height = 18
        plot_width = width - left - right
        max_value = max(float(item["volume_multiple"]) for item in ranked)
        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fcfcfc"/>',
            '<text x="48" y="58" font-size="30" font-weight="700" fill="#111111">10倍命中成交量倍数对比</text>',
            f'<text x="48" y="96" font-size="18" fill="#555555">展示所有{threshold:g}倍命中事件的成交量倍数，从高到低排序</text>',
        ]
        for index, item in enumerate(ranked):
            y = top + index * row_gap
            value = float(item["volume_multiple"])
            bar_width = plot_width * (value / max_value if max_value else 0.0)
            label = f"{item['inst_id']} {item['time_cn']}"
            svg_lines.append(
                f'<text x="{left - 18}" y="{y + 15}" font-size="14" fill="#111111" text-anchor="end">{xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<rect x="{left}" y="{y}" width="{plot_width:.2f}" height="{bar_height}" rx="6" fill="#efefef"/>',
            )
            svg_lines.append(
                f'<rect x="{left}" y="{y}" width="{bar_width:.2f}" height="{bar_height}" rx="6" fill="#8e44ad"/>',
            )
            svg_lines.append(
                f'<text x="{left + bar_width + 10:.2f}" y="{y + 15}" font-size="13" fill="#333333">{value:.2f}x</text>',
            )
        svg_lines.append("</svg>")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(svg_lines))

    def render_next_high_top10(output_path: str) -> None:
        ranked = [
            event
            for event in tenfold_events
            if event.get("next_candle_high_vs_threshold_percent") is not None
        ]
        ranked.sort(
            key=lambda item: float(item["next_candle_high_vs_threshold_percent"]),
            reverse=True,
        )
        ranked = ranked[:10]
        if not ranked:
            render_message_svg(
                output_path,
                "第二根最高价相对10倍价 Top 10",
                "当前没有第二根最高价相对10倍价的数据，需重新扫描后生成。",
            )
            return
        width = 1500
        height = max(520, 160 + len(ranked) * 58)
        left = 340
        right = 120
        top = 130
        row_gap = 52
        bar_height = 30
        plot_width = width - left - right
        max_abs = max(
            abs(float(item["next_candle_high_vs_threshold_percent"])) for item in ranked
        ) or 1.0
        zero_x = left + plot_width / 2
        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fcfcfc"/>',
            '<text x="48" y="58" font-size="30" font-weight="700" fill="#111111">第二根最高价相对10倍价 Top 10</text>',
            f'<text x="48" y="96" font-size="18" fill="#555555">统计{threshold:g}倍命中后第二根K线最高价，相对10倍价是上涨还是回落</text>',
            f'<line x1="{zero_x:.2f}" y1="{top - 20}" x2="{zero_x:.2f}" y2="{height - 30}" stroke="#999999" stroke-dasharray="4 4"/>',
        ]
        for index, item in enumerate(ranked):
            y = top + index * row_gap
            value = float(item["next_candle_high_vs_threshold_percent"])
            label = f"{item['inst_id']} {item['time_cn']}"
            bar_width = (abs(value) / max_abs) * (plot_width / 2 - 30)
            if value >= 0:
                x = zero_x
                fill = "#27ae60"
                text_x = x + bar_width + 10
            else:
                x = zero_x - bar_width
                fill = "#e74c3c"
                text_x = zero_x + 10
            svg_lines.append(
                f'<text x="{left - 20}" y="{y + 22}" font-size="17" fill="#111111" text-anchor="end">{xml_escape(label)}</text>',
            )
            svg_lines.append(
                f'<rect x="{left}" y="{y}" width="{plot_width:.2f}" height="{bar_height}" rx="8" fill="#efefef"/>',
            )
            svg_lines.append(
                f'<rect x="{x:.2f}" y="{y}" width="{bar_width:.2f}" height="{bar_height}" rx="8" fill="{fill}"/>',
            )
            multiple_label = item.get("next_candle_high_vs_threshold_multiple")
            if multiple_label is None:
                info = f"{value:.2f}%"
            else:
                info = f"{float(multiple_label):.3f}x ({value:.2f}%)"
            svg_lines.append(
                f'<text x="{text_x:.2f}" y="{y + 22}" font-size="16" fill="#333333">{info}</text>',
            )
        svg_lines.append("</svg>")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(svg_lines))

    pie_path = os.path.join(summary_dir, "scan_result_pie.svg")
    top_bar_path = os.path.join(summary_dir, "highest_multiple_top10.svg")
    distribution_path = os.path.join(summary_dir, "highest_multiple_distribution.svg")
    close_position_path = os.path.join(summary_dir, "tenfold_close_vs_threshold_pie.svg")
    next_high_path = os.path.join(summary_dir, "next_candle_high_vs_threshold_top10.svg")
    volume_compare_path = os.path.join(summary_dir, "tenfold_event_volume_compare.svg")
    volume_top10_path = os.path.join(summary_dir, "volume_multiple_top10.svg")

    render_pie_svg(pie_path)
    saved_paths.append(pie_path)

    if top_candidates:
        render_top_multiple_bar_svg(top_bar_path)
        saved_paths.append(top_bar_path)
        render_distribution_bar_svg(distribution_path)
        saved_paths.append(distribution_path)

    render_close_vs_threshold_pie(close_position_path)
    saved_paths.append(close_position_path)
    render_next_high_top10(next_high_path)
    saved_paths.append(next_high_path)
    render_event_volume_compare(volume_compare_path)
    saved_paths.append(volume_compare_path)
    render_event_volume_top10(volume_top10_path)
    saved_paths.append(volume_top10_path)

    return saved_paths


def finalize_outputs(
    results: dict[str, Any],
    threshold: float,
    output_json: str | None,
    output_csv: str | None,
    output_chart_dir: str | None,
    chart_window: int,
    hit_list_file: str,
    hit_symbols: list[str],
    generate_charts: bool,
    bar_label: str,
) -> tuple[list[str], list[str]]:
    if output_json:
        write_json(output_json, results)

    if output_csv:
        write_csv(output_csv, results, threshold)

    chart_paths: list[str] = []
    summary_chart_paths: list[str] = []
    if output_chart_dir and generate_charts:
        clear_chart_svgs(output_chart_dir, ("summary_svgs",))
        summary_chart_paths = render_summary_charts(
            chart_dir=output_chart_dir,
            results=results,
            threshold=threshold,
        )

    write_hit_list(hit_list_file, sorted(set(hit_symbols)))
    return chart_paths, summary_chart_paths


def install_signal_handlers() -> None:
    def handle_termination(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_termination)
    signal.signal(signal.SIGINT, handle_termination)


def main() -> int:
    start_time = time.time()
    install_signal_handlers()
    args = parse_args()
    output_json = args.json_out or os.path.join(DEFAULT_OUTPUT_DIR, "result.json")
    output_csv = args.csv_out or os.path.join(DEFAULT_OUTPUT_DIR, "result.csv")
    output_chart_dir = args.chart_dir or os.path.join(DEFAULT_OUTPUT_DIR, "charts")
    progress_file = args.progress_file
    scanned_file = args.scanned_file
    hit_list_file = args.hit_list_file
    results: dict[str, Any] = {}
    symbols = list(args.symbols)
    top_n_value: int | None

    if str(args.top_n).lower() == "all":
        top_n_value = None
    else:
        try:
            top_n_value = int(str(args.top_n))
        except ValueError as exc:
            raise SystemExit("--top-n must be a positive integer or 'all'") from exc
        if top_n_value <= 0:
            raise SystemExit("--top-n must be greater than 0")

    if args.okx_category:
        symbols = select_symbols_from_category(
            host=args.host,
            market=args.okx_market,
            category=args.okx_category,
            top_n=top_n_value,
        )
        print(
            f"使用 OKX 类别 {args.okx_market}/{args.okx_category}，共选中 {len(symbols)} 个币进行分析。",
        )
        if args.okx_category in {"hot-crypto", "crypto-market-cap"}:
            print(
                "注意: 由于 OKX 公开接口未直接提供这两个分类的精确成分，"
                "脚本当前使用 24h 成交额 + 今日涨幅 的代理排序近似处理。",
            )
    elif args.all_futures:
        symbols = select_all_symbols(args.host, "futures")
        print(f"使用 OKX 全量合约模式，共选中 {len(symbols)} 个币进行分析。")
    elif args.all_spot:
        symbols = select_all_symbols(args.host, "spot")
        print(f"使用 OKX 全量现货模式，共选中 {len(symbols)} 个币进行分析。")

    if not symbols:
        raise SystemExit("Please pass symbols, or use --okx-category with --top-n.")

    pre_scanned = set() if args.rescan else load_scanned_symbols(scanned_file)
    normalized_scan_targets = []
    for raw_symbol in symbols:
        inst_id = raw_symbol if args.okx_category else normalize_symbol(raw_symbol)
        normalized_scan_targets.append(inst_id)

    pending_symbols = [inst_id for inst_id in normalized_scan_targets if inst_id not in pre_scanned]
    skipped_symbols = [inst_id for inst_id in normalized_scan_targets if inst_id in pre_scanned]

    if skipped_symbols:
        print(f"已在扫描记录中，跳过 {len(skipped_symbols)} 个币。")
    if args.rescan:
        print("已启用 --rescan，本次忽略已扫描记录。")
    print(f"本次总目标币数: {len(normalized_scan_targets)}")
    print(f"本次待扫描: {len(pending_symbols)} 个币")

    if not pending_symbols:
        print("没有新的币需要扫描。")
        return 0

    init_progress_file(progress_file)
    print(f"扫描进度文件: {progress_file}")
    if output_chart_dir:
        backup_dir = backup_chart_svgs(
            output_chart_dir,
            (
                "ninefold_hit_svgs",
                "tenfold_hit_svgs",
                "highest_sample_svgs",
                "summary_svgs",
            ),
        )
        if backup_dir is not None:
            print(f"已备份旧SVG到: {backup_dir}")

    cumulative_hit_symbols = 0
    cumulative_hit_events = 0
    hit_symbols: list[str] = []
    interrupted = False
    unexpected_error: Exception | None = None

    try:
        for index, inst_id in enumerate(pending_symbols, start=1):
            symbol_start_time = time.time()
            print(
                f"========== 开始扫描 [{index}/{len(pending_symbols)}] {inst_id} ==========",
            )
            try:
                candles = fetch_candles(
                    host=args.host,
                    inst_id=inst_id,
                    bar=args.bar,
                    page_limit=args.page_limit,
                    max_pages=args.max_pages,
                )
                summary = analyze_symbol(candles, args.threshold)
            except Exception as exc:  # noqa: BLE001
                results[inst_id] = {"error": str(exc)}
                append_progress_record(
                    progress_file,
                    inst_id,
                    results[inst_id],
                    args.threshold,
                    "error",
                    index,
                    len(normalized_scan_targets),
                    time.time() - symbol_start_time,
                )
                print(inst_id)
                print(f"- 分析失败: {exc}")
                print(
                    f"[进度 {index}/{len(pending_symbols)}] 累计10倍命中币种: "
                    f"{cumulative_hit_symbols}，累计10倍命中次数: {cumulative_hit_events}",
                )
                print(
                    f"[扫描统计] 总目标: {len(normalized_scan_targets)} | "
                    f"已扫描: {index} | 剩余: {len(pending_symbols) - index}",
                )
                print(
                    f"[状态] 分析失败 | 本币耗时: "
                    f"{format_duration(time.time() - symbol_start_time)}"
                )
                print()
                continue

            results[inst_id] = summary
            print_report(inst_id, summary, args.threshold)
            append_scanned_symbol(scanned_file, inst_id)
            symbol_chart_paths: list[str] = []
            if output_chart_dir:
                symbol_chart_paths = render_symbol_charts(
                    chart_dir=output_chart_dir,
                    inst_id=inst_id,
                    summary=summary,
                    threshold=args.threshold,
                    chart_window=args.chart_window,
                    bar_label=args.bar,
                )
                print(f"- 已生成单币SVG: {len(symbol_chart_paths)} 张")
            append_progress_record(
                progress_file,
                inst_id,
                summary,
                args.threshold,
                "hit" if summary["hit_count"] > 0 else "no_hit",
                index,
                len(normalized_scan_targets),
                time.time() - symbol_start_time,
                generated_svg_count=len(symbol_chart_paths),
            )
            if summary["hit_count"] > 0:
                cumulative_hit_symbols += 1
                cumulative_hit_events += summary["hit_count"]
                hit_symbols.append(inst_id)
                print(
                    f"!!! 10倍命中 {inst_id} | 次数: {summary['hit_count']} | "
                    f"最高: {summary['highest_hit']['multiple']:.4f}x !!!",
                )
                print(
                    f"[状态] 命中 | 本币耗时: "
                    f"{format_duration(time.time() - symbol_start_time)}",
                )
            else:
                print(
                    f"[状态] 未命中 | 本币耗时: "
                    f"{format_duration(time.time() - symbol_start_time)}",
                )
            print(
                f"[进度 {index}/{len(pending_symbols)}] 累计10倍命中币种: "
                f"{cumulative_hit_symbols}，累计10倍命中次数: {cumulative_hit_events}",
            )
            print(
                f"[扫描统计] 总目标: {len(normalized_scan_targets)} | "
                f"已扫描: {index} | 剩余: {len(pending_symbols) - index}",
            )
            print(f"- 已写入扫描记录: {scanned_file}")
            print(f"- 已写入扫描进度文件: {progress_file}")
            print()
    except KeyboardInterrupt:
        interrupted = True
        print()
        print("检测到扫描被中断，开始基于当前已扫描结果生成输出文件和SVG图表...")
    except Exception as exc:  # noqa: BLE001
        unexpected_error = exc
        print()
        print(f"扫描过程中发生异常，开始基于当前已扫描结果生成输出文件和SVG图表: {exc}")

    chart_paths, summary_chart_paths = finalize_outputs(
        results=results,
        threshold=args.threshold,
        output_json=output_json,
        output_csv=output_csv,
        output_chart_dir=output_chart_dir,
        chart_window=args.chart_window,
        hit_list_file=hit_list_file,
        hit_symbols=hit_symbols,
        generate_charts=True,
        bar_label=args.bar,
    )

    if output_json:
        print(f"JSON 已写入: {output_json}")

    if output_csv:
        print(f"CSV 已写入: {output_csv}")

    if output_chart_dir:
        print("单币SVG已在扫描过程中逐个生成。")
        print(f"汇总分析图已生成: {len(summary_chart_paths)} 张")
        for path in summary_chart_paths:
            print(f"- {path}")
        print(f"- 9倍命中图目录: {os.path.join(output_chart_dir, 'ninefold_hit_svgs')}")
        print(f"- 10倍命中图目录: {os.path.join(output_chart_dir, 'tenfold_hit_svgs')}")
        print(f"- 汇总分析图目录: {os.path.join(output_chart_dir, 'summary_svgs')}")

    unique_hit_symbols = sorted(set(hit_symbols))
    print(f"10倍命中币清单已写入: {hit_list_file}")
    print(f"10倍命中币种总数: {len(unique_hit_symbols)}")
    print(f"本次扫描总耗时: {format_duration(time.time() - start_time)}")

    if interrupted:
        return 130
    if unexpected_error is not None:
        print(f"未预期异常详情: {unexpected_error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
