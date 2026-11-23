#!/usr/bin/env python3
"""Rebuild the crawlable Top 50 snapshot from data.js.

Writes static rows into index.html between <!-- TOP50:START --> and
<!-- TOP50:END --> (inside #tbody) and regenerates top-50.html.

Run from the repo root after data.js is updated:

    python scripts/build_top50.py
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_JS = ROOT / "data.js"
INDEX = ROOT / "index.html"
TOP50_PAGE = ROOT / "top-50.html"
MARK_START = "<!-- TOP50:START -->"
MARK_END = "<!-- TOP50:END -->"
N = 50


def parse_data_js(text: str) -> tuple[str, list[dict]]:
    m = re.search(r'DATA_UPDATED_AT="([^"]+)"', text)
    updated = m.group(1) if m else ""
    recs = []
    for block in re.finditer(r"\{([^{}]+)\}", text):
        body = block.group(1)
        if "symbol:" not in body:
            continue
        rec: dict = {}
        for k, v in re.findall(r'(\w+):(null|"[^"]*"|-?\d+(?:\.\d+)?)', body):
            if v == "null":
                rec[k] = None
            elif v.startswith('"'):
                rec[k] = v[1:-1]
            else:
                rec[k] = float(v) if "." in v else int(v)
        if "symbol" in rec:
            recs.append(rec)
    return updated, recs


def sort_key(e: dict):
    v = e.get("ret1y")
    return (v is None, -(v if v is not None else 0))


def fmt_pct(v, digits=1) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{digits}f}%"


def cc(v) -> str:
    if v is None:
        return "na"
    if v > 0:
        return "pos"
    if v < 0:
        return "neg"
    return "nt"


def fmt_price(v) -> str:
    if v is None:
        return "—"
    return f"${float(v):.2f}"


def fmt_yield(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}%"


def format_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %-d, %Y")
    except Exception:
        try:
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %d, %Y")
        except Exception:
            return iso or "unknown date"


def top_n(recs: list[dict], n: int = N) -> list[dict]:
    return sorted(recs, key=sort_key)[:n]


def tbody_rows(rows: list[dict]) -> str:
    out = []
    for i, e in enumerate(rows, 1):
        trap = e.get("ret1y") is not None and e["ret1y"] < 0
        name = html.escape(str(e.get("name") or ""))
        sym = html.escape(str(e.get("symbol") or ""))
        freq = html.escape(str(e.get("freq") or ""))
        cls = ' class="is-trap"' if trap else ""
        trap_badge = '<span class="yp">&#9888;</span>' if trap else ""
        out.append(
            f'<tr{cls}>'
            f'<td></td>'
            f'<td><div class="sc"><span class="rk">{i}</span>'
            f'<span class="sym-btn" style="cursor:default">{sym}</span>'
            f'{trap_badge}</div></td>'
            f'<td class="name-cell"><span class="name-tip" data-tip="{name}">{name}</span></td>'
            f'<td class="r">{fmt_price(e.get("price"))}</td>'
            f'<td class="r"><span class="{cc(e.get("ret1y"))}" style="font-weight:600">{fmt_pct(e.get("ret1y"))}</span></td>'
            f'<td class="r"><span class="{cc(e.get("ytd"))}">{fmt_pct(e.get("ytd"))}</span></td>'
            f'<td class="r"><span class="{cc(e.get("m1"))}">{fmt_pct(e.get("m1"))}</span></td>'
            f'<td class="r"><span class="{cc(e.get("m6"))}">{fmt_pct(e.get("m6"))}</span></td>'
            f'<td class="r">{fmt_yield(e.get("yTTM"))}</td>'
            f'<td><span class="fp">{freq}</span></td>'
            f'</tr>'
        )
    return "\n".join(out)


def standalone_rows(rows: list[dict]) -> str:
    out = []
    for i, e in enumerate(rows, 1):
        trap = e.get("ret1y") is not None and e["ret1y"] < 0
        name = html.escape(str(e.get("name") or ""))
        sym = html.escape(str(e.get("symbol") or ""))
        freq = html.escape(str(e.get("freq") or ""))
        note = " Yield trap" if trap else ""
        out.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td><strong>{sym}</strong>{html.escape(note)}</td>"
            f"<td>{name}</td>"
            f'<td class="{cc(e.get("ret1y"))}">{fmt_pct(e.get("ret1y"))}</td>'
            f'<td class="{cc(e.get("ytd"))}">{fmt_pct(e.get("ytd"))}</td>'
            f'<td class="{cc(e.get("m6"))}">{fmt_pct(e.get("m6"))}</td>'
            f"<td>{fmt_yield(e.get('yTTM'))}</td>"
            f"<td>{freq}</td>"
            "</tr>"
        )
    return "\n".join(out)


def inject_index(rows_html: str) -> None:
    text = INDEX.read_text(encoding="utf-8")
    if MARK_START not in text or MARK_END not in text:
        raise SystemExit(f"Missing {MARK_START} / {MARK_END} markers in index.html")
    pattern = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)
    replacement = f"{MARK_START}\n{rows_html}\n{MARK_END}"
    INDEX.write_text(pattern.sub(replacement, text, count=1), encoding="utf-8")


def write_top50_page(updated: str, rows: list[dict]) -> None:
    date_label = format_date(updated)
    title = "Top 50 High-Yield ETFs by 1Y Total Return | HYBTR"
    desc = (
        f"Static snapshot of the 50 high-yield ETFs with the highest 1-year "
        f"total return in the HYBTR screener, as of {date_label}. "
        f"Not financial advice."
    )
    rows_html = standalone_rows(rows)
    desc_attr = html.escape(desc)
    title_attr = html.escape(title)
    date_attr = html.escape(date_label)
    updated_attr = html.escape(updated)
    page = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<meta name="description" content="__DESC__">
<link rel="canonical" href="https://hybtretfs.com/top-50.html">
<meta property="og:type" content="website">
<meta property="og:url" content="https://hybtretfs.com/top-50.html">
<meta property="og:title" content="__TITLE__">
<meta property="og:description" content="__DESC__">
<meta property="og:image" content="https://hybtretfs.com/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="__TITLE__">
<meta name="twitter:description" content="__DESC__">
<meta name="twitter:image" content="https://hybtretfs.com/og-image.png">
<link rel="icon" type="image/x-icon" href="favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
<link rel="stylesheet" href="guide-page.css">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebPage",
  "name": "Top 50 High-Yield ETFs by 1-Year Total Return",
  "url": "https://hybtretfs.com/top-50.html",
  "description": __DESC_JSON__,
  "isPartOf": {"@type": "WebSite", "name": "HYBTR", "url": "https://hybtretfs.com/"},
  "dateModified": "__UPDATED__"
}
</script>
</head>
<body>
<nav>
  <a href="/">&larr; HYBTR</a>
  <span>Top 50 snapshot</span>
</nav>
<main>
  <h1>Top 50 High-Yield ETFs by 1-Year Total Return</h1>
  <p class="updated">Snapshot as of __DATE__ &middot; Built from the HYBTR screener data file &middot; <a href="/">Open the interactive dashboard</a></p>
  <p>This page lists the 50 eligible high-yield ETFs with the highest 1-year total return in the current HYBTR dataset. Eligibility on the tracker is at least 6 months of price history and a yield TTM of 3% or higher. Funds with a negative 1-year total return are marked as yield traps. Leveraged and other high-risk funds may appear if they meet the screener rules; use the <a href="/">interactive dashboard</a> for caution badges. This is a research snapshot, not financial advice.</p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Symbol</th>
          <th>Fund name</th>
          <th>1Y Total Return</th>
          <th>YTD Total Return</th>
          <th>6M Total Return</th>
          <th>Yield TTM</th>
          <th>Frequency</th>
        </tr>
      </thead>
      <tbody>
__ROWS__
      </tbody>
    </table>
  </div>
  <h2>Related reading</h2>
  <ul class="related">
    <li><a href="/high-yield-etfs-total-return.html">High-yield ETFs ranked by total return</a></li>
    <li><a href="/yield-trap-etfs.html">Yield trap ETFs</a></li>
    <li><a href="/weekly-canadian-high-yield-etfs.html">Weekly-pay and Canadian high-yield ETFs</a></li>
  </ul>
</main>
<footer>
  This tracker is not financial advice. Information is for research and entertainment only.
  &nbsp;|&nbsp; <a href="/privacy.html">Privacy Policy</a>
  &nbsp;|&nbsp; &copy; 2026 Axiom Creative Studios
</footer>
</body>
</html>
"""
    import json
    page = (
        page.replace("__TITLE__", title_attr)
        .replace("__DESC_JSON__", json.dumps(desc))
        .replace("__DESC__", desc_attr)
        .replace("__DATE__", date_attr)
        .replace("__UPDATED__", updated_attr)
        .replace("__ROWS__", rows_html)
    )
    TOP50_PAGE.write_text(page, encoding="utf-8")


def main() -> None:
    updated, recs = parse_data_js(DATA_JS.read_text(encoding="utf-8"))
    rows = top_n(recs, N)
    if len(rows) < N:
        print(f"Warning: only {len(rows)} rows available")
    inject_index(tbody_rows(rows))
    write_top50_page(updated, rows)
    print(f"Top {len(rows)} written to index.html and top-50.html (data as of {updated})")
    if rows:
        print(f"  #1 {rows[0].get('symbol')}  1Y {fmt_pct(rows[0].get('ret1y'))}")


if __name__ == "__main__":
    main()
