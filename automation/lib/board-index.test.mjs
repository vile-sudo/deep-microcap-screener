/**
 * node lib/board-index.test.mjs
 *
 * Regression test for the dedupe defect found on 31-Aug-2026: four
 * companies already on the live board came back through the small-cap
 * sweep, because the sweep matched only `code` (an NSE symbol for
 * dual-listed names) against a BSE feed that carries only BSE numbers.
 *
 * Runs against the real backend/data/companies_raw.json, so it also fails
 * if one of these four is ever removed from the board -- which is the
 * correct behaviour: the fixture and the claim would no longer agree.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { boardIndex, normName } from './board-index.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const board = JSON.parse(
  fs.readFileSync(path.resolve(HERE, '..', '..', 'backend', 'data', 'companies_raw.json'), 'utf8'));
const isOnBoard = boardIndex(board);

let failed = 0;
const check = (label, fn) => {
  try { fn(); console.log(`  ok    ${label}`); }
  catch (e) { failed++; console.log(`  FAIL  ${label}\n        ${e.message.split('\n')[0]}`); }
};

/* The four that actually slipped through, exactly as the BSE scrip master
   presents them: BSE number as `sym`, feed spelling as `name`. Each is on
   the board under its NSE code, which is why `code`-only matching missed
   them. Andhra Petrochemicals and Medicamen carry the feed's "-$" suffix
   in other rows; the two here are the plain spellings. */
const REGRESSIONS = [
  { sym: '532941', name: 'Cords Cable Industries Ltd', boardCode: 'CORDSCABLE' },
  { sym: '500012', name: 'Andhra Petrochemicals Ltd', boardCode: 'ANDHRAPET' },
  { sym: '514418', name: 'Mangalam Organics Ltd', boardCode: 'MANORG' },
  { sym: '513472', name: 'Simplex Castings Ltd', boardCode: 'SIMPLEXCAS' },
];

console.log('caught (were queued as new on 31-Aug-2026, must not be again):');
for (const r of REGRESSIONS) {
  check(`${r.sym} ${r.name} -> ${r.boardCode}`, () => {
    assert.ok(board.some(c => c.code === r.boardCode), `${r.boardCode} is not on the board any more`);
    assert.ok(isOnBoard(r), 'not matched against the board');
  });
}

console.log('\nnot over-matched (real candidates that must still get through):');
/* The asymmetry the module argues for -- a name that merely shares a stem
   with a board name must NOT match -- can't be tested against the live
   board, which happens to contain no such collision today. So assert it
   against a synthetic board, which is also the version that keeps holding
   when the real board changes underneath. */
check('a shared stem is not a match (prefix/substring matching would fail here)', () => {
  const synthetic = boardIndex([{ code: 'DCMSHRIRAM', name: 'DCM Shriram' }]);
  assert.ok(synthetic({ sym: 'DCMSHRIRAM', name: 'DCM Shriram' }), 'sanity: the fixture row itself must match');
  assert.ok(!synthetic({ sym: '523369', name: 'DCM Shriram Industries Ltd-$' }),
    'a different company sharing a stem was wrongly excluded -- this is the failure that loses a find permanently');
});
check('522014 United Drilling Tools Ltd still open', () =>
  assert.ok(!isOnBoard({ sym: '522014', name: 'United Drilling Tools Ltd' }), 'wrongly matched'));
check('540693 Shish Industries Ltd still open', () =>
  assert.ok(!isOnBoard({ sym: '540693', name: 'Shish Industries Ltd' }), 'wrongly matched'));
check('an invented company matches nothing', () =>
  assert.ok(!isOnBoard({ sym: '999999', name: 'Nonesuch Widgets Ltd' }), 'wrongly matched'));

console.log('\nidentifier coverage:');
check('matches on bse_code where the board row is keyed by NSE code', () =>
  assert.ok(isOnBoard({ sym: '532934', name: 'literally anything' })));  // PPAP, added 2026-08-31
check('matches on code for a BSE-only board row', () =>
  assert.ok(isOnBoard({ sym: '539984', name: 'literally anything' })));  // Hindusthan Insulators
check('name normalisation ignores case, punctuation and the Ltd suffix', () =>
  assert.equal(normName('Cords  Cable Industries Ltd-$'), normName('cords cable industries')));
check('empty board matches nothing', () =>
  assert.ok(!boardIndex([])({ sym: 'ANY', name: 'Any Ltd' })));

console.log(failed ? `\n${failed} failed` : '\nall passed');
process.exit(failed ? 1 : 0);
