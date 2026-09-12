import { PLAUSIBLE, hint } from './sectors.mjs';

export const DAILY_SCAN_MODE = true;
export const MAX_CANDIDATES_PER_RUN = 10;
export const MIN_MARKET_CAP_CR = 300;
export const MAX_MARKET_CAP_CR = 20000;

export function shouldQueueCandidate({
  name,
  board,
  mktcap_cr,
  isOnBoard,
  reviewed = false,
}) {
  if (!name || isOnBoard || reviewed) return false;

  const sectorHint = hint(name);
  if (!sectorHint || !PLAUSIBLE.has(sectorHint)) return false;

  if (board === 'BSE' || board === 'BSE-SME') {
    if (mktcap_cr == null) return false;
    if (mktcap_cr < MIN_MARKET_CAP_CR || mktcap_cr > MAX_MARKET_CAP_CR) return false;
  }

  return true;
}
