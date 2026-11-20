# HYBTR — High-Yield, Best Total Return

Public dashboard of US- and Canada-listed dividend ETFs ranked by **1-year total return**, not by yield alone. Live site: [hybtretfs.com](https://hybtretfs.com/).

This is a static GitHub Pages site. The page reads a single `data.js` file (`const D = [ ... ]`) and does not call a market-data API from the browser.

## Data pipeline

Two jobs keep the numbers current. Neither one logs into Seeking Alpha or scrapes it.

1. **Universe and fundamentals** (Seeking Alpha Excel, occasional) — workflow `ingest-sa.yml`, weekday evenings at 8:00 PM America/New_York, plus a manual "Run workflow" button.
   - You download the official screener Excel from Seeking Alpha and save it in the Google Drive folder **Seeking Alpha HYBTR** (see [docs/DRIVE_SETUP.md](docs/DRIVE_SETUP.md)). That is the only manual step.
   - The job picks up the newest `.xlsx` (from Drive, or from `incoming/` if you uploaded one), keeps names with Yield TTM ≥ 6%, writes `data.js`, then runs the Yahoo enrich step below so prices and technicals are fresh on the new list.
   - It skips the file if it was already ingested (see `.ingest_state.json`).
2. **Daily prices, returns, and technicals** (Yahoo Finance, automatic) — workflow `enrich-daily.yml`, weekdays at 11:00 UTC.
   - `scripts/enrich.py` refreshes `price`, `chg`, `w1`, `m1`, `m3`, `m6`, `ytd`, `ret1y`, 52-week high/low, SMAs, vs-SMA, and Wilder RSI(14) from dividend-adjusted Yahoo history.
   - It never overwrites a field with a missing value, and it does not touch Seeking Alpha-only fields (AUM, issuer, asset class, fund type, expense ratio, holdings, frequency, payout/ex-div dates, Yield TTM/FWD).

Both workflows share a concurrency group and rebase before they push so they cannot clobber each other. They commit to `main` as `github-actions[bot]`, which is what GitHub Pages deploys.

## Local commands

```bash
pip install -r requirements.txt pytest

# Preview an Excel without writing anything
python scripts/ingest_sa.py --input /path/to/export.xlsx --dry-run

# Write data.js (does not run the Yahoo enrich step)
python scripts/ingest_sa.py --input /path/to/export.xlsx

# Daily enrich (needs network access to Yahoo)
python scripts/enrich.py
# ENRICH_DRY_RUN=1 ENRICH_LIMIT=20 python scripts/enrich.py

pytest -q
```

The October 27, 2026 sample export is used as a regression check when it is available (set `HYBTR_SA_SAMPLE`); it is not in this repository.

## Email signup

The subscribe form posts to a same-origin-style endpoint (`SUBSCRIBE_ENDPOINT` in `index.html`) which should be the tiny Vercel proxy in `subscribe-proxy/`. The MailerLite API token must not go back into the page. Setup and token rotation: [docs/MAILERLITE_SETUP.md](docs/MAILERLITE_SETUP.md).
