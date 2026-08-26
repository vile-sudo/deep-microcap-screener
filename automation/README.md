# Weekly discovery pipeline

Runs on **your own machine** (Windows Task Scheduler), never on Render. It
finds candidates — brand-new NSE/BSE/SME listings, plus a weekly slice of the
already-listed small/mid-cap universe not on the board yet — reads what
prospectuses it can, and puts them in front of you (and, once a week,
Claude Code with the Screener/Trendlyne MCP servers) for a verdict. Nothing
here ever edits `backend/data/companies_raw.json` or promotes a candidate to
the live board on its own — that stays a decision you make by hand, the same
way it always has.

## The moat gate

A candidate reaching this pipeline's internal queue (sector-plausible by
name alone) is not the same as a candidate reaching the live dashboard.
`lib/publish.mjs` is the one gate between them: it only writes an entry into
`backend/data/candidates_raw.json` (what the dashboard's Candidates queue
button reads) once that entry has `moat_signal: true` — real, sourced
evidence against the five criteria you asked for: import substitution,
being India's leading manufacturer of something, holding a stated India
market share, being the only (or one of very few) Indian companies making
something, or working a genuinely niche, non-commodity segment.

Two things can set `moat_signal: true`:
  - `profile-company.mjs`, when a new listing's own IPO prospectus states a
    monopoly/leading-manufacturer or import-substitution claim in its own
    first-person words.
  - the weekly `weekly-prompt.md` judgement pass, researching a candidate
    (especially a `sweep` one, which has no prospectus) via Screener/
    Trendlyne and finding a sourced hit on one of the five criteria.

`scan-listings.mjs` and `profile-company.mjs` both call `publishCandidates()`
after touching the queue; `update-weekly.mjs --stamp-only` calls it again
after the judgement pass runs, so a verdict written by that pass actually
reaches the dashboard. Sector plausibility alone (the `hint`/`plausible`
fields) still decides what gets *queued* for a look — it just never decides
what gets *published*.

## One-time setup

1. **Node.js** on PATH (18+; the scripts use built-in `fetch`).
2. **pdftotext** (poppler or xpdf) — needed only for reading IPO prospectuses.
   - Windows: ships inside Git for Windows at
     `C:\Program Files\Git\mingw64\bin\pdftotext.exe`, or install poppler
     separately. If it's not found automatically, set an environment
     variable `PDFTOTEXT` to its full path.
   - `unzip` on PATH too, for the occasional zipped prospectus.
3. **Claude Code CLI** (`npm install -g @anthropic-ai/claude-code` or
   however you already have it) with the **Screener** and **Trendlyne** MCP
   servers configured — the same ones you used to build the dashboard
   itself. `run-weekly.cmd` calls `claude -p ... --permission-mode
   bypassPermissions`, so make sure those MCP servers work non-interactively
   before you rely on the scheduled run.
4. Register the scheduled task (from an ordinary, non-admin prompt, in this
   `automation\` folder):
   ```
   schtasks /create /tn "Microcap screener weekly discovery" /tr "\"%cd%\run-weekly.cmd\"" /sc weekly /d SAT /st 17:00 /f
   ```
   Then, in PowerShell, fix the power settings schtasks can't express —
   without this, a laptop that moves to battery mid-run kills the task
   silently:
   ```powershell
   Set-ScheduledTask -TaskName "Microcap screener weekly discovery" -Settings (
     New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
       -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew)
   ```
   Check it: `schtasks /query /tn "Microcap screener weekly discovery" /v /fo list`
   Run it now: `schtasks /run /tn "Microcap screener weekly discovery"`
5. **(Optional but recommended) a Render Deploy Hook**, so publishing is one
   command instead of a push plus a click in the Render dashboard:
   Render → deep-microcap-screener → Settings → Deploy Hook → Create Deploy
   Hook → copy the URL into a new file `automation\deploy-hook.txt` (one
   line, nothing else). This file is git-ignored on purpose — it can trigger
   a redeploy of your live site, so it never gets committed.

## The weekly cycle

```
run-weekly.cmd  (Task Scheduler, Saturdays 17:00)
  ├─ update-weekly.mjs           scan-listings.mjs → profile-company.mjs → stamp
  └─ claude -p weekly-prompt.md  research + verdicts, via Screener/Trendlyne MCP
       (writes back into automation/data/candidates-queue.json, re-stamps)
```

Nothing above pushes or deploys anything. When you're ready to ship what
changed:

```
publish-candidates.cmd
```

This commits `backend/data/candidates_raw.json` + `build_stamp.json` (what
the live dashboard reads) and this pipeline's own record
(`automation/data/candidates-queue.json`, `reviewed-symbols.json`,
`profiles/`), pushes to GitHub, and — if `automation\deploy-hook.txt`
exists — triggers a Render redeploy. Without that file it tells you to
click Manual Deploy yourself.

## Files

| File | What it is |
|---|---|
| `lib/sectors.mjs` | Guesses a sector from a company name alone — the only signal an exchange feed carries. |
| `lib/publish.mjs` | The moat gate — writes `backend/data/candidates_raw.json` from the queue, keeping only entries with `moat_signal: true`. |
| `scan-listings.mjs` | New-listing diff (NSE/BSE/SME) + small/mid-cap sweep of already-listed BSE names not on the board. |
| `profile-company.mjs` | Downloads and reads a new listing's IPO prospectus; extracts company-stated claims/risks/objects. |
| `update-weekly.mjs` | Orchestrates the two above, then stamps `backend/data/build_stamp.json`. |
| `weekly-prompt.md` | What the unattended Claude Code step does with Screener/Trendlyne — research, and rule-outs only. |
| `run-weekly.cmd` | The Task Scheduler entry point. |
| `publish-candidates.cmd` | The only thing that pushes to GitHub / triggers a Render deploy. Always asks first unless you pass `-y`. |
| `data/candidates-queue.json` | Full record: every candidate ever queued, whatever its verdict. |
| `data/reviewed-symbols.json` | One-way ledger of every symbol the sweep has ever surfaced, so it's never re-queued. |
| `data/listings-snapshot.json` | Last week's full symbol set, for the new-listing diff. Not committed — regenerates (re-baselines silently) if lost. |
| `data/profiles/*.json` | One file per profiled new listing — the raw extraction, kept for audit even though it's folded into the queue entry too. |
| `data/weekly.log`, `data/last-run.json` | This machine's run history. Not committed. |

## What this can't verify from here

I built and unit-tested the discovery logic (sector-hint classification, the
new-listing diff, the market-cap sweep and its dedupe, the queue-merge that
feeds the dashboard) against mocked exchange data, from a cloud sandbox whose
network can't actually reach nseindia.com or bseindia.com. The first real run
against live NSE/BSE data needs to happen on your machine — run
`node scan-listings.mjs --dry` by hand first and check the counts look sane
before you trust the scheduled task with it.
