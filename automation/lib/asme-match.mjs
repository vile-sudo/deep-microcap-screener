/**
 * lib/asme-match.mjs — "does ASME's directory name match a listed company?"
 *
 * Deliberately precise, not clever. The first pass at this (done by hand,
 * see frontend/static/asme-certified.js's header) also tried token-overlap
 * and edit-distance fuzzy matching to hunt for near-misses, and both threw
 * up real-looking false positives — "M.E Energy Pvt. Ltd." token-matched
 * dozens of unrelated listed "...Energy..." companies; "Pharmalab India
 * Private Limited" matched "Symbiotec Pharmalab Limited" on the one word
 * they share. Sorting those out needed a human reading each hit. That is
 * fine for a one-off check, wrong for something a scheduled task runs
 * unattended — an unattended false positive ships into a report nobody
 * reads closely, then into the live list. So this module only keeps the
 * two techniques that had a *zero* false-positive rate across all 352
 * names checked by hand:
 *
 *   1. Exact match after normalising both names to the same shape.
 *   2. Division/plant merge: an ASME row like "TATA STEEL LTD. - Growth
 *      Shop" or "Thermax Limited (C&H Group) Unit-II" is recognised as a
 *      division of an already-listed company only when truncating right
 *      after "Limited"/"Ltd" in the ASME row's own name produces an exact
 *      normalised match — never a loose "contains" test, which is what
 *      produced the false positives above.
 *
 * Anything that doesn't clear one of those two stays unmatched. That is
 * the safe failure direction: a real new match sits unflagged for one more
 * week (or gets caught by re-running the one-off, more thorough by-hand
 * check this codebase used to build the list originally), rather than a
 * wrong one entering an unattended report.
 */

/* Upper-case, spell out ASME's "(I)"/"(P)" the way exchange feeds spell
   "India"/"Private" in full, drop the legal-suffix words both sides use
   inconsistently, collapse whitespace. Keeping this as one shared function
   (rather than two similar ones for ASME names vs. exchange names) is the
   point — the whole method only works if both sides go through the exact
   same rules. */
export function normalizeName(s) {
  return String(s || '')
    .toUpperCase()
    .replace(/\(I\)/g, ' INDIA ')
    .replace(/\(P\)/g, ' PRIVATE ')
    .replace(/&/g, ' AND ')
    .replace(/[.,()/-]/g, ' ')
    .replace(/\bTHE\b/g, ' ')
    .replace(/\b(PRIVATE|PVT)\b/g, ' ')
    .replace(/\b(LIMITED|LTD)\b/g, ' ')
    .replace(/\bCOMPANY\b/g, ' ')
    .replace(/\bCORPORATION\b/g, ' ')
    .replace(/\bCORP\b/g, ' ')
    .replace(/\bCO\b/g, ' ')
    .replace(/\bLLP\b/g, ' ')
    .replace(/\bINDIA\b/g, ' ')
    .replace(/[^A-Z0-9 ]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/* The fully space-stripped form. "The K.C.P Limited" and "K.C.P. LTD" both
   normalise to "K C P" (word-separated) but need the spaces gone too before
   they're equal — this is that last step, kept separate because the
   division-merge prefix test below needs the word-separated form first. */
const tight = s => normalizeName(s).replace(/\s+/g, '');

/**
 * Build a lookup from a list of listed-exchange rows ({name, exch, symbol}).
 * Multiple rows can share a tight name (an NSE row and a BSE row for the
 * same company) — they're kept together so the caller can see every
 * exchange a name matched on.
 */
export function buildListedIndex(listedRows) {
  const byTight = new Map();
  for (const row of listedRows) {
    const key = tight(row.name);
    if (!key) continue;
    if (!byTight.has(key)) byTight.set(key, []);
    byTight.get(key).push(row);
  }
  return byTight;
}

/* "TATA STEEL LTD. - Growth Shop" -> "TATA STEEL LTD" (the text up to and
   including the first Limited/Ltd, original casing preserved so it still
   goes through normalizeName the same as anything else). Returns null if
   the name never contains Limited/Ltd, or if there's nothing after it to
   truncate (an ordinary "X Limited" name isn't a division of anything). */
function limitedPrefix(name) {
  const m = String(name || '').match(/^(.*?\b(?:Limited|Ltd)\.?)\b/i);
  if (!m) return null;
  const prefix = m[1];
  if (prefix.length >= name.replace(/[.,]$/, '').length - 1) return null;
  return prefix;
}

/**
 * Match one ASME entity name against the listed index.
 * @returns {{listed: Array<{name,exch,symbol}>, via: 'exact'|'division'} | null}
 */
export function matchAsmeName(asmeName, listedIndex) {
  const exact = listedIndex.get(tight(asmeName));
  if (exact) return { listed: exact, via: 'exact' };

  const prefix = limitedPrefix(asmeName);
  if (prefix) {
    const viaPrefix = listedIndex.get(tight(prefix));
    if (viaPrefix) return { listed: viaPrefix, via: 'division' };
  }
  return null;
}
