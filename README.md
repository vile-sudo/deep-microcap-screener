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

## Screen any Chart (automated)

The **Screen any Chart** page shows a daily candlestick chart — 50/200-day
averages, volume, 52-week high, a 1W-to-Max range and **X-ray: every base
it ever built** (every VCP base and breakout the stock has had, not just
the current one, each boxed and labelled on the chart) — for **every
actively traded company on the NSE and BSE**, roughly 4,000–5,000 of them,
not only the board's own.

- **Data:** the NSE and BSE end-of-day bhavcopy files, which cover SME
  listings too, as far back as each exchange publishes them (NSE: its
  current format from about December 2023, a legacy-format fallback back to
  about January 2020; BSE goes back further still).
  `backend/scripts/update_charts.py` keeps that whole window cached
  (`.bhav_cache/`, ~2450 days), rebuilds every company's candles and writes
  `backend/chart_data/`. The very first run after this window was extended
  is a one-time backfill of thousands of day-files; it downloads for at most
  `BACKFILL_BUDGET` (40 minutes) per run and the next run continues where it
  stopped, so no run has to finish the whole thing. After that each run only
  downloads the new day.
- **Which companies count as traded:** an ordinary share (ISIN `INE…01…`,
  which leaves out the ETFs, NCDs and bonds the bhavcopy also carries) that
  actually traded in the last 20 sessions, averaging at least ₹1 lakh a day.
  A company listed on both exchanges is charted once, NSE preferred, matched
  on ISIN. Every card shows the average value traded a day, in amber under
  ₹5 lakh, so a thin ticker at a 52-week high cannot be mistaken for a
  liquid one.
- **Where the candles live:** a few thousand multi-year series is a few
  hundred MB rewritten nightly — far too much to commit. Only the small
  search index `backend/chart_data/universe.json` (one line per company) is
  in the repo; the candles ride in the `charts` GitHub **release asset**
  (`universe-charts.tar.gz`), which `backend/app/universe.py` fetches once
  per server and serves from `GET /api/charts/u/{key}`. If that fetch fails,
  the board's own charts are unaffected — they are committed as before — and
  only non-board stocks report no data. The board's companies keep their
  fuller, committed charts; the universe build skips them by ISIN and by
  exchange key, so nothing is listed twice.
- **Base detection:** `backend/app/setups.py` — the same VCP/IPO base
  and breakout rules that drive Market view, walked across the whole
  cached history, with RS rating and the "Base characteristics", "Stock
  measures" and "Peers" tabs on the chart drawer.
- **Breakout stage filter and sort:** a "Breakout stage" control (Forming /
  Fresh breakouts 🚀 / Climbing / Played out — Market view's own stages)
  filters the gallery to just that pattern across the whole universe, board
  companies included, and "Sort: freshest breakout" brings the newest ones
  to the top. A card with a fresh breakout is flagged 🚀 even outside that
  filter. `write_universe()` computes this for every universe stock as it
  builds its chart (`stage`, in `chart_data/universe.json`); board
  companies' comes from `GET /api/market/setups`, fetched once when the
  page opens.
- **Alerts:** a fresh base or IPO-base breakout — on any actively traded
  NSE/BSE company, not only the board's — reaches the Alerts bell the same
  session it happens (`app.alerts.universe_breakout_item`, deduplicated
  against Market view's own narrower NSE-only pass). Opening a market-scope
  breakout alert's "Chart" button goes straight to that stock's page here.
- **Automation:** step 5 of `.github/workflows/daily.yml` runs it every
  night at 02:00 IST (after the day's auto-screen, so a company added that
  morning has its chart), and `charts.yml` on every push that changes
  `backend/data/companies_raw.json` — so a company added to the board gets
  its chart without anyone doing anything. It commits the result, uploads
  the universe bundle to the `charts` release, and triggers the Render
  deploy hook.
- **In between runs:** `GET /api/charts/{code}` fetches a newly added company
  live (Yahoo Finance) until the next run replaces it with exchange data.
- **Run it by hand:** `cd backend && python scripts/update_charts.py`, or
  *Actions → Chart gallery update → Run workflow* on GitHub.

## Research Reports (automated)

**Reports** holds a full research report for every company on the board:
key takeaways, business, moat and pricing power, import substitution, why
the market overlooks it, financials with the profit trend, capex and
guidance, the price chart with its Market view stage, risks and red flags,
verification and sources, plus a scorecard, ownership and ASME certificates.

- Built in the browser from each company's research record, chart and stage
  (`frontend/static/app.js`, "Research Reports"), so there is nothing to
  write or regenerate: a company added to the board — including the daily
  auto-screen's — has its report the same day, and the numbers move with
  the 03:00 refresh.
- The library searches the research text and filters by theme and report
  type (deep dive, research note, screening note, triage note, auto-screened).
- Each report has its own link (`#v=reports&r=CODE`), previous/next, and
  **Print / save PDF**, which prints just the report.
- Open one from the Reports tab, a company's scorecard (**Research report ›**)
  or a chart.

### Quarterly deep-dive reports

On top of that summary, each company gets a forensic **deep-dive report**,
rewritten every quarter after it files results, modelled on an institutional
initiation-of-coverage note (the Kusumgar Limited deep-dive): 43 sections from
the executive summary, business model, segments, revenue bridge, customer and
order-book forensics through capacity, capex, margins, working capital,
accounting quality, governance, moat and industry structure to forecast,
scenarios, valuation, reverse DCF, risks, thesis breakers, a monitoring
dashboard, 25 questions for management, a scorecard and a quality-control
review. The Reports page shows it first, with a contents sidebar, a quarter
picker (every past quarter is kept) and a PDF.

| Part | Comes from | How |
|---|---|---|
| Statements, quarterly results, ratios, cash conversion, working capital, shareholding | screener.in public page | code (`scripts/deep_report/model.py`), labelled [F] |
| Multiples, bear/base/bull DCF, WACC × growth sensitivity, reverse DCF, rule-based red flags | the statements above | code, labelled [E], method and inputs printed in the report |
| All written analysis (outline in `scripts/deep_report/sections.py`) | the full latest annual report, the last four earnings-call transcripts, the investor presentation, the credit-rating rationale, exchange announcements, the board's own notes, last quarter's report | **Claude Code** on your Claude Pro/Max plan (`scripts/deep_report/claude_code.py`): it searches and reads the documents like an analyst and writes the report, every claim labelled [F]/[MC]/[AI]/[E] and cited to its document and page |

Rules the writer works under: no invented numbers, customers or order books
("not disclosed" is stated as a finding); valuation figures only from the
code; **no buy/sell rating and no price target** — fair values are mechanical
DCF outputs shown next to the price. Claude Code runs in a scratch folder that
holds only those public documents, with file tools only (no shell, no web).

**Schedule** (step 6 of `daily.yml`): a company is due when it has no report,
or when screener shows a newer results quarter than its report — the report
then waits for that quarter's earnings-call transcript, for up to 21 days.
At most `REPORTS_PER_DAY` a day (default 2). A deep dive takes Claude roughly
15-40 minutes and counts against the plan's usage limits; when a limit is hit
the run stops cleanly and the next morning carries on. PDFs are printed from
the dashboard's report page (`automation/render-report-pdfs.mjs`, step 7) and
uploaded to the repository's `reports` release. Reports are stored as
`backend/reports/<CODE>/<FY27-Q1>.json`; each report's time and usage is logged
in `automation/data/deep-reports.json`.

**Turning it on (Claude Pro / Max plan, no API bill)**

1. On your PC, in a terminal where Claude Code is logged in to your Max
   account, run `claude setup-token` and copy the token it prints.
2. GitHub → repo **Settings → Secrets and variables → Actions → New repository
   secret**: name `CLAUDE_CODE_OAUTH_TOKEN`, value the token. Never commit it
   or paste it anywhere else.
3. Optional **variables** on the same page: `REPORTS_PER_DAY` (default 2),
   `REPORT_MODEL` (default `opus`; `sonnet` uses less of the plan).
4. Try one or two first: *Actions → Deep-dive reports → Run workflow* with a
   company code, read the report on the dashboard, then let the daily run work
   through the board.

On your own PC (uses your logged-in Claude Code, no token needed):
`cd backend && python scripts/deep_reports.py --codes QLINE`. Add `--dry-run`
to only fetch the documents and build the work folder.

(`REPORT_ENGINE=api` with an `ANTHROPIC_API_KEY` secret switches the writer to
the paid API instead - `scripts/deep_report/writer.py`.)

## Sector Research (automated, monthly)

The **Sectors** tab holds deep research on one sector at a time: what is
happening around the world, what is happening in India, and what it means for
Indian listed companies across the value chain (producers, services, equipment,
the gas chain, refiners...). Every figure is cited `(S12)` to a numbered source -
official statistics and regulators, company filings, rating agencies, research
houses and news - and the sources are listed by type at the end. Forecasts and
broker views are attributed to whoever made them; the dashboard gives no ratings.

Files, one folder per sector under `backend/sectors/<slug>/`:

| File | What it is |
|---|---|
| `brief.json` | name, scope, the questions the research must answer, the company universe |
| `YYYY-MM.json` | one edition per month (older editions stay selectable on the page) |
| `numbers.json` | today's screener.in numbers for the companies in the latest edition |
| `latest.json` | daily "Latest developments": dated news items with their sources and the companies they affect, plus a few headline figures (last 45 days) |

`backend/sectors/planned.json` lists the "coming next" cards.

- **Editions** - `scripts/sector_research.py` runs Claude Code (Claude Max plan,
  the same `CLAUDE_CODE_OAUTH_TOKEN` as the deep-dive reports) with web search.
  It gets the brief, last month's edition and the company numbers, and must
  re-verify and update every number. The output is checked (at least 20 sources,
  every citation resolves, known block types) before it is saved. The daily run
  writes a sector's new edition when this month's is missing, normally on the
  1st; `SECTORS_PER_RUN` (default 1) spreads several sectors over several days.
- **Company numbers** - `scripts/sector_numbers.py`, every day.
- **Latest developments** - `scripts/sector_latest.py`, every day: a short Claude
  Code session per sector (Sonnet by default; repo variable `SECTOR_LATEST_MODEL`
  to change) looks for news since the last check and writes 0-8 dated items, each
  with the pages it came from. Items without a source URL, outside the date window
  or already published are dropped before saving. They show at the top of the
  sector page.
- **By hand** - *Actions → Sector research (by hand) → Run workflow* (choose latest
  developments or monthly edition, optionally a sector slug and *force*), or locally `cd backend && python scripts/sector_research.py --sector oil-exploration --force`.
- **Refresh now (one click)** - a **Refresh now** button on each sector page (admin
  only) fires the same latest-developments check straight away, for when
  something big just happened and you don't want to wait for the 2 AM run or open
  GitHub. Needs `GH_DISPATCH_TOKEN` on the server: a fine-grained GitHub token
  scoped to this repo with "Actions: Read and write" (see `.env.example`).
  Server-side cooldown (10 min) stops repeat clicks queuing several runs.

**Adding a sector**: create `backend/sectors/<slug>/brief.json` (copy
`oil-exploration/brief.json` and change the scope, questions and universe),
remove its card from `planned.json`, then run the manual workflow with that slug.

## ASME certification (automated)

Screen filters has an **ASME certified** filter: board companies holding an
active ASME certificate (boiler, pressure-vessel, nuclear-component stamps),
with the other NSE/BSE-listed ASME holders listed underneath. Scorecards of
certified companies show their certificate types and since-date.

- `automation/scan-asme.mjs` reads ASME's CA Connect directory (India,
  active) in a headless browser, matches it to NSE/BSE listings, and on a
  sane result writes `backend/data/asme_raw.json`.
- Step 4 of `.github/workflows/daily.yml` runs it every morning (`asme.yml`
  re-runs it by hand). It also still runs in the local weekly task (`run-weekly.cmd`).
- Each company carries every certificate type with when it was first
  received and last issued; the "not on your board" list sorts by newest or
  oldest certification and filters by date and certificate type.
- `GET /api/asme` matches that list against the board's current companies
  at request time (ticker, exchange symbol, or exact company name), so a
  company added to the board is tagged without a re-scan.

## Movers (automated)

**Movers** lists every NSE stock that closed 4% or more up or down on a
trading day, with why: not just the number, but the filing, news coverage
or bulk/block deal behind it, checked against NSE's own disclosures. It
started as a standalone tool (`backend/app/movers/` says exactly what
changed crossing it into this dashboard) before joining the board here.

- **Universe:** every equity series NSE's bhavcopy carries that day --
  main board, trade-for-trade and the SME platform -- not a fixed index
  list, so a comprehensive session can flag several hundred moves. That is
  by design for a microcap-focused board: a Nifty-Total-Market-only
  screener misses exactly the names this dashboard tracks. A company also
  on the board is marked and links to its scorecard and report.
- **Corporate-action adjustment:** the bhavcopy's `PREV_CLOSE` is not
  adjusted for a dividend, bonus or split going ex that morning, so the raw
  change on an ex-date mixes a real move with pure arithmetic (a 1:1 bonus
  reads as -50%). `app/movers/corpactions.py` reads NSE's corporate actions
  and adjusts the previous close before the 4% test, so a move that only
  existed on paper drops out; what is left is what actually traded.
- **Why it moved**, ranked by how much each source actually proves:
  1. **Filings** — NSE's own corporate announcements, classified and
     windowed to the session they could have moved (a 15:59 filing explains
     the next day's close, not today's); routine housekeeping is set aside.
  2. **News** — allowlisted desks only (Moneycontrol, Mint, Economic Times,
     Business Standard, Reuters, Bloomberg and others), searched per
     company by name (a ticker like "SAILIFE" returns nothing), filtered
     for headlines that actually name the company and report an event
     rather than a listicle.
  3. **Bulk and block deals** — a single trade large enough to matter.
  Each move gets a headline verdict and a confidence: `high` when a filing
  and the press agree, `medium`/`low` for news or a deal alone,
  `mechanical` for an action too complex to quantify (a demerger, say), and
  `none` when nothing was found -- reported honestly rather than guessed.
- **Every flagged move gets checked**, one news search each: filings and
  corporate actions are a handful of requests for the whole day, and news
  is more (one per symbol, politely paced), but on an ordinary day that is
  still a few hundred at most. `MOVERS_NEWS_LIMIT` (default 5000) is a
  safety valve, not a normal ceiling -- board companies and the largest
  moves are checked first, so if a session ever flags an extraordinary
  number of moves the run still finishes rather than growing unbounded. A
  move outside that valve is marked "not checked" on the dashboard, which
  is different from "checked, found nothing."
- **Automation:** its own schedule, `movers.yml`, at **07:00 IST every
  morning** -- separately from the rest of the dashboard's 02:00 IST run,
  and later on purpose: news of yesterday's move is often published the
  next morning, and this scan makes no use of the Claude Max plan the
  02:00 run is paced around, so there is nothing to gain by running it
  earlier. `movers.yml` also reruns or backfills one date by hand
  (`gh workflow run movers.yml -f date=2026-09-10`), and an admin can fire
  it from the dashboard ("Run tonight's scan now" on the Movers page →
  `POST /api/admin/movers/run-now`, needs `GH_DISPATCH_TOKEN` like Sector
  Research's "Refresh now").
- **Data:** one JSON snapshot per trading day in `backend/data/movers/`,
  committed like Sector Research and Reports; `GET /api/movers` serves the
  latest plus the date list, `GET /api/movers/{date}` one session.
- **Run it by hand:** `cd backend && python scripts/run_movers.py`, or
  `--date 2026-09-10` to rescan or backfill a specific session (a rerun
  also gets more of that session's news coverage, since NSE's own
  publication window keeps widening for a day or two after the close).

## News Channel (automated)

**News Channel** tracks China, India and USA news for the sectors each one
dominates globally -- a leading indicator for Indian listed companies
downstream (a Chinese API price hike is a margin story for the Indian
bulk-drug makers who buy from it; a US tariff reroutes demand; a China+1
shift is a tailwind for the Indian maker competing for that order).

- **Two providers, fanned into the same filter:** `app/news_channel.py` does
  the real filtering client-side either way, the same way `app/movers/news.py`
  already does for company news -- a headline only survives if it actually
  contains one of the sector's own keyword phrases (title only, not the
  description), and is flagged `price_move` if it reads like an actual price
  event (a `%`, or a rise/fall/hike/cut/ban/curb word), not just general
  sector coverage.
  - **Google News RSS** -- no API key, no documented daily quota, ~100
    results per query. One query per sector *per country* (not one OR'd
    blob per country the way newsdata.io needs), reusing each sector's own
    keyword list. Unofficial (Google could change or throttle it without
    notice; its own feed restricts use to "personal, non-commercial"),
    paced half a second between requests. Runs regardless of whether
    `NEWSDATA_API_KEY` is set.
  - **[newsdata.io](https://newsdata.io/)** -- kept as a second, independent
    source, not replaced. Free plan is **200 requests/day** and hard-caps
    results at **10 per call** regardless of what's requested, plus a
    100-character query limit -- both real ceilings Google News doesn't
    have, which is why it isn't the primary source anymore. Set
    `NEWSDATA_API_KEY` as an env var on the server and as a GitHub Actions
    secret (never commit it); `category=business` and `prioritydomain=top`
    drop press-release wire noise. Optional -- the feed still runs on
    Google News alone if this key is ever unset.
- **Sectors tracked per country** (the same list drives both sources' queries):
  - **China** -- specialty chemicals, pharma APIs/bulk drugs, rare earths &
    critical minerals, solar, battery/EV materials, steel, electronics
    components.
  - **India** -- generic pharma, IT services, textiles & cotton, gems &
    jewellery, agrochemicals, auto components.
  - **USA** -- semiconductors, biotech/FDA, defence & aerospace, agri
    commodities, oil & gas/shale, tariffs & trade policy.
- **Schedule:** every 5 minutes (`news_channel.yml`), GitHub Actions' own
  shortest supported interval. At that cadence newsdata.io would cost
  288 x 3 = 864 requests/day, well past its 200/day ceiling, but `DAILY_CAP`
  is a hard floor independent of schedule or admin clicks: newsdata.io
  simply stops being called once the day's 180 are used, typically within
  the first few hours. Google News has no such cap and costs roughly
  288 x 19 sector queries/day (~5,500) -- there's no published quota to
  floor under on that side, so a query that starts failing just drops into
  that run's `errors` list rather than breaking anything, and the worst
  case under sustained throttling is a stale feed, not a broken one. Each
  run merges its results into the existing feed rather than replacing it,
  and prunes anything over 48 hours old.
- **Data:** `backend/data/news_channel/latest.json`, committed like Movers;
  `GET /api/news-channel` serves it. An admin can also fire a fetch from
  the dashboard ("Refresh now" on the News Channel page →
  `POST /api/admin/news-channel/run-now`, needs `GH_DISPATCH_TOKEN` like
  Sector Research's "Refresh now" -- rate-limited server-side so repeat
  clicks don't stack up runs).
- **Run it by hand:** `cd backend && python scripts/run_news_channel.py`.

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

**Screens** — the **Screen ▾** menu picks one of four preset screens. They
share one engine (the same breakout, exit and stage rules above) and differ
only in what counts as a base:

- **Volatility Contraction Pattern (VCP)** — *a tight, quiet coil under a
  ceiling*: the highest high of the last ~6 months, at least 3 weeks old, a
  pullback of at most 35%, after a run-up of at least 25%.
- **Blue sky** — *basing at its high, no sellers above*: a VCP base whose
  pivot is also the highest price the stock has traded in our exchange data
  (back to January 2020), with at least a year of trading history. A company
  listed before 2020 may have traded higher before then.
- **Multi-year breakouts** — *clearing a year-plus base*: the highest high of
  up to five years, set at least a year ago and never cleared since, with the
  stock no more than 50% under it.
- **IPO base** — *a young listing's first base*: companies listed in the last
  two years (dated by the first session their ISIN or either exchange ticker
  appears, so renames, splits and a later second listing don't count), basing
  under their post-listing high; the list sorts closest-to-breakout first.

VCP, Blue sky and Multi-year need an uptrend (above the 200-day, 50-day above
the 200-day) to count as Forming. In every screen "on the verge" flags Forming
names within 5% of the pivot, and the market-wide chip counts that screen's
breakouts across every liquid NSE stock.

**All NSE & BSE / Board companies only** — the scope next to the screen menu
(default: All NSE & BSE). All four screens run every trading day over every
actively traded NSE and BSE company, not just the board: `write_universe` in
`scripts/update_charts.py` analyses each stock in Screen any Chart's universe
and writes the ones in a setup under any screen to
`backend/chart_data/market_all.json` (compacted by `setups.compact`, about
1.2 MB / 200 KB gzipped, committed), served at `GET /api/market/setups-all`.
Those stocks show their exchange instead of a theme and have no market cap or
watchlist star; a theme filter shows board companies only. Their history is
the universe files' own, about three years (since listing for recent IPOs),
so for them Blue sky means the highest price in that window and Multi-year
looks back three years rather than five.

`backend/app/setups.py` holds every rule (documented at the top of the
file). `scripts/update_charts.py` runs it and writes
`backend/chart_data/setups.json`, so the daily 03:00 run (and `charts.yml`
whenever `companies_raw.json` changes) keeps it current and redeploys.

NIFTY 50 and SENSEX sit in the Market view bar: live through Zerodha when
connected, delayed otherwise. Zerodha setup, once:
1. Create an app at https://developers.kite.trade (the Connect plan includes
   market data). Set its **Redirect URL** to `https://YOUR-SITE/api/kite/callback`.
2. On Render → Environment, add `KITE_API_KEY`, `KITE_API_SECRET`, and
   `KITE_ADMIN_KEY` (a password you choose). Never commit these.

Daily: Market view → **Connect Zerodha** → enter the admin key → log in.
(Logged in as the dashboard admin, no key is asked.) If your Kite app's
Redirect URL points somewhere else, log in at Zerodha anyway, copy the
`request_token` from the address it lands on (or the whole address) and paste
it under **Or paste a request token** in the same window, within a few minutes.
Zerodha sessions end at 6 a.m. The access token stays on the server and is
never sent to the browser; a redeploy on a host without a persistent disk
drops the session.


## Accounts — log in, sign up, approval

The dashboard sits behind its own login page (`/login`). Anyone can **request
access**; the account stays *pending* until an admin approves it in the
dashboard (account menu → **Manage users**: approve, reject, disable, make
admin, delete). Disabling or rejecting someone ends their sessions at once.

- Passwords: salted scrypt hashes. Sessions: a random token in an HttpOnly,
  SameSite=Lax cookie (Secure on HTTPS), stored only as a SHA-256 hash, 30 days.
- Login and sign-up are rate-limited per IP.
- The admin account comes from `ADMIN_EMAIL` + `ADMIN_PASSWORD`, or from the
  older `AUTH_USERNAME` + `AUTH_PASSWORD` if those are what's set; changing the
  env var changes the admin password on the next start.
- With no admin configured (local development) the site stays open.
- **Accounts must live in Postgres in production** — the SQLite file on a
  Render web service is rebuilt on every deploy. See DEPLOYMENT.md.
- **Account menu** (everything saved per person): **Dark mode** (follows the
  account to every device; `frontend/static/dark.css` is generated from
  `style.css` by `python frontend/tools/build_dark_css.py` — re-run it after
  CSS changes, hand fixes go in `dark-extra.css`), **Profile** (name, password —
  changing it logs out other devices; the main admin's password stays in the
  server settings), **Updates** (new companies, new deep-dive reports and new
  features from `backend/data/updates.json`, unread count, watchlist names
  flagged), **My saved filters** (name and re-apply any dashboard view; also the
  ☆ Save filters button), **Write to us** (messages land in the admin's
  **Messages** inbox), **Log out**. Watchlists are per account too.
  Code: `backend/app/routers/account.py`, `routers/watchlist.py`.
- Code: `backend/app/auth.py` (hashing, sessions, the gate),
  `backend/app/routers/auth.py` (endpoints), `frontend/login.html`.


## What runs by itself

Everything runs on GitHub Actions (no PC needs to be on), in **one run every
night at about 02:00 IST**, while you sleep, so a fresh Claude usage window
is available by the time you're up (`.github/workflows/daily.yml`; GitHub can start
scheduled runs a few minutes late). It commits once and redeploys Render once.
A stage that fails leaves yesterday's data for that part, lets the other
stages run, and marks the run failed (GitHub emails you).

| Step | Script | What it keeps current |
|---|---|---|
| 1. Fundamentals | `backend/scripts/refresh_data.py` | price, market cap, P/E, ROCE, ROE, promoter/FII/DII/public % for every company (screener.in); refuses to write if screener.in blocks |
| 2. Discovery | `automation/update-weekly.mjs --no-asme` | new NSE/BSE/SME listings, small/mid-cap sweep, IPO prospectus reading → Candidates queue |
| 3. Auto-screen | `backend/scripts/auto_screen.py` | adds up to 10 companies a day that pass the board's rules, marked **Auto-added** (below) |
| 4. ASME | `automation/scan-asme.mjs` | ASME certificate holders, certificate types and dates |
| 5. Charts | `backend/scripts/update_charts.py` | candles, Screen any Chart, Market view stages (VCP + IPO base), breakouts, feed |
| 6. Sector research | `backend/scripts/sector_research.py` | a new monthly edition for each sector (Claude Code, Max plan) |
| 7. Sector numbers | `backend/scripts/sector_numbers.py` | screener.in numbers for the companies in each sector report |
| 7b. Sector latest | `backend/scripts/sector_latest.py` | dated, cited latest developments at the top of each sector report |
| 8. Deep-dive reports | `backend/scripts/deep_reports.py` | quarterly deep-dive reports after new results, and their PDFs |

Each step also has its own workflow for a manual re-run (*Actions → Run
workflow*): `fundamentals.yml`, `auto-screen.yml`, `discovery.yml`, `asme.yml`, `charts.yml`, `deep-reports.yml`, `sector-research.yml`
(which also runs on every push that changes `companies_raw.json`).
`uptime.yml` pings the site every 10 minutes, keeping a free instance awake.

**Movers** runs separately, on its own schedule at **07:00 IST**
(`movers.yml`) rather than in the 02:00 IST run above -- it needs no
Claude usage, and running it later catches more of the previous session's
overnight news coverage (see the Movers section above).

**News Channel** also runs separately, **every 5 minutes**
(`news_channel.yml`) -- news is time-sensitive in a way the rest of the
dashboard's once-a-day data isn't, and Google News RSS (its primary source
since it needs no API key and has no daily quota to protect) makes that
cadence viable; newsdata.io, the second source, is kept under its own
200-request/day ceiling by `DAILY_CAP` regardless of how often the workflow
fires (see the News Channel section above).

### Auto-added companies

Step 3 works through the listed universe (BSE's active scrips in the ₹120–10,000 cr
band -- micro and small cap -- plus NSE/SME listings; finance, realty, media and
software names skipped; up to 400 screener.in pages a day, each company re-checked
at most every 90 days, legacy/PSU brand names skipped by name up front). Two things
every candidate still needs, neither skippable: a sector that maps onto a board
theme, and **known_to_public()** -- shareholder count under the tier limit, and
not on the hand-kept legacy/PSU-brand denylist (`LEGACY_BRAND`), added after HMT
Ltd -- a real moat match, but a famous old PSU name, not hidden by any reasonable
meaning of the word -- slipped through on a thinned-out shareholder count alone.
Past those two, a company is added if it clears **any one** of five, independent
of the others -- the point is to catch a company the numbers alone would never
surface, so financials are deliberately not a blocker for a company that clears
one of these on its own:

- **states a moat in its own words** — in its screener.in profile or, failing
  that, its latest annual report (public BSE/NSE filing): import substitution,
  India's leading/largest maker, a stated market share, sole / one of few Indian
  makers, first / pioneer in India, or a niche segment plus an approval (DRDO,
  RDSO, ISRO, USFDA, AS9100, ASME...). Report sentences only count when the
  company is the subject.
- **management guidance above 15%** — a self-referencing, forward-looking
  growth statement in the annual report.
- **PAT turnaround** — the latest reported period profitable after a loss in
  one of the last few periods before it.
- **capacity utilisation ramping up** — a self-referencing statement that
  capacity utilisation will rise, tied to a named near-term period ("from the
  next quarter", "from Q3 FY27"). An operating-leverage inflection still to
  come, not one already reported — "utilisation should improve" with no
  timeframe doesn't count, and neither does a capacity *expansion* on its own.
- **product-mix pivot** — a self-referencing statement describing a strategic
  move (diversifying, foraying into a new segment, a new product/business
  line) explicitly tied to a shift in market or industry demand, not routine
  capacity addition on the same product.

The board's usual fundamentals gates (market cap ₹120–7,500 cr / ₹10,000 cr Tier 2,
promoter ≥ 40%, public ≤ 60%, ROCE ≥ 12%, ROE > 5%) are still computed and still
shown, but no longer required — a company added on any one of the five above
alone, having failed one of these, carries the same "added on request
— gates it does not clear"
disclosure a manually-added company gets, listing exactly what it failed.

Both new signals also show up as **Screen filters** (`Capacity utilisation
ramping up`, `Product-mix pivot / new venture`) and a card badge, so any
company — auto-added or researched by hand — can be flagged this way, not
just what the daily screen finds.

The best 10 by the board's six-pillar score go live, badged **AUTO-ADDED**,
with the matched sentences and links to where they came from, and a
**Screen filters → Auto-added** filter. On a quiet day fewer than five pass;
the rules are not loosened to hit a number. An admin can open an auto-added
company and **Remove from board**; it will not be auto-added again.

#### Logic Gates — editing the rules above, live

Every threshold and keyword list above is a default, not a fixed constant.
An admin can change any of it from **account menu → Admin → Logic Gates**
on the dashboard — no code change, no deploy — and the next auto-screen run
applies it:

- **Numeric gates and flags**: market cap, promoter/public %, ROCE/ROE
  ("quality floor"), institutional and shareholder limits ("shareholding
  pattern"), the P/E + CWIP capex-overhang thresholds, the management-guidance
  threshold, and how many periods back PAT turnaround looks.
- **Moat — extra keywords**: literal phrases added *alongside* the board's
  own tuned rules for each of the five moat categories (import substitution,
  leading maker, market share, sole maker, first mover) — a company only
  needs to match one, from either source.
- **Import substitution — materials**: named materials India imports
  heavily (e.g. "specialty chemicals", "solar cells"). If a company says it
  makes or supplies one of these, that counts as import-substitution
  evidence too, without needing the generic "import substitute" phrasing.
- **Legacy / PSU brand denylist**: famous old PSU/legacy names
  (`legacy_brand_names`, `LEGACY_BRAND` in code) dropped by name before any
  page is fetched, even if they'd otherwise clear a moat, guidance or
  PAT-turnaround gate — added after HMT Ltd cleared a real moat match on a
  thinned-out shareholder count alone. Replaces the built-in list entirely,
  same as materials above, not layered on top of it.
- **Run auto-screen now**: fires `auto-screen.yml` right away instead of
  waiting for the nightly run, using whatever is saved above. Needs
  `GH_DISPATCH_TOKEN` (see the Sector Research section above for how to set
  it up — the same token covers both).

Mechanically: `GET /api/meta/logic-gates` is public (these thresholds are
already documented on this page) and is what `auto_screen.py` reads at the
start of every run, since it has no database access, running in GitHub
Actions — the built-in defaults (`backend/data/logic_gates_defaults.json`)
are the fallback if the site can't be reached. `PUT`/`POST .../reset` are
admin-only and are stored in Postgres, so an edit survives every deploy.

CWIP and PAT-turnaround detection (new: the auto-screen didn't fetch either
before) come straight from screener.in's balance sheet and profit & loss
tables. Management guidance is best-effort — a self-referencing, forward
growth statement matched in the annual report text already fetched for moat
evidence — and every auto-detected guidance flag carries the matched
sentence, unverified, for the same reason the original hand-researched board
never treats guidance as a fact.

Still a person's job, by design or by necessity:
- **Researching auto-added companies** — they arrive with screener.in numbers and
  the company's own moat wording, not a researched verdict.
- **The weekly AI judgement pass** over candidates (`weekly-prompt.md`) needs
  Claude; it still runs from `run-weekly.cmd` on your PC.
- **Zerodha** requires its account holder to log in once a day.
- **Approving sign-ups** (account menu → Manage users).
