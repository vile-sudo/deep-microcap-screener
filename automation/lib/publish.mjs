/**
 * lib/publish.mjs — the one gate for what reaches the live dashboard.
 *
 * The working queue (automation/data/candidates-queue.json) holds every
 * candidate this pipeline has ever surfaced -- every sector-plausible sweep
 * name, every new listing, whatever its state of review. That is a private
 * record, never shown anywhere.
 *
 * backend/data/candidates_raw.json is different: it's what seed.py loads
 * into the live app, and what the dashboard's "Candidates queue" button
 * shows you. A candidate reaches it only once there is actual evidence of
 * a moat, not just a plausible sector:
 *
 *   - import substitution (the company says it replaces an import, or
 *     benefits from Make in India / PLI / a tariff wall)
 *   - a stated India market-share, or "largest/leading manufacturer"
 *     claim -- the company's own words, or Screener's numbers
 *   - being the only, or one of very few, Indian companies making
 *     something
 *   - a genuinely niche segment -- few competitors, specialised product,
 *     not a commodity business
 *
 * "Sector plausible" was never that bar. It only ever meant "worth
 * someone's -- or Claude's -- time to look," which is why it decides what
 * gets queued (scan-listings.mjs), never what gets published. Two things
 * can supply the evidence that does: profile-company.mjs, reading an
 * actual prospectus for a monopoly/import-substitution claim in the
 * company's own first-person words; or the weekly-prompt.md judgement
 * pass, researching a sweep candidate via Screener and NSE filings since it
 * has no prospectus to read. Either way, the field this checks is the same:
 * `moat_signal === true`, with `moat_evidence` saying why.
 */
import fs from 'node:fs';
import path from 'node:path';

export function isPublishable(q) {
  return q.verdict === null && q.moat_signal === true;
}

/* Writes backend/data/candidates_raw.json from the full queue, applying the
   gate above. Called from scan-listings.mjs, profile-company.mjs and
   update-weekly.mjs's --stamp-only step -- anywhere the queue changes and
   the published view needs to catch up. */
export function publishCandidates(queue, backendDataDir) {
  fs.mkdirSync(backendDataDir, { recursive: true });
  const out = path.join(backendDataDir, 'candidates_raw.json');
  fs.writeFileSync(out, JSON.stringify(queue.filter(isPublishable), null, 1));
  return out;
}
