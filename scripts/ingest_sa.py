#!/usr/bin/env python3
"""
Convert an official Seeking Alpha ETF screener Excel export into data.js.

Keeps rows with Yield TTM >= 6%, converts decimals to percent and Excel
serials to YYYY-MM-DD, and writes records in the same field order and
number format as the existing dashboard file. Also updates the "Data:"
and "Last updated:" strings in index.html (same pattern as enrich.py).

Usage:
  python scripts/ingest_sa.py --input export.xlsx
  python scripts/ingest_sa.py --input export.xlsx --output data.js
  python scripts/ingest_sa.py --input export.xlsx --dry-run
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data.js"
DEFAULT_INDEX = ROOT / "index.html"

YIELD_MIN_DECIMAL = 0.06  # Seeking Alpha stores 6% as 0.06

# data.js field order (must stay stable — the page reads these names).
FIELD_ORDER = (
    "symbol",
    "name",
    "price",
    "chg",
    "w1",
    "m1",
    "m6",
    "ytd",
    "ret1y",
    "yTTM",
    "yFWD",
    "freq",
    "aum",
    "issuer",
    "ac",
    "fund_type",
    "holdings",
    "high52",
    "low52",
    "sma50",
    "sma100",
    "sma200",
    "vs50",
    "vs200",
    "rsi",
    "payout",
    "exdiv",
    "inception",
    "exp",
    "perf3y",
    "ret3y",
)

# Excel heading → data.js field. Percent fields are decimals in the export.
PERCENT_FIELDS = {
    "Change %": "chg",
    "1W Perf": "w1",
    "1M Perf": "m1",
    "6M Perf": "m6",
    "YTD Perf": "ytd",
    "1Y Total Return": "ret1y",
    "Yield TTM": "yTTM",
    "Yield FWD": "yFWD",
    "Last Price Vs. 50D SMA": "vs50",
    "Last Price Vs. 200D SMA": "vs200",
    "3Y Perf": "perf3y",
    "3Y Total Return": "ret3y",
    "Expense Ratio": "exp",
}

NUMBER_FIELDS = {
    "Price": "price",
    "AUM": "aum",
    "Holdings": "holdings",
    "52W High": "high52",
    "52W Low": "low52",
    "50D SMA": "sma50",
    "100D SMA": "sma100",
    "200D SMA": "sma200",
    "RSI": "rsi",
}

TEXT_FIELDS = {
    "Symbol": "symbol",
    "Fund Name": "name",
    "Fund Type": "fund_type",
    "Issuer": "issuer",
    "Asset Class & Sub-class": "ac",
    "Frequency": "freq",
}

DATE_FIELDS = {
    "Payout Date": "payout",
    "Ex-Div Date": "exdiv",
}

STRING_FIELDS = {"symbol", "name", "freq", "issuer", "ac", "fund_type"}
DATE_JS_FIELDS = {"payout", "exdiv"}


def patch_openpyxl_conditional_formatting() -> None:
    """Seeking Alpha workbooks can include CF operators openpyxl 3.x rejects."""
    try:
        from openpyxl.worksheet._reader import WorkSheetParser
    except ImportError:
        return

    def _skip(self, element):  # noqa: ANN001
        return None

    WorkSheetParser.parse_formatting = _skip


def load_screener(path: Path) -> list[dict]:
    """Return a list of row dicts from the Screener sheet (first heading wins)."""
    patch_openpyxl_conditional_formatting()
    try:
        import openpyxl
    except ImportError as exc:
        raise SystemExit(
            "openpyxl is required to read .xlsx files. "
            "Install it with: pip install openpyxl"
        ) from exc

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheet_name = "Screener" if "Screener" in wb.sheetnames else wb.sheetnames[0]
        ws = wb[sheet_name]
        rows = ws.iter_rows(values_only=True)
        try:
            header = list(next(rows))
        except StopIteration as exc:
            raise SystemExit(f"No rows in {path}") from exc
        header = [str(h).strip() if h is not None else "" for h in header]
        out: list[dict] = []
        for values in rows:
            if values is None or all(v is None or v == "" for v in values):
                continue
            row: dict = {}
            for key, value in zip(header, values):
                if not key:
                    continue
                # First occurrence of a duplicated heading (52W High/Low appear twice).
                if key not in row:
                    row[key] = value
            if row.get("Symbol"):
                out.append(row)
        return out
    finally:
        wb.close()


def is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() in ("", "-"):
        return True
    return False


def as_float(value) -> float | None:
    if is_blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_percent(value) -> float | None:
    raw = as_float(value)
    if raw is None:
        return None
    return raw * 100.0


def to_date_str(value) -> str | None:
    """Accept datetime, date, pandas Timestamp, or an Excel serial day number."""
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    # pandas Timestamp (avoid importing pandas just for this)
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().date().isoformat()
        except Exception:
            pass
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Excel's 1900 date system (serial 1 = 1899-12-31, with the 1900 leap-year bug).
        # The well-known 1899-12-30 epoch matches official Excel serials.
        try:
            converted = datetime(1899, 12, 30) + timedelta(days=float(value))
            return converted.date().isoformat()
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        # Already ISO, or a common US date
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def row_to_record(row: dict) -> dict | None:
    yttm = as_float(row.get("Yield TTM"))
    if yttm is None or yttm < YIELD_MIN_DECIMAL:
        return None

    rec: dict = {key: None for key in FIELD_ORDER}

    for excel_key, js_key in TEXT_FIELDS.items():
        value = row.get(excel_key)
        rec[js_key] = "" if is_blank(value) else str(value).strip()

    if not rec["symbol"]:
        return None

    for excel_key, js_key in PERCENT_FIELDS.items():
        rec[js_key] = to_percent(row.get(excel_key))

    for excel_key, js_key in NUMBER_FIELDS.items():
        rec[js_key] = as_float(row.get(excel_key))

    for excel_key, js_key in DATE_FIELDS.items():
        rec[js_key] = to_date_str(row.get(excel_key))

    rec["inception"] = None
    return rec


def ingest_rows(rows: list[dict]) -> list[dict]:
    records = [rec for rec in (row_to_record(row) for row in rows) if rec]
    # Same default order as the site: 1Y total return descending, nulls last.
    # Stable, so ties keep the Excel order.
    records.sort(key=lambda r: (r["ret1y"] is None, -(r["ret1y"] or 0)))
    return records


def fmt_num(value: float | None) -> str:
    """Up to 4 decimals, trailing zeros stripped — matches the existing data.js."""
    if value is None:
        return "null"
    if isinstance(value, float) and not math.isfinite(value):
        return "null"
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def js_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def emit_record(rec: dict) -> str:
    parts: list[str] = []
    for key in FIELD_ORDER:
        value = rec.get(key)
        if key == "inception":
            parts.append("inception:null")
        elif key in STRING_FIELDS:
            parts.append(f"{key}:{js_string(value if value is not None else '')}")
        elif key in DATE_JS_FIELDS:
            parts.append(f"{key}:{js_string(value)}" if value else f"{key}:null")
        else:
            parts.append(f"{key}:{fmt_num(value)}")
    return "{" + ",".join(parts) + "}"


def write_data_js(records: list[dict], path: Path) -> str:
    """Write data.js in the historical compact format (no trailing newline)."""
    lines = ["const D=["]
    last = len(records) - 1
    for i, rec in enumerate(records):
        suffix = "," if i < last else ""
        lines.append(emit_record(rec) + suffix)
    lines.append("];")
    text = "\n".join(lines)
    path.write_text(text)
    return text


def format_display_date(day: date | None = None) -> str:
    """Portable 'November 19, 2026' (avoids the Linux-only %-d directive)."""
    day = day or date.today()
    return day.strftime("%B ") + str(day.day) + day.strftime(", %Y")


def update_index_dates(index_path: Path, day: date | None = None) -> str:
    if not index_path.exists():
        return ""
    today_str = format_display_date(day)
    html = index_path.read_text()
    html = re.sub(r"(Data: )[A-Za-z]+ \d+, \d{4}", rf"\g<1>{today_str}", html)
    html = re.sub(r"(Last updated: )[A-Za-z]+ \d+, \d{4}", rf"\g<1>{today_str}", html)
    index_path.write_text(html)
    return today_str


def extract_symbols(data_js: str) -> list[str]:
    return re.findall(r'symbol:"([^"]+)"', data_js)


def load_existing_symbols(path: Path) -> list[str]:
    if not path.exists():
        return []
    return extract_symbols(path.read_text())


def summarize(records: list[dict], previous_symbols: list[str] | None = None) -> str:
    symbols = [r["symbol"] for r in records]
    previous = previous_symbols or []
    prev_set, new_set = set(previous), set(symbols)
    added = [s for s in symbols if s not in prev_set]
    removed = [s for s in previous if s not in new_set]
    traps = [r for r in records if r["ret1y"] is not None and r["ret1y"] < 0]
    ranked = [r for r in records if r["ret1y"] is not None]
    lines = [
        f"Rows kept (Yield TTM >= 6%): {len(records)}",
        f"Added vs current data.js:    {len(added)}"
        + (f" ({', '.join(added[:20])}{'…' if len(added) > 20 else ''})" if added else ""),
        f"Removed vs current data.js:  {len(removed)}"
        + (f" ({', '.join(removed[:20])}{'…' if len(removed) > 20 else ''})" if removed else ""),
        f"Yield traps (1Y total return < 0%): {len(traps)}",
    ]
    if ranked:
        top = ranked[0] if records and records[0]["ret1y"] is not None else max(
            ranked, key=lambda r: r["ret1y"]
        )
        lines.append(
            f"Top 1Y: {top['symbol']} — {top['name']} ({top['ret1y']:+.2f}%)"
        )
    else:
        lines.append("Top 1Y: n/a (no fund has a 1-year return)")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path, help="Seeking Alpha .xlsx export")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to data.js")
    p.add_argument(
        "--index",
        type=Path,
        default=DEFAULT_INDEX,
        help="index.html whose Data:/Last updated: strings should be refreshed",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts and ticker diffs; write nothing",
    )
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    source = args.input.expanduser().resolve()
    if not source.is_file():
        print(f"Input file not found: {source}", file=sys.stderr)
        return 2
    rows = load_screener(source)
    records = ingest_rows(rows)
    previous = load_existing_symbols(args.output)
    print(f"Read {len(rows)} screener rows from {source.name}")
    print(summarize(records, previous))
    if args.dry_run:
        print("Dry run — not writing files")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_data_js(records, args.output)
    today_str = update_index_dates(args.index)
    extra = f" and {args.index} ({today_str})" if today_str else ""
    print(f"Wrote {len(records)} records to {args.output}{extra}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
