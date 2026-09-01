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

Entries researched on or after 31-Aug-2026 also carry `moat_confirmed`,
`confirm_source` and `confirm_note` — the second-source check described
below. An older entry without those fields simply predates the check.

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
Screener and the company's own filings and disclosures on NSE are the only
way this pipeline can ever find moat evidence.

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
   words, or Screener's numbers) to be the largest, first, or a leading
   manufacturer of something in India.
3. **India market-share holder** — a specific, sourced market-share
   percentage or ranking, not a vague "significant presence."
4. **Only Indian company** — being the sole, or one of a genuine handful,
   of Indian companies making a specific product or serving a specific
   niche — not merely "few listed peers," but few *makers* at all.
5. **Working in a niche segment** — a genuinely specialised, non-commodity
   product or service with few competitors, where scale or a long-lived
   customer relationship (not price) is what wins business.

A moat claim needs a source you can point to: a sentence from an annual
report or investor presentation, a Screener data point, a specific figure or
ranking — not your own impression that the business "sounds like" it might
have one. If you can't point to something concrete, it doesn't clear the
bar, however plausible the sector.

The source has to be the company's own published words or a data point you
actually read — never a search engine's summary of them. A summary saying a
company is "India's largest maker of X" is a lead to check, not evidence: go
to the filing or the company's own page and use what it actually claims. If
the primary source turns out to claim something weaker, record the weaker
claim. Overstated leadership claims are common and they are exactly what
this gate exists to catch.

## The second source: who says it besides the company

Everything above establishes that a claim was *made*. It does not establish
that the claim is *true*, and the company is not a neutral witness about its
own market position. So for any candidate you set `moat_signal: true` on,
go one step further and look for the same claim in a source the company
does not control.

This is not a formality. On 31-Aug-2026 all twenty `moat_signal: true`
candidates were put through this check and **eleven of them failed it** —
several contradicted outright by their own rating agency, one whose entire
moat had been demerged into a different listed company nine months earlier,
one that had been queued as a new listing but had actually listed in 2019.
Every one of those had a real, correctly-quoted company claim behind it.

Where the second source lives, best first:

- **Credit-rating rationales** — CARE/CareEdge, CRISIL, ICRA, Infomerics,
  Brickwork, India Ratings. The single most productive source. An agency
  with its own reputation at stake describes the company's market position
  in its own words, and publishes the negatives next to them. Search
  `"<company>" rating rationale CARE ICRA CRISIL`; the PDF URL usually
  surfaces directly.
- **Regulator and government records** — DGTR anti-dumping and safeguard
  findings (which name the domestic producers by name, and are the
  strongest possible evidence for criteria 1 and 4), RDSO approvals, BIS
  and API licence registers, APEDA export tables.
- **A named industry report** — Frost & Sullivan, CareEdge, Technopak and
  the like, as cited in a prospectus. Weaker, because the issuer commissioned
  it, but it is a third party with a name attached. Say so when you rely on it.
- **Peer disclosures** — a competitor's DRHP or annual report naming this
  company, or capacity figures that let you compare directly.

Two mechanical notes, because they cost a pass otherwise. `WebFetch` cannot
read a PDF — it returns binary and the model will tell you it can't parse
it. Download and convert instead:

```
curl -sSL -A "Mozilla/5.0" -o x.pdf "<url>"
"C:\Program Files\Git\mingw64\bin\pdftotext.exe" -layout x.pdf x.txt
```

Then grep for `leading|largest|market position|market share|only|sole|niche|
import|competit`.

**Read the weaknesses section too — this is the part that matters most.**
A rating rationale has a strengths half and a weaknesses half, and quoting
only the first is how a claim survives a check it should have failed. The
same CARE release that credited Cords Cable with an "established position"
also called the cable industry fragmented with "lower entry barriers"; the
first half had been recorded as moat evidence and the second half never
read. If the independent source both supports and undercuts the claim,
record both and say which way you came down.

## What to do for each open candidate

1. **Look it up.** You have two sources, and both are first-class. Use them
   in this order:

   a. **The Screener MCP server** — for the numbers: market cap, sector,
      promoter holding, the financials. `search_company` on the symbol or
      name is the right first call.

   b. **The company's own filings and disclosures on NSE, and its own
      website** — for what the business actually *is*. The annual report,
      the investor presentation, the corporate-announcement disclosures,
      the "about us" and products pages. This is where moat evidence
      actually lives; Screener will rarely hand you a moat claim.

   Screener is thin-to-empty for companies this small — no business
   description, often empty ratio tables. **That is expected and it is not a
   reason to stop.** When Screener has little, go to (b); it is the primary
   source anyway. A candidate may only be left unresearched if *both* came
   up empty, and the research note must then say what you tried.

   For a `sweep` candidate, this is the *only* research this pipeline does —
   there's no prospectus.

2. **Check the sector guess.** `hint` comes from a regex over the company's
   *name* (see `automation/lib/sectors.mjs`) — it can be wrong in both
   directions. If the real business is obviously not this board's kind of
   company (a finance/NBFC arm with an industrial-sounding name, a trading
   or distribution business, an IT-services shop, real estate) — set
   `"verdict": "ruled_out"` and a one-line `"verdict_reason"` explaining
   what the name guessed and what the company actually is.

   Settle this from the company's own description of itself, not from a
   database's sector label. Sector tags on microcaps are frequently stale or
   plain wrong — a genuine specialty manufacturer can sit filed under
   "Investment Companies." A ruled_out on a wrong sector should rest on
   reading what the company says it does.

3. **Rule out on a missing moat too, not just a wrong sector.** If the
   sector guess was right but you genuinely can't find evidence for any of
   the five criteria after looking — a perfectly ordinary, commodity
   business in a crowded field, with nothing distinctive in its own
   materials or in Screener's data — also set `"verdict":
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

5. **Confirm it against a second source.** For every candidate you just set
   `moat_signal: true` on, run the check described in "The second source"
   above, and record what came back in three fields:

   - `"moat_confirmed"`: `true` if a source the company doesn't control
     supports the claim; `"partial"` if that source confirms the business
     and the niche but stops short of the specific leadership or share
     claim; `false` if no independent source could be found, or if one
     contradicts it.
   - `"confirm_source"`: the document, named precisely enough to re-find —
     agency, entity, rating, date. `"CRISIL Ratings rationale, PPAP
     Automotive Limited (CRISIL A-/Stable), reaffirmed 16 January 2026"`,
     not `"CRISIL"`.
   - `"confirm_note"`: a few sentences quoting what the independent source
     actually says, **including whatever in it cuts the other way**.

   Then act on the answer:

   - `true` or `"partial"` — leave `verdict: null`. It stays open on the
     dashboard queue for a person, which is where it was going anyway; the
     confirmation is what tells them how much weight it carries.
   - `false` because a source **contradicts** the claim — set `"verdict":
     "ruled_out"` with the contradiction quoted in `verdict_reason`. This
     is a third authorized ruled_out case, alongside steps 2 and 3.
   - `false` because you simply **couldn't find** an independent source —
     do *not* rule it out. Absence of corroboration for a microcap is
     usually absence of coverage, not evidence against. Leave
     `moat_signal: true` and `verdict: null`, set `moat_confirmed: false`,
     and say in `confirm_note` exactly what you searched so the next pass
     doesn't repeat it.

   Leave `moat_signal` itself alone in all three cases — it records that a
   sourced claim exists, which is still true. `lib/publish.mjs` gates on
   `moat_signal`, so the dashboard queue keeps working exactly as before;
   `moat_confirmed` is the extra column of judgement, not a new gate.

   One more check while you're here, cheap and it caught a real error:
   **confirm the moat still belongs to this listed entity.** Demergers move
   the good business out from under the ticker. BSE 523369's entire case
   was a rayon tyre cord plant that had been demerged into a different
   listed company nine months before the candidate was queued.

6. **Leave a research note regardless.** Whether you ruled it out, flagged a
   moat, or found nothing, add a `"research_note"` field: 2-4 sentences on
   what the company actually does, its approximate market cap and sector
   (Screener's numbers, not the exchange feed's guess), and what
   you did or didn't find against the five criteria. If you found nothing,
   say so plainly rather than padding the note — a candidate can stay open
   with `moat_signal: null` and an honest "nothing stands out yet" note; it
   simply won't reach the dashboard until a later pass finds something.

7. **Flag, don't promote.** If `moat_signal` is true and the case reads as
   genuinely strong, add `"flag": "promising"`. If the evidence is real but
   thin, `"flag": "unclear"`. If you ruled it out in step 2, 3 or 5 you
   don't need a flag — the verdict already says everything. Reserve
   `"promising"` for a candidate that came back `moat_confirmed: true`; a
   claim nobody outside the company has corroborated is `"unclear"`,
   however well the company puts it.

## What NOT to do

- Don't edit `backend/data/companies_raw.json`, `meta_raw.json`, or anything
  else outside `automation/data/`. This pass only ever writes into the queue.
- Don't set a verdict other than `"ruled_out"`. A candidate you're genuinely
  convinced belongs on the board stays `verdict: null` with `moat_signal:
  true` and `flag: "promising"` — the queue (and now the dashboard) is
  where it waits for a person to decide.
- Don't set `moat_signal: true` without `moat_evidence` naming a real
  source. A hunch is not evidence; leave it `null` instead.
- Don't quote the strengths half of an independent document and skip the
  weaknesses half. If you cite a rating rationale at all, you have read the
  whole thing, and `confirm_note` says what was in both halves.
- Don't count a source as independent when it isn't. A newswire carrying a
  company press release (ANI/PTI syndication, the "press-releases-ani" URL
  path) is the company talking; so is an IIFL/Tracxn/IndiaMART profile
  reproducing the About page. Four outlets running the same release is one
  source, not four.
- Don't rule a candidate out merely because no independent source exists.
  For companies this small that is the normal case; see step 5.
- Don't touch the `profile` field profile-company.mjs already wrote, and
  don't re-run web requests against NSE's *prospectus/offer-document*
  endpoints — that's profile-company.mjs's job, not yours. Reading a
  company's annual report, investor presentation or corporate disclosures
  is a different thing and is exactly what you should be doing.
- Don't guess at numbers. If you genuinely cannot find a figure, say so in
  the research note and leave it out rather than inventing a market cap,
  sector, or moat claim.
- Don't treat a thin or empty Screener record as the end of the research.
  It is normal for companies this size and says nothing about the company —
  go to its filings and its own site. "Screener had nothing on it" is not
  an acceptable reason to leave a candidate unresearched, and a pass that
  leaves a pile of candidates untouched on those grounds has not done its
  job. If a data source is down or rate-limited, note it and research
  through the remaining source; the pass is not blocked by any one server.

## When you're done

1. Write the updated array back to `automation/data/candidates-queue.json`,
   preserving every field you didn't touch.
2. Write `automation/data/weekly-report.md`: a short plain-language summary
   — how many candidates were open, how many you ruled out and why in one
   line each, how many you set `moat_signal: true` for and on what
   evidence, how many still need a person to look because neither Screener
   nor the company's own filings had anything on them.

   Split the `moat_signal: true` names by what the second source said:
   confirmed, partial, contradicted (and therefore ruled out), and no
   independent source found. That split is the most useful thing in the
   report — it's the difference between a claim and a checked claim, and
   it's what tells a person which names are worth their evening.
3. Run `node update-weekly.mjs --stamp-only` (from the `automation/`
   directory) so `backend/data/candidates_raw.json` — which now only
   contains entries with `moat_signal: true` — and
   `backend/data/build_stamp.json` reflect your verdicts before anyone next
   opens the dashboard's queue.
4. Stop there. Publishing (`publish-candidates.cmd`) is a person's decision,
   same as it's always been — nothing here pushes to GitHub or Render.
