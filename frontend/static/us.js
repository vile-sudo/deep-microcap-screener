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

/* ================================================================
   Chart Gallery -- candles for the US board + the wider actively-traded
   US market, from Polygon.io (see backend/app/charts_us.py,
   scripts/update_us_charts.py).

   Card design deliberately matches the India board's "Screen any Chart"
   gallery (app.js's galCard/candleSVG) rather than inventing a new one --
   same .gal-grid / .gcard / .gc-x / .st-x / .cs-x classes (already in
   style.css, both themes), same lazy-loaded mini candlestick thumbnail per
   card, same
   IntersectionObserver + max-6-concurrent fetch queue. usCandleSVG() below
   is that same renderer, ported rather than shared (this is a standalone
   script from app.js on purpose -- see the top of this file) and trimmed
   to only the axis:false thumbnail path the grid actually uses (no
   X-ray/base boxes, pivot line or weekly-EMA overlay -- those ride on
   India's base-detection/EMA-crossover pipelines, which us_charts.py
   deliberately doesn't build yet; see that module's own docstring).

   The one deliberate India departure: opening a card's full chart renders
   with KLineChart (vendored at /static/klinecharts.min.js, Apache-2.0)
   instead of a bigger candleSVG, per an explicit earlier decision to use
   a real charting library for the US board's detail view. */
let GAL_DATA = null, GAL_LOADING = null;
let GSERIES = new Map(), GQUEUE = [], GACTIVE = 0, GOBS = null;

const GAL_STATUS = {
  high: ["At 52-week high", "st-high"], near: ["Near 52-week high", "st-near"],
  up: ["Uptrend", "st-up"], flat: ["Sideways", "st-flat"], down: ["Downtrend", "st-down"],
};
const GAL_SORTS = {
  high: {l: "Sort: closest to 52-week high", s: s => s.from_high_pct, dir: -1},
  chg: {l: "Sort: day's change", s: s => s.chg_pct, dir: -1},
  vol: {l: "Sort: volume vs average", s: s => s.vol_ratio, dir: -1},
  name: {l: "Sort: name", s: () => null, d: s => String(s.name || s.symbol || ""), dir: 1},
};

/* Weekly 9/21 EMA crossover -- the exact same screen app.js's India gallery
   uses (backend/app/ema_crossover.py's screen()/crossover()/forming(), run
   over US candles by scripts/update_us_charts.py's ema_crossover.write_us()
   call). Its own small index, fetched once and keyed by board code or bare
   ticker -- the same keys galCardHtml() already uses. */
const GAL_CROSS_LABEL = {bull: ["Bullish crossover", "cross-bull", "⤶"], bear: ["Bearish crossover", "cross-bear", "⤸"]};
function crossBadge(c) {
  if (!c) return null;
  if (c.state === "forming") {
    return {cls: "cross-forming", icon: c.direction === "bull" ? "↗" : "↘", label: "Crossover forming",
      title: `Weekly 9 and 21 EMA are ${c.gap_pct}% apart and converging -- no cross yet`};
  }
  const l = GAL_CROSS_LABEL[c.direction];
  if (!l) return null;
  return {cls: l[1], icon: l[2], label: l[0],
    title: `Weekly 9 EMA crossed the 21 EMA ${c.weeks_ago === 0 ? "this week" : c.weeks_ago + " week" + (c.weeks_ago === 1 ? "" : "s") + " ago"}`};
}
const GX = {map: null, loading: null};
function gxLoad() {
  GX.loading = GX.loading || fetchJSON("/api/charts/ema-crossover?market=us").catch(() => ({crossovers: {}})).then(j => {
    GX.map = j.crossovers || {};
    if (GAL_DATA && !document.getElementById("us-tab-gallery").hidden) galRender();
    return GX.map;
  });
  return GX.loading;
}
function galCross(s) { return s && GX.map ? (GX.map[s.key] || null) : null; }

function galFetchIndex() {
  GAL_LOADING = GAL_LOADING || fetchJSON("/api/charts/universe?market=us").catch(() => null);
  return GAL_LOADING;
}

const US_TABS = ["screens", "market", "gallery", "insider"];
document.querySelectorAll('[data-ustab]').forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll('[data-ustab]').forEach(b => {
      const on = b === btn;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", String(on));
    });
    const tab = btn.dataset.ustab;
    US_TABS.forEach(t => { document.getElementById("us-tab-" + t).hidden = t !== tab; });
    if (tab === "gallery" && !GAL_DATA) openGallery();
    if (tab === "market" && !MKT.data) openMarket();
    if (tab === "insider" && !INS.data) openInsider();
  };
});

async function openGallery() {
  const grid = document.getElementById("us-gal-grid");
  grid.innerHTML = '<p class="view-hint">Loading charts…</p>';
  const j = await galFetchIndex();
  if (!j || !j.stocks) {
    grid.innerHTML = '<div class="us-empty">Could not load the chart gallery.</div>';
    return;
  }
  GAL_DATA = j;
  buildGalControls();
  document.getElementById("us-gal-asof").textContent = j.as_of ? "Last session: " + j.as_of : "";
  if (!GX.map) gxLoad();
  galRender();
}

function buildGalControls() {
  const tr = document.getElementById("us-gal-trend"), so = document.getElementById("us-gal-sort");
  const byStatus = {};
  Object.values(GAL_DATA.stocks || {}).forEach(s => { byStatus[s.status || "none"] = (byStatus[s.status || "none"] || 0) + 1; });
  tr.innerHTML = '<option value="">Trend: all</option>'
    + Object.entries(GAL_STATUS).map(([k, [l]]) => `<option value="${k}">${l} (${byStatus[k] || 0})</option>`).join("");
  so.innerHTML = Object.entries(GAL_SORTS).map(([k, v]) => `<option value="${k}">${v.l}</option>`).join("");
}

const GAL_PAGE = 120;
let galShown = GAL_PAGE;

function galRows() {
  if (!GAL_DATA) return [];
  const q = document.getElementById("us-gal-q").value.trim().toLowerCase();
  const scope = document.getElementById("us-gal-scope").value;
  const trend = document.getElementById("us-gal-trend").value;
  const cross = document.getElementById("us-gal-cross").value;
  const so = GAL_SORTS[document.getElementById("us-gal-sort").value] || GAL_SORTS.high;
  const boardByCode = new Map(DATA.map(d => [d.code, d]));
  const rows = Object.entries(GAL_DATA.stocks || {})
    .map(([key, s]) => {
      const rec = s.on_board ? boardByCode.get(s.code) : null;
      return {key, ...s, sector: rec ? rec.sector : null};
    })
    .filter(s => {
      if (scope === "board" && !s.on_board) return false;
      if (trend && s.status !== trend) return false;
      if (cross) {
        const c = galCross(s);
        if (!c) return false;
        if (cross === "forming" ? c.state !== "forming" : !(c.state === "crossed" && c.direction === cross)) return false;
      }
      if (q && !(s.symbol || "").toLowerCase().includes(q) && !(s.name || "").toLowerCase().includes(q)) return false;
      return true;
    });
  return rows.sort((a, b) => {
    const val = s => so.d ? so.d(s) : so.s(s);
    const x = val(a), y = val(b);
    if (x == null && y == null) return String(a.name || a.symbol).localeCompare(String(b.name || b.symbol));
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * so.dir;
  });
}

function galRender() {
  const rows = galRows();
  document.getElementById("us-gal-count").textContent = `${rows.length} of ${Object.keys(GAL_DATA.stocks || {}).length} companies`
    + (rows.length > galShown ? ` · showing ${Math.min(galShown, rows.length)}` : "");
  const grid = document.getElementById("us-gal-grid");
  if (!rows.length) {
    grid.innerHTML = '<p class="view-hint">No company matches — clear the search, or wait for the next chart build (the US chart pipeline is still filling in).</p>';
    return;
  }
  const shown = rows.slice(0, galShown);
  grid.innerHTML = shown.map(galCardHtml).join("")
    + (rows.length > shown.length ? `<button type="button" class="btn gal-more" id="us-gal-more">Show ${Math.min(GAL_PAGE, rows.length - shown.length)} more</button>` : "");
  if (GOBS) GOBS.disconnect();
  GOBS = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { GOBS.unobserve(e.target); galFetch(e.target.dataset.gchart, e.target.dataset.gboard2 === "1"); }
  }), {rootMargin: "500px 0px"});
  grid.querySelectorAll("[data-gchart]").forEach(el => { if (!GSERIES.has(el.dataset.gchart)) GOBS.observe(el); });
}

function galCardHtml(s) {
  const st = GAL_STATUS[s.status];
  const xb = crossBadge(galCross(s));
  const badge = xb ? `<span class="gc-badge gc-${xb.cls}" title="${esc(xb.title)}">${xb.icon} ${esc(xb.label)}</span>`
    : st ? `<span class="gc-badge ${st[1]}">${st[0]}</span>` : "";
  const chg = s.chg_pct == null ? "" : `<span class="${s.chg_pct >= 0 ? "up" : "dn"}">${s.chg_pct >= 0 ? "+" : ""}${s.chg_pct.toFixed(2)}%</span>`;
  const hi = s.status === "high" ? '<span class="gc-chip">52w high</span>'
    : s.from_high_pct == null ? "" : `<span>${Math.abs(s.from_high_pct).toFixed(1)}% off high</span>`;
  const vol = s.vol_ratio == null ? "" : `<span>vol ${s.vol_ratio}×</span>`;
  const on = s.on_board && WATCH.has(s.code);
  const foot = s.on_board
    ? `<span class="gc-theme">${esc(s.sector || "US board")}</span><button type="button" class="gc-pin${on ? " on" : ""}" data-gpin="${esc(s.code)}" title="${on ? "Remove from" : "Add to"} watchlist" aria-label="Star ${esc(s.name || s.symbol)}">${on ? "★" : "☆"}</button>`
    : `<span class="gc-theme">${esc(s.exchange || "US")}</span><span class="gc-off">Not on the board</span>`;
  return `<article class="gcard" data-gcode="${esc(s.key)}" data-gboard="${s.on_board ? "1" : "0"}" tabindex="0" aria-label="Open the chart for ${esc(s.name || s.symbol)}">
    <div class="gc-head"><span class="gc-tick">${esc(s.symbol)}</span><span class="gc-name" title="${esc(s.name || s.symbol)}">${esc(s.name || s.symbol)}</span>${badge}</div>
    <div class="gc-chart" data-gchart="${esc(s.key)}" data-gboard2="${s.on_board ? "1" : "0"}">${galThumb(s.key)}</div>
    <div class="gc-stats">${chg}<span>${fmtUSD(s.last)}</span>${vol}${hi}</div>
    <div class="gc-foot">${foot}</div>
  </article>`;
}

function galThumb(key) {
  const ser = GSERIES.get(key);
  if (!ser) return '<span class="gc-skel"></span>';
  if (ser.missing || !ser.rows || !ser.rows.length) return '<span class="gc-empty">No price data yet — the chart appears after the next daily update</span>';
  return usCandleSVG(ser.rows, {w: 320, h: 160, sessions: 125});
}

function galFetch(key, isBoard) {
  if (GSERIES.has(key)) return Promise.resolve();
  return new Promise(resolve => { GQUEUE.push([key, isBoard, resolve]); galPump(); });
}
function galPump() {
  while (GACTIVE < 6 && GQUEUE.length) {
    const [key, isBoard, resolve] = GQUEUE.shift();
    GACTIVE++;
    const url = (isBoard ? "/api/charts/" : "/api/charts/u/") + encodeURIComponent(key) + "?market=us";
    fetch(url, {credentials: "same-origin"}).then(r => r.ok ? r.json() : null).catch(() => null).then(j => {
      GACTIVE--;
      GSERIES.set(key, j || {missing: true});
      galPaint(key);
      resolve();
      galPump();
    });
  }
}
function galPaint(key) {
  document.querySelectorAll(`[data-gchart="${CSS.escape(key)}"], [data-mkt-chart="${CSS.escape(key)}"]`).forEach(el => {
    el.innerHTML = galThumb(key);
  });
}
function galPinButtons(code) {
  const on = WATCH.has(code);
  document.querySelectorAll(`[data-gpin="${CSS.escape(code)}"]`).forEach(b => {
    b.classList.toggle("on", on);
    b.textContent = on ? "★" : "☆";
    b.title = (on ? "Remove from" : "Add to") + " watchlist";
  });
}

document.getElementById("us-gal-q").oninput = () => { galShown = GAL_PAGE; galRender(); };
document.getElementById("us-gal-qclear").onclick = () => { document.getElementById("us-gal-q").value = ""; galShown = GAL_PAGE; galRender(); };
document.getElementById("us-gal-scope").onchange = () => { galShown = GAL_PAGE; galRender(); };
document.getElementById("us-gal-trend").onchange = () => { galShown = GAL_PAGE; galRender(); };
document.getElementById("us-gal-cross").onchange = () => { galShown = GAL_PAGE; galRender(); };
document.getElementById("us-gal-sort").onchange = () => { galShown = GAL_PAGE; galRender(); };
document.getElementById("us-gal-clear").onclick = () => {
  document.getElementById("us-gal-q").value = "";
  document.getElementById("us-gal-scope").value = "all";
  document.getElementById("us-gal-trend").value = "";
  document.getElementById("us-gal-cross").value = "";
  document.getElementById("us-gal-sort").value = "high";
  galShown = GAL_PAGE;
  galRender();
};

const usGalGrid = document.getElementById("us-gal-grid");
usGalGrid.onclick = e => {
  if (e.target.closest("#us-gal-more")) { galShown += GAL_PAGE; galRender(); return; }
  const pin = e.target.closest("[data-gpin]");
  if (pin) { e.stopPropagation(); toggleStar(pin.dataset.gpin); galPinButtons(pin.dataset.gpin); return; }
  const card = e.target.closest("[data-gcode]");
  if (card) openChart(card.dataset.gcode, card.dataset.gboard === "1");
};
usGalGrid.onkeydown = e => {
  const card = e.target.closest("[data-gcode]");
  if (card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openChart(card.dataset.gcode, card.dataset.gboard === "1"); }
};

/* A trimmed, thumbnail-only port of app.js's candleSVG() -- candles, the
   50/200-day averages and volume as one small inline SVG, no axis/pivot/
   X-ray box support (the grid never asks for those; see the block comment
   above). Kept deliberately smaller than the original rather than copied
   wholesale, since everything axis/box/pivot-related would be dead code
   here. */
function usCandleSVG(rows, o) {
  const W = o.w, H = o.h, padR = 2, padT = 5, padB = 3;
  const closes = rows.map(r => r[4]);
  const ma = n => { let sum = 0; return closes.map((c, i) => { sum += c; if (i >= n) sum -= closes[i - n]; return i >= n - 1 ? sum / n : null; }); };
  const start = Math.max(0, rows.length - o.sessions);
  const vis = rows.slice(start);
  const m50 = ma(50).slice(start), m200 = ma(200).slice(start);
  const hi52 = Math.max(...rows.map(r => r[2]));
  let lo = Math.min(...vis.map(r => r[3])), hi = Math.max(...vis.map(r => r[2]));
  m50.concat(m200).forEach(v => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
  const showHi = hi52 <= hi * 1.12;
  if (showHi) hi = Math.max(hi, hi52);
  const pad = (hi - lo) * 0.05 || hi * 0.02 || 1;
  hi += pad;
  lo = Math.max(0, lo - pad);
  const inner = H - padT - padB, volH = inner * 0.2, priceH = inner - volH - 6;
  const plotW = W - padR, step = plotW / vis.length, cw = Math.max(0.8, Math.min(8, step * 0.66));
  const y = p => padT + (hi - p) / (hi - lo) * priceH;
  const X = i => i * step + step / 2, f = n => n.toFixed(2);
  const vmax = Math.max(1, ...vis.map(r => r[5]));
  let s = `<svg viewBox="0 0 ${W} ${H}" class="cs" role="img" aria-label="Daily candlestick chart">`;
  vis.forEach((r, i) => {
    const vh = r[5] / vmax * volH;
    s += `<rect x="${f(X(i) - cw / 2)}" y="${f(H - padB - vh)}" width="${f(cw)}" height="${f(Math.max(vh, 0.3))}" class="cs-vol"/>`;
  });
  if (showHi) s += `<line x1="0" x2="${plotW}" y1="${f(y(hi52))}" y2="${f(y(hi52))}" class="cs-hi"/>`;
  let upW = "", dnW = "", upB = "", dnB = "";
  vis.forEach((r, i) => {
    const x = X(i), up = r[4] >= r[1], top = y(Math.max(r[1], r[4])), bh = Math.max(0.9, Math.abs(y(r[1]) - y(r[4])));
    const wick = `M${f(x)} ${f(y(r[2]))}V${f(y(r[3]))}`, body = `M${f(x - cw / 2)} ${f(top)}h${f(cw)}v${f(bh)}h${f(-cw)}Z`;
    if (up) { upW += wick; upB += body; } else { dnW += wick; dnB += body; }
  });
  s += `<path d="${upW}" class="cs-wick up"/><path d="${dnW}" class="cs-wick dn"/><path d="${upB}" class="cs-body up"/><path d="${dnB}" class="cs-body dn"/>`;
  const line = (arr, cls) => {
    let d = "";
    arr.forEach((v, i) => { if (v != null) d += (d ? "L" : "M") + f(X(i)) + " " + f(y(v)); });
    return d ? `<path d="${d}" class="${cls}"/>` : "";
  };
  s += line(m50, "cs-ma50") + line(m200, "cs-ma200");
  return s + "</svg>";
}

let GAL_CHART = null;

async function openChart(key, isBoard) {
  const drawer = document.getElementById("us-drawer"), scrim = document.getElementById("us-scrim");
  drawer.innerHTML = `
    <button class="close" id="us-chart-close" aria-label="Close">&times;</button>
    <div class="us-drawer-body">
      <h3 id="us-chart-title">${esc(key)}</h3>
      <p class="section-copy" id="us-chart-sub">Loading…</p>
      <div class="us-chart-box" id="us-chart-box"></div>
    </div>`;
  drawer.classList.add("on");
  scrim.classList.add("on");
  document.getElementById("us-chart-close").onclick = closeChart;
  scrim.onclick = closeChart;

  if (!GSERIES.has(key)) await galFetch(key, isBoard);
  const data = GSERIES.get(key);
  if (!data || data.missing) {
    document.getElementById("us-chart-sub").textContent = "Could not load this chart.";
    return;
  }
  const rows = data.rows || [];
  document.getElementById("us-chart-title").textContent = data.symbol || key;
  const stats = data.stats || {};
  document.getElementById("us-chart-sub").textContent = rows.length
    ? `${rows.length} sessions · last ${fmtUSD(stats.last)} · as of ${stats.asof || ""}`
    : "No candle data for this stock yet.";
  if (!rows.length) return;

  const klineData = rows.map(r => ({timestamp: Date.parse(r[0] + "T00:00:00Z"), open: r[1], high: r[2], low: r[3], close: r[4], volume: r[5]}));
  if (GAL_CHART) { try { klinecharts.dispose("us-chart-box"); } catch (e) {} GAL_CHART = null; }
  GAL_CHART = klinecharts.init("us-chart-box");
  if (GAL_CHART) {
    GAL_CHART.setStyles(document.documentElement.dataset.mode === "dark" ? "dark" : "light");
    GAL_CHART.createIndicator("VOL", false, {height: 80});
    GAL_CHART.createIndicator("MA");
    GAL_CHART.applyNewData(klineData);
  }
}

function closeChart() {
  document.getElementById("us-drawer").classList.remove("on");
  document.getElementById("us-scrim").classList.remove("on");
  if (GAL_CHART) { try { klinecharts.dispose("us-chart-box"); } catch (e) {} GAL_CHART = null; }
}

/* ================================================================
   Market view -- the US board's counterpart to India's "Spot the
   board's next leaders" (app.js's bp* functions), reusing
   backend/app/setups.py's exact base/breakout engine unchanged
   (scripts/update_us_charts.py's write_setups_us/write_universe). All
   four screens (VCP, Blue Sky, Multi-year, IPO base), cards and list
   views. Not built yet: CSV export, the IPO breakout-window sub-filter,
   the share button and live index quotes. Cards are a simplified,
   India-styled version of bpCard() -- no mini-chart preview or X-ray;
   clicking one reuses the Chart Gallery's own openChart() for the full
   candle view instead of a separate renderer.
   ================================================================ */
const MKT_STAGES = [
  {k: "forming", label: "Forming", hint: "resting before a breakout", title: "Forming — on watch",
    desc: "Strong stocks pausing in a tight, quiet range just under a ceiling, their trading drying up as they coil. A close above the pivot on heavy volume sets one off."},
  {k: "fresh", label: "Fresh breakouts", hint: "cleared the pivot in the last 5 sessions", title: "Fresh breakouts",
    desc: "Closed above the pivot on at least 1.4× their usual volume within the last five sessions, and still holding above the stop."},
  {k: "climbing", label: "Climbing", hint: "broke out earlier, still rising", title: "Climbing",
    desc: "Broke out more than five sessions ago and still in the trade: never 8% under the pivot, never a close under the 50-day average."},
  {k: "played", label: "Played out", hint: "this year's breakouts, stopped or trailed out", title: "Played out",
    desc: "Broke out this year, then either fell 8% under the pivot (stopped) or closed under the 50-day average (trailed out)."},
];
/* the IPO base screen reuses the stages; only the words change */
const MKT_IPO_TEXT = {
  forming: {hint: "IPO base, resting under the post-listing high", title: "IPO bases — on watch",
    desc: "Companies listed in the last two years, holding a base of three weeks or more under their post-listing high. The ones within 5% of it are flagged \"on the verge\"; a close above it on heavy volume is the breakout."},
  fresh: {hint: "cleared the post-listing high in the last 5 sessions", title: "Fresh IPO base breakouts",
    desc: "Recent listings that closed above their post-listing high on at least 1.4× usual volume within the last five sessions."},
  climbing: {hint: "broke out of the IPO base, still rising", title: "Climbing out of the IPO base",
    desc: "Broke out of the IPO base more than five sessions ago and still in the trade."},
  played: {hint: "IPO base breakouts this year, stopped or trailed out", title: "IPO base breakouts — played out",
    desc: "Broke out of the IPO base this year, then fell 8% under the pivot or closed under the moving average."},
};
const MKT_SCREEN_TEXT = {
  ipo: MKT_IPO_TEXT,
  bluesky: {
    forming: {hint: "basing at its all-time high", title: "Blue sky — on watch",
      desc: "Strong stocks basing right at the highest price they have traded in our cached data: no earlier buyer is sitting above, waiting to sell at break-even. The ones within 5% of the pivot are flagged \"on the verge\"; a close above it on heavy volume takes them into blue sky."},
    fresh: {hint: "broke out to a new high in the last 5 sessions", title: "Fresh blue sky breakouts",
      desc: "Closed above a base at their all-time high on at least 1.4× their usual volume within the last five sessions, and still holding above the stop."},
    climbing: {hint: "broke out into blue sky, still rising", title: "Climbing in blue sky",
      desc: "Broke out of a base at their all-time high more than five sessions ago and still in the trade: never 8% under the pivot, never a close under the 50-day average."},
    played: {hint: "this year's blue sky breakouts, stopped or trailed out", title: "Blue sky breakouts — played out",
      desc: "Broke out to a new all-time high this year, then either fell 8% under the pivot (stopped) or closed under the 50-day average (trailed out)."},
  },
  multiyear: {
    forming: {hint: "within 15% of a year-plus ceiling", title: "Multi-year bases — on watch",
      desc: "Stocks in an uptrend and back within 15% of a high that has capped them for a year or more, never more than 50% under it since. The ones within 5% are flagged \"on the verge\"; a close above it on heavy volume is the breakout."},
    fresh: {hint: "cleared a year-plus base in the last 5 sessions", title: "Fresh multi-year breakouts",
      desc: "Closed above a high that had held for a year or more, on at least 1.4× their usual volume, within the last five sessions — and still holding above the stop."},
    climbing: {hint: "cleared a year-plus base, still rising", title: "Climbing out of a multi-year base",
      desc: "Cleared a year-plus base more than five sessions ago and still in the trade: never 8% under the pivot, never a close under the 50-day average."},
    played: {hint: "this year's multi-year breakouts, stopped or trailed out", title: "Multi-year breakouts — played out",
      desc: "Cleared a year-plus base this year, then either fell 8% under the pivot (stopped) or closed under the 50-day average (trailed out)."},
  },
};
function mktStageText(k) {
  const base = MKT_STAGES.find(s => s.k === k);
  const o = (MKT_SCREEN_TEXT[MKT.screen] || {})[k];
  return o ? {...base, ...o} : base;
}
const MKT_SCREENS = {
  vcp: {label: "Volatility Contraction Pattern (VCP)", hero: "Spot the board's next leaders.", heroAll: "Spot the market's next leaders.",
    sub: "actively traded US stocks are scanned after each trading day. Here are the few actually setting up. Pick a stage.",
    subBoard: "Every company on the board is scanned after each trading day. Here are the few actually setting up. Pick a stage."},
  bluesky: {label: "Blue sky", hero: "Stocks with no sellers above them.", heroAll: "Stocks with no sellers above them.",
    sub: "actively traded US stocks, scanned after each trading day. Here are the ones basing at, or breaking out from, the highest price they have traded. Pick a stage.",
    subBoard: "board companies with a year or more of trading, scanned after each trading day. Here are the ones basing at, or breaking out from, the highest price they have traded. Pick a stage."},
  multiyear: {label: "Multi-year breakouts", hero: "Catch stocks clearing a year-plus ceiling.", heroAll: "Catch stocks clearing a year-plus ceiling.",
    sub: "actively traded US stocks, scanned after each trading day. Here are the ones back near, or through, a high that held them for a year or more. Pick a stage.",
    subBoard: "board companies with a year or more of trading, scanned after each trading day. Here are the ones back near, or through, a high that held them for a year or more. Pick a stage."},
  ipo: {label: "IPO base", hero: "Catch recent listings breaking out of their IPO base.", heroAll: "Catch recent listings breaking out of their IPO base.",
    sub: "actively traded US stocks, scanned after each trading day. Here are the recent listings basing under, or breaking out of, their post-listing high. Pick a stage.",
    subBoard: "board companies listed in the last two years, scanned after each trading day. Here are the recent listings basing under, or breaking out of, their post-listing high. Pick a stage."},
};
const MKT_SORTS = {
  rs: {l: "RS rating", k: a => a.rs, dir: -1},
  pivot: {l: "Now vs pivot (%)", k: a => a.now_vs_pivot, dir: -1},
  atr: {l: "Tightening (ATR ratio)", k: a => a.atr_ratio, dir: 1},
  dry: {l: "Volume dry-up", k: a => a.vol_dryup, dir: 1},
  ud: {l: "Up/down volume, net", k: a => a.updown, dir: -1},
  high: {l: "From 52-week high", k: a => a.from_high, dir: 1},
};
const MKT_SCREEN_FIELDS = ["stage", "prev_stage", "pivot", "now_vs_pivot", "flags", "verge", "base", "breakout", "note", "breakouts"];

const MKT = {data: null, loading: null, all: null, allLoading: null, scope: "all", screen: "vcp", stage: "forming", view: "cards", controls: false};
let MKT_BY_CODE = {};
let MKT_OBS = null;

function mktRefreshByCode() {
  MKT_BY_CODE = {};
  DATA.forEach(d => { MKT_BY_CODE[d.code] = {code: d.code, name: d.name, symbol: d.code, sector: d.sector, universe: false}; });
}

async function openMarket() {
  mktRefreshByCode();
  if (!MKT.data || !MKT.controls || (MKT.scope === "all" && !MKT.all)) {
    document.getElementById("us-mkt-results").innerHTML = '<p class="bp-empty">Loading the scan…</p>';
    MKT.loading = MKT.loading || fetchJSON("/api/market/setups?market=us")
      .catch(() => ({stocks: {}, feed: [], counts: {}, market_breakouts: []})).then(j => { MKT.data = j; });
    await Promise.all([MKT.loading, MKT.scope === "all" ? mktLoadAll() : null]);
    if (!MKT.controls) mktControls();
    mktRender();
  } else {
    mktRender();
  }
}

function mktLoadAll() {
  MKT.allLoading = MKT.allLoading || fetchJSON("/api/market/setups-all?market=us").catch(() => ({stocks: {}, feed: []})).then(j => {
    Object.entries(j.stocks || {}).forEach(([k, x]) => {
      if (!MKT_BY_CODE[k]) MKT_BY_CODE[k] = {code: k, universe: true, name: x.name || x.symbol || k, symbol: x.symbol, exchange: x.exchange};
    });
    MKT.all = j;
    return j;
  });
  return MKT.allLoading;
}

function mktControls() {
  MKT.controls = true;
  const sc = document.getElementById("us-mkt-screen"), th = document.getElementById("us-mkt-theme"),
    so = document.getElementById("us-mkt-sort"), scp = document.getElementById("us-mkt-scope");
  sc.value = MKT.screen;
  sc.onchange = () => { MKT.screen = sc.value; mktRender(); };
  scp.value = MKT.scope;
  scp.onchange = () => {
    MKT.scope = scp.value;
    if (MKT.scope === "all" && !MKT.all) {
      document.getElementById("us-mkt-results").innerHTML = '<p class="bp-empty">Loading every actively traded US stock…</p>';
      mktLoadAll().then(() => mktRender());
    } else mktRender();
  };
  const themes = [...new Set(DATA.map(d => d.sector).filter(Boolean))].sort();
  th.innerHTML = '<option value="">All themes</option>' + themes.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
  th.onchange = () => mktRender();
  so.innerHTML = Object.entries(MKT_SORTS).map(([k, v]) => `<option value="${k}">${v.l}</option>`).join("");
  so.value = "rs";
  so.onchange = () => mktRender();
  let t;
  document.getElementById("us-mkt-find").oninput = () => { clearTimeout(t); t = setTimeout(mktRender, 120); };
  document.getElementById("us-mkt-mw").onclick = () => mktMarketWide();
  document.getElementById("us-mkt-cards-btn").onclick = () => { MKT.view = "cards"; mktRender(); };
  document.getElementById("us-mkt-list-btn").onclick = () => { MKT.view = "list"; mktRender(); };
}

function mktView(a) {
  if (MKT.screen === "vcp") return a;
  const v = a[MKT.screen];
  if (!v) return null;
  const out = {...a};
  MKT_SCREEN_FIELDS.forEach(k => { delete out[k]; });
  return Object.assign(out, v);
}

function mktPool() {
  const th = document.getElementById("us-mkt-theme").value, q = document.getElementById("us-mkt-find").value.trim().toLowerCase();
  const src = Object.entries(MKT.data.stocks || {}).concat(MKT.scope === "all" && MKT.all ? Object.entries(MKT.all.stocks || {}) : []);
  return src.map(([code, a]) => [code, mktView(a)]).filter(([code, v]) => {
    const d = MKT_BY_CODE[code];
    if (!d || !v) return false;
    if (th && d.sector !== th) return false;
    if (q && !((d.name || "") + " " + (d.symbol || d.code || "")).toLowerCase().includes(q)) return false;
    return true;
  });
}

function mktBase(a) { return a.base || (a.breakout && {...a.breakout.base, pivot: a.breakout.pivot}); }

function mktRows() {
  const so = MKT_SORTS[document.getElementById("us-mkt-sort").value] || MKT_SORTS.rs;
  return mktPool().filter(([, a]) => a.stage === MKT.stage).sort((x, y) => {
    const a = so.k(x[1]), b = so.k(y[1]);
    if (a == null && b == null) return 0;
    if (a == null) return 1;
    if (b == null) return -1;
    return (a - b) * so.dir;
  });
}

function mktRender() {
  if (!MKT.data) return;
  document.getElementById("us-mkt-upd").textContent = MKT.data.as_of ? "Updated " + MKT.data.as_of : "";
  const scr = MKT_SCREENS[MKT.screen] || MKT_SCREENS.vcp;
  const pool = mktPool(), counts = {};
  pool.forEach(([, a]) => { counts[a.stage] = (counts[a.stage] || 0) + 1; });
  document.getElementById("us-mkt-stages").innerHTML = MKT_STAGES.map(s => mktStageText(s.k)).map(s => `
    <button type="button" class="bp-stage${s.k === MKT.stage ? " on" : ""}" data-mkt-stage="${s.k}">
      <span class="bp-tap">tap to view ›</span><b>${counts[s.k] || 0}</b><span>${esc(s.label)}</span><small>${esc(s.hint)}</small>
    </button>`).join("");
  document.getElementById("us-mkt-title").textContent = MKT.scope === "all" ? scr.heroAll : scr.hero;
  const totalScanned = MKT.scope === "all" && MKT.all ? (MKT.all.scanned || 0) + Object.keys(MKT.data.stocks || {}).length : null;
  document.getElementById("us-mkt-sub").textContent = totalScanned
    ? `All ${totalScanned.toLocaleString("en-US")} ${scr.sub}`
    : scr.subBoard.replace(/^\w/, c => c.toUpperCase());
  document.querySelectorAll("#us-mkt-stages [data-mkt-stage]").forEach(b => {
    b.onclick = () => { MKT.stage = b.dataset.mktStage; mktRender(); };
  });

  const mwKey = MKT.screen === "vcp" ? "market_breakouts" : "market_breakouts_" + MKT.screen;
  const mw = document.getElementById("us-mkt-mw"), mwList = MKT.data[mwKey] || [];
  mw.hidden = !mwList.length;
  if (mwList.length) mw.textContent = `↗ ${mwList.length} broke out market-wide ›`;

  const codes = new Set(pool.map(([c]) => c));
  const feed = (MKT.data.feed || []).concat(MKT.scope === "all" && MKT.all ? MKT.all.feed || [] : [])
    .filter(f => codes.has(f.code) && (f.screen || "vcp") === MKT.screen)
    .sort((x, y) => x.order - y.order || Math.abs(y.chg_pct || 0) - Math.abs(x.chg_pct || 0));
  document.getElementById("us-mkt-feed").innerHTML = `<h3><i>⟲</i>What changed since last close?</h3>
    ${feed.length ? feed.slice(0, 3).map(mktFeedItem).join("") : '<p class="bp-empty" style="padding:14px">Nothing changed stage in the last session.</p>'}
    ${feed.length > 3 ? `<button type="button" class="bp-link" id="us-mkt-full-feed">Show the full feed (${feed.length}) ›</button>` : ""}`;
  document.querySelectorAll("#us-mkt-feed [data-mkt-code]").forEach(b => { b.onclick = () => mktOpenChart(b.dataset.mktCode); });
  const ff = document.getElementById("us-mkt-full-feed");
  if (ff) ff.onclick = () => mktFullFeed(feed);

  const stage = mktStageText(MKT.stage);
  const rows = mktRows();
  document.getElementById("us-mkt-list-title").textContent = stage.title;
  document.getElementById("us-mkt-list-n").textContent = rows.length;
  document.getElementById("us-mkt-desc").textContent = stage.desc;
  document.getElementById("us-mkt-cards-btn").classList.toggle("on", MKT.view === "cards");
  document.getElementById("us-mkt-list-btn").classList.toggle("on", MKT.view === "list");
  const box = document.getElementById("us-mkt-results");
  if (!rows.length) {
    box.innerHTML = `<p class="bp-empty">No ${MKT.scope === "all" ? "stock" : "board company"} is in "${esc(stage.label)}" ${document.getElementById("us-mkt-theme").value || document.getElementById("us-mkt-find").value ? "with these filters" : "right now"}.</p>`;
    return;
  }

  if (MKT.view === "list") {
    box.innerHTML = `<div class="bp-table-wrap"><table class="bp-table"><thead><tr>
      <th>Company</th><th>Price</th><th>Chg</th><th>RS</th><th>vs pivot</th><th>Tightening</th><th>Dry-up</th><th>Up/down</th><th>Base (wks)</th><th>From 52w high</th></tr></thead><tbody>
      ${rows.map(([code, a]) => {
        const d = MKT_BY_CODE[code], b = mktBase(a);
        return `<tr data-mkt-code="${esc(code)}">
        <td><b>${esc(d.name || code)}</b> <span class="bpc-sym">${esc(d.symbol || code)}</span></td>
        <td>${fmtUSD(a.last.c)}</td><td class="${a.last.chg_pct >= 0 ? "up" : "dn"}">${a.last.chg_pct >= 0 ? "+" : ""}${a.last.chg_pct}%</td>
        <td>${a.rs ?? "—"}</td><td>${a.now_vs_pivot == null ? "—" : (a.now_vs_pivot >= 0 ? "+" : "") + a.now_vs_pivot + "%"}</td>
        <td>${a.atr_ratio == null ? "—" : a.atr_ratio.toFixed(2) + "×"}</td><td>${a.vol_dryup == null ? "—" : a.vol_dryup.toFixed(2) + "×"}</td>
        <td>${a.updown == null ? "—" : a.updown.toFixed(2)}</td><td>${b ? b.weeks : "—"}</td>
        <td>${a.from_high == null ? "—" : a.from_high + "%"}</td></tr>`;
      }).join("")}
      </tbody></table></div>`;
  } else {
    box.innerHTML = `<div class="bp-cards">${rows.map(([code, a]) => mktCard(code, a)).join("")}</div>`;
    if (MKT_OBS) MKT_OBS.disconnect();
    MKT_OBS = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting) { MKT_OBS.unobserve(e.target); galFetch(e.target.dataset.mktChart, e.target.dataset.mktBoard === "1"); }
    }), {rootMargin: "400px 0px"});
    box.querySelectorAll("[data-mkt-chart]").forEach(el => { if (!GSERIES.has(el.dataset.mktChart)) MKT_OBS.observe(el); });
  }
  box.querySelectorAll("[data-mkt-code]").forEach(el => {
    el.onclick = e => { if (!e.target.closest("[data-mkt-star]")) mktOpenChart(el.dataset.mktCode); };
  });
  box.querySelectorAll("[data-mkt-star]").forEach(b => {
    b.onclick = e => { e.stopPropagation(); toggleStar(b.dataset.mktStar); mktStarButtons(b.dataset.mktStar); };
  });
}

function mktMeasure(label, val) { return `<div><span>${esc(label)}</span><b>${esc(String(val))}</b></div>`; }

function mktCard(code, a) {
  const d = MKT_BY_CODE[code], l = a.last, b = mktBase(a), on = !d.universe && WATCH.has(code);
  const chg = l.chg_pct >= 0 ? "up" : "dn";
  const ipo = MKT.screen === "ipo" && a.listed;
  const listedLine = ipo ? `<div class="bpc-listed">Listed ${esc(a.listed)} · ${a.sessions_listed} sessions ago · post-listing high ${fmtUSD(a.listing_high)} (${a.from_listing_high}% above today)</div>` : "";
  return `<article class="bpc" data-mkt-code="${esc(code)}" style="cursor:pointer">
    <div class="bpc-top">
      <div style="min-width:0">
        <div class="bpc-title"><span class="bpc-name">${esc(d.name || code)}</span><span class="bpc-sym">${esc(d.symbol || code)}</span>
          ${d.universe ? "" : `<button type="button" class="bpc-star${on ? " on" : ""}" data-mkt-star="${esc(code)}" title="${on ? "Remove from" : "Add to"} watchlist">${on ? "★" : "☆"}</button>`}</div>
        <div class="bpc-ind">${d.universe ? `${esc(d.exchange || "")} · not on the board` : esc(d.sector || "")}</div>
      </div>
      <div class="bpc-right"><div class="bpc-px">${fmtUSD(l.c)} <span class="${chg}">(${l.chg_pct >= 0 ? "+" : ""}${l.chg_pct}%)</span></div></div>
    </div>
    ${listedLine}
    <div class="bpc-ohlc"><b>${esc(a.asof)}</b><span>O ${(+l.o).toFixed(2)}</span><span>H ${(+l.h).toFixed(2)}</span><span>L ${(+l.l).toFixed(2)}</span><span>C ${(+l.c).toFixed(2)}</span><span>RS ${a.rs ?? "—"}</span></div>
    <div class="bpc-chart" data-mkt-chart="${esc(code)}" data-mkt-board="${d.universe ? "0" : "1"}" title="Open the full chart">${galThumb(code)}</div>
    <div class="bpc-m">
      ${mktMeasure("RS rating", a.rs ?? "—")}
      ${mktMeasure("Now vs pivot (%)", a.now_vs_pivot == null ? "—" : (a.now_vs_pivot >= 0 ? "+" : "") + a.now_vs_pivot + "%")}
      ${mktMeasure("Tightening (ATR ratio)", a.atr_ratio == null ? "—" : a.atr_ratio.toFixed(2) + "×")}
      ${mktMeasure("Volume dry-up", a.vol_dryup == null ? "—" : a.vol_dryup.toFixed(2) + "×")}
      ${mktMeasure("Up/down volume, net", a.updown == null ? "—" : a.updown.toFixed(2))}
      ${mktMeasure("From 52-week high", a.from_high == null ? "—" : a.from_high + "%")}
    </div>
    ${a.note ? `<div class="bpc-note">${esc(a.note)}</div>` : ""}
    <div class="bpc-foot">${b ? `<span class="bpc-stage">${b.weeks}-week base · ${fmtUSD(b.pivot || a.pivot)} pivot</span>` : "<span></span>"}<span class="bpc-mood ${a.mood === "Powering up" ? "up" : "dn"}">${a.mood === "Powering up" ? "▲" : "▼"} ${esc(a.mood)}</span></div>
  </article>`;
}

function mktStarButtons(code) {
  const on = WATCH.has(code);
  document.querySelectorAll(`[data-mkt-star="${CSS.escape(code)}"]`).forEach(b => {
    b.classList.toggle("on", on);
    b.textContent = on ? "★" : "☆";
    b.title = (on ? "Remove from" : "Add to") + " watchlist";
  });
}

function mktFeedItem(f) {
  return `<button type="button" class="bp-fi" data-mkt-code="${esc(f.code)}"><span><b>${esc(f.name || f.code)}</b> ${esc(f.text)}</span>
    <span class="bp-fpx">${fmtUSD(f.close)} <span class="${(f.chg_pct || 0) >= 0 ? "up" : "dn"}">${(f.chg_pct || 0) >= 0 ? "+" : ""}${f.chg_pct}%</span></span></button>`;
}

function mktFullFeed(feed) {
  const drawer = document.getElementById("us-drawer"), scrim = document.getElementById("us-scrim");
  drawer.innerHTML = `<button class="close" id="us-mkt-feed-close" aria-label="Close">&times;</button>
    <div class="us-drawer-body"><h3>What changed since last close — ${esc(MKT.data.as_of || "")}</h3>
    ${feed.map(mktFeedItem).join("")}</div>`;
  drawer.classList.add("on");
  scrim.classList.add("on");
  document.getElementById("us-mkt-feed-close").onclick = closeChart;
  scrim.onclick = closeChart;
  drawer.querySelectorAll("[data-mkt-code]").forEach(b => { b.onclick = () => mktOpenChart(b.dataset.mktCode); });
}

function mktMarketWide() {
  const mwKey = MKT.screen === "vcp" ? "market_breakouts" : "market_breakouts_" + MKT.screen;
  const list = MKT.data[mwKey] || [];
  const label = (MKT_SCREENS[MKT.screen] || MKT_SCREENS.vcp).label;
  const drawer = document.getElementById("us-drawer"), scrim = document.getElementById("us-scrim");
  drawer.innerHTML = `<button class="close" id="us-mkt-mw-close" aria-label="Close">&times;</button>
    <div class="us-drawer-body"><h3>↗ ${list.length} broke out market-wide — ${esc(MKT.data.as_of || "")}</h3>
    <p class="section-copy">Every actively traded US stock that closed above its ${esc(label)} pivot today on at least 1.4× usual volume.</p>
    ${list.map(x => `<button type="button" class="bp-fi" data-mkt-code="${esc(x.symbol)}"><span><b>${esc(x.name || x.symbol)}</b>${x.on_board ? ' <span class="us-tag">board</span>' : ""} broke out ${x.above_pct}% above pivot on ${x.vol_x}× volume</span>
      <span class="bp-fpx">${fmtUSD(x.close)} <span class="${x.chg_pct >= 0 ? "up" : "dn"}">${x.chg_pct >= 0 ? "+" : ""}${x.chg_pct}%</span></span></button>`).join("")}
    </div>`;
  drawer.classList.add("on");
  scrim.classList.add("on");
  document.getElementById("us-mkt-mw-close").onclick = closeChart;
  scrim.onclick = closeChart;
  drawer.querySelectorAll("[data-mkt-code]").forEach(b => { b.onclick = () => mktOpenChart(b.dataset.mktCode); });
}

function mktOpenChart(code, isBoard) {
  if (isBoard === undefined) { const d = MKT_BY_CODE[code]; isBoard = !!(d && !d.universe); }
  openChart(code, isBoard);
}

/* ================================================================
   Insider Transactions -- the US board's counterpart to India's Bulk &
   Block Deals tab (app.js's dl* functions / /api/deals), but a genuinely
   different signal: SEC Form 4 insider disclosure (backend/app/movers/
   insider_us.py, scripts/run_insider_us.py), not NSE's counterparty-named
   bulk/block deal disclosure -- the US has no equivalent to the latter.
   Always board-scoped (SEC has no single market-wide "every insider
   transaction today" file the way NSE gives one CSV for deals), so there
   is no "board companies only" toggle here -- everything already is.
   ================================================================ */
const INS = {data: null, built: false};
const INS_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function insDay(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${INS_MONTHS[m - 1]} ${y}`;
}

let INS_SUB_WIRED = false;
function insWireSubtabs() {
  if (INS_SUB_WIRED) return;
  INS_SUB_WIRED = true;
  document.querySelectorAll("[data-inssub]").forEach(t => {
    t.onclick = () => {
      document.querySelectorAll("[data-inssub]").forEach(x => {
        x.classList.toggle("on", x === t);
        x.setAttribute("aria-selected", String(x === t));
      });
      const tab = t.dataset.inssub;
      document.getElementById("inssub-insider").hidden = tab !== "insider";
      document.getElementById("inssub-ann").hidden = tab !== "ann";
      if (tab === "ann") openAnnouncementsUs();
      else if (INS.data) {
        document.getElementById("ins-asof").textContent =
          `${insDay(INS.data.from_date)} – ${insDay(INS.data.to_date)} · ${INS.data.count} transactions`;
      }
    };
  });
}

async function openInsider() {
  insWireSubtabs();
  const body = document.getElementById("ins-body");
  if (!INS.data) {
    body.innerHTML = '<p class="view-hint">Loading…</p>';
    let j;
    try { j = await fetchJSON("/api/insider-us"); } catch (e) { j = null; }
    if (!j || !j.transactions || !j.transactions.length) {
      body.innerHTML = '<p class="view-hint">No insider-transaction scan has run yet. It runs automatically every night.</p>';
      return;
    }
    INS.data = j;
  }
  if (!INS.built) insBuildControls();
  document.getElementById("ins-asof").textContent =
    `${insDay(INS.data.from_date)} – ${insDay(INS.data.to_date)} · ${INS.data.count} transactions`;
  insRenderTable();
}

function insBuildControls() {
  INS.built = true;
  const dates = [...new Set(INS.data.transactions.map(d => d.trade_date))].sort().reverse();
  const dateSel = document.getElementById("ins-date");
  dateSel.innerHTML = '<option value="">Every day on record</option>'
    + dates.map(d => `<option value="${d}">${esc(insDay(d))}${d === dates[0] ? " (latest)" : ""}</option>`).join("");
  dateSel.onchange = insRenderTable;
  let t = 0;
  document.getElementById("ins-q").oninput = () => { clearTimeout(t); t = setTimeout(insRenderTable, 120); };
  document.getElementById("ins-qclear").onclick = () => { document.getElementById("ins-q").value = ""; insRenderTable(); };
  ["ins-type", "ins-side", "ins-sort"].forEach(id => { document.getElementById(id).onchange = insRenderTable; });
  document.getElementById("ins-clear").onclick = () => {
    document.getElementById("ins-q").value = "";
    document.getElementById("ins-date").value = "";
    document.getElementById("ins-type").value = "";
    document.getElementById("ins-side").value = "";
    document.getElementById("ins-sort").value = "value";
    insRenderTable();
  };
}

function insRows() {
  if (!INS.data) return [];
  const q = document.getElementById("ins-q").value.trim().toLowerCase();
  const dateF = document.getElementById("ins-date").value;
  const type = document.getElementById("ins-type").value;
  const side = document.getElementById("ins-side").value;
  const sort = document.getElementById("ins-sort").value;
  let rows = INS.data.transactions.filter(d => {
    if (dateF && d.trade_date !== dateF) return false;
    if (type === "market" && !d.is_market) return false;
    if (side && d.side !== side) return false;
    if (q && !(d.symbol.toLowerCase() + " " + (d.name || "").toLowerCase() + " " + (d.insider || "").toLowerCase()).includes(q)) return false;
    return true;
  });
  rows = rows.slice().sort(sort === "date"
    ? (a, b) => b.trade_date.localeCompare(a.trade_date) || b.value_usd - a.value_usd
    : (a, b) => b.value_usd - a.value_usd);
  return rows;
}

function insRenderTable() {
  const body = document.getElementById("ins-body");
  if (!body) return;
  const rows = insRows();
  document.getElementById("ins-count").textContent = `${rows.length} of ${INS.data.transactions.length}`;
  body.innerHTML = rows.length ? `<div class="mv-tablewrap"><table class="mv-table"><thead><tr>
      <th>Date</th><th>Company</th><th>Insider</th><th>Type</th><th>Side</th><th class="n">Shares</th><th class="n">Price</th><th class="n">Value</th>
    </tr></thead><tbody>${rows.map(insRowHtml).join("")}</tbody></table></div>`
    : '<p class="view-hint">No transaction matches — clear the search or pick another filter.</p>';
}

function insRowHtml(d) {
  return `<tr>
    <td>${esc(insDay(d.trade_date))}</td>
    <td><b class="mv-tick">${esc(d.symbol)}</b><span class="mv-name">${esc(d.name || d.symbol)}</span></td>
    <td><b class="mv-tick">${esc(d.insider)}</b><span class="mv-name">${esc(d.relationship || "")}</span></td>
    <td><span class="mv-badge ${d.is_market ? "mv-high" : "mv-low"}">${esc(d.label)}</span></td>
    <td><span class="${d.side === "BUY" ? "up" : "dn"}">${esc(d.side)}</span></td>
    <td class="n">${Math.round(d.shares).toLocaleString("en-US")}</td>
    <td class="n">${fmtUSD(d.price)}</td>
    <td class="n">${fmtUSD(d.value_usd)}</td>
  </tr>`;
}

/* Announcements sub-tab of the same section -- SEC Form 8-K, the US
   board's counterpart to India's Announcements sub-tab (app.js's AN* /
   /api/announcements). Genuinely closer to a straight port than Insider
   Transactions is (see backend/app/movers/announcements_us.py's
   docstring): 8-K IS the US "material corporate event" filing, with SEC's
   own structured item codes standing in for NSE's free-text category --
   no equivalent to NSE's ~30% "routine filing" noise to filter either.
   Always board-scoped, same reason as Insider Transactions. */
const ANN = {data: null, built: false};

async function openAnnouncementsUs() {
  const body = document.getElementById("ann-body");
  if (!ANN.data) {
    body.innerHTML = '<p class="view-hint">Loading…</p>';
    let j;
    try { j = await fetchJSON("/api/announcements-us"); } catch (e) { j = null; }
    if (!j || !j.announcements || !j.announcements.length) {
      body.innerHTML = '<p class="view-hint">No announcements scan has run yet. It runs automatically every night.</p>';
      return;
    }
    ANN.data = j;
  }
  if (!ANN.built) annBuildControls();
  annRenderTable();
}

function annBuildControls() {
  ANN.built = true;
  const dates = [...new Set(ANN.data.announcements.map(d => d.date))].sort().reverse();
  const dateSel = document.getElementById("ann-date");
  dateSel.innerHTML = '<option value="">Every day on record</option>'
    + dates.map(d => `<option value="${d}">${esc(insDay(d))}${d === dates[0] ? " (latest)" : ""}</option>`).join("");
  dateSel.onchange = annRenderTable;
  let t = 0;
  document.getElementById("ann-q").oninput = () => { clearTimeout(t); t = setTimeout(annRenderTable, 120); };
  document.getElementById("ann-qclear").onclick = () => { document.getElementById("ann-q").value = ""; annRenderTable(); };
  ["ann-kind", "ann-sort"].forEach(id => { document.getElementById(id).onchange = annRenderTable; });
  document.getElementById("ann-clear").onclick = () => {
    document.getElementById("ann-q").value = "";
    document.getElementById("ann-date").value = "";
    document.getElementById("ann-kind").value = "";
    document.getElementById("ann-sort").value = "date";
    annRenderTable();
  };
}

function annRows() {
  if (!ANN.data) return [];
  const q = document.getElementById("ann-q").value.trim().toLowerCase();
  const dateF = document.getElementById("ann-date").value;
  const kind = document.getElementById("ann-kind").value;
  const sort = document.getElementById("ann-sort").value;
  let rows = ANN.data.announcements.filter(d => {
    if (dateF && d.date !== dateF) return false;
    if (kind && d.category !== kind) return false;
    if (q && !(d.symbol.toLowerCase() + " " + (d.name || "").toLowerCase() + " " + (d.summary || "").toLowerCase()).includes(q)) return false;
    return true;
  });
  rows = rows.slice().sort(sort === "weight"
    ? (a, b) => b.weight - a.weight || b.date.localeCompare(a.date)
    : (a, b) => b.date.localeCompare(a.date) || b.weight - a.weight);
  return rows;
}

function annRenderTable() {
  const body = document.getElementById("ann-body");
  if (!body) return;
  const rows = annRows();
  document.getElementById("ann-count").textContent = `${rows.length} of ${ANN.data.announcements.length}`;
  body.innerHTML = rows.length ? `<div class="mv-tablewrap"><table class="mv-table"><thead><tr>
      <th>Date</th><th>Company</th><th>Category</th><th>Items</th><th>Summary</th>
    </tr></thead><tbody>${rows.map(annRowHtml).join("")}</tbody></table></div>`
    : '<p class="view-hint">No announcement matches — clear the search or pick another filter.</p>';
  body.querySelectorAll("[data-ann-url]").forEach(tr => {
    tr.onclick = () => window.open(tr.dataset.annUrl, "_blank", "noopener");
  });
}

const ANN_KIND_CLS = {"results": "mv-high", "order win": "mv-high", "M&A": "mv-high", "regulatory or legal": "mv-med"};
function annRowHtml(d) {
  return `<tr data-ann-url="${esc(d.url)}">
    <td>${esc(insDay(d.date))}</td>
    <td><b class="mv-tick">${esc(d.symbol)}</b><span class="mv-name">${esc(d.name || d.symbol)}</span></td>
    <td><span class="mv-badge ${ANN_KIND_CLS[d.category] || "mv-low"}">${esc(d.category)}</span></td>
    <td>${esc((d.items || []).join(", "))}</td>
    <td>${esc(d.summary || "—")}</td>
  </tr>`;
}
