/**
 * node lib/asme-match.test.mjs
 *
 * Regression test built from the by-hand cross-check that produced
 * frontend/static/asme-certified.js — the real matches it found (must
 * still match), the division rows it merged (must still merge, into the
 * right parent), and the specific false positives the fuzzy passes threw
 * up that this module's precise-only approach must keep rejecting.
 */
import assert from 'node:assert/strict';
import { normalizeName, buildListedIndex, matchAsmeName } from './asme-match.mjs';

let failed = 0;
const check = (label, fn) => {
  try { fn(); console.log(`  ok    ${label}`); }
  catch (e) { failed++; console.log(`  FAIL  ${label}\n        ${e.message.split('\n')[0]}`); }
};

const LISTED = buildListedIndex([
  { name: 'Ador Welding Limited', exch: 'NSE', symbol: 'ADOR' },
  { name: 'Ador Welding Ltd.', exch: 'BSE', symbol: 'ADOR' },
  { name: 'Patels Airtemp (I) Ltd.', exch: 'BSE', symbol: 'PATELSAI' },
  { name: 'KCP Limited', exch: 'NSE', symbol: 'KCP' },
  { name: 'K.C.P. LTD', exch: 'BSE', symbol: 'KCP' },
  { name: 'Tata Steel Limited', exch: 'NSE', symbol: 'TATASTEEL' },
  { name: 'DEE Development Engineers Limited', exch: 'NSE', symbol: 'DEEDEV' },
  { name: 'Thermax Limited', exch: 'NSE', symbol: 'THERMAX' },
  { name: 'Symbiotec Pharmalab Limited', exch: 'NSE', symbol: 'SYMBIOTEC' },
  { name: 'Avadh Sugar & Energy Limited', exch: 'NSE', symbol: 'AVADHSUGAR' },
]);

console.log('real matches (must still match):');
check('exact match, different legal-suffix spelling', () => {
  const m = matchAsmeName('ADOR WELDING LIMITED', LISTED);
  assert.ok(m && m.via === 'exact');
  assert.equal(m.listed.length, 2); // both the NSE and BSE row
});
check('"(I)" vs "India" — the miss the first pass had', () => {
  const m = matchAsmeName('PATELS AIRTEMP (INDIA) LIMITED', LISTED);
  assert.ok(m && m.via === 'exact', 'should match via the (I)->INDIA normalisation');
});
check('"The K.C.P Limited" vs "K.C.P. LTD" — punctuation-only difference', () => {
  const m = matchAsmeName('The K.C.P Limited', LISTED);
  assert.ok(m && m.via === 'exact');
});

console.log('\ndivision/plant merges (must match the parent, via "division"):');
check('"TATA STEEL LTD. - Growth Shop" -> Tata Steel Limited', () => {
  const m = matchAsmeName('TATA STEEL LTD. - Growth Shop', LISTED);
  assert.ok(m && m.via === 'division');
  assert.equal(m.listed[0].symbol, 'TATASTEEL');
});
check('"Dee Development Engineers Limited, Plant-2" -> DEEDEV', () => {
  const m = matchAsmeName('Dee Development Engineers Limited, Plant-2', LISTED);
  assert.ok(m && m.via === 'division');
  assert.equal(m.listed[0].symbol, 'DEEDEV');
});
check('"Thermax Limited C&H Group (A Division of Thermax Limited) UNIT I" -> THERMAX', () => {
  const m = matchAsmeName('Thermax Limited C&H Group (A Division of Thermax Limited) UNIT I', LISTED);
  assert.ok(m && m.via === 'division');
  assert.equal(m.listed[0].symbol, 'THERMAX');
});

console.log('\nfalse positives the fuzzy passes threw up (must NOT match):');
check('"Pharmalab India Private Limited" vs "Symbiotec Pharmalab Limited" — shares one word, different company', () => {
  assert.equal(matchAsmeName('Pharmalab India Private Limited', LISTED), null);
});
check('"AVADH ENGINEERING PRIVATE LIMITED" vs "Avadh Sugar & Energy Limited" — shares one word, different company', () => {
  assert.equal(matchAsmeName('AVADH ENGINEERING PRIVATE LIMITED', LISTED), null);
});
check('a private/LLP entity with no listed counterpart at all', () => {
  assert.equal(matchAsmeName('Gas Projects India Pvt. Ltd.', LISTED), null);
});
check('an ordinary "X Limited" name with nothing to truncate is not treated as a division of itself', () => {
  // limitedPrefix() must return null here, not re-match the same row as its own "division"
  assert.equal(matchAsmeName('Some Unlisted Company Limited', LISTED), null);
});

console.log('\nnormalizeName sanity:');
check('collapses legal-suffix and punctuation differences to the same key', () =>
  assert.equal(normalizeName('K.C.P. LTD'), normalizeName('K C P Limited')));
check('(I) and India normalise the same', () =>
  assert.equal(normalizeName('Patels Airtemp (I) Ltd.'), normalizeName('Patels Airtemp India Ltd.')));

console.log(failed ? `\n${failed} failed` : '\nall passed');
process.exit(failed ? 1 : 0);
