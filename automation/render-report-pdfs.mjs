/*
 * Print deep-dive reports to PDF, using the dashboard's own report page
 * (the same view readers see, with its print stylesheet), so the PDF and the
 * page can never drift apart.
 *
 *   node render-report-pdfs.mjs --base http://127.0.0.1:8799 --list ../reports-today.txt --out ../reports-pdf
 *   node render-report-pdfs.mjs --base http://127.0.0.1:8799 SIKA:FY27-Q1 QLINE:FY26-Q4
 *
 * Each entry is CODE:PERIOD; the file is <out>/<CODE>-<PERIOD>.pdf.
 * The dashboard must be served without accounts (no ADMIN_* / AUTH_* env).
 */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : dflt; };
const base = opt('--base', 'http://127.0.0.1:8799');
const list = opt('--list', null);
const out = opt('--out', 'reports-pdf');
const entries = [...args, ...(list && fs.existsSync(list) ? fs.readFileSync(list, 'utf8').split(/\r?\n/) : [])]
  .map(s => s.trim()).filter(s => /^[A-Za-z0-9&._-]+:FY\d\d-Q[1-4]$/.test(s));

if (!entries.length) { console.log('render-report-pdfs: nothing to render'); process.exit(0); }
fs.mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
let failed = 0;
for (const entry of entries) {
  const [code, period] = entry.split(':');
  const file = path.join(out, `${code}-${period}.pdf`);
  try {
    await page.goto(`${base}/#v=reports&r=${encodeURIComponent(code)}&rp=${period}`, { waitUntil: 'networkidle', timeout: 90000 });
    await page.reload({ waitUntil: 'networkidle', timeout: 90000 });   // the hash is read on load
    await page.waitForSelector('.dr-cover', { timeout: 60000 });
    const name = (await page.textContent('.dr-cover h1')).trim();
    const label = (await page.textContent('.dr-cover .rp-eyebrow')).split('·')[1]?.trim() || period;
    await page.emulateMedia({ media: 'print' });
    // Chromium's very first PDF export after a navigation comes out 10-15x larger than a
    // second export of the identical page (a 2 MB report becomes 25-30 MB) -- reproduced
    // locally against this exact Playwright/Chromium build. A throwaway export first warms
    // whatever makes the difference, so the real, saved export is normal-sized; the visual
    // content is byte-for-byte the same either way, only the file size differs.
    await page.pdf({ format: 'A4', printBackground: true, margin: { top: '14mm', bottom: '16mm', left: '11mm', right: '11mm' } });
    await page.pdf({
      path: file, format: 'A4', printBackground: true,
      margin: { top: '14mm', bottom: '16mm', left: '11mm', right: '11mm' },
      displayHeaderFooter: true,
      headerTemplate: '<span></span>',
      footerTemplate: `<div style="width:100%;font-size:8px;color:#666;padding:0 11mm;display:flex;justify-content:space-between;font-family:Arial,sans-serif">
        <span>${name.replace(/[<>&]/g, '')} — Deep-Dive Research Report, ${label} — not investment advice</span>
        <span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`,
    });
    await page.emulateMedia({ media: 'screen' });
    console.log(`  ${entry}: ${file} (${Math.round(fs.statSync(file).size / 1024)} KB)`);
  } catch (e) {
    failed++;
    console.error(`  ${entry}: FAILED ${e.message.split('\n')[0]}`);
  }
}
await browser.close();
console.log(`render-report-pdfs: ${entries.length - failed} of ${entries.length} rendered`);
process.exit(failed && failed === entries.length ? 1 : 0);
