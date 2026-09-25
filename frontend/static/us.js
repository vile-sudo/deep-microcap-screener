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

/* The screen filters -- the same nine India's Screens page offers, over the
   same field names (backend/scripts/us_lenses.py fills them). The first
   seven are lenses and work one at a time, exactly like India's (clicking
   one switches to it, clicking it again clears it); the last two stack. */
const UTG = {overhang: false, heavycap: false, guide15: false, guideany: false, turn: false, caputil: false,
  pivot: false, haslens: false, ongate: false, nolens: false};
const ULENSES = ["overhang", "heavycap", "guide15", "guideany", "turn", "caputil", "pivot"];
const UTG_TEST = {
  overhang: d => !!d.capex_overhang,
  heavycap: d => !!d.capex_heavy,
  guide15: d => !!d.guidance_over15,
  guideany: d => !!d.guidance_flag,
  turn: d => !!d.pat_turnaround,
  caputil: d => !!d.capacity_util_flag,
  pivot: d => !!d.product_pivot_flag,
  haslens: d => !!d.has_lens_data,
  ongate: d => !(d.gate_failures || []).length,
  nolens: d => !d.has_lens_data,
};

function signalsHtml(d) {
  const s = [];
  if (d.capex_overhang) s.push('<span class="us-sig warn" title="P/E above 40 and CWIP at least 15% of net PP&amp;E">&#9873; PE+CWIP</span>');
  else if (d.capex_heavy) s.push('<span class="us-sig warn" title="CWIP at least 25% of net PP&amp;E">&#127959; heavy capex</span>');
  if (d.guidance_over15) s.push(`<span class="us-sig" title="Revenue growth guided above 15%${d.guidance_derived ? " (derived from the guided dollar range)" : ""}">&#9650; guides &gt;15%</span>`);
  else if (d.guidance_flag) s.push('<span class="us-sig grey" title="A forward revenue statement, not quantified as growth above 15%">&#9650; guidance</span>');
  if (d.pat_turnaround) s.push('<span class="us-sig" title="Latest quarter profitable after a loss in one of the previous three">&#8635; PAT+</span>');
  if (d.capacity_util_flag) s.push('<span class="us-sig" title="Management says capacity utilization will rise from a near-term period">&#9881; capacity</span>');
  if (d.product_pivot_flag) s.push('<span class="us-sig" title="A product-mix change or new venture tied to a shift in demand">&#8644; pivot</span>');
  if ((d.gate_failures || []).length) s.push(`<span class="us-sig grey" title="${esc(d.gate_failures.join("; "))}">fails ${d.gate_failures.length} gate${d.gate_failures.length > 1 ? "s" : ""}</span>`);
  return s.join("") || "—";
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
    <td class="n">${d.cwip_pct_net_block == null ? "—" : fmtN(d.cwip_pct_net_block) + "%"}</td>
    <td class="n">${d.guidance_pct == null ? "—" : fmtN(d.guidance_pct) + "%"}</td>
    <td class="us-wrap">${signalsHtml(d)}</td>
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
    for (const k in UTG) if (UTG[k] && !UTG_TEST[k](d)) return false;
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

function syncLensButtons() {
  document.querySelectorAll("[data-ustg]").forEach(b => {
    const k = b.dataset.ustg;
    b.classList.toggle("on", !!UTG[k]);
    const n = b.querySelector(".tc");
    if (n) n.textContent = DATA.filter(UTG_TEST[k]).length;
  });
}

function render() {
  syncLensButtons();
  const rows = currentRows();
  document.getElementById("us-count").textContent = `${rows.length} of ${DATA.length} companies`;
  const tbody = document.getElementById("us-tbody");
  if (!DATA.length) {
    document.querySelector(".tablewrap").innerHTML = '<div class="us-empty">No US companies on the board yet — the daily screen hasn\'t added any. Check back after the next run.</div>';
    return;
  }
  tbody.innerHTML = rows.length
    ? rows.map(rowHtml).join("")
    : '<tr><td colspan="12" class="us-empty">No company matches these filters.</td></tr>';
  tbody.querySelectorAll("tr[data-code]").forEach(tr => {
    tr.onclick = e => { if (!e.target.closest("[data-star]")) openDrawer(tr.dataset.code, rows); };
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
  updateAlerts();
  try {
    await fetch("/api/watchlist/" + encodeURIComponent(code) + "?market=us", {
      method: on ? "POST" : "DELETE", credentials: "same-origin",
    });
  } catch (e) {
    if (on) WATCH.delete(code); else WATCH.add(code);
    render();
  }
}

/* ---------- company detail: the same scorecard layout as the India board ----------
   Header (theme, rank, links, prev/next through the list you are looking at), a grid
   of the headline numbers, then one section per question: capex, guidance, profit
   trajectory, score breakdown, business, moat, flags. All of it comes from the same
   record the table row does (backend/scripts/us_auto_screen.py, us_lenses.py). */
const US_PILLARS = [["Moat", "s_moat", 25], ["Reshoring", "s_reshoring", 20], ["Insider", "s_insider", 15],
  ["Under-covered", "s_undercovered", 15], ["Financials", "s_financials", 10]];
const US_SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5"];
const US_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
let DRAWER_LIST = [];

function usRank(d) {
  if (d.final_score == null) return null;
  return DATA.filter(x => x.final_score != null && x.final_score > d.final_score).length + 1;
}

/* quarterly net income, $m: bars from a zero line, red below it, latest quarter solid */
function usPatChart(d) {
  const p = d.pat_series_usd_m || [];
  if (!p.length) return '<p class="nd" style="font-size:12.5px;margin:0">No quarterly profit data published yet.</p>';
  const W = 520, H = 112, B = 26, T = 10;
  const vals = p.map(x => x[1]);
  const hi = Math.max(0, ...vals), lo = Math.min(0, ...vals), span = (hi - lo) || 1;
  const zero = T + (hi / span) * (H - T - B);
  const step = (W - 8) / p.length, bw = Math.min(46, step - 8);
  let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label="Net income by quarter">`;
  s += `<line x1="0" x2="${W}" y1="${zero}" y2="${zero}" stroke="var(--axis)" stroke-width="1"/>`;
  p.forEach((x, i) => {
    const v = x[1], cx = 4 + i * step + step / 2;
    const y = v >= 0 ? zero - (v / span) * (H - T - B) : zero;
    const h = Math.max(1.5, Math.abs(v) / span * (H - T - B));
    const col = v < 0 ? "var(--crit)" : (i === p.length - 1 ? "var(--s1)" : "var(--s3)");
    const m = String(x[0]).slice(0, 7).split("-");
    const lab = m.length === 2 ? US_MONTHS[+m[1] - 1] + " '" + m[0].slice(2) : String(x[0]);
    s += `<rect x="${cx - bw / 2}" y="${y}" width="${bw}" height="${h}" rx="2" fill="${col}" fill-opacity="${i === p.length - 1 ? .95 : .62}"><title>quarter ended ${esc(x[0])}: $${v}m</title></rect>`;
    s += `<text x="${cx}" y="${H - 12}" text-anchor="middle" class="tk" style="font-size:9px">${esc(lab)}</text>`;
    s += `<text x="${cx}" y="${v >= 0 ? y - 3 : y + h + 9}" text-anchor="middle" class="tk" style="font-size:9px">${v}</text>`;
  });
  return s + "</svg>";
}

function usDrawerBody(d) {
  const gates = (d.gate_failures || []).filter(Boolean);
  const pen = (d.penalty_detail || []).filter(Boolean);
  const warns = (d.warnings || []).filter(Boolean);
  const auto = d.source === "us-auto" || d.screen === "us-auto";
  const sources = (d.evidence_sources || []).map(u => `<a class="lnk" href="${esc(u)}" target="_blank" rel="noopener">${/sec\.gov/i.test(u) ? "SEC filings" : "source"} ↗</a>`).join(" ");
  const netPpe = d.cwip_usd_m != null && d.cwip_pct_net_block ? d.cwip_usd_m / d.cwip_pct_net_block * 100 : null;
  let out = `<div class="kv">
      <div><span>Composite score</span><b style="color:var(--s1)">${fmtN(d.final_score)}</b></div>
      <div><span>Market cap</span><b>${fmtUSDShort(d.market_cap_usd)}</b></div>
      <div><span>Price</span><b>${d.price ? "$" + fmtN(d.price, 2) : "—"}</b></div>
      <div><span>P/E</span><b>${fmtN(d.pe)}</b></div>
      <div><span>CWIP</span><b>${d.cwip_usd_m == null ? "—" : "$" + fmtN(d.cwip_usd_m) + "m"}</b></div>
      <div><span>CWIP / net PP&amp;E</span><b${d.capex_overhang ? ' style="color:var(--crit)"' : ""}>${d.cwip_pct_net_block == null ? "—" : fmtN(d.cwip_pct_net_block) + "%"}</b></div>
      <div><span>Insider ownership</span><b>${d.insider_pct == null ? "—" : fmtN(d.insider_pct) + "%"}</b></div>
      <div><span>Institutions</span><b>${instCell(d)}</b></div>
      <div><span>ROE</span><b>${d.roe_pct == null ? "—" : fmtN(d.roe_pct) + "%"}</b></div>
      <div><span>Holders of record</span><b>${d.num_shareholders ? Number(d.num_shareholders).toLocaleString("en-US") : "—"}</b></div>
      <div><span>Guided growth</span><b>${d.guidance_pct == null ? "—" : fmtN(d.guidance_pct, 0) + "%"}</b></div>
      <div><span>Added</span><b style="font-size:13px">${esc(d.added_on || "—")}</b></div>
    </div>`;

  if (auto) {
    out += `<div class="sec auto-sec"><h4>Auto-added by the daily US screen</h4>
      <p>Added on ${esc(d.added_on || "")} because the company describes a moat in its own SEC 10-K and the numbers clear the board's gates.
      The figures are from Finnhub and SEC EDGAR and the moat is the company's own wording, matched by rules &mdash;
      nobody has researched it yet, so read it as a lead, not a verdict.</p>
      ${sources ? `<p class="auto-src">Evidence: ${sources}</p>` : ""}</div>`;
  }
  if (gates.length) {
    out += `<div class="sec"><h4>Fails the fundamentals gates on</h4><ul class="gates">${gates.map(g => `<li>✕ ${esc(g)}</li>`).join("")}</ul></div>`;
  }

  if (d.has_lens_data) {
    out += `<div class="sec"><h4>Capex &amp; CWIP — capacity that is not earning yet</h4>`
      + (d.cwip_usd_m == null
        ? '<p class="nd">No construction-in-progress balance was found in the latest filing.</p>'
        : `<p><b>$${fmtN(d.cwip_usd_m)}m</b> in construction in progress${netPpe ? ` against net PP&amp;E of <b>$${fmtN(netPpe, 0)}m</b> &mdash; ${fmtN(d.cwip_pct_net_block)}%` : ""}.</p>`)
      + (d.capex_overhang ? `<p style="margin-top:9px;color:var(--crit);font-size:12.5px"><b>⚑ Flagged.</b> A P/E of ${fmtN(d.pe)} is being paid while ${fmtN(d.cwip_pct_net_block, 0)}% of the asset base is still under construction &mdash; the multiple assumes the new capacity works.</p>`
        : d.capex_heavy ? '<p style="margin-top:9px;font-size:12.5px"><b>Heavy capex:</b> construction in progress is at least 25% of net PP&amp;E.</p>' : "")
      + "</div>";

    out += `<div class="sec"><h4>Management's own growth outlook</h4>`
      + (d.guidance_note
        ? `<p class="quote">${esc(d.guidance_note)}</p><p class="qsrc">${d.guidance_pct != null ? `implies <b style="color:${d.guidance_over15 ? "var(--good-ink)" : "var(--ink2)"}">${fmtN(d.guidance_pct, 0)}% revenue growth</b>${d.guidance_derived ? " (derived from the guided dollar range)" : ""}` : "no percentage given"}</p>`
        : '<p class="nd">No forward revenue-growth statement from management was found in filings, earnings calls or investor presentations. Backlog announcements and capacity expansions were checked and deliberately not counted as guidance.</p>')
      + "</div>";

    if (d.capacity_util_note) out += `<div class="sec"><h4>Capacity utilization</h4><p class="quote">${esc(d.capacity_util_note)}</p></div>`;
    if (d.product_pivot_note) out += `<div class="sec"><h4>Product-mix pivot</h4><p class="quote">${esc(d.product_pivot_note)}</p></div>`;

    out += `<div class="sec"><h4>Profit trajectory · quarterly net income, $m</h4>${usPatChart(d)}`
      + (d.pat_turnaround ? '<p style="margin-top:8px;color:var(--s1);font-size:12.5px"><b>↻ Latest quarter profitable after a loss in one of the previous three.</b></p>' : "")
      + "</div>";
  } else {
    out += `<div class="sec"><h4>Capex, guidance &amp; profit trajectory</h4>
      <p class="caveat">Not pulled for this name yet. The capex, guidance and quarterly-profit pass runs daily after the screen and covers new names the day after they are added.</p></div>`;
  }

  out += usEarningsSection(d) + usInstitutionsSection(d) + usNewsSection(d);

  if (US_PILLARS.some(p => d[p[1]] != null)) {
    out += `<div class="sec"><h4>Score breakdown</h4>` + US_PILLARS.map(([l, k, mx], i) => {
      const v = +d[k] || 0, pct = Math.max(2, v / mx * 100);
      return `<div class="bar"><i>${l}</i><span class="track"><span class="fill" style="width:${pct}%;background:var(${US_SERIES[i]})"></span></span>
        <b>${v.toFixed(1)}<span style="color:var(--muted);font-weight:400">/${mx}</span></b></div>`;
    }).join("")
      + (pen.length ? `<p style="margin-top:10px;font-size:12px;color:var(--ink2)"><b style="color:var(--crit)">Risk penalty −${fmtN(d.risk_penalty, 0)}:</b> ${pen.map(esc).join(" · ")}</p>` : "")
      + (d.score_rationale ? `<p class="caveat" style="margin-top:8px">${esc(d.score_rationale)}</p>` : "")
      + "</div>";
  }

  if (d.business) out += `<div class="sec"><h4>What the business actually does</h4><p>${esc(d.business)}</p></div>`;
  if (d.moat_note) out += `<div class="sec"><h4>The moat — the company's own words</h4><p>${esc(d.moat_note)}</p></div>`;
  if (d.why_obscure) out += `<div class="sec"><h4>Why the market ignores it</h4><p>${esc(d.why_obscure)}</p></div>`;
  if (d.risk_note) out += `<div class="sec"><h4>The biggest risk</h4><p>${esc(d.risk_note)}</p></div>`;
  if (warns.length) out += `<div class="sec"><h4>Flags &amp; risks</h4><ul class="warns">${warns.map(w => `<li>⚠ ${esc(w)}</li>`).join("")}</ul></div>`;
  return out;
}

function openDrawer(code, list) {
  const d = DATA.find(x => x.code === code);
  if (!d) return;
  if (list) DRAWER_LIST = list;
  else if (!DRAWER_LIST.some(x => x.code === code)) DRAWER_LIST = currentRows();
  const i = DRAWER_LIST.findIndex(x => x.code === code);
  const drawer = document.getElementById("us-drawer"), scrim = document.getElementById("us-scrim");
  const rank = usRank(d), starred = WATCH.has(d.code);
  const auto = d.source === "us-auto" || d.screen === "us-auto";
  const secUrl = ((d.evidence_sources || []).find(u => /sec\.gov/i.test(u)))
    || `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(d.code)}&type=10-K`;
  drawer.dataset.kind = "company";
  drawer.innerHTML = `
    <div class="dhead">
      <button class="close" id="us-drawer-close" aria-label="Close">&times;</button>
      <div class="dnav">
        <span class="dpos">${i >= 0 ? `${i + 1} of ${DRAWER_LIST.length}` : ""}</span>
        <button class="dpin${starred ? " on" : ""}" id="us-dpin" title="${starred ? "Remove from" : "Add to"} watchlist">${starred ? "★" : "☆"}</button>
        <button id="us-dprev" title="Previous company (←)"${i <= 0 ? " disabled" : ""}>‹</button>
        <button id="us-dnext" title="Next company (→)"${i < 0 || i >= DRAWER_LIST.length - 1 ? " disabled" : ""}>›</button>
      </div>
      <div class="thm" style="margin-bottom:6px">${esc(d.theme || d.sector || "")}${rank ? " · rank " + rank : " · unranked"}<span class="tierbadge">${auto ? "US auto-screen · not yet researched" : "US board"}</span></div>
      <h2 style="margin:0 0 3px;font-size:19px">${esc(d.name)}</h2>
      <div class="tc">${esc(d.code)}${d.sector ? " · " + esc(d.sector) : ""}</div>
      <div style="margin-top:9px">
        ${d.claim_grade ? `<span class="cg" style="margin-right:4px">claim: ${esc(d.claim_grade)}</span>` : ""}
        ${d.has_lens_data ? (signalsHtml(d).replace(/^—$/, "")) : '<span class="pend" style="font-size:11px">capex / guidance pass pending</span>'}
      </div>
      <div class="links">
        <button type="button" class="lnk lnk-report" id="us-dchart">Price chart ›</button>
        <a class="lnk" href="${esc(secUrl)}" target="_blank" rel="noopener">SEC filings (10-K) ↗</a>
        <a class="lnk" href="https://finance.yahoo.com/quote/${encodeURIComponent(d.code)}" target="_blank" rel="noopener">Yahoo Finance ↗</a>
        <a class="lnk" href="https://www.google.com/search?q=${encodeURIComponent(d.name + " " + d.code + " stock news")}" target="_blank" rel="noopener">News ↗</a>
      </div>
    </div>
    <div class="dbody">${usDrawerBody(d)}</div>`;
  drawer.classList.add("on");
  scrim.classList.add("on");
  drawer.scrollTop = 0;
  document.getElementById("us-drawer-close").onclick = closeDrawer;
  scrim.onclick = closeDrawer;
  const step = n => { const nx = DRAWER_LIST[i + n]; if (nx) openDrawer(nx.code); };
  document.getElementById("us-dprev").onclick = () => step(-1);
  document.getElementById("us-dnext").onclick = () => step(1);
  document.getElementById("us-dpin").onclick = () => { toggleStar(d.code); openDrawer(d.code); };
  document.getElementById("us-dchart").onclick = () => openChart(d.code, true);
}
function closeDrawer() {
  const drawer = document.getElementById("us-drawer");
  drawer.classList.remove("on");
  delete drawer.dataset.kind;
  document.getElementById("us-scrim").classList.remove("on");
}
addEventListener("keydown", e => {
  const drawer = document.getElementById("us-drawer");
  if (!drawer.classList.contains("on")) return;
  if (e.key === "Escape") { drawer.dataset.kind === "company" ? closeDrawer() : closeChart(); return; }
  if (drawer.dataset.kind !== "company" || e.target.closest("input,select,textarea")) return;
  if (e.key === "ArrowLeft") document.getElementById("us-dprev").click();
  if (e.key === "ArrowRight") document.getElementById("us-dnext").click();
});

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
/* Lens filters are one at a time (like India's); the two stacking ones toggle freely. */
document.querySelectorAll("[data-ustg]").forEach(b => {
  b.onclick = () => {
    const k = b.dataset.ustg, on = !UTG[k];
    if (ULENSES.includes(k)) ULENSES.forEach(x => { UTG[x] = false; });
    UTG[k] = on;
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
  const mn = document.getElementById("us-method-n");
  if (mn) mn.textContent = DATA.length;
  render();
  renderOverview();
  auxLoadAll();
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

const US_TABS = ["overview", "screens", "themes", "market", "earnings", "news", "gallery", "insider", "method"];
/* tab switching: showUsTab(), further down */

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
  drawer.dataset.kind = "chart";
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
  delete document.getElementById("us-drawer").dataset.kind;
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

/* ================================================================
   Themes -- the US counterpart to India's Themes page (app.js's theme
   folders). A theme is the company's own industry classification, taken
   from the data as it stands, so a new sector shows up the day the daily
   screen first adds a company in it -- nothing to maintain by hand.
   Pick a folder to see only that theme's companies; the scorecard's
   prev/next then walks that list.
   ================================================================ */
const TH = {theme: null};
const THEME_FOLDER_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const themeOf = d => d.theme || d.sector || "Other";

function openThemes() { renderThemes(); }

function renderThemes() {
  const panel = document.getElementById("us-theme-panel"), heading = document.getElementById("us-theme-heading");
  const results = document.getElementById("us-theme-results"), box = document.getElementById("us-themes");
  const groups = new Map();
  DATA.forEach(d => { const t = themeOf(d); if (!groups.has(t)) groups.set(t, []); groups.get(t).push(d); });
  if (TH.theme && !groups.has(TH.theme)) TH.theme = null;
  panel.hidden = heading.hidden = !!TH.theme;
  results.hidden = !TH.theme;

  if (!TH.theme) {
    if (!groups.size) { box.innerHTML = '<p class="view-hint">No US companies on the board yet — the daily screen hasn\'t added any.</p>'; return; }
    const order = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
    box.innerHTML = order.map(([t, rows]) => {
      const scored = rows.filter(r => r.final_score != null);
      const avg = scored.length ? scored.reduce((s, r) => s + r.final_score, 0) / scored.length : null;
      return `<button type="button" class="chip" data-uth="${esc(t)}" title="${rows.length} compan${rows.length === 1 ? "y" : "ies"}${avg != null ? " · average score " + avg.toFixed(1) : ""}">`
        + `<span class="theme-ic">${THEME_FOLDER_SVG}</span><span class="theme-name">${esc(t)}</span> <span class="tc">${rows.length}</span></button>`;
    }).join("");
    box.querySelectorAll("[data-uth]").forEach(b => {
      b.onclick = () => { TH.theme = b.dataset.uth; renderThemes(); window.scrollTo({top: 0, behavior: "smooth"}); };
    });
    return;
  }

  const names = groups.get(TH.theme).slice().sort((a, b) => (b.final_score ?? -1) - (a.final_score ?? -1) || String(a.name).localeCompare(String(b.name)));
  results.innerHTML = `<div class="theme-results-head"><button class="theme-back" id="us-theme-back">← All themes</button>
      <div><span class="eyebrow">COMPANIES IN THEME</span><h3>${esc(TH.theme)}</h3><p>${names.length} screened compan${names.length === 1 ? "y" : "ies"}, highest score first</p></div>
      <span class="folder-count">${names.length} names</span></div>
    <div class="company-files">${names.map(d => {
      const on = WATCH.has(d.code);
      return `<button class="company-file" data-uth-company="${esc(d.code)}"><span class="file-symbol">${esc((d.name || "?").slice(0, 1).toUpperCase())}</span>`
        + `<span class="file-copy"><b>${esc(d.name || "Unnamed company")}</b><small>${esc(d.code)} · Score ${fmtN(d.final_score)} · ${fmtUSDShort(d.market_cap_usd)}</small></span>`
        + `<span class="file-pin${on ? " on" : ""}" data-uth-pin="${esc(d.code)}" role="button" title="${on ? "Remove from" : "Add to"} watchlist" aria-label="Star ${esc(d.name)}">${on ? "★" : "☆"}</span><span class="file-arrow">›</span></button>`;
    }).join("")}</div>`;
  document.getElementById("us-theme-back").onclick = () => { TH.theme = null; renderThemes(); };
  results.querySelectorAll("[data-uth-pin]").forEach(s => {
    s.onclick = e => { e.stopPropagation(); toggleStar(s.dataset.uthPin).then(renderThemes); };
  });
  results.querySelectorAll("[data-uth-company]").forEach(b => {
    b.onclick = () => openDrawer(b.dataset.uthCompany, names);
  });
}

/* ================================================================
   Earnings -- calendar and results for the board's companies. Written
   nightly by scripts/run_earnings_us.py (Finnhub free tier) into
   data/earnings_us/latest.json, served by /api/earnings-us. The same
   data feeds the "Earnings" section of each company's scorecard.
   ================================================================ */
const EARN = {data: null, loading: null, sub: "upcoming", built: false};
function earnLoad() {
  EARN.loading = EARN.loading || fetchJSON("/api/earnings-us").then(j => { EARN.data = j; return j; }).catch(() => null);
  return EARN.loading;
}
earnLoad().then(() => { if (EARN.built) earnRender(); });

const EARN_DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
function earnUtc(iso) { const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number); return Date.UTC(y, m - 1, d); }
function earnToday() { const n = new Date(); return Date.UTC(n.getFullYear(), n.getMonth(), n.getDate()); }
function earnDays(iso) { return Math.round((earnUtc(iso) - earnToday()) / 86400000); }
function earnDay(iso) {
  if (!iso) return "—";
  const t = new Date(earnUtc(iso)), [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return `${EARN_DOW[t.getUTCDay()]} ${d} ${INS_MONTHS[m - 1]}${y !== new Date().getFullYear() ? " " + y : ""}`;
}
function earnIn(iso) {
  const n = earnDays(iso);
  return n === 0 ? "today" : n === 1 ? "tomorrow" : n === -1 ? "yesterday" : n > 0 ? `in ${n} days` : `${-n} days ago`;
}
const earnHour = h => ({bmo: "Before open", amc: "After close", dmh: "During hours"}[h] || "—");
const earnQ = (q, y) => q && y ? `Q${q} ${y}` : "—";
const earnEps = v => v == null ? "—" : (v < 0 ? "−" : "") + "$" + Math.abs(v).toFixed(2);
const earnRev = v => v == null ? "—" : fmtUSDShort(v / 1e6);
/* beat / miss / in line, from the surprise percent (or the raw numbers when no percent came back) */
function earnOutcome(r) {
  let p = r.surprise_pct;
  if (p == null && r.eps_actual != null && r.eps_estimate) p = (r.eps_actual - r.eps_estimate) / Math.abs(r.eps_estimate) * 100;
  if (p == null) return {kind: "", pct: null};
  return {kind: p >= 1 ? "beat" : p <= -1 ? "miss" : "inline", pct: p};
}
function earnSurprise(r) {
  const o = earnOutcome(r);
  if (o.pct == null) return "—";
  const t = Math.abs(o.pct) >= 1000 ? (o.pct > 0 ? "> +1000%" : "< −1000%") : (o.pct > 0 ? "+" : "") + o.pct.toFixed(1) + "%";   /* a near-zero estimate makes the percent meaningless */
  return o.kind === "beat" ? `<span class="earn-beat">▲ ${t}</span>` : o.kind === "miss" ? `<span class="earn-miss">▼ ${t}</span>` : `<span>${t}</span>`;
}
function earnStreak(results) {
  return (results || []).slice(0, 4).reverse().map(r => {
    const k = earnOutcome(r).kind;
    return k === "beat" ? '<span class="earn-beat" title="beat">●</span>' : k === "miss" ? '<span class="earn-miss" title="missed">●</span>' : '<span title="in line or unknown" style="color:var(--muted)">○</span>';
  }).join("");
}

async function openEarnings() {
  const body = document.getElementById("earn-body");
  if (!EARN.data) body.innerHTML = '<p class="view-hint">Loading…</p>';
  await earnLoad();
  const j = EARN.data;
  if (!j || !j.companies || !Object.keys(j.companies).length) {
    document.getElementById("earn-summary").innerHTML = "";
    body.innerHTML = '<p class="view-hint">The earnings scan has not run yet. It runs automatically every night, and right after the daily screen adds a company.</p>';
    return;
  }
  if (!EARN.built) earnBuildControls();
  earnRender();
}

function earnBuildControls() {
  EARN.built = true;
  const sectors = [...new Set(DATA.map(d => d.sector).filter(Boolean))].sort();
  document.getElementById("earn-sector").innerHTML = '<option value="">All sectors</option>' + sectors.map(s => `<option>${esc(s)}</option>`).join("");
  document.querySelectorAll("[data-earnsub]").forEach(t => {
    t.onclick = () => {
      EARN.sub = t.dataset.earnsub;
      document.querySelectorAll("[data-earnsub]").forEach(x => { x.classList.toggle("on", x === t); x.setAttribute("aria-selected", String(x === t)); });
      earnSyncWindow();
      earnRender();
    };
  });
  let timer = 0;
  document.getElementById("earn-q").oninput = () => { clearTimeout(timer); timer = setTimeout(earnRender, 120); };
  document.getElementById("earn-qclear").onclick = () => { document.getElementById("earn-q").value = ""; earnRender(); };
  ["earn-sector", "earn-window", "earn-outcome", "earn-watch"].forEach(id => { document.getElementById(id).onchange = earnRender; });
  document.getElementById("earn-clear").onclick = () => {
    document.getElementById("earn-q").value = "";
    document.getElementById("earn-sector").value = "";
    document.getElementById("earn-outcome").value = "";
    document.getElementById("earn-watch").checked = false;
    earnSyncWindow(true);
    earnRender();
  };
  earnSyncWindow(true);
}
/* the window menu means "reports coming in the next…" on Upcoming and "reported in the last…" on Recent results */
function earnSyncWindow(reset) {
  const sel = document.getElementById("earn-window"), up = EARN.sub === "upcoming";
  const opts = up ? [["7", "Next 7 days"], ["14", "Next 14 days"], ["30", "Next 30 days"], ["60", "Next 60 days"], ["", "Every date on record"]]
                  : [["1", "Latest quarter"], ["2", "Last 2 quarters"], ["4", "Last 4 quarters"], ["", "Every quarter on record"]];
  sel.innerHTML = opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  sel.value = up ? "60" : "1";
  document.getElementById("earn-outcome").hidden = up;
}

function earnCompanies() {
  const by = {}; DATA.forEach(d => { by[d.code] = d; });
  return Object.entries(EARN.data.companies || {}).filter(([c]) => by[c]).map(([c, e]) => ({code: c, d: by[c], e}));
}
function earnFilterCompany(x) {
  const q = document.getElementById("earn-q").value.trim().toLowerCase();
  const sector = document.getElementById("earn-sector").value;
  if (sector && x.d.sector !== sector) return false;
  if (document.getElementById("earn-watch").checked && !WATCH.has(x.code)) return false;
  if (q && !(x.d.name + " " + x.code + " " + (x.d.sector || "")).toLowerCase().includes(q)) return false;
  return true;
}

function earnRender() {
  const all = earnCompanies(), body = document.getElementById("earn-body");
  const win = document.getElementById("earn-window").value;
  document.getElementById("earn-asof").textContent = EARN.data.asof ? `Updated ${insDay(EARN.data.asof)} · ${all.length} companies` : "";

  /* headline numbers, over the whole board rather than the filtered view */
  const nextDays = all.filter(x => x.e.next).map(x => earnDays(x.e.next.date)).filter(n => n >= 0);
  const latest = all.map(x => (x.e.results || [])[0]).filter(Boolean);
  const beats = latest.filter(r => earnOutcome(r).kind === "beat").length, misses = latest.filter(r => earnOutcome(r).kind === "miss").length;
  document.getElementById("earn-summary").innerHTML =
    `<div class="earn-stat"><span>Reporting this week</span><b>${nextDays.filter(n => n <= 7).length}</b></div>`
    + `<div class="earn-stat"><span>Next 30 days</span><b>${nextDays.filter(n => n <= 30).length}</b></div>`
    + `<div class="earn-stat"><span>Beat last quarter</span><b class="earn-beat">${beats}</b></div>`
    + `<div class="earn-stat"><span>Missed last quarter</span><b class="earn-miss">${misses}</b></div>`;

  const count = document.getElementById("earn-count");
  if (EARN.sub === "upcoming") {
    let rows = all.filter(x => x.e.next && earnFilterCompany(x));
    rows = rows.filter(x => { const n = earnDays(x.e.next.date); return n >= 0 && (!win || n <= +win); });
    rows.sort((a, b) => a.e.next.date.localeCompare(b.e.next.date) || String(a.d.name).localeCompare(String(b.d.name)));
    count.textContent = `${rows.length} report${rows.length === 1 ? "" : "s"}`;
    const list = rows.map(x => x.d);
    body.innerHTML = rows.length ? `<div class="mv-tablewrap"><table class="mv-table"><thead><tr>
        <th>Date</th><th>Company</th><th>Sector</th><th>When</th><th>Quarter</th><th class="n">EPS est.</th><th class="n">Revenue est.</th><th>Last four</th><th class="n">Last surprise</th>
      </tr></thead><tbody>${rows.map(x => {
        const n = x.e.next, last = (x.e.results || [])[0], soon = earnDays(n.date) <= 7;
        return `<tr data-earn-code="${esc(x.code)}" style="cursor:pointer">
          <td><b>${esc(earnDay(n.date))}</b> <span class="us-sig ${soon ? "earn-soon" : "grey"}">${esc(earnIn(n.date))}</span></td>
          <td><b class="mv-tick">${esc(x.code)}</b><span class="mv-name">${esc(x.d.name)}</span></td>
          <td>${esc(x.d.sector || "—")}</td>
          <td>${esc(earnHour(n.hour))}</td>
          <td>${esc(earnQ(n.quarter, n.year))}</td>
          <td class="n">${earnEps(n.eps_estimate)}</td>
          <td class="n">${earnRev(n.revenue_estimate)}</td>
          <td class="earn-streak">${earnStreak(x.e.results) || "—"}</td>
          <td class="n">${last ? earnSurprise(last) : "—"}</td>
        </tr>`; }).join("")}</tbody></table></div>`
      : '<p class="view-hint">No company on the board reports in this window — widen it, or clear the filters.</p>';
    body.querySelectorAll("[data-earn-code]").forEach(tr => { tr.onclick = () => openDrawer(tr.dataset.earnCode, list); });
    return;
  }

  const outcome = document.getElementById("earn-outcome").value;
  let rows = all.filter(earnFilterCompany).flatMap(x => (x.e.results || []).slice(0, win ? +win : undefined).map(r => ({...x, r})));
  rows = rows.filter(x => !outcome || earnOutcome(x.r).kind === outcome);
  rows.sort((a, b) => String(b.r.period || b.r.reported || "").localeCompare(String(a.r.period || a.r.reported || "")) || String(a.d.name).localeCompare(String(b.d.name)));
  count.textContent = `${rows.length} result${rows.length === 1 ? "" : "s"}`;
  const list = [...new Map(rows.map(x => [x.code, x.d])).values()];
  body.innerHTML = rows.length ? `<div class="mv-tablewrap"><table class="mv-table"><thead><tr>
      <th>Company</th><th>Sector</th><th>Quarter</th><th class="n">EPS actual</th><th class="n">EPS est.</th><th class="n">Surprise</th><th class="n">Revenue</th><th class="n">Rev. est.</th><th>Last four</th>
    </tr></thead><tbody>${rows.map(x => {
      const r = x.r, revBeat = r.revenue_actual != null && r.revenue_estimate ? (r.revenue_actual >= r.revenue_estimate ? "earn-beat" : "earn-miss") : "";
      return `<tr data-earn-code="${esc(x.code)}" style="cursor:pointer">
        <td><b class="mv-tick">${esc(x.code)}</b><span class="mv-name">${esc(x.d.name)}</span></td>
        <td>${esc(x.d.sector || "—")}</td>
        <td><b>${esc(earnQ(r.quarter, r.year))}</b><span class="mv-name">${r.period ? "ended " + esc(insDay(r.period)) : ""}${r.reported ? " · reported " + esc(earnDay(r.reported)) : ""}</span></td>
        <td class="n">${earnEps(r.eps_actual)}</td>
        <td class="n">${earnEps(r.eps_estimate)}</td>
        <td class="n">${earnSurprise(r)}</td>
        <td class="n ${revBeat}">${earnRev(r.revenue_actual)}</td>
        <td class="n">${earnRev(r.revenue_estimate)}</td>
        <td class="earn-streak">${earnStreak(x.e.results) || "—"}</td>
      </tr>`; }).join("")}</tbody></table></div>
      <p class="section-copy" style="margin-top:8px">● beat estimates · ● missed · ○ in line or unknown — the last four quarters, oldest to newest. Revenue is shown only where the data feed carries it.</p>`
    : '<p class="view-hint">No results match — widen the quarters shown, or clear the filters.</p>';
  body.querySelectorAll("[data-earn-code]").forEach(tr => { tr.onclick = () => openDrawer(tr.dataset.earnCode, list); });
}

/* the "Earnings" section of a company's scorecard */
function usEarningsSection(d) {
  const e = EARN.data && EARN.data.companies && EARN.data.companies[d.code];
  if (!e || (!e.next && !(e.results || []).length)) return "";
  const n = e.next, res = (e.results || []).slice(0, 4);
  let out = '<div class="sec"><h4>Earnings</h4>';
  if (n) {
    out += `<p><b>Next report: ${esc(earnDay(n.date))}</b> (${esc(earnIn(n.date))})${n.hour ? " · " + esc(earnHour(n.hour).toLowerCase()) : ""}`
      + `${n.eps_estimate != null ? ` · EPS estimate <b>${earnEps(n.eps_estimate)}</b>` : ""}${n.revenue_estimate != null ? ` · revenue estimate <b>${earnRev(n.revenue_estimate)}</b>` : ""}</p>`;
  } else {
    out += '<p class="nd">No upcoming report date is published yet.</p>';
  }
  if (res.length) {
    const beats = res.filter(r => earnOutcome(r).kind === "beat").length, known = res.filter(r => earnOutcome(r).kind).length;
    out += `<div class="mv-tablewrap" style="margin-top:9px"><table class="mv-table"><thead><tr><th>Quarter</th><th>Quarter ended</th><th class="n">EPS</th><th class="n">Est.</th><th class="n">Surprise</th></tr></thead><tbody>`
      + res.map(r => `<tr><td>${esc(earnQ(r.quarter, r.year))}</td><td>${r.period ? esc(insDay(r.period)) : r.reported ? esc(earnDay(r.reported)) : "—"}</td>`
        + `<td class="n">${earnEps(r.eps_actual)}</td><td class="n">${earnEps(r.eps_estimate)}</td><td class="n">${earnSurprise(r)}</td></tr>`).join("")
      + `</tbody></table></div>`
      + (known ? `<p class="qsrc" style="margin-top:6px">Beat the EPS estimate in <b>${beats} of the last ${known}</b> quarters.</p>` : "");
  }
  return out + "</div>";
}

/* ================================================================
   Shared data for the Overview, News, Alerts and scorecard sections.
   Every file below is written by a scheduled job (see .github/workflows/
   news_us.yml, institutions_us.yml, board_disclosures_us.yml,
   charts_us.yml) and only read here, so nothing on this page needs a
   manual refresh: when a job commits new data and the server picks it
   up, the next page load shows it.
   ================================================================ */
const AUX = {news: null, inst: null, ins: null, ann: null, setups: null};
const AUX_URL = {news: "/api/news-us", inst: "/api/institutions-us", ins: "/api/insider-us",
  ann: "/api/announcements-us", setups: "/api/market/setups?market=us"};
const AUX_LOADING = {};
function auxLoad(k) {
  AUX_LOADING[k] = AUX_LOADING[k] || fetchJSON(AUX_URL[k]).then(j => (AUX[k] = j)).catch(() => null);
  return AUX_LOADING[k];
}
function auxLoadAll() {
  return Promise.all([earnLoad(), ...Object.keys(AUX_URL).map(auxLoad)]).then(refreshDerived);
}
function refreshDerived() {
  renderOverview();
  updateAlerts();
  if (NEWSUI.built) newsRender();
}

const unent = s => String(s || "").replace(/&#(\d+);/g, (m, n) => String.fromCharCode(+n)).replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&nbsp;/g, " ").replace(/[\u200b\u200c\u200d\ufeff]/g, "");
const safeUrl = u => /^https?:\/\//i.test(u || "") ? u : "#";
function ago(iso) {
  const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  return m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
}
const daysSince = iso => Math.floor((earnToday() - earnUtc(iso)) / 86400000);
const compactUSD = v => v >= 1e6 ? "$" + (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "M" : v >= 1e3 ? "$" + Math.round(v / 1e3) + "k" : "$" + Math.round(v);
function showUsTab(tab) {
  document.querySelectorAll("[data-ustab]").forEach(b => {
    const on = b.dataset.ustab === tab;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", String(on));
  });
  US_TABS.forEach(t => { document.getElementById("us-tab-" + t).hidden = t !== tab; });
  if (tab === "overview") renderOverview();
  if (tab === "gallery" && !GAL_DATA) openGallery();
  if (tab === "themes") openThemes();
  if (tab === "earnings") openEarnings();
  if (tab === "news") openNews();
  if (tab === "market" && !MKT.data) openMarket();
  if (tab === "insider" && !INS.data) openInsider();
  window.scrollTo({top: 0, behavior: "smooth"});
}
document.querySelectorAll("[data-ustab]").forEach(btn => { btn.onclick = () => showUsTab(btn.dataset.ustab); });

/* ================================================================
   Overview -- the stat tiles and "what is happening" panels. A tile is
   a shortcut: it opens Screens with exactly that filter applied.
   ================================================================ */
const OV_ICON = {
  building: '<path d="M4 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16M14 9h5a1 1 0 0 1 1 1v11M8 8h2M8 12h2M8 16h2M3 21h18"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  trend: '<path d="M3 17l6-6 4 4 8-8M15 7h6v6"/>',
  cycle: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4"/>',
  hourglass: '<path d="M6 3h12M6 21h12M7 3c0 5 5 6 5 9s-5 4-5 9M17 3c0 5-5 6-5 9s5 4 5 9"/>',
  calendar: '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
  buy: '<path d="M12 20V6M6 12l6-6 6 6"/>',
};
const ovIcon = n => `<svg class="ic-svg" viewBox="0 0 24 24" aria-hidden="true">${OV_ICON[n] || OV_ICON.folder}</svg>`;

/* open Screens with one filter on (or none) */
function goScreens(key) {
  for (const k in UTG) UTG[k] = false;
  document.getElementById("us-q").value = "";
  document.getElementById("us-sector").value = "";
  document.getElementById("us-watch-only").checked = false;
  if (key) UTG[key] = true;
  showUsTab("screens");
  render();
}
function insBuys14() {
  const rows = (AUX.ins && AUX.ins.transactions) || [];
  return rows.filter(t => t.is_market && t.side === "BUY" && daysSince(t.trade_date) <= 14);
}
function earnThisWeek() {
  if (!EARN.data || !EARN.data.companies) return null;
  return earnCompanies().filter(x => x.e.next && earnDays(x.e.next.date) >= 0 && earnDays(x.e.next.date) <= 7);
}

function renderOverview() {
  const tiles = document.getElementById("us-tiles"), grid = document.getElementById("ov-grid");
  if (!tiles || !DATA.length) {
    if (tiles) { tiles.innerHTML = ""; grid.innerHTML = '<p class="view-hint">No US companies on the board yet — the daily screen hasn\'t added any.</p>'; }
    return;
  }
  const n = k => DATA.filter(d => d[k]).length, themes = new Set(DATA.map(themeOf)).size;
  const wk = earnThisWeek(), buys = AUX.ins ? insBuys14() : null;
  const T = [
    [DATA.length, "Companies on the board", `${themes} themes · added by the daily US screen`, null, "building", "all"],
    [themes, "Themes covered", "Industry folders; new ones appear as the screen finds them", "var(--s6)", "folder", "themes"],
    [n("capex_overhang"), "High P/E + heavy CWIP", "P/E &gt; 40 and CWIP &#8805; 15% of net PP&amp;E", "var(--crit)", "flag", "overhang"],
    [n("guidance_over15"), "Management guides &gt; 15%", `A further ${n("guidance_flag") - n("guidance_over15")} made an unquantified forward statement`, "var(--good-ink)", "trend", "guide15"],
    [n("pat_turnaround"), "Profit turned positive", "Latest quarter profitable after a loss in the prior three", "var(--s1)", "cycle", "turn"],
    [DATA.filter(d => !d.has_lens_data).length, "Awaiting the capex pass", "Capex, guidance and quarterly profit not pulled yet", "var(--muted)", "hourglass", "nolens"],
    [wk ? wk.length : "—", "Reporting this week", "Earnings dates in the next 7 days", "var(--s4)", "calendar", "earnings"],
    [buys ? buys.length : "—", "Insider purchases, 14 days", "Open-market buys by officers and directors", "var(--good-ink)", "buy", "insider"],
  ];
  tiles.innerHTML = T.map(([v, k, note, c, ic, tg]) => {
    const share = ["overhang", "guide15", "turn", "nolens"].includes(tg) && DATA.length ? v / DATA.length * 100 : null;
    const bar = share === null ? "" : `<div class="tbar" title="${v} of ${DATA.length} companies"><i style="width:${Math.max(share, 1.2).toFixed(1)}%;background:${c || "var(--ink2)"}"></i></div>`;
    return `<button type="button" class="tile" data-utile="${tg}" title="Show these" style="--tile:${c || "#0a73a8"}"><span class="tile-ic">${ovIcon(ic)}</span>`
      + `<div class="v"${c ? ` style="color:${c}"` : ""}>${v}</div><div class="k">${k}</div>${bar}<div class="n">${note}</div></button>`;
  }).join("");
  tiles.querySelectorAll("[data-utile]").forEach(b => {
    b.onclick = () => {
      const t = b.dataset.utile;
      if (t === "themes" || t === "earnings" || t === "insider") return showUsTab(t);
      goScreens(t === "all" ? null : t);
    };
  });

  const top = DATA.filter(d => d.final_score != null).sort((a, b) => b.final_score - a.final_score).slice(0, 6);
  const added = DATA.filter(d => d.added_on).sort((a, b) => b.added_on.localeCompare(a.added_on) || (b.final_score || 0) - (a.final_score || 0)).slice(0, 6);
  const soon = (EARN.data && EARN.data.companies ? earnCompanies() : []).filter(x => x.e.next && earnDays(x.e.next.date) >= 0)
    .sort((a, b) => a.e.next.date.localeCompare(b.e.next.date)).slice(0, 6);
  const news = ((AUX.news && AUX.news.items) || []).filter(i => DATA.some(d => d.code === i.symbol)).slice(0, 6);
  const buyRows = buys ? buys.slice().sort((a, b) => b.trade_date.localeCompare(a.trade_date) || b.value_usd - a.value_usd).slice(0, 5) : [];
  const HI = new Set(["results", "order win", "M&A", "management change", "regulatory or legal", "fundraise"]);
  const filings = ((AUX.ann && AUX.ann.announcements) || []).filter(a => HI.has(a.category)).slice(0, 5);

  const card = (title, go, goLabel, inner) => `<div class="ov-card"><h3><span>${title}</span>${go ? `<button type="button" data-ov-go="${go}">${goLabel} →</button>` : ""}</h3>${inner}</div>`;
  const list = (rows, empty) => rows.length ? `<ul class="ov-list">${rows.join("")}</ul>` : `<p class="ov-empty">${empty}</p>`;
  const li = (code, left, sub, right) => `<li data-ov-code="${esc(code)}"><span><b>${left}</b>${sub ? `<small>${sub}</small>` : ""}</span><span class="r">${right}</span></li>`;
  grid.innerHTML =
    card("Top ranked", "screens", "All screens", list(top.map(d => li(d.code, esc(d.name), esc(d.sector || ""), fmtN(d.final_score))), "No scored companies yet."))
    + card("Reporting soon", "earnings", "Earnings calendar", list(soon.map(x => li(x.code, esc(x.d.name), `${esc(earnHour(x.e.next.hour))} · ${esc(earnQ(x.e.next.quarter, x.e.next.year))}`, `${esc(earnDay(x.e.next.date))}<small>${esc(earnIn(x.e.next.date))}</small>`)), "No upcoming dates yet — the earnings scan runs nightly."))
    + card("Latest headlines", "news", "All news", list(news.map(i => li(i.symbol, esc(i.headline), `${esc(i.symbol)} · ${esc(i.source)}`, esc(ago(i.datetime)))), "No headlines yet — the news job runs every three hours."))
    + card("Insider buying", "insider", "Board disclosures", list(buyRows.map(t => li(t.symbol, esc(t.name || t.symbol), `${esc(t.insider)}${t.relationship ? " · " + esc(t.relationship) : ""}`, `${compactUSD(t.value_usd)}<small>${esc(insDay(t.trade_date))}</small>`)), "No open-market insider purchases in the last 14 days."))
    + card("Material filings", "insider", "Board disclosures", list(filings.map(a => li(a.symbol, esc(a.name || a.symbol), esc(a.category) + " — " + esc(unent(a.summary).slice(0, 70)), esc(insDay(a.date)))), "No recent material 8-K filings."))
    + card("Just added", "screens", "All screens", list(added.map(d => li(d.code, esc(d.name), esc(d.sector || ""), esc(insDay(d.added_on)))), "Nothing added yet."));
  grid.querySelectorAll("[data-ov-code]").forEach(el => { el.onclick = () => openDrawer(el.dataset.ovCode); });
  grid.querySelectorAll("[data-ov-go]").forEach(el => { el.onclick = () => (el.dataset.ovGo === "screens" ? goScreens(null) : showUsTab(el.dataset.ovGo)); });
  const asof = (AUX.news && AUX.news.fetched_at) || (EARN.data && EARN.data.fetched_at);
  document.getElementById("ov-asof").textContent = asof ? "Data refreshed " + ago(asof) : "";
}

/* ================================================================
   News tab
   ================================================================ */
const NEWSUI = {built: false, limit: 60};
async function openNews() {
  const body = document.getElementById("news-body");
  if (!AUX.news) body.innerHTML = '<p class="view-hint">Loading…</p>';
  await auxLoad("news");
  if (!AUX.news || !AUX.news.items || !AUX.news.items.length) {
    body.innerHTML = '<p class="view-hint">No headlines yet. The news job runs automatically every three hours.</p>';
    return;
  }
  if (!NEWSUI.built) newsBuild();
  newsRender();
}
function newsBuild() {
  NEWSUI.built = true;
  const withNews = [...new Map(AUX.news.items.map(i => [i.symbol, i.name])).entries()].sort((a, b) => String(a[1]).localeCompare(String(b[1])));
  document.getElementById("news-company").innerHTML = '<option value="">All companies</option>' + withNews.map(([s, n]) => `<option value="${esc(s)}">${esc(n)} (${esc(s)})</option>`).join("");
  document.getElementById("news-sector").innerHTML = '<option value="">All sectors</option>' + [...new Set(DATA.map(d => d.sector).filter(Boolean))].sort().map(s => `<option>${esc(s)}</option>`).join("");
  let t = 0;
  document.getElementById("news-q").oninput = () => { clearTimeout(t); t = setTimeout(() => { NEWSUI.limit = 60; newsRender(); }, 120); };
  document.getElementById("news-qclear").onclick = () => { document.getElementById("news-q").value = ""; NEWSUI.limit = 60; newsRender(); };
  ["news-company", "news-sector", "news-watch"].forEach(id => { document.getElementById(id).onchange = () => { NEWSUI.limit = 60; newsRender(); }; });
  document.getElementById("news-clear").onclick = () => {
    document.getElementById("news-q").value = ""; document.getElementById("news-company").value = "";
    document.getElementById("news-sector").value = ""; document.getElementById("news-watch").checked = false;
    NEWSUI.limit = 60; newsRender();
  };
}
function newsRender() {
  const body = document.getElementById("news-body");
  if (!AUX.news || !AUX.news.items) return;
  const by = {}; DATA.forEach(d => { by[d.code] = d; });
  const q = document.getElementById("news-q").value.trim().toLowerCase();
  const co = document.getElementById("news-company").value, sec = document.getElementById("news-sector").value;
  const wl = document.getElementById("news-watch").checked;
  const rows = AUX.news.items.filter(i => {
    const d = by[i.symbol];
    if (!d) return false;
    if (co && i.symbol !== co) return false;
    if (sec && d.sector !== sec) return false;
    if (wl && !WATCH.has(i.symbol)) return false;
    return !q || (i.headline + " " + i.summary + " " + i.name + " " + i.symbol).toLowerCase().includes(q);
  });
  document.getElementById("news-count").textContent = `${rows.length} of ${AUX.news.count} headlines`;
  document.getElementById("news-asof").textContent = AUX.news.fetched_at ? "Updated " + ago(AUX.news.fetched_at) : "";
  if (!rows.length) { body.innerHTML = '<p class="view-hint">No headline matches — clear the search or pick another filter.</p>'; return; }
  const shown = rows.slice(0, NEWSUI.limit);
  let day = "", html = "";
  shown.forEach(i => {
    const dd = i.datetime.slice(0, 10), n = daysSince(dd);
    if (dd !== day) { day = dd; html += `<div class="us-news-day">${n <= 0 ? "Today" : n === 1 ? "Yesterday" : esc(insDay(dd))}</div>`; }
    html += `<article class="us-news-item"><div class="meta"><b data-news-code="${esc(i.symbol)}" title="Open scorecard">${esc(i.symbol)}</b> · ${esc(i.name)} · ${esc(i.source)} · ${esc(ago(i.datetime))}</div>`
      + `<a class="hl" href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.headline)}</a>${i.summary ? `<p>${esc(i.summary)}</p>` : ""}</article>`;
  });
  if (rows.length > shown.length) html += `<p style="text-align:center;margin-top:12px"><button class="btn" id="news-more">Show ${Math.min(60, rows.length - shown.length)} more</button></p>`;
  body.innerHTML = html;
  body.querySelectorAll("[data-news-code]").forEach(el => { el.onclick = () => openDrawer(el.dataset.newsCode); });
  const more = document.getElementById("news-more");
  if (more) more.onclick = () => { NEWSUI.limit += 60; newsRender(); };
}

/* ================================================================
   Alerts -- a bell over data the nightly jobs already write: earnings
   in the next week, insider open-market purchases, material 8-Ks, and
   price signals from the chart job. Computed here, so an alert appears
   the moment its source data does; which ones you have seen is kept in
   this browser.
   ================================================================ */
const ALERT_SEEN_KEY = "us.alerts.seen", ALERT_WL_KEY = "us.alerts.wl";
const ALERT_8K = new Set(["results", "order win", "M&A", "management change", "regulatory or legal", "fundraise"]);
let ALERTS = [];
const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
function alertsSeen() { try { return new Set(JSON.parse(lsGet(ALERT_SEEN_KEY) || "[]")); } catch (e) { return new Set(); } }

function buildAlerts() {
  const by = {}; DATA.forEach(d => { by[d.code] = d; });
  const A = [];
  if (EARN.data && EARN.data.companies) {
    earnCompanies().forEach(x => {
      const n = x.e.next;
      if (!n) return;
      const dd = earnDays(n.date);
      if (dd < 0 || dd > 7) return;
      A.push({id: `earn:${x.code}:${n.date}`, code: x.code, kind: "Earnings", rank: dd,
        title: `${x.d.name} reports ${earnIn(n.date)}`,
        sub: `${earnDay(n.date)}${n.hour ? " · " + earnHour(n.hour).toLowerCase() : ""}${n.eps_estimate != null ? " · EPS est. " + earnEps(n.eps_estimate) : ""}`});
    });
  }
  if (AUX.ins && AUX.ins.transactions) {
    AUX.ins.transactions.forEach(t => {
      if (!by[t.symbol] || !t.is_market || t.side !== "BUY" || t.value_usd < 25000) return;
      const dd = daysSince(t.trade_date);
      if (dd > 14) return;
      A.push({id: `ins:${t.accession}:${t.insider}:${t.shares}`, code: t.symbol, kind: "Insider buy", rank: 100 + dd,
        title: `${t.insider} bought ${compactUSD(t.value_usd)} of ${t.symbol}`,
        sub: `${t.relationship || "Insider"} · ${insDay(t.trade_date)} at ${fmtUSD(t.price)}`});
    });
  }
  if (AUX.ann && AUX.ann.announcements) {
    AUX.ann.announcements.forEach(a => {
      if (!by[a.symbol] || !ALERT_8K.has(a.category)) return;
      const dd = daysSince(a.date);
      if (dd > 7) return;
      A.push({id: `ann:${a.accession}:${a.symbol}`, code: a.symbol, kind: "8-K filing", rank: 100 + dd,
        title: `${a.name || a.symbol}: ${a.category}`, sub: `${insDay(a.date)} · ${unent(a.summary).slice(0, 90)}`});
    });
  }
  if (AUX.setups && AUX.setups.feed) {
    AUX.setups.feed.forEach(f => {
      if (!by[f.code]) return;
      A.push({id: `feed:${f.code}:${f.kind}:${AUX.setups.as_of}`, code: f.code, kind: "Price signal", rank: 100,
        title: `${f.name || f.code} ${f.text}`, sub: `${f.chg_pct != null ? (f.chg_pct > 0 ? "+" : "") + f.chg_pct + "% · " : ""}close ${fmtUSD(f.close)}`});
    });
  }
  return A.sort((a, b) => (WATCH.has(b.code) - WATCH.has(a.code)) || a.rank - b.rank);
}

function alertsVisible() {
  return lsGet(ALERT_WL_KEY) === "1" && WATCH.size ? ALERTS.filter(a => WATCH.has(a.code)) : ALERTS;
}
function updateAlerts() {
  ALERTS = buildAlerts();
  const seen = alertsSeen(), unseen = alertsVisible().filter(a => !seen.has(a.id)).length;
  const badge = document.getElementById("us-alerts-n");
  badge.hidden = !unseen;
  badge.textContent = unseen > 99 ? "99+" : unseen;
  if (!document.getElementById("us-alerts-panel").hidden) alertsPanel();
}
function alertsPanel() {
  const panel = document.getElementById("us-alerts-panel"), seen = alertsSeen();
  const wl = lsGet(ALERT_WL_KEY) === "1", rows = alertsVisible();
  panel.innerHTML = `<div class="us-alerts-head"><b>Alerts</b>
      <label title="Only companies you have starred"><input type="checkbox" id="us-al-wl"${wl ? " checked" : ""}${WATCH.size ? "" : " disabled"}> My watchlist</label>
      <button type="button" id="us-al-read">Mark all seen</button></div>`
    + (rows.length ? rows.map((a, i) => `<div class="us-alert${seen.has(a.id) ? "" : " unseen"}" data-al="${i}"><div><b>${esc(a.title)}</b><small>${esc(a.kind)} · ${esc(a.sub)}</small></div></div>`).join("")
      : '<div class="us-alert-empty">Nothing needs your attention right now.<br>Earnings dates, insider buying, key filings and price signals appear here as they happen.</div>');
  document.getElementById("us-al-wl").onchange = e => { lsSet(ALERT_WL_KEY, e.target.checked ? "1" : "0"); updateAlerts(); };
  document.getElementById("us-al-read").onclick = () => {
    const s = alertsSeen(); ALERTS.forEach(a => s.add(a.id));
    lsSet(ALERT_SEEN_KEY, JSON.stringify([...s].slice(-800))); updateAlerts();
  };
  panel.querySelectorAll("[data-al]").forEach(el => {
    el.onclick = () => {
      const a = rows[+el.dataset.al], s = alertsSeen(); s.add(a.id);
      lsSet(ALERT_SEEN_KEY, JSON.stringify([...s].slice(-800)));
      closeAlerts(); updateAlerts(); openDrawer(a.code);
    };
  });
}
function closeAlerts() {
  document.getElementById("us-alerts-panel").hidden = true;
  document.getElementById("us-alerts-btn").setAttribute("aria-expanded", "false");
}
document.getElementById("us-alerts-btn").onclick = e => {
  e.stopPropagation();
  const panel = document.getElementById("us-alerts-panel");
  if (!panel.hidden) return closeAlerts();
  panel.hidden = false;
  document.getElementById("us-alerts-btn").setAttribute("aria-expanded", "true");
  alertsPanel();
};
document.addEventListener("click", e => { if (!e.target.closest(".us-bellwrap")) closeAlerts(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeAlerts(); });

/* ================================================================
   Scorecard sections: institutions (SEC 13F) and recent news
   ================================================================ */
function instFor(d) { return AUX.inst && AUX.inst.companies && AUX.inst.companies[d.code]; }
function instCell(d) {
  if (d.inst_pct != null) return fmtN(d.inst_pct) + "%";
  const i = instFor(d);
  return i && i.inst_pct != null ? (i.over_100 ? "100%+" : fmtN(i.inst_pct) + "%") : "—";
}
function usInstitutionsSection(d) {
  const i = instFor(d);
  if (!i) return "";
  const per = i.period ? insDay(i.period) : "the latest quarter";
  let out = `<div class="sec"><h4>Institutional holders — SEC Form 13F</h4><p><b>${i.institutions.toLocaleString("en-US")}</b> institutions reported holding <b>${Math.round(i.shares_held).toLocaleString("en-US")}</b> shares`
    + (i.inst_pct != null ? `, about <b>${i.over_100 ? "100%+" : fmtN(i.inst_pct) + "%"}</b> of shares outstanding` : "") + ` (as of ${esc(per)}).</p>`;
  if (i.top && i.top.length) {
    out += `<div class="mv-tablewrap" style="margin-top:9px"><table class="mv-table"><thead><tr><th>Largest holders</th><th class="n">Shares</th><th class="n">% of company</th></tr></thead><tbody>`
      + i.top.map(t => `<tr><td>${esc(t.name)}</td><td class="n">${Math.round(t.shares).toLocaleString("en-US")}</td><td class="n">${t.pct == null ? "—" : fmtN(t.pct) + "%"}</td></tr>`).join("") + "</tbody></table></div>";
  }
  return out + `<p class="caveat" style="margin-top:8px">13F is a quarterly snapshot filed up to 45 days after quarter-end and leaves out small positions, so read the percentage as “at least this much”. Where several entities of one manager report the same shares it can overstate.</p></div>`;
}
function usNewsSection(d) {
  const items = ((AUX.news && AUX.news.items) || []).filter(i => i.symbol === d.code).slice(0, 5);
  if (!items.length) return "";
  return `<div class="sec"><h4>Recent news</h4>` + items.map(i =>
    `<div class="us-news-item" style="padding:8px 0"><div class="meta">${esc(i.source)} · ${esc(ago(i.datetime))}</div><a class="hl" style="font-size:13.5px" href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.headline)}</a></div>`).join("") + "</div>";
}
