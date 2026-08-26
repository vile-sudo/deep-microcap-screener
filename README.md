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
