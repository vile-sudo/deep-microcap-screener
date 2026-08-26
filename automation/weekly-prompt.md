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
  "moat_signal": null, "moat_evidence": null,
  "seen_on": "2026-08-26", "source": "sweep"
}
```

An entry with `"source": "new-listing"` may also carry a `profile` object —
that's `profile-company.mjs` having already read the IPO prospectus and
extracted company-stated claims, risk factors and objects of the issue. If
that profile already tripped `moat_signal: true` (see `MOAT_CLAIM_KEYS` in
`profile-company.mjs`), the evidence came from the company's own filed
prospectus — treat it as settled, don't re-litigate it, and spend your time
on the entries still sitting at `moat_signal: null`.

An entry with `"source": "sweep"` never carries a `profile`, because a sweep
candidate is an already-listed company with no IPO prospectus to read.
That's exactly why your research step exists for those — for a sweep name,
Screener and Trendlyne are the only way this pipeline can ever find moat
evidence.

Only look at entries where `verdict === null` — anything else has already
been ruled on and isn't yours to touch.

## The bar: this is a moat gate, not a sector filter

`automation/lib/publish.mjs` is what actually puts a candidate in front of
a person on the live dashboard, and it only does that for an entry with
`moat_signal === true`. "Sector plausible" (the `hint`/`plausible` fields
scan-listings.mjs set) was never that bar — it only ever meant "worth a
look." Your job this pass is to find out, for each open candidate, whether
there is real evidence of a moat, using the same five criteria PK asked for
by name:

1. **Import substitute** — the company's product replaces something India
   currently imports, or it explicitly benefits from Make in India / PLI /
   an anti-dumping duty or tariff wall built around that substitution.
2. **India's leading manufacturer** — a stated claim (the company's own
   words, or Screener/Trendlyne's numbers) to be the largest, first, or a
   leading manufacturer of something in India.
3. **India market-share holder** — a specific, sourced market-share
   percentage or ranking, not a vague "significant presence."
4. **Only Indian company** — being the sole, or one of a genuine handful,
   of Indian companies making a specific product or serving a specific
   niche — not merely "few listed peers," but few *makers* at all.
5. **Working in a niche segment** — a genuinely specialised, non-commodity
   product or service with few competitors, where scale or a long-lived
   customer relationship (not price) is what wins business.

A moat claim needs a source you can point to: a sentence from an annual
report or investor presentation, a Screener/Trendlyne data point, a
specific figure or ranking — not your own impression that the business
"sounds like" it might have one. If you can't point to something concrete,
it doesn't clear the bar, however plausible the sector.

## What to do for each open candidate

1. **Look it up.** Use the Screener and Trendlyne MCP servers to pull what's
   actually knowable about the company: business description, sector,
   market cap, promoter holding, and — specifically — anything matching one
   of the five criteria above. `entities search` on the symbol or name is
   usually the right first call on either server. For a `sweep` candidate,
   this is the *only* research this pipeline does — there's no prospectus.

2. **Check the sector guess.** `hint` comes from a regex over the company's
   *name* (see `automation/lib/sectors.mjs`) — it can be wrong in both
   directions. If the real business is obviously not this board's kind of
   company (a finance/NBFC arm with an industrial-sounding name, a trading
   or distribution business, an IT-services shop, real estate) — set
   `"verdict": "ruled_out"` and a one-line `"verdict_reason"` explaining
   what the name guessed and what the company actually is.

3. **Rule out on a missing moat too, not just a wrong sector.** If the
   sector guess was right but you genuinely can't find evidence for any of
   the five criteria after looking — a perfectly ordinary, commodity
   business in a crowded field, with nothing distinctive in its own
   materials or in Screener/Trendlyne's data — also set `"verdict":
   "ruled_out"`, with a `"verdict_reason"` like `"researched — no moat
   evidence found: <one line on why it's a commodity business>"`. This is
   the only other verdict you're authorized to set, alongside the
   sector-mismatch case in step 2. Do not set anything like `"promoted"`,
   `"added"`, or `"verified"` — putting a company on the live board is
   still a person's call, made with the same depth of research the existing
   375 companies got (see the `moat_note` / `pricing_power_note` fields on
   any of them for the bar). Nothing you do here ever touches
   `companies_raw.json`.

4. **Set moat_signal when you find real evidence.** If your research turns
   up a sourced hit on one or more of the five criteria, set
   `"moat_signal": true` and `"moat_evidence"` to an array of short,
   specific strings, each naming the criterion and the source, e.g.
   `"India market-share holder: ~34% share in organised XYZ segment
   (Screener, FY25 annual report)"`. Leave `verdict: null` — a
   `moat_signal: true` entry is exactly what should stay open and reach the
   dashboard queue; it is not itself a promotion to the board.

5. **Leave a research note regardless.** Whether you ruled it out, flagged a
   moat, or found nothing, add a `"research_note"` field: 2-4 sentences on
   what the company actually does, its approximate market cap and sector
   (Screener/Trendlyne's numbers, not the exchange feed's guess), and what
   you did or didn't find against the five criteria. If you found nothing,
   say so plainly rather than padding the note — a candidate can stay open
   with `moat_signal: null` and an honest "nothing stands out yet" note; it
   simply won't reach the dashboard until a later pass finds something.

6. **Flag, don't promote.** If `moat_signal` is true and the case reads as
   genuinely strong, add `"flag": "promising"`. If the evidence is real but
   thin, `"flag": "unclear"`. If you ruled it out in step 2 or 3 you don't
   need a flag — the verdict already says everything.

## What NOT to do

- Don't edit `backend/data/companies_raw.json`, `meta_raw.json`, or anything
  else outside `automation/data/`. This pass only ever writes into the queue.
- Don't set a verdict other than `"ruled_out"`. A candidate you're genuinely
  convinced belongs on the board stays `verdict: null` with `moat_signal:
  true` and `flag: "promising"` — the queue (and now the dashboard) is
  where it waits for a person to decide.
- Don't set `moat_signal: true` without `moat_evidence` naming a real
  source. A hunch is not evidence; leave it `null` instead.
- Don't touch the `profile` field profile-company.mjs already wrote, and
  don't re-run web requests against NSE's prospectus endpoints — that's not
  your job this pass.
- Don't guess at numbers. If Screener/Trendlyne has nothing on a symbol
  (common for a name that just IPO'd on SME and hasn't been indexed yet),
  say that in the research note and leave both `verdict` and `moat_signal`
  as `null` rather than inventing a market cap, sector, or moat claim.

## When you're done

1. Write the updated array back to `automation/data/candidates-queue.json`,
   preserving every field you didn't touch.
2. Write `automation/data/weekly-report.md`: a short plain-language summary
   — how many candidates were open, how many you ruled out and why in one
   line each, how many you set `moat_signal: true` for and on what
   evidence, how many still need a person to look because Screener/Trendlyne
   had nothing on them.
3. Run `node update-weekly.mjs --stamp-only` (from the `automation/`
   directory) so `backend/data/candidates_raw.json` — which now only
   contains entries with `moat_signal: true` — and
   `backend/data/build_stamp.json` reflect your verdicts before anyone next
   opens the dashboard's queue.
4. Stop there. Publishing (`publish-candidates.cmd`) is a person's decision,
   same as it's always been — nothing here pushes to GitHub or Render.
