import assert from 'node:assert/strict';
import { MAX_CANDIDATES_PER_RUN, shouldQueueCandidate, DAILY_SCAN_MODE } from './scan-rules.mjs';

assert.equal(DAILY_SCAN_MODE, true, 'daily scan mode should be enabled');
assert.equal(MAX_CANDIDATES_PER_RUN, 10, 'daily scan should cap output at 10 candidates');

assert.equal(
  shouldQueueCandidate({ name: 'Astra Pharma Ltd', board: 'BSE', mktcap_cr: 1200, isOnBoard: false, reviewed: false }),
  true,
  'plausible pharma name should be queued'
);

assert.equal(
  shouldQueueCandidate({ name: 'Infosys Ltd', board: 'BSE', mktcap_cr: 1100, isOnBoard: false, reviewed: false }),
  false,
  'ruled-out software name should not be queued'
);

assert.equal(
  shouldQueueCandidate({ name: 'Medica Labs Ltd', board: 'BSE', mktcap_cr: 1500, isOnBoard: true, reviewed: false }),
  false,
  'already-on-board names should not be queued'
);

console.log('scan-rules ok');
