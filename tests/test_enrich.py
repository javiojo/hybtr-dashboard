"""Unit tests for scripts/enrich.py technicals and return helpers.

Uses synthetic price series only — no Yahoo network calls.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import enrich  # noqa: E402


def series_from(values, start="2025-01-02", freq="B") -> pd.Series:
    index = pd.bdate_range(start=start, periods=len(values), freq=freq)
    return pd.Series(values, index=index, dtype="float64")


def test_to_yf_ticker_canada_and_dots():
    assert enrich.to_yf_ticker("SPY") == "SPY"
    assert enrich.to_yf_ticker("SUHE:CA") == "SUHE.TO"
    assert enrich.to_yf_ticker("AMHE.U:CA") == "AMHE-U.TO"


def test_trailing_return_one_month():
    # 22 business days, last close 110, close one month earlier 100 → +10%
    today = date(2025, 2, 3)
    idx = pd.bdate_range(end="2025-02-03", periods=30)
    values = [100.0] * len(idx)
    values[-1] = 110.0
    # Put 100 at or before today-1 month (2025-01-03).
    close = pd.Series(values, index=idx)
    result = enrich.trailing_return(close, today, months=1)
    assert result == pytest.approx(10.0)


def test_compute_returns_chg_ytd_and_ret1y_logic():
    """Existing price/chg/ytd/ret1y behaviour must stay intact."""
    today = date(2026, 3, 10)
    # Build a series covering ~14 months, ending on today.
    idx = pd.bdate_range(end="2026-03-10", periods=300)
    close = pd.Series(100.0, index=idx)
    close.iloc[-1] = 120.0
    close.iloc[-2] = 100.0  # chg: 20%
    # First close on/after Jan 1, 2026
    jan = close[close.index >= "2026-01-01"]
    close.loc[jan.index[0]] = 80.0  # ytd: 120/80 - 1 = 50%
    # Close at or before today-12 months (2025-03-10)
    target = pd.Timestamp(today) - pd.DateOffset(months=12)
    past = close[close.index <= target]
    close.loc[past.index[-1]] = 60.0  # ret1y: 120/60 - 1 = 100%

    out = enrich.compute_returns(close, today)
    assert out["price"] == pytest.approx(120.0)
    assert out["chg"] == pytest.approx(20.0)
    assert out["ytd"] == pytest.approx(50.0)
    assert out["ret1y"] == pytest.approx(100.0)
    assert "w1" in out and "m1" in out and "m3" in out and "m6" in out


def test_simple_moving_average_window_and_short_history():
    close = series_from([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert enrich.simple_moving_average(close, 5) == pytest.approx(8.0)  # 6+7+8+9+10
    assert enrich.simple_moving_average(close, 10) == pytest.approx(5.5)
    assert enrich.simple_moving_average(close, 11) is None


def test_wilder_rsi_all_gains_and_all_losses():
    # 15 closes (14 up-changes) of steadily rising prices → RSI 100
    rising = series_from([float(i) for i in range(1, 16)])
    assert enrich.wilder_rsi(rising, 14) == pytest.approx(100.0)
    falling = series_from([float(i) for i in range(16, 1, -1)])
    assert enrich.wilder_rsi(falling, 14) == pytest.approx(0.0)
    assert enrich.wilder_rsi(rising, 14) is not None
    assert enrich.wilder_rsi(series_from([1, 2, 3]), 14) is None


def test_wilder_rsi_hand_calculated_seed_and_step():
    """First 14 changes seed the averages; the 15th change is Wilder-smoothed.

    Closes: 10, then 14 gains of +1 (closes 11..24), then one loss of 2 (close 22).
    Seed avg gain = 1, avg loss = 0 → after 14 periods RSI would be 100, but we
    have 16 closes so one more step: avg_gain = (1*13 + 0)/14 = 13/14,
    avg_loss = (0*13 + 2)/14 = 2/14, RS = 6.5, RSI = 100 - 100/7.5 = 86.666...
    """
    values = [10.0] + [10.0 + i for i in range(1, 15)] + [22.0]
    assert len(values) == 16
    close = series_from(values)
    rsi = enrich.wilder_rsi(close, 14)
    expected = 100.0 - (100.0 / (1.0 + 6.5))
    assert rsi == pytest.approx(expected)


def test_compute_technicals_sma_vs_and_52w():
    today = date(2026, 6, 1)
    idx = pd.bdate_range(end="2026-06-01", periods=260)
    close = pd.Series(100.0, index=idx)
    close.iloc[-1] = 110.0
    close.iloc[-5] = 130.0  # 52w high if within the last year
    close.iloc[-10] = 70.0  # 52w low
    # Last 50 closes: 49 of 100 plus maybe the 70, 130, 110 depending on position
    out = enrich.compute_technicals(close, today)
    assert out["high52"] == pytest.approx(130.0)
    assert out["low52"] == pytest.approx(70.0)
    assert out["sma50"] == pytest.approx(close.iloc[-50:].mean())
    assert out["sma100"] == pytest.approx(close.iloc[-100:].mean())
    assert out["sma200"] == pytest.approx(close.iloc[-200:].mean())
    assert out["vs50"] == pytest.approx((110.0 / out["sma50"] - 1) * 100)
    assert out["vs200"] == pytest.approx((110.0 / out["sma200"] - 1) * 100)
    assert out["rsi"] is not None


def test_compute_technicals_leaves_gaps_when_history_is_short():
    today = date(2026, 6, 1)
    close = series_from([10, 11, 12, 13, 14], start="2026-05-25")
    out = enrich.compute_technicals(close, today)
    assert "sma50" not in out
    assert "sma200" not in out
    assert "rsi" not in out  # needs 15 closes
    assert "high52" in out and "low52" in out


def test_update_record_skips_none_and_preserves_other_fields():
    line = (
        '{symbol:"FOO",name:"Foo Fund",price:10,chg:1,w1:2,m1:3,m6:4,ytd:5,'
        "ret1y:6,yTTM:7.5,yFWD:8,freq:\"Monthly\",aum:100,issuer:\"X\",ac:\"Y\","
        "fund_type:\"Traditional\",holdings:9,high52:12,low52:8,sma50:10,"
        "sma100:10,sma200:10,vs50:0,vs200:0,rsi:50,payout:\"2026-01-01\","
        "exdiv:\"2025-12-30\",inception:null,exp:0.5,perf3y:null,ret3y:null},"
    )
    results = {
        "FOO": {
            "price": 11.5,
            "chg": 2.25,
            "w1": 1.5,
            "m1": 3.25,
            "m3": 4.5,
            "m6": None,  # leave old 4
            "ytd": 5.5,
            "ret1y": 6.5,
            "high52": 13,
            "low52": 7.5,
            "sma50": 10.25,
            "rsi": 61.2345,
            "yTTM": 99,  # must not be written even if present in the dict
        }
    }
    updated = enrich.update_record(line, results)
    assert "price:11.5" in updated
    assert "chg:2.25" in updated
    assert "w1:1.5" in updated
    assert "m1:3.25,m3:4.5" in updated
    assert "m6:4" in updated  # unchanged
    assert "ytd:5.5" in updated
    assert "ret1y:6.5" in updated
    assert "high52:13" in updated
    assert "low52:7.5" in updated
    assert "sma50:10.25" in updated
    assert "rsi:61.2345" in updated
    assert "yTTM:7.5" in updated  # Seeking Alpha field preserved
    assert "yFWD:8" in updated
    assert "aum:100" in updated
    assert "issuer:\"X\"" in updated
    assert "exp:0.5" in updated
    assert "freq:\"Monthly\"" in updated


def test_fmt_matches_historical_style():
    assert enrich.fmt(38.07) == "38.07"
    assert enrich.fmt(46.52001770643643) == "46.52"
    assert enrich.fmt(None) == "null"
    assert enrich.fmt(0) == "0"
