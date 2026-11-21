#!/usr/bin/env python3
"""
Daily enrichment for hybtretfs.com.

Reads data.js, fetches dividend-adjusted closes from yfinance for every symbol,
recomputes total returns and technicals, and surgically rewrites those fields
in place.

Updated every run (when a value can be computed):
  price, chg, w1, m1, m3, m6, ytd, ret1y,
  high52, low52, sma50, sma100, sma200, vs50, vs200, rsi (Wilder 14)

Never overwritten with NaN — the previous value is left in place.
Seeking Alpha-only fields are left untouched:
  aum, issuer, ac, fund_type, exp, holdings, freq, payout, exdiv, yTTM, yFWD

yTTM / yFWD could later be recomputed from Yahoo dividend history on days
with no fresh Excel; that is intentionally not done in this change.

Also updates "Data:" / "Last updated:" date strings in index.html.
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

# Fields Path A may rewrite when a fresh value is available.
RETURN_FIELDS = ("price", "chg", "w1", "m1", "m6", "ytd", "ret1y")
TECHNICAL_FIELDS = (
    "high52",
    "low52",
    "sma50",
    "sma100",
    "sma200",
    "vs50",
    "vs200",
    "rsi",
)
UPDATE_FIELDS = RETURN_FIELDS + TECHNICAL_FIELDS


def format_display_date(day: date | None = None) -> str:
    """Portable 'November 19, 2026' (avoids the Linux-only %-d directive)."""
    day = day or date.today()
    return day.strftime("%B ") + str(day.day) + day.strftime(", %Y")


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


def _normalize_index(close: pd.Series) -> pd.Series:
    if getattr(close.index, "tz", None) is not None:
        close = close.copy()
        close.index = close.index.tz_localize(None)
    return close


def trailing_return(close: pd.Series, today: date, **offset_kwargs) -> float | None:
    """Last close at-or-before today minus the given DateOffset, vs latest close."""
    if close.empty:
        return None
    latest = float(close.iloc[-1])
    target = pd.Timestamp(today) - pd.DateOffset(**offset_kwargs)
    past = close[close.index <= target]
    if past.empty:
        return None
    past_close = float(past.iloc[-1])
    if past_close <= 0:
        return None
    return (latest / past_close - 1) * 100


def compute_returns(close: pd.Series, today: date) -> dict[str, float | None]:
    """Compute price/chg/w1/m1/m3/m6/ytd/ret1y from a dividend-adjusted close series.

    w1 and m1 were added so the daily job can keep 1-week and 1-month returns
    fresh between Seeking Alpha drops. m3/m6/ytd/ret1y/chg/price are unchanged.
    """
    if close.empty:
        return {}
    close = _normalize_index(close)

    latest = float(close.iloc[-1])
    out: dict[str, float | None] = {"price": latest}

    # chg vs previous trading day
    if len(close) >= 2:
        prev = float(close.iloc[-2])
        if prev > 0:
            out["chg"] = (latest / prev - 1) * 100

    # YTD: first close on/after Jan 1
    ytd_start = pd.Timestamp(today.year, 1, 1)
    ytd_window = close[close.index >= ytd_start]
    if not ytd_window.empty:
        first = float(ytd_window.iloc[0])
        if first > 0:
            out["ytd"] = (latest / first - 1) * 100

    out["w1"] = trailing_return(close, today, days=7)
    out["m1"] = trailing_return(close, today, months=1)
    out["m3"] = trailing_return(close, today, months=3)
    out["m6"] = trailing_return(close, today, months=6)
    out["ret1y"] = trailing_return(close, today, months=12)

    return out


def simple_moving_average(close: pd.Series, window: int) -> float | None:
    """SMA of the last `window` closes. None if there is not enough history."""
    if window <= 0 or len(close) < window:
        return None
    return float(close.iloc[-window:].mean())


def wilder_rsi(close: pd.Series, period: int = 14) -> float | None:
    """Wilder (smoothed) RSI. Needs at least period+1 closes.

    Seed = simple average of the first `period` gains/losses; subsequent
    values use Wilder's smoothing: ((prev * (period-1)) + current) / period.
    Path-dependent, so the full series is used, not a rolling window of 14.
    """
    if period <= 0 or len(close) < period + 1:
        return None
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    # iloc[1 : period+1] is the first `period` changes (diff[0] is NaN).
    avg_gain = float(gains.iloc[1 : period + 1].mean())
    avg_loss = float(losses.iloc[1 : period + 1].mean())
    for i in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + float(gains.iloc[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(losses.iloc[i])) / period
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_technicals(close: pd.Series, today: date) -> dict[str, float | None]:
    """52-week high/low, SMAs, vs-SMA percentages, and Wilder RSI(14)."""
    if close.empty:
        return {}
    close = _normalize_index(close)
    latest = float(close.iloc[-1])
    out: dict[str, float | None] = {}

    start_52 = pd.Timestamp(today) - pd.DateOffset(years=1)
    window_52 = close[close.index >= start_52]
    if not window_52.empty:
        out["high52"] = float(window_52.max())
        out["low52"] = float(window_52.min())

    for window, field in ((50, "sma50"), (100, "sma100"), (200, "sma200")):
        value = simple_moving_average(close, window)
        if value is not None:
            out[field] = value

    if out.get("sma50") and out["sma50"] > 0:
        out["vs50"] = (latest / out["sma50"] - 1) * 100
    if out.get("sma200") and out["sma200"] > 0:
        out["vs200"] = (latest / out["sma200"] - 1) * 100

    rsi = wilder_rsi(close, 14)
    if rsi is not None:
        out["rsi"] = rsi

    return out


def compute_all(close: pd.Series, today: date) -> dict[str, float | None]:
    """Returns + technicals for one symbol. Missing keys mean 'leave old value'."""
    out = {}
    out.update(compute_returns(close, today))
    out.update(compute_technicals(close, today))
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
    for field in UPDATE_FIELDS:
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
            results[orig] = compute_all(close, today)
        except Exception as e:
            print(f"  compute failed for {orig}: {e}", file=sys.stderr)
            failed.append(orig)

    fail_ratio = len(failed) / len(symbols) if symbols else 0
    print(f"Computed enrichment for {len(results)} symbols. Failed: {len(failed)} ({fail_ratio:.1%})")
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
    today_str = format_display_date(today)
    html = INDEX_HTML.read_text()
    html = re.sub(r"(Data: )[A-Za-z]+ \d+, \d{4}", rf"\g<1>{today_str}", html)
    html = re.sub(r"(Last updated: )[A-Za-z]+ \d+, \d{4}", rf"\g<1>{today_str}", html)
    INDEX_HTML.write_text(html)

    print(f"Updated data.js ({len(results)} symbols) and index.html ({today_str})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
