#!/usr/bin/env node
/**
 * scan-listings.mjs — find plausible candidates: brand-new listings, plus a
 * weekly slice of the already-listed SME/small-cap/mid-cap universe that
 * isn't on the board yet.
 *
 *   node scan-listings.mjs            # fetch, diff, sweep, update the queue
 *   node scan-listings.mjs --dry      # show what would change, write nothing
 *
 * Two separate jobs live here, because they answer two different questions.
 *
 * Job 1 — new listings (set difference against last week's snapshot)
 *   NSE publishes two master files and BSE one, all carrying every listed
 *   symbol. Newness is decided by set difference against the prior snapshot,
 *   not by the exchange's own DATE OF LISTING column -- that column is the
 *   listing date of the *current series*, not the IPO date, and gets
 *   re-stamped. Automobile Corporation of Goa, Addi Industries and Advik
 *   Capital -- all long-listed -- once all read a 2026 date in the same file
 *   Infosys correctly showed 1995 in. Filtering on that column would call
 *   hundreds of old rows "new" on a re-stamp week. Set difference is immune
 *   to that: a symbol not in last week's snapshot is new, whatever its date
 *   column claims. The first run against a fresh snapshot can report nothing
 *   for exactly this reason -- there is nothing yet to diff against.
 *
 * Job 2 — the small/mid-cap sweep (this board's actual ask)
 *   A screener built to find import-substitution microcaps misses most of
 *   its universe if it only ever looks at this week's IPOs -- the great
 *   majority of small- and mid-cap companies listed years ago and will never
 *   appear in a "new listings" diff. So this also walks BSE's scrip master
 *   (the only one of the three sources carrying a market-cap figure at all),
 *   filters to the small/mid-cap band, and queues names not already on the
 *   board and not already put in front of you before.
 *
 *   "Not already put in front of you" matters: BSE alone lists several
 *   thousand small/mid-cap companies, so a plain diff against the board
 *   would flood the queue once and then repeat forever. reviewed-symbols.json
 *   is a one-way ledger -- once a symbol has been queued, it is never queued
 *   again even if you later ruled it out and it fell out of the open queue.
 *   The sweep is also capped per run (SWEEP_BATCH_MAX) so it arrives as a
 *   readable weekly batch, not a one-time deluge.
 *
 * What this script does NOT do
 *   It does not decide whether a company has a moat or replaces an import --
 *   neither fact exists in any exchange feed, and a sector guessed from a
 *   name is not evidence of one. So nothing this script queues reaches the
 *   live dashboard by itself: every entry is written with `moat_signal:
 *   null` (not yet evidenced), and lib/publish.mjs's gate keeps it out of
 *   backend/data/candidates_raw.json until profile-company.mjs finds a
 *   monopoly/import-substitution claim in an actual prospectus, or the
 *   weekly-prompt.md judgement pass finds real evidence via Screener/
 *   Trendlyne and sets moat_signal itself. Sector-plausible is this
 *   script's whole vocabulary -- it only ever means "worth a closer look."
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PLAUSIBLE, hint } from './lib/sectors.mjs';
import { publishCandidates } from './lib/publish.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DRY = process.argv.includes('--dry');

const DIR = path.join(HERE, 'data');                          // this pipeline's own record
const BACKEND_DATA = path.resolve(HERE, '..', 'backend', 'data'); // what the live app reads
const SNAP = path.join(DIR, 'listings-snapshot.json');
const QUEUE = path.join(DIR, 'candidates-queue.json');          // full history, all verdicts
const REVIEWED = path.join(DIR, 'reviewed-symbols.json');       // one-way "already surfaced" ledger
const EXISTING_BOARD = path.join(BACKEND_DATA, 'companies_raw.json');

/* Rough SEBI-style bands in crore. These are a starting point, not a
   definition handed down from anywhere -- adjust freely. The point of
   having them as named constants up top is that "why 20000?" should be
   answerable by editing one line, not by re-deriving the sweep logic. */
const SMALL_CAP_MIN_CR = 300;
const SMALL_CAP_MAX_CR = 5000;
const MID_CAP_MAX_CR = 20000;

/* How many never-before-queued sweep names to add in one run. The SME/IPO
   side of the queue runs at two or three a week naturally; the sweep could
   add thousands in one pass if uncapped, which stops being a queue you can
   review and starts being a second dataset to reconcile. */
const SWEEP_BATCH_MAX = 25;

const SOURCES = [
  { board: 'SME', kind: 'nse-csv', url: 'https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv' },
  { board: 'NSE', kind: 'nse-csv', url: 'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv' },
  /* BSE was the blind spot for job 1 and is the *only* source for job 2: it
     is the sole feed of the three that carries a market-cap figure at all.
     Its scrip master has no listing-date column, which costs nothing here --
     newness has never been decided by date. Groups M, MT, MS are its SME
     platform. */
  { board: 'BSE', kind: 'bse-json', url: 'https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active' },
];
const BSE_SME_GROUPS = new Set(['M', 'MT', 'MS']);

/* The archives host serves these to a plain client, but it wants to look
   like a browser came from the NSE site. Without the referer it
   intermittently 403s. */
const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
  'Referer': 'https://www.nseindia.com/',
  'Accept': 'text/csv,*/*',
};

const say = (...a) => console.log(...a);
const today = () => new Date().toISOString().slice(0, 10);
const loadJSON = (p, fallback) => (fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : fallback);

/* ------------------------------------------------------------------ fetch */
async function pull({ board, kind, url }) {
  const headers = kind === 'bse-json'
    ? { ...HEADERS, Referer: 'https://www.bseindia.com/', Origin: 'https://www.bseindia.com', Accept: 'application/json' }
    : HEADERS;
  const res = await fetch(url, { headers });
  if (!res.ok) throw new Error(`${board}: HTTP ${res.status} from ${url}`);

  if (kind === 'bse-json') {
    const rows = await res.json();
    if (!Array.isArray(rows) || rows.length < 100)
      throw new Error(`${board}: got ${Array.isArray(rows) ? rows.length : 'a non-array'} — treating as a bad fetch`);
    return rows.map(r => ({
      sym: String(r.SCRIP_CD || '').trim(),
      name: (r.Scrip_Name || '').trim(),
      listed: '',
      isin: (r.ISIN_NUMBER || '').trim().toUpperCase(),
      mktcap_cr: Number.isFinite(+r.Mktcap) && +r.Mktcap > 0 ? +r.Mktcap : null,
      url: (r.NSURL || '').trim() || null,
      board: BSE_SME_GROUPS.has((r.GROUP || '').trim()) ? 'BSE-SME' : 'BSE',
    })).filter(r => r.sym && r.name);
  }

  const text = await res.text();
  const lines = text.split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) throw new Error(`${board}: file came back with ${lines.length} lines — treating as a bad fetch`);
  return lines.slice(1).map(l => {
    const c = l.split(',').map(x => (x || '').trim());
    return {
      sym: (c[0] || '').toUpperCase(), name: c[1] || '', listed: c[3] || '',
      isin: (c.find(x => /^IN[0-9A-Z]{10}$/i.test(x)) || '').toUpperCase(),
      board,
    };
  }).filter(r => r.sym);
}

/* ------------------------------------------------------------------- main */
const rows = [];
for (const src of SOURCES) {
  const got = await pull(src);
  const by = {};
  got.forEach(r => { by[r.board] = (by[r.board] || 0) + 1; });
  say(`  ${src.board.padEnd(4)} ${String(got.length).padStart(5)} symbols  ${Object.entries(by).map(([k, v]) => k + ' ' + v).join(', ')}`);
  rows.push(...got);
}

const keyOf = r => `${r.board.startsWith('BSE') ? 'BSE' : 'NSE'}:${r.sym}`;
const seen = new Map(rows.map(r => [keyOf(r), r]));

/* ------------------------------------------------------- job 1: new listings */
const snapPrior = loadJSON(SNAP, null);
let fresh = [];
if (!snapPrior) {
  say(`\n  No snapshot yet, so nothing can be called new. Writing the baseline: ${seen.size} symbols recorded.`);
} else {
  const prior = new Set(snapPrior.symbols || []);
  const currentSources = [...new Set(rows.map(r => (r.board.startsWith('BSE') ? 'BSE' : 'NSE')))];
  const legacy = !Array.isArray(snapPrior.sources);
  const priorSources = new Set(snapPrior.sources || []);
  const newSources = legacy ? currentSources : currentSources.filter(s => !priorSources.has(s));
  if (legacy) say('\n  snapshot predates source tracking — re-baselining once');
  else if (newSources.length) say(`\n  ${newSources.join(', ')} added to the scan — baselining it rather than calling every company new`);

  fresh = [...seen.entries()]
    .filter(([k]) => !prior.has(k) && !newSources.includes(k.split(':')[0]))
    .map(([, r]) => r);
}

const byIsin = new Set();
const dedupedFresh = fresh.filter(r => {
  if (!r.isin) return true;
  if (byIsin.has(r.isin)) return false;
  byIsin.add(r.isin);
  return true;
});

const SME_BOARDS = new Set(['SME', 'BSE-SME']);
const newlyQueued = dedupedFresh.map(r => {
  const h = hint(r.name);
  const known = h ? PLAUSIBLE.has(h) : null;
  const keep = known === true || (known === null && SME_BOARDS.has(r.board));
  return {
    sym: r.sym, name: r.name, board: r.board, isin: r.isin || null, listed: r.listed || null,
    hint: h, why: known === true ? 'sector plausible' : known === null ? 'sector unknown, SME platform' : 'sector ruled out',
    plausible: keep, verdict: null, seen_on: today(), source: 'new-listing',
    /* Not evidence of a moat -- just a sector guess. Stays null (not yet
       evidenced) until profile-company.mjs finds a monopoly/import-
       substitution claim in the prospectus, or weekly-prompt.md's research
       finds one. See lib/publish.mjs: only moat_signal === true ever
       reaches the live dashboard. */
    moat_signal: null, moat_evidence: null,
  };
});

say(`\n  new symbols since ${snapPrior ? snapPrior.taken : 'n/a'}: ${fresh.length}${
  fresh.length - dedupedFresh.length ? ` (${fresh.length - dedupedFresh.length} the same company on both exchanges, counted once)` : ''}`);
say(`  of those, a sector where import substitution is even possible: ${newlyQueued.filter(q => q.plausible).length}`);

/* --------------------------------------------------- job 2: the sweep */
const board = loadJSON(EXISTING_BOARD, []);
const onBoard = new Set(board.map(c => String(c.code || '').toUpperCase()));
const reviewed = new Set(loadJSON(REVIEWED, []));

const bseUniverse = rows.filter(r => r.board === 'BSE' || r.board === 'BSE-SME');
const swept = bseUniverse.filter(r => {
  if (onBoard.has(r.sym.toUpperCase())) return false;   // already on the live board
  if (reviewed.has(r.sym)) return false;                // already surfaced at some point, don't repeat
  if (r.mktcap_cr == null) return false;                // no market cap figure, can't band it
  if (r.mktcap_cr < SMALL_CAP_MIN_CR || r.mktcap_cr > MID_CAP_MAX_CR) return false;
  const h = hint(r.name);
  return h && PLAUSIBLE.has(h);                          // mainboard-scale universe needs a known sector
}).sort((a, b) => a.mktcap_cr - b.mktcap_cr)             // smaller first -- closer to this board's usual range
  .slice(0, SWEEP_BATCH_MAX)
  .map(r => ({
    sym: r.sym, name: r.name, board: r.board, isin: r.isin || null, listed: null,
    hint: hint(r.name), why: r.mktcap_cr <= SMALL_CAP_MAX_CR ? 'small-cap sweep' : 'mid-cap sweep',
    mktcap_cr: r.mktcap_cr, plausible: true, verdict: null, seen_on: today(), source: 'sweep',
    /* A sweep candidate has no prospectus, so this can only ever be set by
       weekly-prompt.md's research -- never by this script. Stays private
       (see lib/publish.mjs) until that research finds real evidence. */
    moat_signal: null, moat_evidence: null,
  }));

say(`\n  small/mid-cap sweep of already-listed BSE names: ${swept.length} added this run` +
  ` (band ₹${SMALL_CAP_MIN_CR}–${MID_CAP_MAX_CR} cr, cap ${SWEEP_BATCH_MAX}/run, already-on-board and already-seen names excluded)`);
for (const q of [...newlyQueued.filter(q => q.plausible), ...swept].slice(0, 15))
  say(`    ${q.board.padEnd(7)} ${q.sym.padEnd(13)} ${q.name.slice(0, 40).padEnd(41)} ${q.hint || ''}${q.mktcap_cr ? '  ₹' + q.mktcap_cr + 'cr' : ''}`);

/* -------------------------------------------------------------- write out */
if (DRY) {
  say('\n  --dry: nothing written.');
  process.exit(0);
}

fs.mkdirSync(DIR, { recursive: true });
fs.mkdirSync(BACKEND_DATA, { recursive: true });

/* candidates-queue.json is the full record: every candidate ever queued,
   whatever its verdict. Re-running never drops a row that already has a
   verdict or a profile attached -- only the open (verdict === null) set is
   what a fresh sweep or scan can add to. */
const held = loadJSON(QUEUE, []);
const knownSyms = new Set(held.map(q => q.sym));
const toAdd = [...newlyQueued, ...swept].filter(q => !knownSyms.has(q.sym));
const queue = [...held, ...toAdd];
fs.writeFileSync(QUEUE, JSON.stringify(queue, null, 1));

const newlySeen = toAdd.map(q => q.sym);
if (newlySeen.length) fs.writeFileSync(REVIEWED, JSON.stringify([...reviewed, ...newlySeen].sort(), null, 0));

fs.writeFileSync(SNAP, JSON.stringify({
  taken: today(),
  sources: [...new Set(rows.map(r => (r.board.startsWith('BSE') ? 'BSE' : 'NSE')))],
  symbols: [...seen.keys()].sort(),
}, null, 0));

/* candidates_raw.json is the published view -- only entries with actual
   moat evidence (see lib/publish.mjs). A plain scan+sweep run essentially
   never adds to it directly: nothing here has been researched yet. It's
   republished anyway so a verdict/moat_signal written by an earlier
   weekly-prompt.md run (still sitting in the queue) doesn't regress. */
const publishedPath = publishCandidates(queue, BACKEND_DATA);
const open = queue.filter(q => q.verdict === null);
const published = queue.filter(q => q.verdict === null && q.moat_signal === true);

say(`\n  queue: ${held.length} held + ${toAdd.length} added → ${open.length} open, ${published.length} with moat evidence → ${path.relative(process.cwd(), publishedPath)}`);
