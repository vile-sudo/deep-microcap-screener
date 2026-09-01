#!/usr/bin/env node
/**
 * profile-company.mjs — read a new listing's prospectus and write down what
 * it claims.
 *
 *   node profile-company.mjs                 # profile every open candidate that has no profile yet
 *   node profile-company.mjs SYM1 SYM2        # profile named symbols regardless
 *   node profile-company.mjs --keep           # leave the downloaded PDF and text behind
 *
 * Scope: this only ever reads a prospectus, so it only ever has something to
 * do for candidates whose `source` is `new-listing` (see scan-listings.mjs).
 * A small/mid-cap sweep candidate is an already-listed company -- it has no
 * IPO prospectus to fetch, has probably already filed several annual
 * reports, and is exactly what weekly-prompt.md's Claude Code step (with the
 * Screener MCP server and NSE filings) is for instead. Running this against a
 * sweep symbol is harmless -- it will just report "no offer document filed"
 * and move on -- but it will never be where a sweep candidate gets read.
 *
 * Why the prospectus and not the annual report, for a new listing
 *   A company that listed this month has not filed an annual report. NSE's
 *   annual-reports endpoint returns {"data":[]} for a name that just listed
 *   and a full report for one listed years ago -- so for a brand-new
 *   listing, the prospectus is not the second-best source, it is the only
 *   one.
 *
 * What "confirmed" can and cannot mean here
 *   A prospectus is a selling document. Every one of them describes a moat,
 *   because that is what the strengths section is for. So nothing extracted
 *   here is verification of anything: it is a record of what the company
 *   said about itself, under oath and in public, which is exactly what this
 *   pipeline calls `company-stated`. This script never writes `verified`
 *   and has no way to. That needs an outside source and a person.
 *
 *   The section worth the most is not the one the company wrote to impress
 *   you. Customer concentration, promoter litigation and single-plant
 *   exposure appear in Risk Factors because they legally must, so those are
 *   weighted separately below and kept verbatim.
 *
 * Needs pdftotext on PATH (xpdf or poppler). unzip is used when a
 * prospectus is archived.
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { publishCandidates } from './lib/publish.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIR = path.join(HERE, 'data');
const BACKEND_DATA = path.resolve(HERE, '..', 'backend', 'data');
const QUEUE = path.join(DIR, 'candidates-queue.json');
const OUT = path.join(DIR, 'profiles');
const KEEP = process.argv.includes('--keep');
const ARGS = process.argv.slice(2).filter(a => !a.startsWith('--')).map(s => s.toUpperCase());

const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
  'Referer': 'https://www.nseindia.com/',
};

/* Evidence patterns. Two rules learned from running these over a real
   prospectus: a bare /only|sole/ matched 161 times in one document because
   "only" is an ordinary English word, so a monopoly claim has to be
   anchored to a noun; and a hit count on its own is useless to a reader, so
   every match keeps the sentence it came from. */
const CLAIMS = [
  ['accreditation', /\b(NABL|NABH|AERB|CDSCO|USFDA|US ?FDA|WHO-?GMP|EU ?GMP|OECD ?GLP|ANVISA|MHRA|AS ?9100|ISO[ -]?\d{4,5}|BIS\b|CE ?mark|DRDO|BARC)\b/gi],
  ['monopoly_claim', /\b(?:sole|only|largest)\s+(?:\w+\s+){0,3}(?:manufacturer|producer|supplier|exporter|player)\b|\bfirst\s+(?:\w+\s+){0,2}(?:in India|to manufacture|to produce|to develop)\b|\b(?:one|among)\s+(?:of\s+)?the\s+(?:only|few)\s+(?:\w+\s+){0,3}(?:manufacturer|producer|supplier|player|compan)\w*/gi],
  ['import_substitution', /\b(import substitut\w*|indigenis\w*|indigeniz\w*|Make in India|Atmanirbhar|PLI scheme|production linked incentive)\b/gi],
  ['ip', /\b(patent(?:s|ed)?|trademark(?:s|ed)?|proprietary (?:technolog|process|formulation)\w*)\b/gi],
];

/* Of the four CLAIMS buckets, these two are what actually count as moat
   evidence for lib/publish.mjs's gate -- a claim to be the sole/largest/
   first-in-India manufacturer of something, or an explicit import-
   substitution/Make-in-India/PLI story. `accreditation` and `ip` are real
   signals worth showing in the profile, but a certification alone isn't a
   claim to be India's leading manufacturer or a niche near-monopoly, so
   they don't by themselves clear the bar for the live dashboard. */
const MOAT_CLAIM_KEYS = new Set(['monopoly_claim', 'import_substitution']);

/* Disclosed because it must be, which is what makes it worth more than the
   strengths section. */
const RISKS = [
  ['customer_concentration', /\b(?:top (?:five|ten|two|three|5|10|2|3) customers?|customer concentration|depend\w+ on a (?:limited|small) number of customers)\b/gi],
  ['promoter_litigation', /\b(?:criminal (?:proceeding|complaint|case)\w*|proceedings? (?:against|involving) (?:our|the) promoter\w*)\b/gi],
  ['single_site', /\b(?:single (?:manufacturing )?(?:facility|unit|plant)|all of our (?:manufacturing )?operations are (?:located|situated))\b/gi],
  ['related_party', /\b(?:related part(?:y|ies) transactions?)\b/gi],
];

/* Objects of the issue. Fresh capital going into plant is a different
   proposition from promoters selling down, and the prospectus says which in
   plain words. */
const OBJECTS = [
  ['offer_for_sale', /\boffer for sale\b/gi],
  ['fresh_issue', /\bfresh issue\b/gi],
  ['capital_expenditure', /\b(capital expenditure|purchase of (?:plant|machinery|equipment)|setting up of|expansion of our)\b/gi],
  ['working_capital', /\bworking capital requirement\w*/gi],
  ['general_corporate', /\bgeneral corporate purpose\w*/gi],
];

const say = (...a) => console.log(...a);

/* pdftotext is found by name in an interactive shell and not found at all
   by Task Scheduler, which starts with the machine PATH and nothing else.
   On a machine where it ships inside Git for Windows, whose bin directory
   is only ever added to PATH by Git Bash, a scheduled run fails on every
   candidate with spawnSync pdftotext ENOENT while the same command works
   by hand. Resolve it to a real path rather than trusting PATH. */
function findPdftotext() {
  const named = process.env.PDFTOTEXT;
  if (named && fs.existsSync(named)) return named;
  try { execFileSync('pdftotext', ['-v'], { stdio: 'pipe' }); return 'pdftotext'; } catch { /* keep looking */ }
  const guesses = [
    'C:/Program Files/Git/mingw64/bin/pdftotext.exe',
    'C:/Program Files (x86)/Git/mingw64/bin/pdftotext.exe',
    'C:/Program Files/poppler/bin/pdftotext.exe',
    '/usr/bin/pdftotext', '/usr/local/bin/pdftotext', '/opt/homebrew/bin/pdftotext',
  ];
  for (const g of guesses) if (fs.existsSync(g)) return g;
  return null;
}
const PDFTOTEXT = findPdftotext();

/* one sentence around a match, so the output can be read rather than counted */
function contexts(text, re, cap = 3) {
  const out = [], seen = new Set();
  for (const m of text.matchAll(re)) {
    const i = m.index;
    let s = text.lastIndexOf('.', i), e = text.indexOf('.', i + m[0].length);
    s = s < 0 || i - s > 320 ? Math.max(0, i - 160) : s + 1;
    e = e < 0 || e - i > 320 ? Math.min(text.length, i + 200) : e + 1;
    const sent = text.slice(s, e).trim().replace(/\s+/g, ' ');
    const key = sent.slice(0, 60);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ match: m[0].trim(), sentence: sent });
    if (out.length >= cap) break;
  }
  return out;
}

/* Every prospectus carries an industry-overview chapter, usually bought from
   a research agency, and it is thick with the exact words being searched
   for here. Unfiltered it offered "India's position as the second-largest
   producer of steel" as a monopoly claim and "government initiatives such
   as the PLI scheme" as import-substitution evidence: both true about the
   country, neither a claim about the company.

   What separates the two reliably is not position but voice. A company
   describes itself in the first person -- "we are the sole manufacturer",
   "our products replace imports" -- while the industry chapter is written
   about India in the third. So a claim only counts when the sentence
   carrying it also says we, our, or the Company. Risk factors and objects
   are not filtered this way: those sections are about the company by
   construction. */
const FIRST_PERSON = /\b(?:we|our|us|the Company|our Company|the Issuer)\b/i;

function scan(text, spec, { firstPersonOnly = false } = {}) {
  const found = {};
  for (const [key, re] of spec) {
    const all = contexts(text, re, Infinity);
    const kept = firstPersonOnly ? all.filter(c => FIRST_PERSON.test(c.sentence)) : all;
    if (!kept.length) continue;
    found[key] = {
      count: kept.length,
      dropped_as_industry_commentary: firstPersonOnly ? all.length - kept.length : 0,
      terms: [...new Set(kept.map(c => c.match.replace(/\s+/g, ' ')))].slice(0, 8),
      evidence: kept.slice(0, 3),
    };
  }
  return found;
}

/* ------------------------------------------------------------ offer docs */
let OFFERDOCS = null;
async function offerDocs() {
  if (OFFERDOCS) return OFFERDOCS;
  OFFERDOCS = [];
  for (const index of ['equities', 'sme']) {
    const res = await fetch(`https://www.nseindia.com/api/corporates/offerdocs?index=${index}`, { headers: HEADERS });
    if (!res.ok) { say(`  offer-doc feed (${index}): HTTP ${res.status}`); continue; }
    const rows = await res.json();
    rows.forEach(r => OFFERDOCS.push({ ...r, index }));
  }
  return OFFERDOCS;
}

/* Match on the symbol the exchange gives, and only fall back to the company
   name when the feed leaves symbol blank. Name matching alone is not safe:
   searching this feed for "Credent" can return a different company
   entirely. */
function findDoc(rows, sym, name) {
  const s = sym.toUpperCase();
  let hit = rows.find(r => (r.symbol || '').trim().toUpperCase() === s);
  if (!hit && name) {
    const n = name.toLowerCase().replace(/\b(limited|ltd\.?|private|pvt\.?)\b/g, '').trim();
    hit = rows.find(r => (r.company || '').toLowerCase().replace(/\b(limited|ltd\.?|private|pvt\.?)\b/g, '').trim() === n);
  }
  return hit || null;
}

/* Final prospectus first -- it is the filed document rather than the draft,
   and it is more often a plain PDF. The drafts are frequently zipped. */
function pickDocument(d) {
  for (const [field, label] of [['fpAttach', 'final prospectus'], ['rhpAttach', 'red herring prospectus'], ['drhpAttach', 'draft prospectus']]) {
    const url = (d[field] || '').trim();
    if (url && /^https?:/i.test(url)) return { url, label, date: d[field.replace('Attach', 'Date')] || null };
  }
  return null;
}

/* --------------------------------------------------------------- extract */
async function textOf(url, tag) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'prosp-'));
  const res = await fetch(url, { headers: HEADERS });
  if (!res.ok) {
    const e = new Error(`HTTP ${res.status} fetching the prospectus`);
    /* 404/410 mean the exchange stopped serving a document it once listed --
       common for the older drafts. That is the same kind of absence as a
       candidate with no offer document at all, so the caller counts it as a
       skip. Anything else is us failing to read a document that is there. */
    e.goneAtSource = res.status === 404 || res.status === 410;
    throw e;
  }
  const buf = Buffer.from(await res.arrayBuffer());
  const zipped = /\.zip$/i.test(url) || buf.subarray(0, 2).toString('latin1') === 'PK';
  const file = path.join(tmp, zipped ? `${tag}.zip` : `${tag}.pdf`);
  fs.writeFileSync(file, buf);

  let pdf = file;
  if (zipped) {
    try { execFileSync('unzip', ['-qq', '-o', file, '-d', tmp], { stdio: 'pipe' }); }
    catch { throw new Error('prospectus is a zip archive and could not be expanded'); }
    const pdfs = fs.readdirSync(tmp).filter(f => /\.pdf$/i.test(f))
      .map(f => ({ f, size: fs.statSync(path.join(tmp, f)).size })).sort((a, b) => b.size - a.size);
    if (!pdfs.length) throw new Error('zip archive held no PDF');
    pdf = path.join(tmp, pdfs[0].f);
  }

  const txt = pdf.replace(/\.pdf$/i, '.txt');
  execFileSync(PDFTOTEXT, ['-q', pdf, txt], { stdio: 'pipe' });
  const text = fs.readFileSync(txt, 'utf8');
  return { text, bytes: buf.length, dir: tmp };
}

/* Rewrites both the full record (candidates-queue.json) and the published
   view (backend/data/candidates_raw.json, which seed.py reads). Called once
   at the end rather than after every candidate, so a run that profiles ten
   companies writes these files ten times fewer than it could. */
function publish(queue) {
  fs.writeFileSync(QUEUE, JSON.stringify(queue, null, 1));
  publishCandidates(queue, BACKEND_DATA);
}

/* ------------------------------------------------------------------ main */
if (!fs.existsSync(QUEUE) && !ARGS.length) {
  say('No candidates queue yet. Run scan-listings.mjs first, or pass symbols explicitly.');
  process.exit(0);
}
const queue = fs.existsSync(QUEUE) ? JSON.parse(fs.readFileSync(QUEUE, 'utf8')) : [];
const targets = ARGS.length
  ? ARGS.map(s => queue.find(q => q.sym === s)).filter(Boolean)
  : queue.filter(q => q.verdict === null && !q.profile);

if (!targets.length) { say('Nothing open and unprofiled in the queue.'); process.exit(0); }
if (!PDFTOTEXT) {
  say('pdftotext could not be found, so no prospectus can be read.');
  say('Install poppler or xpdf, or set PDFTOTEXT to its full path. Nothing written.');
  process.exit(1);
}
say(`Profiling ${targets.length} candidate${targets.length === 1 ? '' : 's'}.  extractor: ${PDFTOTEXT}\n`);
/* A run where every candidate failed used to print its closing line and
   exit 0 -- failures are counted and returned. A candidate with no offer
   document on file (every sweep candidate, by design -- see the header
   comment) is a skip, not a failure. */
let failed = 0, skipped = 0;

const rows = await offerDocs();
say(`  offer-document feed: ${rows.length} entries\n`);
fs.mkdirSync(OUT, { recursive: true });

for (const t of targets) {
  say(`  ${t.sym}${t.name ? ' — ' + t.name : ''}`);
  const doc = findDoc(rows, t.sym, t.name);
  if (!doc) { skipped++; say('    no offer document filed under this symbol — skipped\n'); continue; }
  const pick = pickDocument(doc);
  if (!pick) { skipped++; say('    entry found but it carries no document link — skipped\n'); continue; }
  say(`    ${pick.label}${pick.date ? ' (' + pick.date + ')' : ''}`);

  let got;
  try { got = await textOf(pick.url, t.sym); }
  catch (e) {
    if (e.goneAtSource) { skipped++; say(`    the exchange no longer serves this document (${e.message}) — skipped\n`); }
    else { failed++; say(`    could not read it: ${e.message}\n`); }
    continue;
  }

  const text = got.text.replace(/\s+/g, ' ');
  const profile = {
    symbol: t.sym,
    company: doc.company || t.name || null,
    isin: doc.isin || t.isin || null,
    board: t.board || (doc.index === 'sme' ? 'SME' : 'NSE'),
    source: { document: pick.label, url: pick.url, filed: pick.date, pdf_bytes: got.bytes, extracted_chars: got.text.length },
    /* the whole point of this field: everything below is what the company
       says about itself, not something that has been checked against
       anybody else */
    grade: 'company-stated',
    verified_against: null,
    claims: scan(text, CLAIMS, { firstPersonOnly: true }),
    risk_factors: scan(text, RISKS),
    objects_of_the_issue: scan(text, OBJECTS),
    profiled_on: new Date().toISOString().slice(0, 10),
  };
  fs.writeFileSync(path.join(OUT, `${t.sym}.json`), JSON.stringify(profile, null, 1));
  if (!KEEP) fs.rmSync(got.dir, { recursive: true, force: true });

  /* Attach the profile to the queue entry in place, so the dashboard's
     queue UI (frontend/static/app.js, #ipoq) can render it without a
     second file to look up. */
  const entry = queue.find(q => q.sym === t.sym);
  if (entry) {
    entry.profile = profile;

    /* This is the only place a new-listing candidate can clear
       lib/publish.mjs's gate without a person or the weekly-prompt.md
       research pass -- and only because the evidence is the company's own
       first-person words in a filed, public document, not this script's
       opinion. A hit on accreditation/ip alone does not set this; see the
       comment on MOAT_CLAIM_KEYS above. */
    const moatHits = Object.entries(profile.claims).filter(([k]) => MOAT_CLAIM_KEYS.has(k));
    if (moatHits.length) {
      entry.moat_signal = true;
      entry.moat_evidence = moatHits.flatMap(([k, v]) =>
        v.evidence.map(e => `${k.replace(/_/g, ' ')}: "${e.sentence}" (prospectus, company-stated)`));
    }
    /* No hit doesn't mean no moat -- a prospectus rarely states a market
       share figure the way a later annual report might. Leave moat_signal as
       scan-listings.mjs set it (null) so weekly-prompt.md still gets a
       chance to find evidence a different way. */
  }

  const line = o => Object.entries(o).map(([k, v]) => `${k} ${v.count}`).join(', ') || 'none';
  say(`    ${(got.bytes / 1048576).toFixed(1)} MB → ${got.text.length.toLocaleString('en-US')} chars`);
  say(`    claims:  ${line(profile.claims)}`);
  say(`    risks:   ${line(profile.risk_factors)}`);
  say(`    objects: ${line(profile.objects_of_the_issue)}`);
  say(`    → automation/data/profiles/${t.sym}.json  (graded company-stated)\n`);
}

publish(queue);
const done = targets.length - failed - skipped;
say(`${done} profiled, ${skipped} skipped, ${failed} failed.`);
say('These are the companies\' own words. Nothing here is verified, and this script cannot verify it.');
process.exit(failed ? 1 : 0);
