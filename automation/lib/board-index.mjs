/**
 * lib/board-index.mjs — "is this exchange row already on the live board?"
 *
 * A board row and an exchange feed row identify the same company by
 * different names. backend/data/companies_raw.json keys rows by `code`,
 * which is the Screener slug -- the NSE symbol wherever one exists
 * (CORDSCABLE, ANDHRAPET, MANORG, SIMPLEXCAS) and the BSE number only for
 * BSE-only names. The BSE scrip master the sweep walks knows nothing but
 * the BSE number.
 *
 * Matching on `code` alone therefore misses every dual-listed company on
 * the board. On 31-Aug-2026 it did exactly that: four names already on the
 * board came back through the sweep, were researched again from scratch,
 * and reached the candidates queue as though they were finds. Hence
 * matching on every identifier a row carries -- code, nse_code, bse_code --
 * with the company name as a fallback.
 *
 * The name fallback is exact-after-normalisation, never a prefix or
 * substring test, and that is a deliberate asymmetry. A loose match fails
 * in the expensive direction: it would silently drop a real candidate that
 * merely shares a stem with a board name ("DCM Shriram Industries" against
 * "DCM Shriram"), and a candidate that is never queued is never seen
 * again, because reviewed-symbols.json is a one-way ledger. A duplicate
 * costs one research pass; a false exclusion costs the find permanently.
 */

/* Upper-case, drop everything that isn't a letter or digit, then drop a
   trailing LIMITED/LTD. Punctuation removal is what makes the exchange
   feeds' decorations vanish -- "Medicamen Biotech Ltd-$" and "DCM Shriram
   Industries Ltd-$" both normalise cleanly. */
export const normName = s =>
  String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/(LIMITED|LTD)$/, '');

/**
 * Build a membership test over the board.
 * @param {Array<object>} board  parsed companies_raw.json
 * @returns {(row: {sym?: string, name?: string}) => boolean}
 */
export function boardIndex(board) {
  const ids = new Set();
  const names = new Set();
  for (const c of board || []) {
    for (const id of [c.code, c.nse_code, c.bse_code]) if (id) ids.add(String(id).toUpperCase());
    if (c.name) names.add(normName(c.name));
  }
  return row => ids.has(String(row.sym ?? '').toUpperCase()) || names.has(normName(row.name));
}
