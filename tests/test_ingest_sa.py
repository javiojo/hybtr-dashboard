"""Tests for scripts/ingest_sa.py.

The official October 27 Seeking Alpha export is used as a regression check
when it is present on disk. It is not in the repo (it is vendor data) and
those tests skip cleanly when the file is absent.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ingest_sa  # noqa: E402

SAMPLE_NAME = "sa-export-2026-10-27.xlsx"
# Fields Path A (enrich.py) may overwrite on a later weekday run.
PATH_A_FIELDS = {
    "price",
    "chg",
    "w1",
    "m1",
    "m3",
    "m6",
    "ytd",
    "ret1y",
    "high52",
    "low52",
    "sma50",
    "sma100",
    "sma200",
    "vs50",
    "vs200",
    "rsi",
}
# Must match ingest output vs the Oct 27 commit exactly.
STABLE_FIELDS = [
    "symbol",
    "name",
    "yTTM",
    "yFWD",
    "freq",
    "aum",
    "issuer",
    "ac",
    "fund_type",
    "holdings",
    "payout",
    "exdiv",
    "inception",
    "exp",
    "perf3y",
    "ret3y",
]
# At ingest time (before enrich) these should also match the Oct 27 file.
INGEST_ALSO_MATCHES = [
    "price",
    "chg",
    "w1",
    "m1",
    "m6",
    "ytd",
    "ret1y",
    "high52",
    "low52",
    "sma50",
    "sma100",
    "sma200",
    "vs50",
    "vs200",
    "rsi",
]


def sample_xlsx_path() -> Path | None:
    env = os.environ.get("HYBTR_SA_SAMPLE")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(ROOT.parent / SAMPLE_NAME)
    candidates.append(Path("/workspace/hybtr") / SAMPLE_NAME)
    for path in candidates:
        if path.is_file():
            return path
    return None


def parse_data_js(text: str) -> list[dict]:
    records = []
    for line in text.splitlines():
        if not line.startswith("{symbol:"):
            continue
        rec = {}
        for match in re.finditer(r"[{,]([A-Za-z_][A-Za-z0-9_]*):", line):
            key = match.group(1)
            rest = line[match.end() :]
            if rest.startswith('"'):
                inner = re.match(r'"((?:\\.|[^"\\])*)"', rest)
                rec[key] = inner.group(1) if inner else ""
            elif rest.startswith("null"):
                rec[key] = None
            else:
                num = re.match(r"-?\d+(?:\.\d+)?", rest)
                rec[key] = num.group(0) if num else None
        records.append(rec)
    return records


def write_synthetic_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Screener"
    headers = [
        "Rank",
        "Symbol",
        "Fund Name",
        "Price",
        "Change %",
        "Fund Type",
        "Issuer",
        "Asset Class & Sub-class",
        "AUM",
        "Expense Ratio",
        "1W Perf",
        "1M Perf",
        "6M Perf",
        "YTD Perf",
        "1Y Total Return",
        "52W High",
        "52W Low",
        "Holdings",
        "Payout Date",
        "Ex-Div Date",
        "Yield TTM",
        "Yield FWD",
        "Frequency",
        "50D SMA",
        "Last Price Vs. 50D SMA",
        "100D SMA",
        "Last Price Vs. 100D SMA",
        "200D SMA",
        "Last Price Vs. 200D SMA",
        "RSI",
        "3Y Perf",
        "3Y Total Return",
    ]
    ws.append(headers)
    # Excel serial 46318 = 2026-10-23 (1899-12-30 epoch).
    payout_serial = 46318
    exdiv_serial = 46317
    # Keep: 12% yield, rich fields, blank asset class, dates as serials.
    ws.append(
        [
            1,
            "KEEP1",
            'Acme "Alpha" Income ETF',
            10.5,
            0.0105,
            "Traditional",
            "Acme",
            None,  # ac → ""
            1234567.89,
            0.0069,
            0.011,
            -0.02,
            0.1,
            0.2,
            0.4471,
            12.0,
            8.25,
            17,
            payout_serial,
            exdiv_serial,
            0.12,
            0.11,
            "Monthly",
            10.25,
            0.0243902439,
            10.0,
            0.05,
            9.5,
            0.105263,
            55.5,
            "-",
            "-",
        ]
    )
    # Keep: 6.00% exactly, Canadian ticker, datetime dates, 3Y figures.
    ws.append(
        [
            2,
            "KEEP2:CA",
            "Beta Yield ETF",
            20,
            -0.0033,
            "Leveraged",
            "Beta Inc",
            "Equity",
            5000000,
            0.01,
            0.0,
            0.01,
            None,  # m6 missing
            0.05,
            None,  # no 1Y return
            21.5,
            15,
            8,
            datetime(2026, 10, 1),
            date(2026, 9, 28),
            0.06,
            0.061,
            "Weekly",
            19.5,
            0.025641,
            19,
            0.0526,
            18,
            0.1111,
            60.12345,
            0.12,
            0.25,
        ]
    )
    # Drop: yield 2.99%
    ws.append(
        [
            3,
            "DROP1",
            "Too Low Yield ETF",
            30,
            0,
            "Traditional",
            "Gamma",
            "Bond",
            1,
            0.002,
            0,
            0,
            0,
            0,
            0,
            30,
            30,
            1,
            None,
            None,
            0.0299,
            0.02,
            "Annual",
            30,
            0,
            30,
            0,
            30,
            0,
            50,
            None,
            None,
        ]
    )
    wb.save(path)
    wb.close()


def test_fmt_num_strips_trailing_zeros():
    assert ingest_sa.fmt_num(38.07) == "38.07"
    assert ingest_sa.fmt_num(1.0) == "1"
    assert ingest_sa.fmt_num(-9.0238) == "-9.0238"
    assert ingest_sa.fmt_num(0.0) == "0"
    assert ingest_sa.fmt_num(None) == "null"


def test_to_date_serial_and_datetime():
    assert ingest_sa.to_date_str(46318) == "2026-10-23"
    assert ingest_sa.to_date_str(datetime(2026, 10, 22)) == "2026-10-22"
    assert ingest_sa.to_date_str(date(2026, 10, 22)) == "2026-10-22"
    assert ingest_sa.to_date_str("-") is None
    assert ingest_sa.to_date_str(None) is None


def test_to_percent():
    assert ingest_sa.to_percent(0.47864872) == pytest.approx(47.864872)
    assert ingest_sa.to_percent("-") is None


def test_yield_filter_and_mapping_on_synthetic(tmp_path: Path):
    xlsx = tmp_path / "tiny.xlsx"
    write_synthetic_xlsx(xlsx)
    rows = ingest_sa.load_screener(xlsx)
    records = ingest_sa.ingest_rows(rows)
    symbols = [r["symbol"] for r in records]
    assert symbols == ["KEEP1", "KEEP2:CA"]  # KEEP1 has 1Y 44.71%, KEEP2 has null 1Y
    keep1 = records[0]
    assert keep1["name"] == 'Acme "Alpha" Income ETF'
    assert keep1["ac"] == ""
    assert keep1["chg"] == pytest.approx(1.05)
    assert keep1["exp"] == pytest.approx(0.69)
    assert keep1["yTTM"] == pytest.approx(12.0)
    assert keep1["payout"] == "2026-10-23"
    assert keep1["exdiv"] == "2026-10-22"
    assert keep1["inception"] is None
    assert keep1["perf3y"] is None
    assert keep1["ret3y"] is None
    assert keep1["holdings"] == 17
    keep2 = records[1]
    assert keep2["ret1y"] is None
    assert keep2["m6"] is None
    assert keep2["yTTM"] == pytest.approx(6.0)
    assert keep2["payout"] == "2026-10-01"
    assert keep2["exdiv"] == "2026-09-28"
    assert keep2["perf3y"] == pytest.approx(12.0)
    assert keep2["ret3y"] == pytest.approx(25.0)

    out = tmp_path / "data.js"
    text = ingest_sa.write_data_js(records, out)
    assert 'name:"Acme \\"Alpha\\" Income ETF"' in text
    assert 'ac:""' in text
    assert "inception:null" in text
    assert "ret1y:null" in text
    today_iso = date.today().isoformat()
    assert text.startswith(f'const DATA_UPDATED_AT="{today_iso}";\nconst D=[')
    assert text.endswith("];")
    assert not text.endswith("];\n")


def test_dry_run_writes_nothing(tmp_path: Path, capsys):
    xlsx = tmp_path / "tiny.xlsx"
    write_synthetic_xlsx(xlsx)
    existing = tmp_path / "data.js"
    existing.write_text('const D=[\n{symbol:"OLD",name:"Old",price:1,chg:0,w1:0,m1:0,m6:0,ytd:0,ret1y:1,yTTM:7,yFWD:7,freq:"Monthly",aum:1,issuer:"X",ac:"Y",fund_type:"Traditional",holdings:1,high52:1,low52:1,sma50:1,sma100:1,sma200:1,vs50:0,vs200:0,rsi:50,payout:null,exdiv:null,inception:null,exp:1,perf3y:null,ret3y:null}\n];')
    index = tmp_path / "index.html"
    index.write_text("Data: January 1, 2020\nLast updated: January 1, 2020\n")
    before_js = existing.read_text()
    before_html = index.read_text()
    rc = ingest_sa.main(
        ["--input", str(xlsx), "--output", str(existing), "--index", str(index), "--dry-run"]
    )
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Rows kept (Yield TTM >= 3%): 2" in captured
    assert "KEEP1" in captured and "KEEP2:CA" in captured
    assert "OLD" in captured  # removed
    assert "Dry run — not writing files" in captured
    assert existing.read_text() == before_js
    assert index.read_text() == before_html


def test_writes_data_and_updates_index_dates(tmp_path: Path):
    xlsx = tmp_path / "tiny.xlsx"
    write_synthetic_xlsx(xlsx)
    out = tmp_path / "data.js"
    index = tmp_path / "index.html"
    index.write_text("Data: January 1, 2020\nLast updated: January 1, 2020\n")
    rc = ingest_sa.main(
        ["--input", str(xlsx), "--output", str(out), "--index", str(index)]
    )
    assert rc == 0
    js = out.read_text()
    today_iso = date.today().isoformat()
    assert js.startswith(f'const DATA_UPDATED_AT="{today_iso}";\nconst D=[')
    records = parse_data_js(js)
    assert [r["symbol"] for r in records] == ["KEEP1", "KEEP2:CA"]
    today = ingest_sa.format_display_date()
    html = index.read_text()
    assert f"Data: {today}" in html
    assert f"Last updated: {today}" in html


def test_update_index_dates_replaces_placeholder(tmp_path: Path):
    index = tmp_path / "index.html"
    index.write_text('<div id="navMeta">Data: —</div>\n<span id="footerDate">Last updated: —</span>\n')
    day = date(2026, 11, 25)
    label = ingest_sa.update_index_dates(index, day)
    assert label == "November 25, 2026"
    html = index.read_text()
    assert "Data: November 25, 2026" in html
    assert "Last updated: November 25, 2026" in html


def test_index_reads_data_updated_at_with_static_fallback():
    html = (ROOT / "index.html").read_text()
    assert 'src="data.js"' in html
    assert re.search(r'id="navMeta">Data: [A-Za-z]+ \d+, \d{4}<', html)
    assert re.search(r'id="footerDate">Last updated: [A-Za-z]+ \d+, \d{4}<', html)
    assert "typeof DATA_UPDATED_AT==='string'" in html
    assert "n.textContent='Data: '+label" in html
    assert "f.textContent='Last updated: '+label" in html


@pytest.fixture
def sa_sample() -> Path:
    path = sample_xlsx_path()
    if path is None:
        pytest.skip(
            "Seeking Alpha sample xlsx not present "
            "(set HYBTR_SA_SAMPLE or place sa-export-2026-10-27.xlsx next to the repo)"
        )
    return path


def test_oct27_keeps_exactly_869(sa_sample: Path):
    rows = ingest_sa.load_screener(sa_sample)
    records = ingest_sa.ingest_rows(rows)
    assert len(rows) == 1057
    assert len(records) == 869
    assert records[0]["symbol"] == "MRNY"
    traps = [r for r in records if r["ret1y"] is not None and r["ret1y"] < 0]
    assert len(traps) == 299


def test_oct27_regression_against_c0556eb(sa_sample: Path, tmp_path: Path):
    result = subprocess.run(
        ["git", "show", "c0556eb:data.js"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    expected = parse_data_js(result.stdout)
    rows = ingest_sa.load_screener(sa_sample)
    records = ingest_sa.ingest_rows(rows)
    out = tmp_path / "data.js"
    produced_text = ingest_sa.write_data_js(records, out)
    produced = parse_data_js(produced_text)

    assert [r["symbol"] for r in produced] == [r["symbol"] for r in expected]
    assert len(produced) == 869

    stable_mismatches: list[str] = []
    ingest_mismatches: list[str] = []
    for got, want in zip(produced, expected):
        sym = got["symbol"]
        for field in STABLE_FIELDS:
            if got.get(field) != want.get(field):
                stable_mismatches.append(
                    f"{sym}.{field}: got {got.get(field)!r} want {want.get(field)!r}"
                )
        for field in INGEST_ALSO_MATCHES:
            if got.get(field) != want.get(field):
                ingest_mismatches.append(
                    f"{sym}.{field}: got {got.get(field)!r} want {want.get(field)!r}"
                )

    assert not stable_mismatches, (
        "Seeking Alpha-only / Path-A-stable fields must match c0556eb data.js "
        f"exactly ({len(stable_mismatches)} mismatches):\n"
        + "\n".join(stable_mismatches[:30])
    )
    assert not ingest_mismatches, (
        "Ingest-time fields (also present in the Oct 27 Excel) should match "
        f"c0556eb data.js exactly ({len(ingest_mismatches)} mismatches):\n"
        + "\n".join(ingest_mismatches[:30])
    )
    # Full file should be identical at ingest time (no Path A rewrite yet).
    assert produced_text == result.stdout
