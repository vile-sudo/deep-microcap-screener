# Weekly discovery — judgement pass

You are running unattended, once a week, right after `update-weekly.mjs` has
finished its mechanical half (scan NSE/BSE, sweep the small/mid-cap
universe, read any new-listing prospectuses). Nobody is at a keyboard to
answer a question, so if something is genuinely ambiguous, write down the
ambiguity in the candidate's own record rather than guessing past it.

## What you're picking up

Read `automation/data/candidates-queue.json`. It's a flat array; each entry
looks roughly like:

```json
{
  "sym": "SOMECO", "name": "Some Company Limited", "board": "BSE",
  "hint": "precision engineering", "why": "small-cap sweep",
  "mktcap_cr": 1450, "plausible": true, "verdict": null,
  "seen_on": "2026-08-26", "source": "sweep"
}
```

An entry with `"source": "new-listing"` may also carry a `profile` object —
that's `profile-company.mjs` having already read the IPO prospectus and
extracted company-stated claims, risk factors and objects of the issue. An
entry with `"source": "sweep"` never will, because a sweep candidate is an
already-listed company with no IPO prospectus to read; that's exactly why
your research step exists for those.

Only look at entries where `verdict === null` — anything else has already
been ruled on and isn't yours to touch.

## What to do for each open candidate

1. **Look it up.** Use the Screener and Trendlyne MCP servers to pull what's
   actually knowable about the company: business description, sector,
   market cap, promoter holding, and anything resembling a moat or an
   import-substitution story. `entities search` on the symbol or name is
   usually the right first call on either server. For a `sweep` candidate,
   this is the *only* research this pipeline does — there's no prospectus.

2. **Check the sector guess.** `hint` comes from a regex over the company's
   *name* (see `automation/lib/sectors.mjs`) — it can be wrong in both
   directions. If the real business is obviously not this board's kind of
   company (a finance/NBFC arm with an industrial-sounding name, a trading
   or distribution business, an IT-services shop, real estate) — set
   `"verdict": "ruled_out"` and a one-line `"verdict_reason"` explaining
   what the name guessed and what the company actually is. This is the
   *only* verdict you're authorized to set. Do not set anything like
   `"promoted"`, `"added"`, or `"verified"` — that decision, and the actual
   edit to `backend/data/companies_raw.json`, is a person's call, made with
   the same depth of research the existing 375 companies got (see the
   `moat_note` / `pricing_power_note` fields on any of them for the bar).
   Nothing you do here ever touches that file.

3. **Leave a research note.** Whether you ruled it out or not, add a
   `"research_note"` field: 2-4 sentences on what the company actually does,
   its approximate market cap and sector (Screener/Trendlyne's numbers, not
   the exchange feed's guess), and — only if you found one — a specific,
   named reason a moat or import-substitution case might exist (a
   certification, a stated export share, a customer-concentration risk that
   cuts the other way). If you found nothing suggesting either, say so
   plainly rather than padding the note; a candidate can stay open with an
   honest "nothing stands out yet" note.

4. **Flag, don't promote.** If the research note reads as genuinely
   promising, add `"flag": "promising"`. If it's a coin flip, `"flag":
   "unclear"`. If you ruled it out in step 2 you don't need a flag — the
   verdict already says everything.

## What NOT to do

- Don't edit `backend/data/companies_raw.json`, `meta_raw.json`, or anything
  else outside `automation/data/`. This pass only ever writes into the queue.
- Don't set a verdict other than `"ruled_out"`. A candidate you're genuinely
  convinced belongs on the board stays `verdict: null` with `flag:
  "promising"` — the queue is where it waits for a person.
- Don't touch the `profile` field profile-company.mjs already wrote, and
  don't re-run web requests against NSE's prospectus endpoints — that's not
  your job this pass.
- Don't guess at numbers. If Screener/Trendlyne has nothing on a symbol
  (common for a name that just IPO'd on SME and hasn't been indexed yet),
  say that in the research note and leave it open rather than inventing a
  market cap or sector.

## When you're done

1. Write the updated array back to `automation/data/candidates-queue.json`,
   preserving every field you didn't touch.
2. Write `automation/data/weekly-report.md`: a short plain-language summary
   — how many candidates were open, how many you ruled out and why in one
   line each, how many you flagged promising, how many still need a person
   to look because Screener/Trendlyne had nothing on them.
3. Run `node update-weekly.mjs --stamp-only` (from the `automation/`
   directory) so `backend/data/candidates_raw.json` and
   `backend/data/build_stamp.json` reflect your verdicts before anyone next
   opens the dashboard's queue.
4. Stop there. Publishing (`publish-candidates.cmd`) is a person's decision,
   same as it's always been — nothing here pushes to GitHub or Render.
