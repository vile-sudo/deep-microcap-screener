"use strict";
/* ================================================================
   Deep Sweep — US board (Phase 2 MVP)
   A separate, smaller script from app.js on purpose -- see main.py's /us
   route comment. Same login, same look (shared style.css/dark.css), its
   own board: fetch, filter, sort, a detail drawer, watchlist, CSV export.
   Not the India board's full feature set (no lenses, compare, saved
   filters) -- those land in a later phase once this core is proven.
   ================================================================ */

async function fetchJSON(url) {
  const r = await fetch(url, {credentials: "same-origin"});
  if (r.status === 401) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname);
    throw new Error("Login required");
  }
  if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
  return r.json();
}

const esc = s => String(s == null ? "" : s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const fmtN = (v, d = 1) => v === null || v === undefined || isNaN(v) ? "—" : (+v).toFixed(d);
const fmtUSD = v => v === null || v === undefined || isNaN(v) ? "—" : "$" + Math.round(v).toLocaleString("en-US");
/* $m from the API -> a compact $M/$B label -- the US equivalent of
   fmtI+cr/lakh in app.js, kept separate rather than reused (see the plan:
   currency formatting isn't a shared seam, new formatters for a new
   board). */
function fmtUSDShort(capM) {
  if (capM === null || capM === undefined || isNaN(capM)) return "—";
  if (capM >= 1000) return "$" + (capM / 1000).toFixed(1) + "B";
  return "$" + Math.round(capM) + "M";
}

let DATA = [], WATCH = new Set(), SORT_KEY = "final_score", SORT_DIR = -1;

function dark(){ document.documentElement.dataset.mode === "dark"; }
document.getElementById("us-dark-toggle").onclick = () => {
  const on = document.documentElement.dataset.mode === "dark";
  if (on) { delete document.documentElement.dataset.mode; try{localStorage.setItem("dms.mode","light");}catch(e){} }
  else { document.documentElement.dataset.mode = "dark"; try{localStorage.setItem("dms.mode","dark");}catch(e){} }
};
document.getElementById("us-logout").onclick = async () => {
  await fetch("/api/auth/logout", {method: "POST", credentials: "same-origin"}).catch(() => {});
  location.href = "/login";
};

function moatEvidenceLine(d) {
  const note = d.moat_note || "";
  const short = note.length > 160 ? note.slice(0, 160) + "…" : note;
  return esc(short);
}

function rowHtml(d) {
  const starred = WATCH.has(d.code);
  return `<tr data-code="${esc(d.code)}">
    <td><button class="us-star${starred ? " on" : ""}" data-star="${esc(d.code)}" title="${starred ? "Remove from" : "Add to"} watchlist">${starred ? "★" : "☆"}</button></td>
    <td><b>${esc(d.name)}</b> <span class="us-tag">${esc(d.code)}</span></td>
    <td>${esc(d.sector || "—")}</td>
    <td class="n">${fmtN(d.final_score)}</td>
    <td class="n">${fmtUSDShort(d.market_cap_usd)}</td>
    <td class="n">${fmtN(d.pe)}</td>
    <td class="n">${fmtN(d.roe_pct)}</td>
    <td class="n">${fmtN(d.insider_pct)}</td>
    <td class="us-wrap">${moatEvidenceLine(d)}</td>
  </tr>`;
}

function currentRows() {
  const q = document.getElementById("us-q").value.trim().toLowerCase();
  const sector = document.getElementById("us-sector").value;
  const watchOnly = document.getElementById("us-watch-only").checked;
  let rows = DATA.filter(d => {
    if (sector && d.sector !== sector) return false;
    if (watchOnly && !WATCH.has(d.code)) return false;
    if (q) {
      const hay = (d.name + " " + d.code + " " + (d.sector || "")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  rows.sort((a, b) => {
    let x = a[SORT_KEY], y = b[SORT_KEY];
    if (typeof x === "string" || typeof y === "string") return String(x || "").localeCompare(String(y || "")) * SORT_DIR;
    x = x === null || x === undefined ? null : +x;
    y = y === null || y === undefined ? null : +y;
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return (x - y) * SORT_DIR;
  });
  return rows;
}

function render() {
  const rows = currentRows();
  document.getElementById("us-count").textContent = `${rows.length} of ${DATA.length} companies`;
  const tbody = document.getElementById("us-tbody");
  if (!DATA.length) {
    document.querySelector(".tablewrap").innerHTML = '<div class="us-empty">No US companies on the board yet — the daily screen hasn\'t added any. Check back after the next run.</div>';
    return;
  }
  tbody.innerHTML = rows.length
    ? rows.map(rowHtml).join("")
    : '<tr><td colspan="9" class="us-empty">No company matches these filters.</td></tr>';
  tbody.querySelectorAll("tr[data-code]").forEach(tr => {
    tr.onclick = e => { if (!e.target.closest("[data-star]")) openDrawer(tr.dataset.code); };
    tr.style.cursor = "pointer";
  });
  tbody.querySelectorAll("[data-star]").forEach(btn => {
    btn.onclick = e => { e.stopPropagation(); toggleStar(btn.dataset.star); };
  });
}

async function toggleStar(code) {
  const on = !WATCH.has(code);
  if (on) WATCH.add(code); else WATCH.delete(code);
  render();
  try {
    await fetch("/api/watchlist/" + encodeURIComponent(code) + "?market=us", {
      method: on ? "POST" : "DELETE", credentials: "same-origin",
    });
  } catch (e) {
    if (on) WATCH.delete(code); else WATCH.add(code);
    render();
  }
}

function openDrawer(code) {
  const d = DATA.find(x => x.code === code);
  if (!d) return;
  const drawer = document.getElementById("us-drawer"), scrim = document.getElementById("us-scrim");
  const sources = (d.evidence_sources || []).map(u => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join("<br>");
  drawer.innerHTML = `
    <button class="close" id="us-drawer-close" aria-label="Close">&times;</button>
    <div class="us-drawer-body">
      <h3>${esc(d.name)} <span class="us-tag">${esc(d.code)}</span></h3>
      <p class="section-copy">${esc(d.sector || "")}${d.claim_grade ? " · claim grade: " + esc(d.claim_grade) : ""}</p>
      <div class="us-drawer-row"><b>Score</b>${fmtN(d.final_score)}</div>
      <div class="us-drawer-row"><b>Market cap</b>${fmtUSD(d.market_cap_usd)}m</div>
      <div class="us-drawer-row"><b>P/E</b>${fmtN(d.pe)}</div>
      <div class="us-drawer-row"><b>ROE %</b>${fmtN(d.roe_pct)}</div>
      <div class="us-drawer-row"><b>Insider %</b>${fmtN(d.insider_pct)}</div>
      <div class="us-drawer-row"><b>Moat evidence</b>${esc(d.moat_note || "—")}</div>
      ${d.gate_failures && d.gate_failures.length ? `<div class="us-drawer-row"><b>Fails the fundamentals gates on</b>${d.gate_failures.map(esc).join("; ")}</div>` : ""}
      ${d.warnings && d.warnings.length ? `<div class="us-drawer-row"><b>Warnings</b>${d.warnings.map(esc).join("<br>")}</div>` : ""}
      ${sources ? `<div class="us-drawer-row"><b>Sources</b>${sources}</div>` : ""}
      <div class="us-drawer-row"><b>Added</b>${esc(d.added_on || "—")}</div>
    </div>`;
  drawer.classList.add("on");
  scrim.classList.add("on");
  document.getElementById("us-drawer-close").onclick = closeDrawer;
  scrim.onclick = closeDrawer;
}
function closeDrawer() {
  document.getElementById("us-drawer").classList.remove("on");
  document.getElementById("us-scrim").classList.remove("on");
}

function buildSectorOptions() {
  const sel = document.getElementById("us-sector");
  const counts = {};
  DATA.forEach(d => { if (d.sector) counts[d.sector] = (counts[d.sector] || 0) + 1; });
  sel.innerHTML = '<option value="">All sectors</option>'
    + Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([s, n]) => `<option value="${esc(s)}">${esc(s)} (${n})</option>`).join("");
}

document.querySelectorAll("#us-tbl thead th[data-k]").forEach(th => {
  th.onclick = () => {
    const k = th.dataset.k;
    if (SORT_KEY === k) SORT_DIR *= -1; else { SORT_KEY = k; SORT_DIR = k === "name" ? 1 : -1; }
    render();
  };
});
document.getElementById("us-q").oninput = render;
document.getElementById("us-qclear").onclick = () => { document.getElementById("us-q").value = ""; render(); };
document.getElementById("us-sector").onchange = render;
document.getElementById("us-watch-only").onchange = render;
document.getElementById("us-csv").onclick = () => { location.href = "/api/companies/export.csv?market=us"; };

async function boot() {
  const [companies, meta, watchlist] = await Promise.all([
    fetchJSON("/api/companies?market=us"),
    fetchJSON("/api/meta?market=us"),
    fetchJSON("/api/watchlist?market=us").catch(() => ({codes: []})),
  ]);
  DATA = companies;
  WATCH = new Set(watchlist.codes || []);
  document.title = "Deep Sweep US — " + DATA.length + " companies";
  document.getElementById("us-asof").textContent = meta.build_new ? "Updated " + meta.build_new : "";
  buildSectorOptions();
  render();
}
boot().catch(e => {
  document.querySelector(".tablewrap").innerHTML = `<div class="us-empty">Could not load the US board: ${esc(e.message)}</div>`;
});
