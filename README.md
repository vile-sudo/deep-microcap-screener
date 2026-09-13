# Deep Microcap Screener

A Python-backed rebuild of the single-file "Deep Microcap Screener" research
dashboard — same 375 companies, same filters, sliders, search, scorecards,
watchlist and CSV export — now split into a real **backend (FastAPI +
SQLite/Postgres)** and **frontend (static HTML/CSS/JS)**, so it can be
deployed as a live, updatable web app instead of a file you re-send by hand.

```
screener-app/
├── backend/
│   ├── app/
│   │   ├── main.py          FastAPI app: mounts the API + serves the frontend
│   │   ├── config.py        Settings (env vars), see .env.example
│   │   ├── database.py      SQLAlchemy engine/session
│   │   ├── models.py        Company + MetaKV tables
│   │   ├── seed.py          Loads data/*.json into the database
│   │   └── routers/
│   │       ├── companies.py GET /api/companies, /api/companies/{code}, CSV export
│   │       └── meta.py      GET /api/meta, /api/meta/stats
│   ├── data/
│   │   ├── companies_raw.json   The 375 company research records (source of truth)
│   │   └── meta_raw.json        Build/version history, screen legend, theme labels
│   ├── scripts/
│   │   └── refresh_data.py  Re-pulls quantitative fields from screener.in
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html
│   └── static/
│       ├── style.css
│       └── app.js           Fetches from the API, then drives the whole board
├── docker-compose.yml
└── DEPLOYMENT.md
```

## Why a backend at all?

The original file worked by embedding a 1.6 MB JavaScript array directly
into the HTML — great for "send one file, it just works," bad for "update
the data without resending the file to everyone," and impossible to query
programmatically. This version keeps every interactive feature but:

- stores the data in a real database (SQLite by default, swap in Postgres
  with one environment variable for a multi-instance deployment),
- serves it over a documented REST API (`/docs` for interactive Swagger
  UI) that other tools — a notebook, a script, a second frontend — can hit
  directly,
- lets you refresh the data and have every visitor see the update on
  their next page load, with no file to resend.

## Data fidelity notes

Two real issues in the original dataset would have crashed the original
page for some rows; both are fixed here rather than reproduced:

1. **20 companies** (`screen: "v8-weekly"`) used a screen code the
   original board's legend never defined — opening their detail card or
   exporting them to CSV would throw a JavaScript error in the source
   file. Fixed in `app/seed.py` by patching the legend.
2. **35 companies** had `penalty_detail` stored as a bare string instead
   of a list — the drawer's rendering code assumes a list everywhere and
   would crash on these. Normalized on load in `app/seed.py`.

Also: 45 companies (the `v8-moat` screen, rubric `v5`) score a
**"Strategic"** pillar instead of "Under-covered" — the original code's
hardcoded pillar list would have silently shown these as 0/15
"Under-covered" and hidden their real score. `app/static/app.js` now shows
whichever pillar the record actually carries.

Every other field, note, score and badge is carried over verbatim.

## Quickstart (local)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # defaults are fine as-is
python -m app.seed                   # loads data/*.json into data/screener.db
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 — that's the dashboard. The API itself is at
http://localhost:8000/api/companies, with interactive docs at
http://localhost:8000/docs.

## Docker

```bash
docker compose up --build
```

Same result, containerized, with the SQLite file persisted in a named
volume so it survives rebuilds.

## Refreshing the data

```bash
cd backend
source .venv/bin/activate
python scripts/refresh_data.py           # re-pulls quantitative fields from screener.in
python -m app.seed                       # reloads data/companies_raw.json into the DB
```

See `scripts/refresh_data.py` for what it does and does not update — it
touches price/P-E/ROCE/ROE/holding-pattern fields only, never the
qualitative research (business description, moat notes, scores). Adding a
genuinely new company or re-scoring one is research work: edit
`data/companies_raw.json` by hand (or however you generated it originally
— including Screener/Trendlyne lookups run interactively in a Claude
session), then re-seed.

## Deploying it live

See **DEPLOYMENT.md** for step-by-step instructions for Render, Railway,
Fly.io, and a plain VPS.

## Chart Gallery (automated)

The **Chart Gallery** page shows a daily candlestick chart — 50/200-day
averages, volume, 52-week high — for every company on the board.

- **Data:** the NSE and BSE end-of-day bhavcopy files, which cover SME
  listings too. `backend/scripts/update_charts.py` keeps a one-year cache of
  them, rebuilds every company's candles (back-adjusted for splits and
  bonuses) and writes `backend/chart_data/`.
- **Automation:** `.github/workflows/charts.yml` runs it every weekday
  evening after the exchanges publish, and on every push that changes
  `backend/data/companies_raw.json` — so a company added to the board gets
  its chart without anyone doing anything. It commits the result and
  triggers the Render deploy hook.
- **In between runs:** `GET /api/charts/{code}` fetches a newly added company
  live (Yahoo Finance) until the next run replaces it with exchange data.
- **Run it by hand:** `cd backend && python scripts/update_charts.py`, or
  *Actions → Chart gallery update → Run workflow* on GitHub.

## ASME certification (automated)

Screen filters has an **ASME certified** filter: board companies holding an
active ASME certificate (boiler, pressure-vessel, nuclear-component stamps),
with the other NSE/BSE-listed ASME holders listed underneath. Scorecards of
certified companies show their certificate types and since-date.

- `automation/scan-asme.mjs` reads ASME's CA Connect directory (India,
  active) in a headless browser, matches it to NSE/BSE listings, and on a
  sane result writes `backend/data/asme_raw.json`.
- `.github/workflows/asme.yml` runs it every Saturday, commits and
  redeploys. It also still runs in the local weekly task (`run-weekly.cmd`).
- Each company carries every certificate type with when it was first
  received and last issued; the "not on your board" list sorts by newest or
  oldest certification and filters by date and certificate type.
- `GET /api/asme` matches that list against the board's current companies
  at request time (ticker, exchange symbol, or exact company name), so a
  company added to the board is tagged without a re-scan.

## Market view (automated stage screen)

**Market view** is a stage screen for the board, rebuilt after every trading
day with no manual step:

- **Stages** — Forming (in a base under its pivot), Fresh breakouts (cleared
  the pivot on volume in the last 5 sessions), Climbing, Played out (this
  year's breakouts that were stopped or trailed out).
- **Cards** — candles with the pivot line and base box, plus RS rating, now
  vs pivot, tightening (ATR ratio), volume dry-up, up/down volume, distance
  from the 52-week high, squat / failed-poke flags and a Powering up /
  Cooling off read. Cards or list, sortable, filterable by theme, CSV.
- **What changed since last close** — breakouts, moves to Climbing, exits,
  new setups, new 52-week highs/lows; and how many liquid NSE stocks broke
  out market-wide.

**Screen: IPO base** — companies listed in the last two years (dated by the
first session their ISIN or either exchange ticker appears, so renames,
splits and a later second listing don't count), basing under their
post-listing high. Same four stages; "on the verge" flags Forming names
within 5% of the pivot, and the list sorts closest-to-breakout first.

`backend/app/setups.py` holds every rule (documented at the top of the
file). `scripts/update_charts.py` runs it and writes
`backend/chart_data/setups.json`, so the existing
`.github/workflows/charts.yml` schedule (weekday evenings, and whenever
`companies_raw.json` changes) keeps it current and redeploys.

NIFTY 50 and SENSEX sit in the Market view bar: live through Zerodha when
connected, delayed otherwise. Zerodha setup, once:
1. Create an app at https://developers.kite.trade (the Connect plan includes
   market data). Set its **Redirect URL** to `https://YOUR-SITE/api/kite/callback`.
2. On Render → Environment, add `KITE_API_KEY`, `KITE_API_SECRET`, and
   `KITE_ADMIN_KEY` (a password you choose). Never commit these.

Daily: Market view → **Connect Zerodha** → enter the admin key → log in.
Zerodha sessions end at 6 a.m. The access token stays on the server and is
never sent to the browser; a redeploy on a host without a persistent disk
drops the session.
