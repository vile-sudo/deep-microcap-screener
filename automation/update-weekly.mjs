#!/usr/bin/env node
/**
 * update-weekly.mjs — the whole weekly pass, in order, with a record of what happened.
 *
 *   node update-weekly.mjs              # scan+sweep, profile, stamp
 *   node update-weekly.mjs --no-profile # scan+sweep and stamp only (skips the PDF downloads)
 *   node update-weekly.mjs --stamp-only # only re-stamp, run nothing else
 *
 * Order matters: scan-listings finds what is new/worth a look and writes the
 * queue, profile-company reads that queue and pulls each new listing's
 * prospectus, and only then is backend/data/build_stamp.json stamped -- so
 * the freshness date the dashboard shows means "these steps finished," not
 * "something was tried."
 *
 * What this script does NOT do
 *   It does not decide whether a candidate belongs on the board, and it does
 *   not touch backend/data/companies_raw.json. It fills a queue and reads
 *   prospectuses. The judgement half of the week -- classifying a sweep
 *   candidate's real sector and market-cap band with the Screener/Trendlyne
 *   MCP servers, weighing a claim, deciding a verdict -- is run-weekly.cmd's
 *   second half, using weekly-prompt.md, never this script. And even that
 *   half only ever writes verdicts into the queue; putting a company on the
 *   live board is still something you do by hand, then ship with
 *   publish-candidates.cmd.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { publishCandidates } from './lib/publish.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SKIP_PROFILE = process.argv.includes('--no-profile');
/* The discovery half of update-weekly.mjs runs before run-weekly.cmd's Claude
   Code step, which is what actually assigns verdicts. Stamping before that
   has run would leave the board advertising last week's queue_open count, so
   run-weekly.cmd calls this again with --stamp-only once the judgement step
   has finished writing. */
const STAMP_ONLY = process.argv.includes('--stamp-only');

const DIR = path.join(HERE, 'data');
const BACKEND_DATA = path.resolve(HERE, '..', 'backend', 'data');
const QUEUE = path.join(DIR, 'candidates-queue.json');
const LOG = path.join(DIR, 'last-run.json');
const BUILD_STAMP_OUT = path.join(BACKEND_DATA, 'build_stamp.json');

const stamp = () => new Date().toISOString();
const day = () => stamp().slice(0, 10);
const run = [];

function step(name, fn) {
  const t0 = Date.now();
  process.stdout.write(`\n── ${name}\n`);
  try {
    fn();
    run.push({ step: name, ok: true, seconds: +((Date.now() - t0) / 1000).toFixed(1) });
  } catch (e) {
    /* A failed step is recorded and the pass continues where it safely can.
       A scan that cannot reach NSE should not stop the board being stamped
       with an honest date. */
    run.push({ step: name, ok: false, seconds: +((Date.now() - t0) / 1000).toFixed(1), error: (e.message || String(e)).split('\n')[0] });
    process.stdout.write(`   failed: ${(e.message || e).toString().split('\n')[0]}\n`);
  }
}

/* On a non-zero exit execFileSync throws, and with piped stdio the child's
   output is only on the error object. Printing it just on the happy path
   meant the one run that needed explaining was the one that explained
   nothing. */
const node = (script, args = []) => {
  try {
    process.stdout.write(execFileSync(process.execPath, [path.join(HERE, script), ...args], { encoding: 'utf8', stdio: 'pipe' }));
  } catch (e) {
    if (e.stdout) process.stdout.write(e.stdout);
    if (e.stderr) process.stdout.write(e.stderr);
    throw new Error(`${script} exited ${e.status ?? 'abnormally'}`);
  }
};

if (!STAMP_ONLY) step('scan listings + sweep small/mid-cap universe', () => node('scan-listings.mjs'));

if (!SKIP_PROFILE && !STAMP_ONLY) {
  const open = fs.existsSync(QUEUE)
    ? JSON.parse(fs.readFileSync(QUEUE, 'utf8')).filter(q => q.verdict === null && !q.profile)
    : [];
  if (!open.length) process.stdout.write('   nothing open and unprofiled — nothing to profile\n');
  else step('profile queued new listings', () => node('profile-company.mjs'));
}

/* The dashboard has no clock of its own and no way to fetch one, so the date
   it shows has to be written into it here. Without this the board cannot
   tell a reader that it is stale, which is the one thing a board updated by
   a scheduled job most needs to be able to say. */
step('stamp the board', () => {
  const queue = fs.existsSync(QUEUE) ? JSON.parse(fs.readFileSync(QUEUE, 'utf8')) : [];
  const open = queue.filter(q => q.verdict === null);
  const published = queue.filter(q => q.verdict === null && q.moat_signal === true);
  const profiled = queue.filter(q => q.profile).length;
  const info = { scanned_on: day(), queue_open: open.length, published: published.length, profiles: profiled };
  fs.mkdirSync(BACKEND_DATA, { recursive: true });
  fs.writeFileSync(BUILD_STAMP_OUT, JSON.stringify(info, null, 1));
  /* --stamp-only runs after run-weekly.cmd's Claude Code judgement pass has
     written new moat_signal/verdict values into the queue -- this is what
     makes those verdicts actually reach the dashboard. Without this call the
     board would keep showing whatever candidates/publish.mjs published a
     week ago, no matter what the judgement pass decided. */
  publishCandidates(queue, BACKEND_DATA);
  process.stdout.write(`   scanned ${info.scanned_on} · ${info.queue_open} open in the queue · ${info.published} with moat evidence · ${info.profiles} profiled\n`);
});

fs.mkdirSync(DIR, { recursive: true });
fs.writeFileSync(LOG, JSON.stringify({ finished: stamp(), steps: run }, null, 1));

if (STAMP_ONLY) {
  process.stdout.write('\n  --stamp-only: board re-stamped, nothing else run.\n');
  process.exit(0);
}

const bad = run.filter(s => s.ok === false);
process.stdout.write(`\n${bad.length ? bad.length + ' step(s) failed' : 'all steps completed'} — automation/data/last-run.json\n`);
process.stdout.write('Nothing here was pushed or deployed. Review the queue, get a verdict on\n');
process.stdout.write('anything worth it, then ship with:  publish-candidates.cmd\n');
process.exit(bad.length ? 1 : 0);
