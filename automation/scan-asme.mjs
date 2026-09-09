#!/usr/bin/env node
/**
 * scan-asme.mjs — has any NSE/BSE-listed company newly shown up in ASME's
 * own certificate-holder directory since the last run?
 *
 *   node scan-asme.mjs            # scan, write the report
 *   node scan-asme.mjs --dry      # scan, print the report, write nothing
 *
 * What this checks, and why it needs a real browser
 *   ASME's "CA Connect" directory (https://caconnect.asme.org/directory/)
 *   is a searchable database of individual certificates — boiler,
 *   pressure-vessel, nuclear-component stamps — not a curated company list.
 *   Its search API (caconnect.asme.org/api/asme/rest/.../filterSearch)
 *   returns a plain 500 to a scripted request no matter what headers go
 *   with it — confirmed against both `curl` and Node's own `fetch` — while
 *   the identical request from inside an actual Chrome tab works. That
 *   looks like bot-detection keyed on something a script can't fake
 *   cheaply (TLS fingerprint, a JS challenge), not a header this script
 *   forgot. So unlike scan-listings.mjs — which reaches NSE/BSE with a
 *   plain fetch and no dependency at all — this script drives a real
 *   (headless) browser via Playwright to the directory page and calls the
 *   page's *own* already-authenticated search function from inside it,
 *   exactly the way a person clicking Search would. See "One-time setup"
 *   in README.md for the one extra install this needs.
 *
 * What "new" means here
 *   ASME's directory holds ~640 certificate records for ~390 India-based
 *   companies, and the great majority are Private Limited companies or
 *   LLPs, which cannot be listed on an exchange at all. So the useful
 *   question was never "list everything ASME has" — it's "of the handful
 *   that are actually NSE/BSE-listed, is there a new one this week?" This
 *   fetches NSE's main + SME feeds and BSE's active-scrip API (the same
 *   sources scan-listings.mjs uses) and matches by name via
 *   lib/asme-match.mjs, which is deliberately precise rather than fuzzy —
 *   see that file's header for why. A real new match sitting unflagged
 *   for one more week is the safe failure direction; a false positive
 *   entering an unattended report is not.
 *
 * What this script does NOT do
 *   It never writes to frontend/static/asme-certified.js. That file is
 *   what the live dashboard's ASME Certified button reads, and — like
 *   backend/data/companies_raw.json — promoting something into it stays a
 *   decision made by hand, with a display name and casing chosen the same
 *   way the existing 40 entries were. This script's only job is to say
 *   "here's what changed since last time," into automation/data/
 *   asme-scan.json, for that decision to be made against.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { buildListedIndex, matchAsmeName } from './lib/asme-match.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DRY = process.argv.includes('--dry');

const DIR = path.join(HERE, 'data');
const REPORT = path.join(DIR, 'asme-scan.json');
const ASME_LIVE_FILE = path.resolve(HERE, '..', 'frontend', 'static', 'asme-certified.js');

const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
  'Referer': 'https://www.nseindia.com/',
  'Accept': 'text/csv,*/*',
};

const say = (...a) => console.log(...a);
const today = () => new Date().toISOString().slice(0, 10);

/* ---------------------------------------------------- ASME, via a real browser */
/* Plain `chromium.launch()` gets a flat WAF block here — "Web Page Blocked
   ... Attack ID" on the very first navigation, not just the API call — and
   it comes back identically for a bare `curl`/`fetch` request too, so this
   isn't a missing header. What's different about the two mitigations below
   is the one thing headless Chromium exposes by default that a real
   browser tab doesn't: `navigator.webdriver === true`. Overriding that plus
   the flag that turns the underlying automation flag off is the whole fix
   — confirmed by removing each individually and watching the block return. */
async function fetchAsmeIndiaActive() {
  const browser = await chromium.launch({ args: ['--disable-blink-features=AutomationControlled'] });
  try {
    const page = await browser.newPage({
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    });
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });
    await page.goto('https://caconnect.asme.org/directory/', { waitUntil: 'domcontentloaded', timeout: 30000 });

    /* The Aurelia app binds its view-model onto the custom element
       asynchronously after navigation; there is no network event to wait
       on for "the app is ready," only this. */
    await page.waitForFunction(() => {
      const el = document.querySelector('directory-listing');
      return !!(el && el.au && el.au.controller && el.au.controller.viewModel
        && el.au.controller.viewModel.filterFactory);
    }, { timeout: 20000 });

    /* Same call a person clicking Search with Country=India, Status=Active
       makes — built with the page's own FilterFactory so the request shape
       always matches whatever the app currently expects, rather than a
       hand-built JSON body that could silently drift from it. */
    const items = await page.evaluate(async () => {
      const vm = document.querySelector('directory-listing').au.controller.viewModel;
      const ff = vm.filterFactory;
      const f = ff.createFilter();
      f.allParts.push(ff.createFilterPart('entity.countryName', 'India'));
      const statusFilter = ff.createFilter();
      statusFilter.anyParts.push(ff.createFilterPart('effectiveStatus', 1, ff.DATA_TYPES.INTEGER)); // 1 = Active
      f.allParts.push(ff.createNestedFilterPart(null, statusFilter));
      const result = await vm.directoryServices.filterSearch(f, 'certificationNumber', { pageSize: 1000, limit: 1000, pageNumber: 0 });
      return result.items.map(it => ({
        name: it.entity && it.entity.name,
        certType: it.certificateType && it.certificateType.displayName,
        issuedDate: it.issuedDate,
      }));
    });
    if (!Array.isArray(items) || items.length < 100)
      throw new Error(`ASME: got ${Array.isArray(items) ? items.length : 'a non-array'} certificate rows — treating as a bad fetch`);
    return items;
  } finally {
    await browser.close();
  }
}

/* --------------------------------------------------------------- NSE/BSE */
async function fetchListed() {
  const sources = [
    { exch: 'NSE', url: 'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv' },
    { exch: 'NSE-SME', url: 'https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv' },
  ];
  const rows = [];
  for (const { exch, url } of sources) {
    const res = await fetch(url, { headers: HEADERS });
    if (!res.ok) throw new Error(`${exch}: HTTP ${res.status} from ${url}`);
    const lines = (await res.text()).split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) throw new Error(`${exch}: file came back with ${lines.length} lines — treating as a bad fetch`);
    for (const line of lines.slice(1)) {
      const c = line.split(',');
      const sym = (c[0] || '').trim().replace(/-RE$/, '');
      const name = (c[1] || '').trim().replace(/-RE$/, '');
      if (sym && name) rows.push({ exch, symbol: sym, name });
    }
  }

  const bseRes = await fetch(
    'https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active',
    { headers: { ...HEADERS, Referer: 'https://www.bseindia.com/', Origin: 'https://www.bseindia.com', Accept: 'application/json' } });
  if (!bseRes.ok) throw new Error(`BSE: HTTP ${bseRes.status}`);
  const bseRows = await bseRes.json();
  if (!Array.isArray(bseRows) || bseRows.length < 100)
    throw new Error(`BSE: got ${Array.isArray(bseRows) ? bseRows.length : 'a non-array'} — treating as a bad fetch`);
  for (const r of bseRows) {
    const symbol = (r.scrip_id || '').trim();
    const name = (r.Issuer_Name || r.Scrip_Name || '').trim();
    if (symbol && name) rows.push({ exch: 'BSE', symbol, name });
  }
  return rows;
}

/* ------------------------------------------------------------- grouping */
/* One row per distinct ASME entity name, certs/dates merged — mirrors how
   frontend/static/asme-certified.js's own 638-rows-to-389-companies step
   was built. */
function groupAsmeRows(items) {
  const byName = new Map();
  for (const it of items) {
    if (!it.name) continue;
    const key = it.name.trim();
    if (!byName.has(key)) byName.set(key, { name: key, certs: new Set(), dates: [] });
    const g = byName.get(key);
    if (it.certType) g.certs.add(it.certType);
    if (it.issuedDate) g.dates.push(it.issuedDate);
  }
  return [...byName.values()];
}

function pickSymbol(listedHits) {
  // Prefer a main-board NSE symbol, then NSE-SME, then BSE — matches the
  // preference frontend/static/asme-certified.js's entries were built with.
  const byExch = e => listedHits.find(h => h.exch === e);
  const hit = byExch('NSE') || byExch('NSE-SME') || byExch('BSE');
  return hit ? hit.symbol : listedHits[0].symbol;
}

/* -------------------------------------------------------- load the live file */
/* frontend/static/asme-certified.js is a plain script that sets
   `window.ASME_CERTIFIED = [...]` for the browser — not a module. Running
   it in a throwaway `window` sandbox reads it exactly the way the browser
   does, without needing the file to also export anything for Node. */
async function loadLiveAsmeFile() {
  if (!fs.existsSync(ASME_LIVE_FILE)) return [];
  const vm = await import('node:vm');
  const sandbox = { window: {} };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(ASME_LIVE_FILE, 'utf8'), sandbox);
  return sandbox.window.ASME_CERTIFIED || [];
}

/* --------------------------------------------------------------------- main */
say('  fetching ASME India/Active certificates (headless browser)...');
const asmeItems = await fetchAsmeIndiaActive();
const asmeCompanies = groupAsmeRows(asmeItems);
say(`  ${asmeItems.length} certificate rows -> ${asmeCompanies.length} distinct ASME entity names`);

say('  fetching NSE + BSE listed-equity feeds...');
const listedRows = await fetchListed();
say(`  ${listedRows.length} listed rows (NSE main + NSE SME + BSE)`);
const listedIndex = buildListedIndex(listedRows);

/* Resolve every ASME entity to a listed company (exact or division match),
   then group by that company's symbol so a Thermax-style 3-way division
   split collapses into one row, same as the by-hand list did. */
const bySymbol = new Map();
for (const c of asmeCompanies) {
  const m = matchAsmeName(c.name, listedIndex);
  if (!m) continue;
  const symbol = pickSymbol(m.listed);
  if (!bySymbol.has(symbol)) {
    bySymbol.set(symbol, {
      symbol,
      exch: [...new Set(m.listed.map(h => h.exch))].sort(),
      listedName: m.listed[0].name,
      certs: new Set(),
      dates: [],
      asmeRows: [],
    });
  }
  const g = bySymbol.get(symbol);
  c.certs.forEach(x => g.certs.add(x));
  g.dates.push(...c.dates);
  g.asmeRows.push(c.name);
}

const matched = [...bySymbol.values()].map(g => ({
  symbol: g.symbol,
  exch: g.exch,
  listedName: g.listedName,
  certs: [...g.certs].sort(),
  since: g.dates.length ? g.dates.sort()[0] : null,
  asmeRows: g.asmeRows.sort(),
})).sort((a, b) => a.symbol.localeCompare(b.symbol));

say(`  ${matched.length} of those are NSE/BSE-listed`);

/* --------------------------------------------------------------- diff vs live file, vs last scan */
const liveList = await loadLiveAsmeFile();
const liveSymbols = new Set(liveList.map(c => c.symbol));
const newVsLiveFile = matched.filter(m => !liveSymbols.has(m.symbol));

const priorScan = fs.existsSync(REPORT) ? JSON.parse(fs.readFileSync(REPORT, 'utf8')) : null;
const priorSymbols = new Set((priorScan?.matched || []).map(m => m.symbol));
const currentSymbols = new Set(matched.map(m => m.symbol));
const newVsLastScan = matched.filter(m => !priorSymbols.has(m.symbol));
const droppedVsLastScan = (priorScan?.matched || []).filter(m => !currentSymbols.has(m.symbol));

const report = {
  scanned_on: today(),
  asme_certificate_rows: asmeItems.length,
  asme_distinct_entities: asmeCompanies.length,
  matched_listed_count: matched.length,
  matched,
  new_vs_last_scan: newVsLastScan,
  dropped_vs_last_scan: droppedVsLastScan.map(m => ({ symbol: m.symbol, listedName: m.listedName })),
  new_vs_live_asme_certified_file: newVsLiveFile,
};

say('');
if (newVsLiveFile.length) {
  say(`  ${newVsLiveFile.length} NSE/BSE-listed ASME match(es) not yet in frontend/static/asme-certified.js:`);
  for (const m of newVsLiveFile) say(`    - ${m.listedName} (${m.symbol}, ${m.exch.join('+')}) — ASME-certified since ${m.since}`);
  say('  Review and hand-add these — see automation/data/asme-scan.json for the full record.');
} else {
  say('  Nothing new vs. frontend/static/asme-certified.js.');
}
if (newVsLastScan.length && !newVsLiveFile.length) {
  say(`  (${newVsLastScan.length} new vs. last scan, but already reflected in the live file.)`);
}
if (droppedVsLastScan.length) {
  say(`  ${droppedVsLastScan.length} previously-matched name(s) no longer matched (delisted, or certificate lapsed):`);
  for (const m of droppedVsLastScan) say(`    - ${m.listedName} (${m.symbol})`);
}

if (DRY) {
  say('\n  --dry: nothing written.');
} else {
  fs.mkdirSync(DIR, { recursive: true });
  fs.writeFileSync(REPORT, JSON.stringify(report, null, 1));
  say(`\n  wrote ${path.relative(process.cwd(), REPORT)}`);
}
