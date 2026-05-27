#!/usr/bin/env python3
"""
Daily enrichment for hybtretfs.com.

Reads data.js, fetches dividend-adjusted closes from yfinance for every symbol,
recomputes total returns (price, chg, m3, m6, ytd, ret1y), and surgically rewrites
those fields in place. All other fields (yTTM, yFWD, aum, sma*, rsi, etc.) are
preserved as-is. Updates "Data:" / "Last updated:" date strings in index.html.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DATA_JS = ROOT / "data.js"
INDEX_HTML = ROOT / "index.html"

CHUNK_SIZE = 50
FAIL_THRESHOLD = 0.20  # abort if >20% of symbols fail

# Optional override for local testing: ENRICH_LIMIT=20 only processes 20 symbols.
LIMIT = int(os.environ.get("ENRICH_LIMIT", "0")) or None
DRY_RUN = os.environ.get("ENRICH_DRY_RUN") == "1"


def to_yf_ticker(sym: str) -> str:
    """Convert dashboard symbol (e.g. 'AMDY:CA', 'AMHE.U:CA') to a Yahoo ticker."""
    if sym.endswith(":CA"):
        base = sym[:-3].replace(".", "-")
        return base + ".TO"
    return sym


def extract_symbols(content: str) -> list[str]:
    """Return all symbols from data.js, in the order they appear."""
    return re.findall(r'symbol:"([^"]+)"', content)


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def fetch_history(tickers: list[str]) -> dict[str, pd.Series]:
    """Batch download adjusted closes. Returns {ticker: Close series}."""
    out: dict[str, pd.Series] = {}
    for chunk in chunked(tickers, CHUNK_SIZE):
        for attempt in range(3):
            try:
                df = yf.download(
                    chunk,
                    period="14mo",
                    auto_adjust=True,
                    progress=False,
                    group_by="ticker",
                    threads=True,
                )
                break
            except Exception as e:
                print(f"  download attempt {attempt + 1} failed: {e}", file=sys.stderr)
                if attempt == 2:
                    df = None
                else:
                    time.sleep(2 ** attempt)
        if df is None or df.empty:
            continue
        if len(chunk) == 1:
            t = chunk[0]
            if "Close" in df.columns:
                out[t] = df["Close"].dropna()
        else:
            for t in chunk:
                if t in df.columns.get_level_values(0):
                    try:
                        close = df[t]["Close"].dropna()
                        if not close.empty:
                            out[t] = close
                    except Exception:
                        pass
        time.sleep(0.5)
    return out


def compute_returns(close: pd.Series, today: date) -> dict[str, float | None]:
    """Compute price/chg/m3/m6/ytd/ret1y from a dividend-adjusted close series."""
    if close.empty:
        return {}
    # Drop tz info so .date() comparisons work
    if getattr(close.index, "tz", None) is not None:
        close = close.copy()
        close.index = close.index.tz_localize(None)

    latest = float(close.iloc[-1])
    out: dict[str, float | None] = {"price": round(latest, 4)}

    # chg vs previous trading day
    if len(close) >= 2:
        prev = float(close.iloc[-2])
        if prev > 0:
            out["chg"] = round((latest / prev - 1) * 100, 4)

    # YTD: first close on/after Jan 1
    ytd_start = pd.Timestamp(today.year, 1, 1)
    ytd_window = close[close.index >= ytd_start]
    if not ytd_window.empty:
        out["ytd"] = round((latest / float(ytd_window.iloc[0]) - 1) * 100, 4)

    # Trailing N-month total return: last close at-or-before today-N-months
    def trailing(months: int) -> float | None:
        target = pd.Timestamp(today) - pd.DateOffset(months=months)
        past = close[close.index <= target]
        if past.empty:
            return None
        past_close = float(past.iloc[-1])
        if past_close <= 0:
            return None
        return round((latest / past_close - 1) * 100, 4)

    out["m3"] = trailing(3)
    out["m6"] = trailing(6)
    out["ret1y"] = trailing(12)

    return out


# Regex covers JSON-ish numeric values and `null`. Uses non-greedy float pattern.
_NUM_OR_NULL = r"-?\d+(?:\.\d+)?|null"


def fmt(v: float | None) -> str:
    if v is None:
        return "null"
    # Drop trailing zeros but keep up to 4 decimals
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def update_record(line: str, results: dict[str, dict]) -> str:
    """Surgically update one record line in data.js."""
    m = re.search(r'symbol:"([^"]+)"', line)
    if not m:
        return line
    sym = m.group(1)
    if sym not in results:
        return line
    r = results[sym]

    # Update existing fields only when we computed a value (skip when None)
    for field in ("price", "chg", "m6", "ytd", "ret1y"):
        if r.get(field) is None:
            continue
        line = re.sub(
            rf"\b{field}:(?:{_NUM_OR_NULL})",
            f"{field}:{fmt(r[field])}",
            line,
            count=1,
        )

    # m3 is new — drop any prior copy, then inject after m1
    line = re.sub(rf",\s*m3:(?:{_NUM_OR_NULL})", "", line)
    if r.get("m3") is not None:
        line = re.sub(
            rf"(\bm1:(?:{_NUM_OR_NULL}))",
            rf"\1,m3:{fmt(r['m3'])}",
            line,
            count=1,
        )

    return line


def main() -> int:
    content = DATA_JS.read_text()
    symbols = extract_symbols(content)
    if LIMIT:
        symbols = symbols[:LIMIT]
    print(f"Found {len(symbols)} symbols in data.js")

    yf_map = {s: to_yf_ticker(s) for s in symbols}
    unique_tickers = sorted(set(yf_map.values()))
    print(f"Fetching {len(unique_tickers)} unique Yahoo tickers in chunks of {CHUNK_SIZE}")

    ticker_close = fetch_history(unique_tickers)
    print(f"Got history for {len(ticker_close)}/{len(unique_tickers)} tickers")

    today = date.today()
    results: dict[str, dict] = {}
    failed: list[str] = []
    for orig in symbols:
        close = ticker_close.get(yf_map[orig])
        if close is None or close.empty:
            failed.append(orig)
            continue
        try:
            results[orig] = compute_returns(close, today)
        except Exception as e:
            print(f"  compute failed for {orig}: {e}", file=sys.stderr)
            failed.append(orig)

    fail_ratio = len(failed) / len(symbols) if symbols else 0
    print(f"Computed returns for {len(results)} symbols. Failed: {len(failed)} ({fail_ratio:.1%})")
    if failed:
        print(f"Failed symbols: {', '.join(failed[:50])}{'...' if len(failed) > 50 else ''}")

    if fail_ratio > FAIL_THRESHOLD:
        print(f"ABORT: failure rate {fail_ratio:.1%} exceeds {FAIL_THRESHOLD:.0%}", file=sys.stderr)
        return 2

    if DRY_RUN:
        print("Dry run — not writing files")
        return 0

    # Rewrite data.js line by line so all non-target fields stay byte-for-byte intact
    new_lines = [update_record(line, results) for line in content.split("\n")]
    DATA_JS.write_text("\n".join(new_lines))

    # Update both date strings in index.html
    today_str = today.strftime("%B %-d, %Y")
    html = INDEX_HTML.read_text()
    html = re.sub(r"(Data: )[A-Za-z]+ \d+, \d{4}", rf"\g<1>{today_str}", html)
    html = re.sub(r"(Last updated: )[A-Za-z]+ \d+, \d{4}", rf"\g<1>{today_str}", html)
    INDEX_HTML.write_text(html)

    print(f"Updated data.js ({len(results)} symbols) and index.html ({today_str})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
