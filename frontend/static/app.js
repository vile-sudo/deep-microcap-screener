"use strict";

/* ================================================================
   Deep Microcap Screener — frontend
   Loads the full research dataset and board metadata from the API,
   then drives the same interactive board as the original single-file
   version: filters, sliders, full-text search, a sortable table, a
   company detail drawer, watchlist, side-by-side compare, CSV export,
   shareable URL state, and keyboard navigation.
   ================================================================ */

async function fetchJSON(url) {
  const r = await fetch(url, {credentials: "same-origin"});
  if (r.status === 401) {
    /* session ended (logged out elsewhere, expired, or access removed) */
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash);
    throw new Error("Login required");
  }
  if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
  return r.json();
}

async function boot() {
  const [DATA, META, ASME] = await Promise.all([
    fetchJSON("/api/companies"),
    fetchJSON("/api/meta"),
    /* the board still loads if the ASME list can't */
    fetchJSON("/api/asme").catch(function(){ return {scanned_on: null, companies: []}; }),
  ]);

  const BUILD = META.build || {version: "\u2014", built: "\u2014", history: []};
  const SCREENS = META.screens || {};
  const SHORT = META.short || {};
  const BUILD_STAMP = META.build_stamp;
  const CANDIDATES = META.candidates || [];
  const BUILD_NEW = META.build_new;

  /* Keep the header build stamp and page chrome sourced from the API
     instead of hand-edited on every publish. */
  document.title = "Deep Sweep — " + DATA.length + " companies";
  const qElBoot = document.getElementById("q");
  if (qElBoot) qElBoot.placeholder = qElBoot.placeholder.replace("Search companies", "Search " + DATA.length + " companies");
  const verElBoot = document.querySelector(".vver"), dateElBoot = document.querySelector(".vdate");
  if (verElBoot) verElBoot.textContent = BUILD.version || "\u2014";
  if (dateElBoot) dateElBoot.textContent = BUILD.built ? ("Updated " + BUILD.built) : "";

  /* Per-screen counts on the methodology card, computed from the live data
     rather than hand-typed, so the write-up can never drift out of sync
     with what is actually on the board. */
  (function fillScreenCounts(){
    const counts = {};
    DATA.forEach(function(d){ counts[d.screen] = (counts[d.screen]||0) + 1; });
    function set(id, key){ const el=document.getElementById(id); if(el) el.textContent = counts[key]!==undefined ? counts[key] : "0"; }
    set("n-v3-deep","v3-deep"); set("n-v7-new","v7-new"); set("n-v3-screen","v3-screen");
    set("n-v4-moat","v4-moat"); set("n-v4-triage","v4-triage"); set("n-v5-new","v5-new");
    set("n-v6-new","v6-new"); set("n-v8-moat","v8-moat"); set("n-v8-weekly","v8-weekly"); set("n-user","user");
    set("n-deep-sweep","deep-sweep");
  })();

const THEMES = [...new Set(DATA.map(d=>(d.theme||'').split(' / ')[0]))];
/* search index is built here rather than shipped in the data — same result, ~400KB smaller file */
const QFIELDS=['name','code','nse_code','bse_code','theme','sector','industry','business','moat_note',
  'import_substitution','why_obscure','risk_note','pricing_power_note','guidance_quote','cwip_note',
  'claim_grade','triage_verdict','liquidity_note','verify_comment','theme_detail'];
DATA.forEach(d=>{
  d._q = (QFIELDS.map(k=>d[k]||'').join(' ') + ' ' + (d.warnings||[]).join(' ')
          + ' ' + SCREENS_LABEL(d.screen)).toLowerCase();
  /* name and tickers only: a company search should land on that company, not on
     every write-up that happens to mention it */
  d._n = [d.name,d.code,d.nse_code,d.bse_code].filter(Boolean).join(' ').toLowerCase();
});
/* true when the search matches a company name or ticker; only when nothing does
   is the search widened to the research text, and the hit line says so */
let NAMEHIT = false;
function SCREENS_LABEL(s){return {'v3-deep':'v3 deep','v3-screen':'v3 screen','v4-moat':'v4 moat monopoly',
  'v4-triage':'triage','v5-new':'v5 new sweep','v6-new':'v6 new sectors','v7-new':'v7 depth expansion','user':'requested added on request','deep-sweep':'Deep Sweep','auto':'auto-added'}[s]||'';}
const SERIES = ['--s1','--s2','--s3','--s4','--s5','--s6'];
/* Nine themes cannot be given nine safe categorical hues, and cycling six would make
   two themes share a colour - a key that lies. So no chart encodes theme by colour:
   the theme is always spelled out in text, and the marks use a single hue. */

/* Candidates from the exchange listing scan. Deliberately a separate array from DATA:
   these are unscored, unverified names that a script noticed, and mixing them into the
   board would put things carrying no research beside 310 things that do. Promotion out
   of here is a human act. scan-listings.mjs rewrites everything between the markers. */
/* When the weekly pass last finished. The board has no clock it can trust and no way to
   fetch one, so this is written in by update-weekly.mjs. If it is missing, the freshness
   chip says the scan has never run rather than inventing a date. */

/* ASME certificate holders from the weekly scan, keyed by the board company
   each one is (the API does the matching). Drives the Screen filter, the
   ASME line on scorecards and the ASME Certified list. */
const ASME_BY_CODE = {};
(ASME.companies||[]).forEach(c=>{ if(c.board_code) ASME_BY_CODE[c.board_code]=c; });
{ const n=document.getElementById('asme-filter-n'); if(n) n.textContent=Object.keys(ASME_BY_CODE).length; }
{ const n=document.getElementById('auto-filter-n'); if(n) n.textContent=DATA.filter(d=>d.screen==='auto').length; }
const CG={'verified':'cg-verified','company-stated':'cg-company','none claimed':'cg-none','DEBUNKED':'cg-debunked'};
const pend = '<span class="pend" title="Not pulled yet for this name">&#8943;</span>';
const shortT = t => SHORT[t] || t;
const base = d => (d.theme||'').split(' / ')[0];
const scrURL = d => `https://www.screener.in/company/${d.code}/`;
const exURL = d => d.nse_code && !/^\d+$/.test(d.nse_code)
  ? `https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(d.nse_code)}`
  : (d.bse_code ? `https://www.bseindia.com/stock-share-price/a/a/${d.bse_code}/` : null);
const nz = v => (v===null||v===undefined||v==='')?null:+v;
const fmt = (v,d=1)=> v===null||v===undefined||isNaN(v) ? '—' : (+v).toFixed(d);
const fmtI = v => v===null||v===undefined||isNaN(v) ? '—' : Math.round(v).toLocaleString('en-IN');
const esc = s => String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

/* ---------- icons ----------
   One small line-icon set for tiles, theme folders and Market view stages.
   Paths are drawn on a 24px grid and stroked with currentColor. */
const ICON_PATHS={
  building:'<path d="M5 20V6.5L12 3l7 3.5V20"/><path d="M9 20v-4h6v4"/><path d="M9 9.5h.01M15 9.5h.01M9 13h.01M15 13h.01"/>',
  folder:'<path d="M3.5 7h6l2 2.2h9V19h-17z"/>',
  flag:'<path d="M5.5 21V4"/><path d="M5.5 4.5h11l-2.2 3.8 2.2 3.8h-11"/>',
  trend:'<path d="M3.5 17.5l6-6 4 4 7-7.5"/><path d="M15 8h5.5v5.5"/>',
  cycle:'<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4.2h-4.2"/>',
  hourglass:'<path d="M7 3.5h10M7 20.5h10"/><path d="M8 3.5c0 5 8 5 8 8.5s-8 3.5-8 8.5M16 3.5c0 5-8 5-8 8.5s8 3.5 8 8.5"/>',
  shield:'<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
  plus:'<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="M12 8v8M8 12h8"/>',
  bolt:'<path d="M13 3L5.5 13.5H12L11 21l7.5-10.5H12z"/>',
  gear:'<circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.2M12 18.3v2.2M20.5 12h-2.2M5.7 12H3.5M18 6l-1.6 1.6M7.6 16.4L6 18M18 18l-1.6-1.6M7.6 7.6L6 6"/>',
  chip:'<rect x="6.5" y="6.5" width="11" height="11" rx="2"/><path d="M9.5 3.5v3M14.5 3.5v3M9.5 17.5v3M14.5 17.5v3M3.5 9.5h3M3.5 14.5h3M17.5 9.5h3M17.5 14.5h3"/>',
  flask:'<path d="M9.5 3.5h5M10.5 3.5v5.5L5 19a1.5 1.5 0 0 0 1.3 2h11.4a1.5 1.5 0 0 0 1.3-2l-5.5-10V3.5"/><path d="M7.5 15h9"/>',
  cube:'<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
  pulse:'<path d="M3 12h4l2.5-5.5 4 11 2.5-5.5H21"/>',
  car:'<path d="M4 16.5V12l2-5h12l2 5v4.5"/><path d="M3.5 12h17"/><circle cx="7.5" cy="16.5" r="1.8"/><circle cx="16.5" cy="16.5" r="1.8"/>',
  thread:'<path d="M5 20L19 4"/><circle cx="17.5" cy="5.5" r="2.5"/><path d="M4 8c4 0 6 2 8 4s4 4 8 4"/>',
  bricks:'<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><path d="M3.5 9.7h17M3.5 14.3h17M9 5v4.7M15 9.7v4.6M9 14.3V19"/>',
  bag:'<path d="M5 8.5h14l-1 12H6z"/><path d="M9 8.5V7a3 3 0 0 1 6 0v1.5"/>',
  leaf:'<path d="M5 19c0-8.5 5.5-14 15-14 0 9.5-5.5 15-14 15"/><path d="M5 19l7.5-7.5"/>',
  box:'<path d="M3.5 8L12 4l8.5 4v8L12 20l-8.5-4z"/><path d="M3.5 8L12 12l8.5-4M12 12v8M7.8 6l8.4 4"/>',
  truck:'<path d="M3 6.5h11v9H3zM14 10h4l3 3v2.5h-7"/><circle cx="7" cy="17" r="1.8"/><circle cx="17.5" cy="17" r="1.8"/>',
  drop:'<path d="M12 3.5c3.5 4.2 6 7.5 6 10.5a6 6 0 0 1-12 0c0-3 2.5-6.3 6-10.5z"/>',
  factory:'<path d="M3.5 20V10l5 3V10l5 3V5h3v15z"/><path d="M3.5 20h17"/>',
  coil:'<path d="M4 12c2-4 4-4 6 0s4 4 6 0 3-3 4-2"/><path d="M4 17h16"/>',
  rocket:'<path d="M12 3c3.5 2 5 5.5 5 9l-2 3H9l-2-3c0-3.5 1.5-7 5-9z"/><circle cx="12" cy="9.5" r="1.6"/><path d="M9.5 18L8 21M14.5 18L16 21"/>',
  mountain:'<path d="M3 19.5l6-9 3.5 5 2.5-3.5 6 7.5z"/><path d="M14.5 5.5h4v4"/>',
  done:'<path d="M5.5 21V4"/><path d="M5.5 4.5h11l-2.2 3.8 2.2 3.8h-11"/><path d="M13 17.5l1.8 1.8 3.7-3.7"/>',
};
const icon = name => `<svg class="ic-svg" viewBox="0 0 24 24" aria-hidden="true">${ICON_PATHS[name]||ICON_PATHS.folder}</svg>`;
const THEME_ICON=[[/defen|aero/i,'shield'],[/pharma/i,'flask'],[/medical|diagnos/i,'plus'],[/health|testing/i,'pulse'],[/electric|energy|power/i,'bolt'],
  [/machin|engineer|capital/i,'gear'],[/electron|semi/i,'chip'],[/chem|pharma/i,'flask'],[/material|mineral|metal/i,'cube'],
  [/auto|mobility/i,'car'],[/textile/i,'thread'],[/building|construct/i,'bricks'],[/consumer|appliance/i,'bag'],
  [/agri|food/i,'leaf'],[/packag|plastic/i,'box'],[/transport|logist|rail|marine/i,'truck'],[/water|environ/i,'drop'],[/process/i,'factory']];
const themeIcon = t => icon((THEME_ICON.find(([re])=>re.test(t))||[0,'folder'])[1]);
const TILE_ICON={all:'building', themes:'folder', overhang:'flag', guide15:'trend', turn:'cycle', nolens:'hourglass'};

/* ---------- stat tiles ---------- */
(function(){
  const n = k => DATA.filter(d=>d[k]).length;
  const pending = DATA.filter(d=>!d.has_lens_data).length;
  const t = [
    [String(DATA.length),'Companies on the board','89 v3 · 58 v4 · 11 triage · 144 from three 2026 sweeps · 8 requested',null,'all'],
    [String(THEMES.length),'Themes covered','Every one now has real depth — the thinnest holds 5 names, the largest 64',"var(--s6)",'themes'],
    [String(n('capex_overhang')),'High P/E + heavy CWIP','P/E &gt; 40 and CWIP &#8805; 15% of net block',"var(--crit)",'overhang'],
    [String(n('guidance_over15')),'Management guides &gt; 15%','A further '+(n('guidance_flag')-n('guidance_over15'))+' made an unquantified forward statement',"var(--good-ink)",'guide15'],
    [String(n('pat_turnaround')),'PAT turned positive','Latest period profitable after a loss in the prior three',"var(--s1)",'turn'],
    [String(pending),'Awaiting the capex pass','CWIP, guidance and quarterly PAT not yet pulled for these',"var(--muted)",'nolens'],
  ];
  /* Every tile that maps onto a filter is a button - the number you just read is the
     quickest route to the names behind it. */
  document.getElementById('tiles').innerHTML = t.map(([v,k,nn,c,tg])=>{
    const tag = tg ? 'button' : 'div';
    const tip = tg==='all' ? 'Show every company on the board' : tg==='themes' ? 'Open the theme folders' : 'Show only these companies';
    const at  = tg ? ` type="button" class="tile" data-tile="${tg}" title="${tip}"` : ' class="tile"';
    /* A bare count leaves the reader doing arithmetic against 310. The share bar puts
       the denominator back without spending a second number on it. The first tile IS
       the denominator, and the themes tile counts themes rather than companies, so
       neither gets one. */
    const num = Number(String(v).replace(/[^0-9.]/g,''));
    const share = (tg && tg!=='all' && tg!=='themes' && isFinite(num) && DATA.length) ? (num/DATA.length)*100 : null;
    const bar = share===null ? '' : `<div class="tbar" title="${num} of ${DATA.length} companies — ${share.toFixed(share<1?1:0)}%"><i style="width:${Math.max(share,1.2).toFixed(1)}%;background:${c||'var(--ink2)'}"></i></div>`;
    return `<${tag}${at} style="--tile:${c||'#0a73a8'}"><span class="tile-ic">${icon(TILE_ICON[tg]||'building')}</span><div class="v"${c?` style="color:${c}"`:''}>${v}</div><div class="k">${k}</div>${bar}<div class="n">${nn}</div></${tag}>`;
  }).join('');
})();

/* ---------- state ---------- */
const SL = [
  {k:'promoter_pct',      lab:'Min promoter holding',      step:1,    unit:'%'},
  {k:'fii_pct',           lab:'Min FII holding',           step:0.25, unit:'%'},
  {k:'dii_pct',           lab:'Min DII holding',           step:0.25, unit:'%'},
  {k:'public_pct',        lab:'Max public / retail float', step:1,    unit:'%', inv:true},
  {k:'market_cap_cr',     lab:'Max market cap',            step:100,  unit:' cr', inv:true, pre:'₹'},
  {k:'num_shareholders',  lab:'Max retail shareholders',   step:500,  unit:'',  inv:true},
  {k:'roce_pct',          lab:'Min ROCE',                  step:1,    unit:'%'},
  {k:'cwip_pct_net_block',lab:'Min CWIP % of net block',   step:1,    unit:'%'},
  {k:'final_score',       lab:'Min composite score',       step:1,    unit:''},
];
/* Ranges are derived from the data so that the default position of every slider
   excludes nothing. Hard-coded bounds silently dropped the larger v4 names. */
SL.forEach(s=>{
  const vals = DATA.map(d=>nz(d[s.k])).filter(v=>v!==null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  lo = Math.floor(lo/s.step)*s.step;
  hi = Math.ceil(hi/s.step)*s.step;
  if(hi<=lo) hi = lo + s.step;
  s.min = lo; s.max = hi; s.v = s.inv ? hi : lo;
});

/* Recent listings. There is no listing_date on these records, so this reads the
   research notes rather than a field — which is why the chip says "flagged in the
   notes" and not "listed since". It finds the 81 names whose own write-up calls them
   an SME IPO or a recent listing; a recent listing whose note never mentions it will
   be missed. A real listing-date index has to come from the exchanges, not from prose. */
const IPO_RE = /\b(IPO|SME (?:IPO|listing|platform)|listed in 20\d\d|recently listed|newly listed|freshly listed|lock-?in|anchor investor)\b/i;
const ipoText = d => [d.liquidity_note,d.why_obscure,d.data_note,d.business,d.risk_note,d.watch_note,d.moat_note]
  .filter(Boolean).join(' ');
const RECENT_LISTING = new WeakMap();
function isRecentListing(d){
  if(!RECENT_LISTING.has(d)){
    /* when the note does name a year, honour it — a 2021 listing is not a recent one.
       When it does not, keep the name: SME-platform listings skew recent anyway. */
    const y = listingYear(d);
    RECENT_LISTING.set(d, IPO_RE.test(ipoText(d)) && !(y && +y < 2023));
  }
  return RECENT_LISTING.get(d);
}
/* the year, when the note happens to give one — shown in the chip's tooltip */
function listingYear(d){
  const m = ipoText(d).match(/(?:listed|listing|IPO)\D{0,24}?(20[12]\d)|(20[12]\d)\D{0,24}?(?:listed|listing|IPO)/i);
  return m ? (m[1]||m[2]) : null;
}

const TG = {overhang:false, heavycap:false, guide15:false, guideany:false, turn:false, haslens:false, ipo:false, asme:false, auto:false,
            nolens:false, watch:false,
            nosme:false, nopledge:false, realsub:false, cheap:false, ongate:false};
/* The watchlist is the one piece of state that belongs to the reader rather than to the
   data, so it is kept in localStorage and survives a reload. */
let WATCH = new Set();
try{ WATCH = new Set(JSON.parse(localStorage.getItem('dms.watch')||'[]')); }catch(e){}
/* 'server' once logged in: the list lives in the user's account (/api/watchlist),
   private to them and the same on every device. 'local' with accounts off. */
var WMODE = 'local';
let BUSY = false;
let QUERY = '', QTERMS = [], NEWSINCE = null;
let activeThemes = new Set(THEMES);
let sortKey='final_score', sortDir=-1;

/* ---------- pages ----------
   The board is one page at a time, following the sidebar:
     overview  - the stat tiles; a tile shows only the companies behind it
     themes    - the theme folders; a folder shows only that theme's companies
     companies - the search box; a search shows only the companies it names
     filters   - the screen filters; a filter shows only the companies it matches
     method    - how the scores work
   Moving to another page clears whatever was picked on the last one, so a theme
   chosen on Themes never quietly narrows what Screen filters shows. */
const VIEWS=['overview','themes','market','watchlist','companies','filters','gallery','reports','method'];
const NAV={'overview-link':'overview','themes-link':'themes','market-link':'market','watchlist-link':'watchlist',
           'companies-link':'companies','filters-link':'filters','gallery-link':'gallery','reports-link':'reports'};
const LENSES=['overhang','heavycap','guide15','guideany','turn','haslens','ipo','asme','auto'];
const TILE_LABEL={all:'Companies on the board',overhang:'High P/E + heavy CWIP',guide15:'Management guides > 15%',
                  turn:'PAT turned positive',nolens:'Awaiting the capex pass'};
let VIEW='overview', NAVID='overview-link', TILE=null;

function setView(v, opts){
  opts=opts||{};
  VIEW = VIEWS.includes(v) ? v : 'overview';
  NAVID = opts.nav || VIEW+'-link';
  if(!opts.keep) clearFilters();
  document.body.dataset.view=VIEW;
  document.querySelectorAll('[data-views]').forEach(el=>{ el.hidden=!el.dataset.views.split(' ').includes(VIEW); });
  document.querySelectorAll('.side-link').forEach(l=>l.classList.toggle('active', l.id===NAVID));
  if(!opts.keep) window.scrollTo({top:0,behavior:'smooth'});
  render();
  if(VIEW==='gallery') openGallery();
  if(VIEW==='market') openMarket();
  if(VIEW==='reports'){ if(!opts.keep) RP.code=null; openReports(); }
  const ts=document.getElementById('top-search');
  if(ts && VIEW!=='companies') ts.value='';
}
Object.keys(NAV).forEach(id=>{
  const link=document.getElementById(id);
  if(link) link.onclick=e=>{ e.preventDefault(); setView(NAV[id],{nav:id}); };
});

/* Does the current page have a picked set of companies to list? */
function sliderMoved(){ return SL.some(s=>s.inv ? s.v<s.max : s.v>s.min); }
function showResults(){
  if(VIEW==='overview')  return TILE!==null;
  if(VIEW==='companies') return QTERMS.length>0 || !!NEWSINCE;
  if(VIEW==='filters')   return LENSES.some(k=>TG[k]) || sliderMoved();
  return false;
}

/* ---------- theme folders ---------- */
const themeBox = document.getElementById('themes');
THEMES.forEach(t=>{
  const b=document.createElement('button');
  b.type='button'; b.className='chip'; b.dataset.t=t;
  const n=DATA.filter(d=>base(d)===t).length;
  b.innerHTML=`<span class="theme-ic">${themeIcon(t)}</span><span class="theme-name">${shortT(t)}</span> <span class="tc">${n}</span>`;
  b.onclick=()=>{
    activeThemes=new Set([t]);
    syncChips(); render();
    window.scrollTo({top:0,behavior:'smooth'});
  };
  themeBox.appendChild(b);
});
function syncChips(){
  const one=activeThemes.size===1;
  [...themeBox.children].forEach(c=>c.classList.toggle('on', one && activeThemes.has(c.dataset.t)));
}
function renderThemeResults(){
  const box=document.getElementById('theme-results');
  if(!box) return;
  const theme = (VIEW==='themes' && activeThemes.size===1) ? [...activeThemes][0] : null;
  /* a picked theme replaces the folder list: only that theme's companies are on screen */
  document.getElementById('theme-panel').hidden = !!theme;
  document.getElementById('theme-heading').hidden = !!theme;
  box.hidden = !theme;
  if(!theme){ box.innerHTML=''; return; }
  const names=DATA.filter(d=>base(d)===theme).sort((a,b)=>String(a.name||'').localeCompare(String(b.name||'')));
  box.innerHTML=`<div class="theme-results-head"><button class="theme-back" id="theme-back">← All themes</button><div><span class="eyebrow">COMPANIES IN THEME</span><h3>${esc(shortT(theme))}</h3><p>${names.length} screened compan${names.length===1?'y':'ies'}</p></div><span class="folder-count">${names.length} names</span></div>
    <div class="company-files">${names.map(d=>`<button class="company-file" data-theme-company="${esc(d.code)}"><span class="file-symbol">${esc((d.name||'?').slice(0,1).toUpperCase())}</span><span class="file-copy"><b>${esc(d.name||'Unnamed company')}</b><small>${esc(d.code||'')} · Score ${fmt(d.final_score,1)}</small></span><span class="file-pin${WATCH.has(d.code)?' on':''}" data-file-pin="${esc(d.code)}" role="button" title="${WATCH.has(d.code)?'Remove from':'Add to'} watchlist" aria-label="Star ${esc(d.name)}">${WATCH.has(d.code)?'★':'☆'}</span><span class="file-arrow">›</span></button>`).join('')}</div>`;
  box.querySelector('#theme-back').onclick=()=>{ activeThemes=new Set(THEMES); syncChips(); render(); };
  /* the drawer's prev/next walks CURRENT, so point it at this theme's list */
  box.querySelectorAll('[data-file-pin]').forEach(s=>s.onclick=e=>{ e.stopPropagation(); togglePin(s.dataset.filePin); });
  box.querySelectorAll('[data-theme-company]').forEach(b=>b.onclick=()=>{
    const d=names.find(row=>String(row.code)===b.dataset.themeCompany);
    if(d){ CURRENT=names; openDrawer(d); }
  });
}

/* ---------- sliders ---------- */
const slBox=document.getElementById('sliders');
SL.forEach((s,i)=>{
  const d=document.createElement('div'); d.className='sl';
  d.innerHTML=`<label>${s.lab} <b id="sv${i}"></b></label>
    <input type="range" id="si${i}" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.v}">`;
  slBox.appendChild(d);
  const inp=d.querySelector('input'), out=d.querySelector('b');
  const upd=()=>{ s.v=+inp.value;
    const atEnd = s.inv ? (s.v>=s.max) : (s.v<=s.min);
    out.textContent = atEnd ? 'any' : (s.pre||'')+(s.step<1?s.v.toFixed(2):Math.round(s.v).toLocaleString('en-IN'))+s.unit;
  };
  inp.oninput=()=>{upd();render();}; upd();
});

/* Screen filters are one at a time: clicking a filter shows only its companies,
   clicking another switches to that one, clicking the same one again clears it. */
document.querySelectorAll('[data-tg]').forEach(b=>{
  b.onclick=()=>{
    const k=b.dataset.tg, on=!TG[k];
    if(LENSES.includes(k)) LENSES.forEach(x=>TG[x]=false);
    TG[k]=on;
    syncTgButtons(); render();
  };
});
/* ---------- search ---------- */
const qIn=document.getElementById('q'), qWrap=document.getElementById('swrap');
/* Top bar: logo goes home, the search box opens Companies with the query,
   footer links follow the same routes as the tabs. */
(function(){
  const ts=document.getElementById('top-search');
  let timer=0;
  const go=()=>{ const v=ts.value; if(VIEW!=='companies') setView('companies'); setQuery(v); ts.focus(); };
  ts.oninput=()=>{ clearTimeout(timer); timer=setTimeout(go,160); };
  ts.onkeydown=e=>{ if(e.key==='Enter'){ clearTimeout(timer); go(); } if(e.key==='Escape'){ ts.value=''; go(); } };
  document.getElementById('brand-link').onclick=()=>setView('overview');
  document.querySelectorAll('[data-foot]').forEach(b=>b.onclick=()=>{
    const id=b.dataset.foot;
    if(id==='method') setView('method',{nav:null});
    else document.getElementById(id).click();
  });
})();
function setQuery(v){
  qIn.value=v; QUERY=v.trim().toLowerCase();
  QTERMS = QUERY ? QUERY.split(/\s+/).filter(Boolean) : [];
  qWrap.classList.toggle('has', !!QUERY);
  render();
}
/* a keystroke used to rebuild all 310 rows and redraw all three charts; at that
   size the page visibly stalls mid-word, so settle briefly before re-rendering */
let qTimer=0;
qIn.oninput=()=>{ clearTimeout(qTimer); qTimer=setTimeout(()=>{ setQuery(qIn.value); document.getElementById('top-search').value=qIn.value; },110); };
document.getElementById('qclear').onclick=()=>{setQuery(''); qIn.focus();};
addEventListener('keydown',e=>{
  if(e.key==='/' && !/^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement||{}).tagName||'')){ e.preventDefault(); document.getElementById('top-search').focus(); }
  if(e.key==='Escape' && document.activeElement===qIn && QUERY){ setQuery(''); }
});
const SUGG=['torpedo','CRGO','NABL','DRDO','sole Indian','debunked','import substitution',
            'switchgear','forging','submarine','data centre','railway','pricing power','SME'];
document.getElementById('sugg').innerHTML =
  SUGG.map(s=>`<button data-q="${s}">${s}</button>`).join('') +
  /* the build date is derived from the data (see backend/app/seed.py), so this
     button follows it instead of carrying its own hand-typed copy of the date */
  (BUILD_NEW ? `<button data-new="${BUILD_NEW}">&#9733; new in this build (${DATA.filter(d=>d.added_on===BUILD_NEW).length})</button>` : '');
document.getElementById('sugg').onclick=e=>{
  const b=e.target.closest('button'); if(!b) return;
  if(b.dataset.new){ NEWSINCE = NEWSINCE===b.dataset.new?null:b.dataset.new; b.style.borderStyle=NEWSINCE?'solid':'dashed'; render(); }
  else setQuery(b.dataset.q===QUERY?'':b.dataset.q);
};
function hl(s){
  if(!QTERMS.length) return esc(s);
  let out=esc(s);
  QTERMS.forEach(term=>{
    const re=new RegExp('('+term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig');
    out=out.replace(re,'<mark>$1</mark>');
  });
  return out;
}

/* ---------- what changed ---------- */
document.getElementById('vhist').innerHTML = BUILD.history.map(h=>
  `<li><b>${h.v} — ${h.d}</b><div class="vd">${h.v===BUILD.version?'current build':''}</div>
   <ul>${h.items.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></li>`).join('');
document.getElementById('whatsnew').onclick=()=>{
  document.getElementById('changelog').scrollIntoView({behavior:'smooth',block:'start'});
};

/* ---------- filtering ---------- */
function pass(d){
  if(TG.watch && !WATCH.has(d.code)) return false;
  if(TG.nolens && d.has_lens_data) return false;
  if(QTERMS.length){ const hay=NAMEHIT?d._n:d._q; if(!QTERMS.every(w=>hay.includes(w))) return false; }
  if(NEWSINCE && d.added_on!==NEWSINCE) return false;
  if(!activeThemes.has(base(d))) return false;
  for(const s of SL){
    const v=nz(d[s.k]); if(v===null) continue;
    if(s.inv){ if(v>s.v) return false; } else { if(v<s.v) return false; }
  }
  if(TG.overhang && !d.capex_overhang) return false;
  if(TG.heavycap && !d.capex_heavy) return false;
  if(TG.guide15  && !d.guidance_over15) return false;
  if(TG.guideany && !d.guidance_flag) return false;
  if(TG.turn     && !d.pat_turnaround) return false;
  if(TG.haslens  && !d.has_lens_data) return false;
  if(TG.ipo      && !isRecentListing(d)) return false;
  if(TG.asme     && !ASME_BY_CODE[d.code]) return false;
  if(TG.auto     && d.screen!=='auto') return false;
  if(TG.ongate   && (d.gate_failures||[]).length) return false;
  if(TG.nosme && (nz(d.num_shareholders)!==null && d.num_shareholders<3000)) return false;
  if(TG.nopledge && (nz(d.promoter_pledge_pct)||0)>0.5) return false;
  if(TG.realsub){ const t=(d.import_substitution||'').toLowerCase();
    if(!t || t.startsWith('none') || t.includes('domestic demand play') || d.import_sub_verdict==='wrong') return false; }
  if(TG.cheap && (nz(d.pe)===null || d.pe>40)) return false;
  return true;
}

/* ---------- signals ---------- */
function sigBadges(d, full){
  let h='';
  if(d.capex_overhang) h+=`<span class="badge b-capex" title="P/E above 40 and CWIP at least 15% of net block">⚑ PE+CWIP</span>`;
  else if(d.capex_heavy) h+=`<span class="badge b-heavy" title="CWIP at least 25% of net block">🏗 capex</span>`;
  if(d.guidance_over15) h+=`<span class="badge b-guide" title="Management guides revenue growth above 15%">▲ ${fmt(d.guidance_pct,0)}%</span>`;
  else if(d.guidance_flag) h+=`<span class="badge nd" title="Management made a forward growth statement but did not quantify it">▲ outlook</span>`;
  if(d.pat_turnaround) h+=`<span class="badge b-turn" title="Latest reported period profitable after a loss in the prior three">↻ PAT+</span>`;
  if(full && d.source==='user') h+=`<span class="badge b-user">added on request</span>`;
  return h || '<span class="nd">—</span>';
}

/* ---------- table ---------- */
const COLS=[
  {k:'pin',   l:'★',      cls:'pincell', tip:'Pin a company to your watchlist',
     f:d=>`<button class="pin${WATCH.has(d.code)?' on':''}" data-pin="${esc(d.code)}" title="${WATCH.has(d.code)?'Remove from':'Add to'} watchlist" aria-label="Pin ${esc(d.name)}">${WATCH.has(d.code)?'★':'☆'}</button>`,
     sortf:d=>WATCH.has(d.code)?1:0},
  {k:'rank',  l:'#',        f:d=>d.rank ?? '<span class="nd" title="Auto-added: scored by the daily screen, not ranked with the researched board">auto</span>'},
  {k:'name',  l:'Company',  f:d=>`<a class="nm" href="${scrURL(d)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Open on screener.in">${hl(d.name)} <span class="ext">↗</span></a><span class="tc">${hl(d.code||'')}${d.tier===2?' · Tier 2':''}${d.added_on===BUILD_NEW?' · <b class="newbadge">NEW</b>':''}${ASME_BY_CODE[d.code]?' · <b class="asme-tag" title="Holds an active ASME certificate">ASME</b>':''}${d.screen==='auto'?' · <b class="auto-tag" title="Auto-added by the daily 7 AM screen; not yet researched by hand">AUTO-ADDED</b>':''}</span>`},
  {k:'theme', l:'Theme',    f:d=>`<span class="thm">${shortT(base(d))}</span>`},
  {k:'final_score',l:'Score', f:d=>{
      if(d.final_score===null||d.final_score===undefined)
        return `<span class="pend" title="Never scored — triage only">not scored</span>`;
      const isV4=d.rubric==='v4';
      const pct=Math.max(0,Math.min(100,+d.final_score));
      const bar=`<span class="sbar${isV4?' v4':''}" title="${fmt(d.final_score,1)} out of 100 on the ${isV4?'v4 moat':'v3'} rubric${isV4?' — a v4 score is not comparable with a v3 score, which is why this bar is hatched':''}"><i style="width:${pct.toFixed(1)}%"></i></span>`;
      return `<span class="sc">${fmt(d.final_score,1)}</span>${isV4?'<sup class="rub" title="scored on the v4 moat rubric — not comparable with a v3 score">v4</sup>':''}${d.risk_penalty>0?`<span class="pen">−${fmt(d.risk_penalty,0)}</span>`:''}${d.verified?`<span class="flag f-${d.verified}">${d.verified==='clean'?'✓':'!'}</span>`:''}${bar}`;}},
  {k:'signals',l:'Signals', f:d=>d.has_lens_data?sigBadges(d,true):pend, cls:'sig',
     sortf:d=>(d.capex_overhang?4:0)+(d.guidance_over15?2:0)+(d.pat_turnaround?1:0)},
  {k:'market_cap_cr',l:'M-cap ₹cr', f:d=>fmtI(d.market_cap_cr)},
  {k:'pe',    l:'P/E',      f:d=>fmt(d.pe,1)},
  {k:'cwip_cr',l:'CWIP ₹cr', f:d=>d.has_lens_data?(d.cwip_cr===null||d.cwip_cr===undefined?'—':(d.cwip_cr>=10?fmtI(d.cwip_cr):fmt(d.cwip_cr,1))):pend},
  {k:'cwip_pct_net_block',l:'CWIP %NB', f:d=>{
      if(!d.has_lens_data) return pend;
      const v=nz(d.cwip_pct_net_block); if(v===null) return '—';
      const c = v>=25 ? 'var(--crit)' : v>=15 ? 'var(--s4)' : 'var(--ink2)';
      return `<span style="color:${c}">${v>=100?fmtI(v):fmt(v,1)}</span>`;}},
  {k:'guidance_pct',l:'Guide %', f:d=>{
      if(!d.has_lens_data) return pend;
      if(d.guidance_pct!==null&&d.guidance_pct!==undefined)
        return `<span style="color:${d.guidance_over15?'var(--good-ink)':'var(--ink2)'}">${fmt(d.guidance_pct,0)}</span>`;
      return d.guidance_flag ? '<span class="nd" title="Forward statement made, no number given">n/q</span>' : '—';}},
  {k:'promoter_pct',l:'Promoter %', f:d=>fmt(d.promoter_pct)},
  {k:'fii_pct',l:'FII %',   f:d=>fmt(d.fii_pct,2)},
  {k:'dii_pct',l:'DII %',   f:d=>fmt(d.dii_pct,2)},
  {k:'num_shareholders',l:'Holders', f:d=>fmtI(d.num_shareholders)},
  {k:'roce_pct',l:'ROCE %', f:d=>fmt(d.roce_pct)},
  {k:'roe_pct',l:'ROE %',   f:d=>fmt(d.roe_pct)},
];
const thead=document.getElementById('thead');
let HIDDEN=new Set();
const VIS=()=>COLS.filter(c=>!HIDDEN.has(c.k));
function buildHead(){
  thead.innerHTML='';
  VIS().forEach(c=>{
    const th=document.createElement('th'); th.dataset.k=c.k;
    th.innerHTML=c.l+' <span class="arw"></span>';
    th.title = c.tip || ('Sort by '+String(c.l).replace(/<[^>]+>/g,''));
    th.onclick=()=>{ if(sortKey===c.k) sortDir*=-1; else {sortKey=c.k; sortDir = (c.k==='rank'||c.k==='name')?1:-1;} render(); };
    thead.appendChild(th);
  });
}
const SORTF = Object.fromEntries(COLS.filter(c=>c.sortf).map(c=>[c.k,c.sortf]));

let CURRENT=[], CUR=-1, DRAWERI=-1;
/* The table, its header and the count line belong to whichever page picked the
   companies; until something is picked the page shows a one-line hint instead. */
const HINT={overview:'Click a tile above to see the companies behind that number.',
            companies:'Type a company name or ticker above — only the companies it names will be shown.',
            filters:'Click a filter above to see only the companies that match it.'};
function renderResultsFrame(){
  const show=showResults();
  const asmeList = show && VIEW==='filters' && TG.asme;
  const head=document.getElementById('results-head'), cnt=document.getElementById('count');
  document.getElementById('research').hidden=!show || asmeList;
  cnt.hidden = !(show || HINT[VIEW]) || asmeList;
  cnt.classList.toggle('view-hint', !show);
  if(!show) cnt.textContent = HINT[VIEW] || '';
  let label='';
  if(show && VIEW==='overview') label = TILE_LABEL[TILE] || '';
  if(show && VIEW==='filters'){
    const on=LENSES.find(k=>TG[k]);
    const b=on && document.querySelector(`.filter-panel [data-tg="${on}"]`);
    label = b ? (b.dataset.label || b.textContent.trim()) : 'Numeric filters';
  }
  head.hidden = !label;
  renderAsmeOffBoard(asmeList);
  if(label){
    const sub = asmeList
      ? `Every NSE/BSE-listed company holding an active ASME certificate${ASME.scanned_on?` (scan of ${esc(ASME.scanned_on)})`:''}, with each certificate type and when it was received. Your board's companies are marked.`
      : 'Only the companies that match are listed below';
    head.innerHTML=`<div><b>${esc(label)}</b><small>${sub}</small></div><button class="theme-back" id="results-clear" type="button">✕ Clear</button>`;
    head.querySelector('#results-clear').onclick=()=>{ clearFilters(); render(); };
  }
}
/* The ASME certified filter: one list of every NSE/BSE-listed ASME
   certificate holder, the board's own companies and the rest together, in
   one format. Each card shows every certificate type with when it was first
   received and last (re)issued; board companies also carry their theme,
   score and a way into their scorecard. The list sorts and filters by
   certification date, certificate type and whether a company is on the board. */
const ASME_OFF={sort:'newest', when:'', type:'', board:''};
const ASME_OFF_SORTS={
  newest:  {l:'Newest certificate first',          f:(a,b)=>String(b.latest||b.since||'').localeCompare(String(a.latest||a.since||''))},
  oldest:  {l:'Oldest certification first',        f:(a,b)=>String(a.since||'9999').localeCompare(String(b.since||'9999'))},
  recent:  {l:'Most recently certified companies', f:(a,b)=>String(b.since||'').localeCompare(String(a.since||''))},
  name:    {l:'Name A–Z',                          f:(a,b)=>String(a.name).localeCompare(String(b.name))},
};
const ASME_OFF_WHEN={
  '':    {l:'Certified: any time'},
  '1y':  {l:'Certificate issued in the last year',    test:c=>asmeYearsAgo(c.latest||c.since)<=1},
  '3y':  {l:'Certificate issued in the last 3 years', test:c=>asmeYearsAgo(c.latest||c.since)<=3},
  'old': {l:'Certified for over 10 years',            test:c=>asmeYearsAgo(c.since)>10},
};
function asmeYearsAgo(iso){
  if(!iso) return Infinity;
  return (Date.now()-Date.parse(String(iso).slice(0,10)))/(365.25*864e5);
}
function asmeDate(iso){
  if(!iso) return '—';
  const [y,m,d]=String(iso).slice(0,10).split('-').map(Number);
  return `${d} ${GMONTHS[m-1]} ${y}`;
}
function asmeCertRows(c){
  const types=window.ASME_CERT_TYPES||{};
  const details=(c.cert_details&&c.cert_details.length) ? c.cert_details : (c.certs||[]).map(code=>({code, first:c.since, latest:null}));
  return details.map(x=>`<tr>
      <td><b>${esc(types[x.code]||x.code)}</b> <span class="asme-code">${esc(x.code)}</span></td>
      <td>${asmeDate(x.first)}</td>
      <td>${x.latest&&x.latest!==x.first?asmeDate(x.latest):'—'}</td>
    </tr>`).join('');
}
function renderAsmeOffBoard(on){
  const box=document.getElementById('asme-offboard');
  const all=(ASME.companies||[]);
  box.hidden = !on || !all.length;
  if(box.hidden){ box.innerHTML=''; return; }
  const types=window.ASME_CERT_TYPES||{};
  const byCode={}; DATA.forEach(d=>{ byCode[d.code]=d; });
  const onBoard=all.filter(c=>c.board_code && byCode[c.board_code]).length;
  const typeNames=[...new Set(all.flatMap(c=>(c.certs||[]).map(x=>types[x]||x)))].sort();
  const when=ASME_OFF_WHEN[ASME_OFF.when]||ASME_OFF_WHEN[''];
  const list=all.filter(c=>(!when.test||when.test(c))
      && (!ASME_OFF.type||(c.certs||[]).some(x=>(types[x]||x)===ASME_OFF.type))
      && (ASME_OFF.board!=='on' || byCode[c.board_code]) && (ASME_OFF.board!=='off' || !byCode[c.board_code]))
    .sort((ASME_OFF_SORTS[ASME_OFF.sort]||ASME_OFF_SORTS.newest).f);
  const opt=(obj,cur)=>Object.entries(obj).map(([k,v])=>`<option value="${k}"${k===cur?' selected':''}>${v.l}</option>`).join('');
  const boards={'':{l:`All ASME certified (${all.length})`}, on:{l:`On your board (${onBoard})`}, off:{l:`Not on your board (${all.length-onBoard})`}};
  box.innerHTML=`<div class="asme-off-controls">
      <select class="gal-select" id="asme-off-board" aria-label="Board">${opt(boards,ASME_OFF.board)}</select>
      <select class="gal-select" id="asme-off-sort" aria-label="Sort">${opt(ASME_OFF_SORTS,ASME_OFF.sort)}</select>
      <select class="gal-select" id="asme-off-when" aria-label="When certified">${opt(ASME_OFF_WHEN,ASME_OFF.when)}</select>
      <select class="gal-select" id="asme-off-type" aria-label="Certificate type"><option value="">All certificate types</option>${typeNames.map(t=>`<option${t===ASME_OFF.type?' selected':''}>${esc(t)}</option>`).join('')}</select>
      <span class="asme-off-n">${list.length} of ${all.length}</span>
    </div>
    ${list.length ? `<div class="asme-off-grid">${list.map(c=>{
      const d=byCode[c.board_code], yrs=asmeYearsAgo(c.since), on=d && WATCH.has(d.code);
      const sym=String(c.symbol||'').replace(/-[A-Z]$/,'');
      return `<article class="asme-off${d?' on-board':''}">
        <div class="asme-off-top">
          <span class="gc-tick">${esc(c.symbol)}</span>
          <b class="asme-off-name">${esc(d?d.name:c.name)}</b>
          ${d?`<span class="asme-tag">On your board</span><button type="button" class="bpc-star${on?' on':''}" data-asme-star="${esc(d.code)}" title="${on?'Remove from':'Add to'} watchlist">${on?'★':'☆'}</button>`:''}
        </div>
        ${d?`<div class="asme-off-board">${esc(shortT(base(d)))} · Score <b>${fmt(d.final_score,1)}</b> · M-cap ₹${fmtI(d.market_cap_cr)} cr · P/E ${fmt(d.pe,1)} · ROCE ${fmt(d.roce_pct)}%</div>`:''}
        <div class="asme-off-meta">${esc((c.exch||[]).join(' + '))} · ASME-certified since <b>${asmeDate(c.since)}</b>${isFinite(yrs)?` (${yrs<1?'under a year':Math.floor(yrs)+' yr'+(Math.floor(yrs)===1?'':'s')})`:''}${c.latest&&c.latest!==c.since?` · latest certificate <b>${asmeDate(c.latest)}</b>`:''}</div>
        <table class="asme-certs-table"><thead><tr><th>Certificate type</th><th>First received</th><th>Latest issued</th></tr></thead><tbody>${asmeCertRows(c)}</tbody></table>
        <div class="asme-off-acts">
          ${d?`<button type="button" class="bp-btn" data-asme-card="${esc(d.code)}">Open scorecard</button>`:''}
          <a class="bp-btn" href="https://www.screener.in/company/${encodeURIComponent(sym)}/" target="_blank" rel="noopener">screener.in ↗</a>
        </div>
      </article>`; }).join('')}</div>`
    : '<p class="view-hint">No company matches these filters.</p>'}`;
  const bind=(id,key)=>{ document.getElementById(id).onchange=e=>{ ASME_OFF[key]=e.target.value; renderAsmeOffBoard(true); }; };
  bind('asme-off-board','board'); bind('asme-off-sort','sort'); bind('asme-off-when','when'); bind('asme-off-type','type');
  box.querySelectorAll('[data-asme-card]').forEach(b=>b.onclick=()=>{
    const d=byCode[b.dataset.asmeCard]; if(!d) return;
    CURRENT=list.map(c=>byCode[c.board_code]).filter(Boolean); openDrawer(d);
  });
  box.querySelectorAll('[data-asme-star]').forEach(b=>b.onclick=()=>{
    togglePin(b.dataset.asmeStar);   /* re-renders the page, which redraws this list */
  });
}
function render(){
  if(BUSY) return;                       /* batched updates render once, at the end */
  renderThemeResults();
  renderResultsFrame();
  buildHead();
  const cols=VIS();
  NAMEHIT = QTERMS.length>0 && DATA.some(d=>QTERMS.every(w=>d._n.includes(w)));
  const rows=DATA.filter(pass).sort((a,b)=>{
    const g=SORTF[sortKey];
    let x = g? g(a) : a[sortKey], y = g? g(b) : b[sortKey];
    if(typeof x==='string'||typeof y==='string') return String(x||'').localeCompare(String(y||''))*sortDir;
    x=nz(x); y=nz(y);
    if(x===null&&y===null) return 0;
    if(x===null) return 1; if(y===null) return -1;
    return (x-y)*sortDir;
  });
  CURRENT=rows;
  [...thead.children].forEach(th=>{
    th.querySelector('.arw').textContent = th.dataset.k===sortKey ? (sortDir>0?'▲':'▼') : '';
  });
  const tb=document.getElementById('tbody');
  tb.innerHTML = rows.length ? '' : `<tr><td colspan="${cols.length}" class="empty">${
      (TG.watch && !WATCH.size) ? 'Your watchlist is empty — click the ☆ beside any company to pin it.'
    : QUERY ? `Nothing matches <b>“${esc(QUERY)}”</b> with the current filters. Try a single word, or clear the filters.`
    : 'No company matches these filters — loosen one of the sliders or turn a toggle off.'}</td></tr>`;
  document.getElementById('hits').innerHTML = QUERY
    ? (NAMEHIT || !rows.length
        ? `<b>${rows.length}</b> compan${rows.length===1?'y':'ies'} named “${esc(QUERY)}”`
        : `No company named “${esc(QUERY)}” — <b>${rows.length}</b> mention${rows.length===1?'s':''} it in their research`)
    : (NEWSINCE?`<b>${rows.length}</b> added in this build`:`${DATA.length} companies · <b>/</b> to search · <b>?</b> for shortcuts`);
  if(!showResults()){ CURRENT=rows; renderWatchlist(); syncURL(); return; }
  /* one reflow for the whole table instead of one per row */
  const frag=document.createDocumentFragment();
  rows.forEach((d,i)=>{
    const tr=document.createElement('tr');
    tr.dataset.i=i;
    tr.innerHTML=cols.map(c=>`<td${c.cls?` class="${c.cls}"`:''}>${c.f(d)}</td>`).join('');
    tr.onclick=e=>{ if(e.target.closest('[data-pin]')) return; CUR=i; markCursor(); openDrawer(d); };
    const pb=tr.querySelector('[data-pin]');
    if(pb) pb.onclick=e=>{ e.stopPropagation(); togglePin(d.code); };
    frag.appendChild(tr);
  });
  tb.appendChild(frag);
  if(CUR>=rows.length) CUR=rows.length-1;
  markCursor();
  const cnt=k=>rows.filter(r=>r[k]).length;
  const pnd=rows.filter(r=>!r.has_lens_data).length;
  document.getElementById('count').textContent =
    `${rows.length} of ${DATA.length} companies shown · ${cnt('capex_overhang')} flagged high P/E + heavy CWIP · ${cnt('guidance_over15')} guiding above 15% · ${cnt('pat_turnaround')} with PAT turning positive`
    + (pnd?` · ⋯ ${pnd} still awaiting the capex/guidance pull`:'')
    + ` · click any row for the full scorecard, or the ☆ to pin it`;
  renderWatchlist(); syncURL();
}

/* ---------- PAT sparkline ---------- */
function patChart(d){
  const p=d.pat_periods||[];
  if(!p.length) return `<p class="nd" style="font-size:12.5px;margin:0">No periodic profit data published yet.</p>`;
  const W=520,H=112,B=26,T=10;
  const vals=p.map(x=>x[1]);
  const hi=Math.max(0,...vals), lo=Math.min(0,...vals), span=(hi-lo)||1;
  const zero=T+(hi/span)*(H-T-B);
  const bw=Math.min(46,(W-8)/p.length-8);
  const step=(W-8)/p.length;
  let s=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label="Profit after tax by period">`;
  s+=`<line x1="0" x2="${W}" y1="${zero}" y2="${zero}" stroke="var(--axis)" stroke-width="1"/>`;
  p.forEach((x,i)=>{
    const v=x[1], cx=4+i*step+step/2;
    const y=v>=0?zero-(v/span)*(H-T-B):zero;
    const h=Math.max(1.5,Math.abs(v)/span*(H-T-B));
    const col=v<0?'var(--crit)':(i===p.length-1?'var(--s1)':'var(--s3)');
    s+=`<rect x="${cx-bw/2}" y="${y}" width="${bw}" height="${h}" rx="2" fill="${col}" fill-opacity="${i===p.length-1?.95:.62}"><title>${esc(x[0])}: ₹${v}cr</title></rect>`;
    s+=`<text x="${cx}" y="${H-12}" text-anchor="middle" class="tk" style="font-size:9px">${esc(String(x[0]).replace(' 20',"'"))}</text>`;
    s+=`<text x="${cx}" y="${v>=0?y-3:y+h+9}" text-anchor="middle" class="tk" style="font-size:9px">${v}</text>`;
  });
  return s+'</svg>';
}

/* ---------- drawer ---------- */
const drawer=document.getElementById('drawer'), scrim=document.getElementById('scrim');
const PILL_BASE=[['Moat','s_moat',25],['Import substitution','s_import_sub',20],['Promoter','s_promoter',15],
            ['Institutional','s_institutional',15]];
const PILL_TAIL=[['Financials','s_financials',10]];
/* v5-rubric records (the v8-moat wave) score a "strategic" pillar instead of
   "under-covered" -- show whichever one the record actually carries rather
   than silently rendering the missing one as zero. */
function pillarsFor(d){
  const mid = (d.s_strategic!==undefined && d.s_strategic!==null)
    ? ['Strategic','s_strategic',15] : ['Under-covered','s_undercovered',15];
  return [...PILL_BASE, mid, ...PILL_TAIL];
}
function openDrawer(d){
  const inst=(nz(d.fii_pct)||0)+(nz(d.dii_pct)||0);
  const warns=(d.warnings||[]).filter(Boolean);
  const gates=(d.gate_failures||[]).filter(Boolean);
  const pen=(d.penalty_detail||[]).filter(Boolean);
  drawer.innerHTML=`
   <div class="dhead">
     <button class="close" onclick="closeDrawer()">×</button>
     <div class="dnav">
       <span class="dpos" id="dpos"></span>
       <button class="dpin${WATCH.has(d.code)?' on':''}" id="dpin" title="${WATCH.has(d.code)?'Remove from':'Add to'} watchlist (w)">${WATCH.has(d.code)?'★':'☆'}</button>
       <button id="dprev" title="Previous company (←)">‹</button>
       <button id="dnext" title="Next company (→)">›</button>
     </div>
     <div class="thm" style="margin-bottom:6px">${base(d)} · ${d.screen==='auto'?'auto-added '+esc(d.added_on||''):d.final_score===null?'unranked':'rank '+d.rank}<span class="tierbadge">${d.tier===1?'Tier 1 · institutions present':'Tier 2 · no institutions yet'}</span></div>
     <h2 style="margin:0 0 3px;font-size:19px">${hl(d.name)}</h2>
     <div class="tc">${d.code||''}${d.industry?' · '+esc(d.industry):''}</div>
     <div style="margin-top:9px">
       <span class="badge ${SCREENS[d.screen].cls}" title="${SCREENS[d.screen].full}">${SCREENS[d.screen].lab}</span>
       ${d.claim_grade?`<span class="cg ${CG[d.claim_grade]||'cg-none'}" style="margin-right:4px">claim: ${esc(d.claim_grade)}</span>`:''}
       ${d.has_lens_data?sigBadges(d,false):'<span class="pend" style="font-size:11px">capex / guidance pass pending</span>'}
     </div>
     <div class="links">
       <a class="lnk" href="${scrURL(d)}" target="_blank" rel="noopener">screener.in ↗</a>
       <button type="button" class="lnk lnk-report" id="dreport">Research report ›</button>
       <a class="lnk" href="${scrURL(d)}#documents" target="_blank" rel="noopener">Filings &amp; annual reports ↗</a>
       ${exURL(d)?`<a class="lnk" href="${exURL(d)}" target="_blank" rel="noopener">${d.nse_code&&!/^\d+$/.test(d.nse_code)?'NSE':'BSE'} quote ↗</a>`:''}
       <a class="lnk" href="https://www.google.com/search?q=${encodeURIComponent(d.name+' India company news')}" target="_blank" rel="noopener">News ↗</a>
     </div>
   </div>
   <div class="dbody">
     <div class="kv">
       <div><span>${d.rubric==='v4'?'Score (v4 rubric)':'Composite score'}</span><b style="color:var(--s1)">${d.final_score===null||d.final_score===undefined?'—':fmt(d.final_score)}</b></div>
       <div><span>Market cap</span><b>₹${fmtI(d.market_cap_cr)} cr</b></div>
       <div><span>Price</span><b>${d.price?'₹'+fmtI(d.price):'—'}</b></div>
       <div><span>P/E</span><b>${fmt(d.pe)}</b></div>
       <div><span>CWIP</span><b>${d.cwip_cr===null||d.cwip_cr===undefined?'—':'₹'+fmtI(d.cwip_cr)+' cr'}</b></div>
       <div><span>CWIP / net block</span><b${d.capex_overhang?' style="color:var(--crit)"':''}>${nz(d.cwip_pct_net_block)===null?'—':fmt(d.cwip_pct_net_block,1)+'%'}</b></div>
       <div><span>Promoter</span><b>${nz(d.promoter_pct)===null?'—':fmt(d.promoter_pct)+'%'}</b></div>
       <div><span>FII + DII</span><b>${(nz(d.fii_pct)===null&&nz(d.dii_pct)===null)?'—':fmt(inst,2)+'%'}</b></div>
       <div><span>Public float</span><b>${nz(d.public_pct)===null?'—':fmt(d.public_pct)+'%'}</b></div>
       <div><span>ROCE</span><b>${nz(d.roce_pct)===null?'—':fmt(d.roce_pct)+'%'}</b></div>
       <div><span>ROE</span><b>${nz(d.roe_pct)===null?'—':fmt(d.roe_pct)+'%'}</b></div>
       <div><span>Retail holders</span><b>${fmtI(d.num_shareholders)}</b></div>
     </div>

     ${d.screen==='auto'?`<div class="sec auto-sec"><h4>Auto-added by the daily screen</h4>
       <p>Added on ${esc(d.added_on||'')} because it passes the board's gates (market cap, promoter, public float, ROCE, ROE, shareholders)
       and the company itself states a moat. The numbers are screener.in's and the moat is the company's own wording, matched by rules —
       nobody has researched it yet, so read it as a lead, not a verdict.</p>
       ${(d.evidence_sources||[]).length?`<p class="auto-src">Evidence: ${(d.evidence_sources||[]).map(u=>`<a class="lnk" href="${esc(u)}" target="_blank" rel="noopener">${/\.pdf/i.test(u)?'annual report':'screener.in profile'} ↗</a>`).join(' ')}</p>`:''}
       ${ME&&ME.is_admin?`<button type="button" class="btn auto-hide" id="d-hide">Remove from board</button>`:''}</div>`:''}

     ${gates.length?`<div class="sec"><h4>Added on request — gates it does not clear</h4>
       <ul class="gates">${gates.map(g=>`<li>✕ ${esc(g)}</li>`).join('')}</ul>
       <p style="margin-top:8px;font-size:12px">It is on the board because you asked for it, and its score carries an explicit penalty for sitting outside the screen. The research is real; the ranking is not comparable like-for-like with names the screen surfaced on its own.</p></div>`:''}

     ${d.has_lens_data ? `
     <div class="sec"><h4>Capex &amp; CWIP — capacity that is not earning yet</h4>
       <p style="margin-bottom:8px"><b>${d.cwip_cr===null||d.cwip_cr===undefined?'—':'₹'+fmtI(d.cwip_cr)+' cr'}</b> in capital work-in-progress${nz(d.cwip_prev_cr)!==null?` (from ₹${fmtI(d.cwip_prev_cr)} cr a year earlier)`:''} against a net block of <b>${d.net_block_cr===null||d.net_block_cr===undefined?'—':'₹'+fmtI(d.net_block_cr)+' cr'}</b>${nz(d.cwip_pct_net_block)!==null?` — <b>${fmt(d.cwip_pct_net_block,1)}%</b>`:''}.</p>
       <p>${hl(d.cwip_note)||'—'}</p>
       ${d.capex_overhang?`<p style="margin-top:9px;color:var(--crit);font-size:12.5px"><b>⚑ Flagged.</b> A P/E of ${fmt(d.pe,1)} is being paid while ${fmt(d.cwip_pct_net_block,0)}% of the asset base is still under construction — the multiple assumes the new capacity works.</p>`:''}
     </div>

     <div class="sec"><h4>Management's own growth outlook</h4>
       ${d.guidance_quote?`<p class="quote">${esc(d.guidance_quote)}</p><p class="qsrc">${esc(d.guidance_source)}${d.guidance_pct!==null&&d.guidance_pct!==undefined?` · implies <b style="color:${d.guidance_over15?'var(--good-ink)':'var(--ink2)'}">${fmt(d.guidance_pct,0)}% revenue growth</b>`:' · no percentage given'}</p>`
        :`<p class="nd">No forward revenue-growth statement from management was found in filings, earnings calls or investor presentations. Order-book announcements and capacity expansions were checked and deliberately not counted as guidance.</p>`}
     </div>

     <div class="sec"><h4>Profit trajectory${d.period_type?` · ${d.period_type} reporting`:''}</h4>
       ${patChart(d)}
       ${d.pat_turn_note?`<p style="margin-top:8px;color:var(--s1);font-size:12.5px"><b>↻ ${esc(d.pat_turn_note)}</b></p>`:''}
     </div>` : `
     <div class="sec"><h4>Capex, guidance &amp; profit trajectory</h4>
       <p class="caveat">Not pulled for this name yet. The CWIP, management-guidance and quarterly-PAT pass has been completed for the v3 deep-dive names, the fresh v5 sweep and the names you added by hand. It is still outstanding for the v4 moat cohort, the v3 screening-pass names and the triage list.</p>
     </div>`}

     ${d.pricing_power_note?`<div class="sec"><h4>Pricing power — the five-year margin record</h4><p>${hl(d.pricing_power_note)}</p></div>`:''}
     ${d.v3_gates?`<div class="sec"><h4>How it fares against the v3 gates</h4><p>${hl(d.v3_gates)}</p></div>`:''}
     ${d.watch_note?`<div class="sec"><h4>Watch</h4><p>${hl(d.watch_note)}</p></div>`:''}
     ${d.triage_verdict?`<div class="sec"><h4>Triage verdict</h4><p><b>${esc(d.triage_verdict)}</b></p></div>`:''}

     ${ASME_BY_CODE[d.code]?(a=>`<div class="sec"><h4>ASME certification</h4>
       <p><b class="asme-tag">ASME</b> Active certificate holder since <b>${asmeDate(a.since)}</b>${a.latest&&a.latest!==a.since?`, latest certificate issued <b>${asmeDate(a.latest)}</b>`:''}.</p>
       <table class="asme-certs-table"><thead><tr><th>Certificate type</th><th>First received</th><th>Latest issued</th></tr></thead><tbody>${asmeCertRows(a)}</tbody></table>
       <p class="caveat" style="margin-top:6px">From ASME's own CA Connect directory, matched to this listing by the weekly scan${ASME.scanned_on?' of '+esc(ASME.scanned_on):''}.</p></div>`)(ASME_BY_CODE[d.code]):''}
     ${d.data_note?`<div class="sec"><h4>Data caveats on this name</h4><p class="caveat">${esc(d.data_note)}</p></div>`:''}

     ${nz(d.s_moat)!==null?`<div class="sec"><h4>Score breakdown</h4>
       ${pillarsFor(d).map(([l,k,mx],i)=>{
         const v=nz(d[k])||0, pct=Math.max(2,(v/mx)*100);
         return `<div class="bar"><i>${l}</i>
           <span class="track"><span class="fill" style="width:${pct}%;background:var(${SERIES[i]})"></span></span>
           <b>${v.toFixed(1)}<span style="color:var(--muted);font-weight:400">/${mx}</span></b></div>`;
       }).join('')}
       ${pen.length?`<p style="margin-top:10px;font-size:12px;color:var(--ink2)"><b style="color:var(--crit)">Risk penalty −${fmt(d.risk_penalty,0)}:</b> ${pen.map(esc).join(' · ')}</p>`:''}
     </div>` : (pen.length?`<div class="sec"><h4>Itemised penalties</h4><p><b style="color:var(--crit)">−${fmt(d.risk_penalty,0)}:</b> ${pen.map(esc).join(' · ')}</p></div>`:'')}

     ${d.business?`<div class="sec"><h4>What the business actually does</h4><p>${hl(d.business)}</p></div>`:''}
     ${d.import_substitution?`<div class="sec"><h4>Import-substitution angle${d.import_sub_verdict?` · independently rated <b style="color:${d.import_sub_verdict==='solid'?'var(--good-ink)':'var(--serious)'}">${d.import_sub_verdict}</b>`:''}</h4><p>${hl(d.import_substitution)}</p></div>`:''}
     ${d.moat_note?`<div class="sec"><h4>${d.screen==='v4-moat'||d.screen==='v4-triage'?'The exclusivity claim, and the evidence for it':'The moat'}</h4><p>${hl(d.moat_note)}</p></div>`:''}
     ${d.why_obscure?`<div class="sec"><h4>Why the market ignores it</h4><p>${hl(d.why_obscure)}</p></div>`:''}
     ${d.risk_note?`<div class="sec"><h4>The biggest risk</h4><p>${hl(d.risk_note)}</p></div>`:''}
     ${d.liquidity_note?`<div class="sec"><h4>Liquidity reality check</h4><p>${hl(d.liquidity_note)}</p></div>`:''}
     ${d.verify_comment?`<div class="sec"><h4>Independent verification</h4><p>${hl(d.verify_comment)}</p></div>`:''}
     ${warns.length?`<div class="sec"><h4>Flags &amp; risks</h4><ul class="warns">${warns.map(w=>`<li>⚠ ${esc(w)}</li>`).join('')}</ul></div>`:''}
     ${d.completeness!==null&&d.completeness!==undefined?`<div class="sec"><h4>Data completeness</h4><p>${d.completeness}% of tracked fields populated. Raw score ${fmt(d.score)}; confidence-adjusted score ${fmt(d.adj_score)}; after risk penalty ${fmt(d.final_score)}.</p></div>`:''}
   </div>`;
  drawer.classList.add('on'); scrim.classList.add('on');
  drawer.scrollTop=0;
  /* wire the header nav so the drawer walks the filtered list without ever closing */
  DRAWERI = CURRENT.indexOf(d);
  const dp=drawer.querySelector('#dprev'), dn=drawer.querySelector('#dnext'), dpos=drawer.querySelector('#dpos');
  if(dpos) dpos.textContent = DRAWERI>=0 ? `${DRAWERI+1} of ${CURRENT.length}` : '';
  if(dp){ dp.disabled = DRAWERI<=0;                             dp.onclick=()=>stepDrawer(-1); }
  if(dn){ dn.disabled = DRAWERI<0 || DRAWERI>=CURRENT.length-1; dn.onclick=()=>stepDrawer(1);  }
  const dpin=drawer.querySelector('#dpin');
  if(dpin) dpin.onclick=()=>{ togglePin(d.code); openDrawer(d); };
  const drep=drawer.querySelector('#dreport');
  if(drep) drep.onclick=()=>openReport(d.code);
  const dhide=drawer.querySelector('#d-hide');
  if(dhide) dhide.onclick=async ()=>{
    if(!confirm(`Remove ${d.name} from the board? It won't be auto-added again. (Admins can restore it via the API.)`)) return;
    dhide.disabled=true;
    try{
      const r=await fetch(`/api/companies/${encodeURIComponent(d.code)}/remove`,{method:'POST',credentials:'same-origin'});
      if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||('HTTP '+r.status));
      const i=DATA.indexOf(d); if(i>=0) DATA.splice(i,1);
      closeDrawer(); render();
    }catch(e){ dhide.disabled=false; alert('Could not remove: '+e.message); }
  };
}
function closeDrawer(){ drawer.classList.remove('on'); scrim.classList.remove('on'); }
  window.closeDrawer = closeDrawer;
scrim.onclick=closeDrawer;
addEventListener('keydown',e=>{ if(e.key==='Escape') closeDrawer(); });

/* ---------- theme toggle ---------- */
const tbtn=document.getElementById('theme');
if(tbtn){
  tbtn.onclick=()=>{
    const dark=document.documentElement.dataset.theme==='dark';
    document.documentElement.dataset.theme=dark?'light':'dark';
    tbtn.textContent=dark?'◐ Dark':'◑ Light';
    render();
  };
  if(matchMedia('(prefers-color-scheme: dark)').matches){ document.documentElement.dataset.theme='dark'; tbtn.textContent='◑ Light'; }
}

/* ==================================================================
   Interactive layer
   Everything below turns the board from a page you read into one you
   drive: a watchlist that survives a reload, side-by-side comparison,
   a live account of what is narrowing the view, a shareable URL, CSV
   of exactly what is on screen, column control, and keyboard driving.
   ================================================================== */

/* ---------- watchlist ---------- */
function saveWatch(){
  if(WMODE==='server'){
    fetch('/api/watchlist',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({codes:[...WATCH]})})
      .then(r=>{ if(!r.ok) throw new Error(r.status); }).catch(()=>watchFailed());
    return;
  }
  try{ localStorage.setItem('dms.watch', JSON.stringify([...WATCH])); }catch(e){}
}
function togglePin(code){
  if(!code) return;
  const on=!WATCH.has(code);
  if(on) WATCH.add(code); else WATCH.delete(code);
  if(WMODE==='server'){
    fetch('/api/watchlist/'+encodeURIComponent(code),{method:on?'POST':'DELETE',credentials:'same-origin'})
      .then(r=>{ if(!r.ok) throw new Error(r.status); })
      .catch(()=>{ if(on) WATCH.delete(code); else WATCH.add(code); render(); watchFailed(); });
  } else saveWatch();
  render();
}
let WFAIL=0;
function watchFailed(){
  if(Date.now()-WFAIL<5000) return;   /* one message, not one per click */
  WFAIL=Date.now();
  openModal('<h3>Watchlist not saved</h3><p class="sub">The change could not be saved to your account — check your connection, or log in again if your session ended. Reload the page to see your saved watchlist.</p>');
}
/* Once logged in, the account's list replaces the browser one. Stars made in this
   browser before accounts existed are copied into the account once, then removed
   from the browser, so another person using the same computer never inherits them. */
async function watchSync(){
  let j;
  try{ j=await fetchJSON('/api/watchlist'); }catch(e){ return; }
  if(!j || !j.accounts) return;
  let local=[];
  try{ local=JSON.parse(localStorage.getItem('dms.watch')||'[]'); }catch(e){}
  if(Array.isArray(local) && local.length){
    try{
      const r=await fetch('/api/watchlist',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({codes:local,merge:true})});
      if(r.ok){ j=await r.json(); try{ localStorage.removeItem('dms.watch'); }catch(e){} }
    }catch(e){}
  }
  WMODE='server';
  WATCH=new Set(j.codes||[]);
  render();
}
function syncTgButtons(){
  document.querySelectorAll('[data-tg]').forEach(b=>b.classList.toggle('on', !!TG[b.dataset.tg]));
  document.querySelectorAll('[data-tile]').forEach(b=>b.classList.toggle('on', b.dataset.tile===TILE));
}
/* ---------- my watchlist ----------
   A shelf above the board for the names you pinned, so returning to them is a
   glance rather than a hunt. It replaces the floating tray the pins used to get:
   same actions, but it holds still and shows the numbers.

   Logged in, the pins belong to the account (watchSync, /api/watchlist): private
   and the same on every device. With accounts off they are localStorage, per
   browser, and the section hands you a link that carries the list elsewhere. */
let WLARM = 0;   /* Clear is two-step: a stray click must not take the whole list */

function renderWatchlist(){
  const box=document.getElementById('wl');
  const codes=[...WATCH];
  document.getElementById('wcount').textContent = codes.length || '';

  if(!codes.length){
    WLARM=0;
    box.innerHTML = `<p class="wlhint"><b>No starred companies yet.</b> `
      + `Click the ☆ beside any company — in Overview, Themes, Companies or Screen filters, or on its scorecard — and it will show up here.</p>`;
    return;
  }

  const byCode={}; DATA.forEach(d=>{ byCode[d.code]=d; });
  const head=`<div class="wlhead">
      <h2>My watchlist <span class="tc">${codes.length} ${codes.length===1?'company':'companies'}</span></h2>
      <div class="wlacts">
        <button class="btn" id="wlcmp" title="Put them side by side (c)">Compare ${codes.length}</button>
        ${WMODE==='server'?'<span class="wl-saved" title="Saved to your account: private to you, the same on every device you log in from">&#10003; Saved to your account</span>':'<button class="btn" id="wllink" title="Copies a link that carries this watchlist — open it on another device to get the same pins">&#128279; Copy watchlist link</button>'}
        <button class="btn" id="wlclr">${WLARM?'Click again to clear':'Clear'}</button>
      </div>
    </div>`;

  const body = `<div class="wltab"><table>
      <thead><tr><th></th><th>Company</th><th>Theme</th><th>Score</th><th>M-cap ₹cr</th><th>P/E</th><th>ROCE %</th><th class="sig">Signals</th></tr></thead>
      <tbody>` + codes.map(c=>{
        const d=byCode[c];
        if(!d) return `<tr data-wlgone="${esc(c)}"><td><button class="wlx" data-unpin="${esc(c)}" title="Unpin" aria-label="Unpin ${esc(c)}">&times;</button></td>`
          + `<td colspan="7" class="wlgone">${esc(c)} — pinned earlier, no longer on this board</td></tr>`;
        return `<tr data-wl="${esc(d.code)}">
          <td><button class="wlx" data-unpin="${esc(d.code)}" title="Remove from watchlist" aria-label="Unpin ${esc(d.name)}">&times;</button></td>
          <td><a class="nm" href="${scrURL(d)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Open on screener.in">${esc(d.name)} <span class="ext">↗</span></a><span class="tc">${esc(d.code||'')}</span></td>
          <td><span class="thm">${esc(shortT(base(d)))}</span></td>
          <td><span class="sc">${fmt(d.final_score,1)}</span>${d.rubric==='v4'?'<sup class="rub" title="scored on the v4 moat rubric — not comparable with a v3 score">v4</sup>':''}</td>
          <td>${fmtI(d.market_cap_cr)}</td>
          <td>${fmt(d.pe,1)}</td>
          <td>${fmt(d.roce_pct)}</td>
          <td class="sig">${d.has_lens_data?sigBadges(d,true):pend}</td>
        </tr>`;
      }).join('')
      + `</tbody></table></div>`;
  box.innerHTML = head + body;

  document.getElementById('wlcmp').onclick=openCompare;
  if(document.getElementById('wllink')) document.getElementById('wllink').onclick=async ()=>{
    const url=location.origin+location.pathname+location.search+'#w='+codes.join(',');
    const btn=document.getElementById('wllink');
    try{
      await navigator.clipboard.writeText(url);
      flash(btn,'✓ Copied');
    }catch(e){
      openModal(`<h3>Your watchlist as a link</h3>
        <p class="sub">The browser would not write to the clipboard from here, so copy it by hand. Opening it anywhere restores these ${codes.length} pins.</p>
        <p class="quote" style="word-break:break-all;border-left-color:var(--s1);background:color-mix(in srgb,var(--s1) 9%,transparent);margin-top:14px">${esc(url)}</p>`);
    }
  };
  document.getElementById('wlclr').onclick=()=>{
    if(!WLARM){ WLARM=1; renderWatchlist(); setTimeout(()=>{ if(WLARM){ WLARM=0; renderWatchlist(); } },4000); return; }
    WLARM=0; WATCH.clear(); saveWatch();
    if(TG.watch){ TG.watch=false; syncTgButtons(); }
    render();
  };
  box.querySelectorAll('[data-unpin]').forEach(b=>{
    b.onclick=e=>{ e.stopPropagation(); togglePin(b.dataset.unpin); };
  });
  box.querySelectorAll('[data-wl]').forEach(tr=>{
    tr.style.cursor='pointer';
    /* the scorecard's prev/next walks CURRENT, so point it at the watchlist */
    tr.onclick=()=>{ const d=byCode[tr.dataset.wl]; if(d){ CURRENT=codes.map(c=>byCode[c]).filter(Boolean); openDrawer(d); } };
  });
}
/* ---------- side-by-side comparison ----------
   dir: +1 higher is better, -1 lower is better, 0 no winner is claimed.
   Market cap, FII+DII and CWIP deliberately have no winner - on this board
   bigger is not better for any of the three. */
const CMPROWS=[
  {l:'Composite score',    dir: 1, crossRubric:true, v:d=>nz(d.final_score), t:d=>fmt(d.final_score)+(d.rubric==='v4'?' <sup class="rub">v4</sup>':'')},
  {l:'Screen',             dir: 0, v:null,                            t:d=>`<span class="badge ${SCREENS[d.screen].cls}">${SCREENS[d.screen].lab}</span>`},
  {l:'Theme',              dir: 0, v:null,                            t:d=>esc(shortT(base(d)))},
  {l:'Market cap ₹cr',dir: 0, v:d=>nz(d.market_cap_cr),          t:d=>fmtI(d.market_cap_cr)},
  {l:'P/E',                dir:-1, v:d=>nz(d.pe),                     t:d=>fmt(d.pe)},
  {l:'ROCE %',             dir: 1, v:d=>nz(d.roce_pct),               t:d=>fmt(d.roce_pct)},
  {l:'ROE %',              dir: 1, v:d=>nz(d.roe_pct),                t:d=>fmt(d.roe_pct)},
  {l:'Promoter %',         dir: 1, v:d=>nz(d.promoter_pct),           t:d=>fmt(d.promoter_pct)},
  {l:'FII + DII %',        dir: 0, v:d=>(nz(d.fii_pct)||0)+(nz(d.dii_pct)||0), t:d=>fmt((nz(d.fii_pct)||0)+(nz(d.dii_pct)||0),2)},
  {l:'Retail holders',     dir:-1, v:d=>nz(d.num_shareholders),       t:d=>fmtI(d.num_shareholders)},
  {l:'CWIP % of net block',dir: 0, v:d=>nz(d.cwip_pct_net_block),     t:d=>nz(d.cwip_pct_net_block)===null?'—':fmt(d.cwip_pct_net_block,1)},
  {l:'Guidance %',         dir: 1, v:d=>nz(d.guidance_pct),           t:d=>nz(d.guidance_pct)===null?(d.guidance_flag?'n/q':'—'):fmt(d.guidance_pct,0)},
  {l:'Claim grade',        dir: 0, v:null,                            t:d=>d.claim_grade?`<span class="cg ${CG[d.claim_grade]||'cg-none'}">${esc(d.claim_grade)}</span>`:'<span class="nd">—</span>'},
  {l:'Signals',            dir: 0, v:null,                            t:d=>d.has_lens_data?sigBadges(d,false):'<span class="pend">pending</span>'},
];
function openCompare(){
  const byCode={}; DATA.forEach(d=>{ byCode[d.code]=d; });
  const list=[...WATCH].map(c=>byCode[c]).filter(Boolean);
  if(!list.length) return;
  const mixed = new Set(list.map(d=>d.rubric)).size>1;
  let h=`<h3>Side by side — ${list.length} pinned ${list.length===1?'company':'companies'}</h3>
    <p class="sub">The best value in a row is marked only where better has a direction. Market cap, institutional holding and CWIP are shown without a winner — on this board bigger is not better for any of them.</p>
    ${mixed?'<p class="caveat" style="margin-top:12px">Your pins mix the two rubrics. A <b>v4</b> score measures defensibility, a v3 score measures undiscovered quality — the two numbers are not comparable, and the score row is marked accordingly.</p>':''}
    <div style="overflow:auto;max-height:70vh"><table class="cmp"><thead><tr><th>Metric</th>`;
  h+=list.map(d=>`<th><a class="nm" style="display:inline;max-width:none" href="${scrURL(d)}" target="_blank" rel="noopener">${esc(d.name)} <span class="ext">↗</span></a></th>`).join('')+`</tr></thead><tbody>`;
  CMPROWS.forEach(r=>{
    let best=null;
    /* Never crown a winner on score when the pins straddle both rubrics - that is
       precisely the comparison the rest of the board refuses to make. */
    const dir = (mixed && r.crossRubric) ? 0 : r.dir;
    if(dir && r.v){
      const vals=list.map(r.v).filter(v=>v!==null&&v!==undefined&&!isNaN(v));
      if(vals.length>1) best = dir>0 ? Math.max(...vals) : Math.min(...vals);
    }
    h+=`<tr><td>${r.l}</td>`+list.map(d=>{
      const v=r.v?r.v(d):null;
      const win = best!==null && v!==null && v!==undefined && Math.abs(v-best)<1e-9;
      return `<td${win?' class="best"':''}>${r.t(d)}</td>`;
    }).join('')+`</tr>`;
  });
  h+=`</tbody></table></div>`;
  openModal(h);
}

/* ---------- modal ---------- */
const modal=document.getElementById('modal'), mbody=document.getElementById('mbody');
function openModal(html){ mbody.innerHTML=html; modal.classList.add('on'); modal.scrollTop=0; }
function closeModal(){ modal.classList.remove('on'); }
document.getElementById('mclose').onclick=closeModal;
modal.onclick=e=>{ if(e.target===modal) closeModal(); };

/* The version history is no longer a section on the page, so the button opens it. */
document.getElementById('whatsnew').onclick=()=>{
  openModal(`<h3>Version history — what changed, and when</h3>
    <p class="sub">This file is a snapshot, not a live page. Current build <b>${esc(BUILD.version)}</b>, built ${esc(BUILD.built)}.</p>
    <ul class="vhist" style="margin-top:20px">`
    + BUILD.history.map(h=>`<li><b>${esc(h.v)} — ${esc(h.d)}</b><div class="vd">${h.v===BUILD.version?'current build':''}</div>
        <ul>${h.items.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></li>`).join('')
    + `</ul>`);
};

/* ---------- reset: clears the search, tiles, filters, sliders and the NEW pill ---------- */
function clearFilters(){
  const wasBusy=BUSY; BUSY=true;
  SL.forEach((s,i)=>{ s.v = s.inv? s.max : s.min; const el=document.getElementById('si'+i); el.value=s.v; el.dispatchEvent(new Event('input')); });
  Object.keys(TG).forEach(k=>TG[k]=false);
  TILE=null;
  activeThemes=new Set(THEMES); syncChips();
  NEWSINCE=null; const nb=document.querySelector('[data-new]'); if(nb) nb.style.borderStyle='dashed';
  sortKey='final_score'; sortDir=-1;
  qIn.value=''; QUERY=''; QTERMS=[]; qWrap.classList.remove('has');
  CUR=-1;
  syncTgButtons();
  BUSY=wasBusy;
}
document.getElementById('reset').onclick=()=>{ clearFilters(); render(); };

/* ---------- shareable view state ----------
   Every choice a reader makes goes into the URL fragment, so a filtered view can
   be sent to someone else (or bookmarked) and reopened exactly as it was. */
function syncURL(){
  const p=new URLSearchParams();
  if(VIEW!=='overview') p.set('v',VIEW);
  if(VIEW==='reports' && RP.code){ p.set('r',RP.code); if(DR.period) p.set('rp',DR.period); if(DR.tab==='board') p.set('rt','board'); }
  if(TILE) p.set('tile',TILE);
  if(QUERY) p.set('q',QUERY);
  if(NEWSINCE) p.set('new',NEWSINCE);
  if(activeThemes.size<THEMES.length) p.set('t',[...activeThemes].map(t=>THEMES.indexOf(t)).join(','));
  const tg=Object.keys(TG).filter(k=>TG[k]); if(tg.length) p.set('tg',tg.join(','));
  const sl=SL.map((s,i)=>[s,i]).filter(([s])=> s.inv ? s.v<s.max : s.v>s.min).map(([s,i])=>i+':'+s.v);
  if(sl.length) p.set('sl',sl.join(','));
  if(sortKey!=='final_score'||sortDir!==-1) p.set('sort',sortKey+':'+sortDir);
  if(HIDDEN.size) p.set('hide',[...HIDDEN].join(','));
  if(WATCH.size && WMODE!=='server') p.set('w',[...WATCH].join(','));   /* an account's list stays private */
  const h=p.toString();
  try{ history.replaceState(null,'',location.pathname+location.search+(h?'#'+h:'')); }catch(e){}
}
function applyState(){
  const raw=location.hash.replace(/^#/,''); if(!raw) return;
  const p=new URLSearchParams(raw);
  const get=k=>p.get(k);
  if(VIEWS.includes(get('v'))) VIEW=get('v');
  if(get('r')) RP.code=get('r');
  if(get('rp') && /^FY\d\d-Q[1-4]$/.test(get('rp'))) DR.period=get('rp');
  if(get('rt')==='board') DR.tab='board';
  if(get('tile') && (get('tile') in TILE_LABEL)) TILE=get('tile');
  if(get('w')) get('w').split(',').filter(Boolean).forEach(c=>WATCH.add(c));
  if(get('t')){ const v=get('t').split(',').map(n=>THEMES[+n]).filter(Boolean); if(v.length) activeThemes=new Set(v); }
  if(get('tg')) get('tg').split(',').forEach(k=>{ if(k in TG) TG[k]=true; });
  if(get('sl')) get('sl').split(',').forEach(x=>{
    const bits=x.split(':'), s=SL[+bits[0]], v=+bits[1];
    if(s && !isNaN(v)){ s.v=Math.min(s.max,Math.max(s.min,v)); const el=document.getElementById('si'+bits[0]); if(el) el.value=s.v; }
  });
  if(get('sort')){ const bits=get('sort').split(':'); if(COLS.some(c=>c.k===bits[0])){ sortKey=bits[0]; sortDir=+bits[1]===1?1:-1; } }
  if(get('hide')) HIDDEN=new Set(get('hide').split(',').filter(Boolean));
  if(get('new')) NEWSINCE=get('new');
  if(get('q')){ qIn.value=get('q'); QUERY=get('q').trim().toLowerCase(); QTERMS=QUERY?QUERY.split(/\s+/).filter(Boolean):[]; qWrap.classList.toggle('has',!!QUERY); }
  syncChips(); syncTgButtons(); buildColPop();
  SL.forEach((s,i)=>{ const el=document.getElementById('si'+i); if(el) el.dispatchEvent(new Event('input')); });
}
function flash(btn,msg){
  const old=btn.innerHTML; btn.innerHTML=msg; btn.disabled=true;
  setTimeout(()=>{ btn.innerHTML=old; btn.disabled=false; },1500);
}
document.getElementById('copylink').onclick=async ()=>{
  syncURL();
  const url=location.href;
  try{
    await navigator.clipboard.writeText(url);
    flash(document.getElementById('copylink'),'✓ Link copied');
  }catch(e){
    openModal(`<h3>Link to this view</h3>
      <p class="sub">The browser would not write to the clipboard from here, so copy it by hand. Everything you have set — search, filters, sort, columns and pins — is in it.</p>
      <p class="quote" style="word-break:break-all;border-left-color:var(--s1);background:color-mix(in srgb,var(--s1) 9%,transparent);margin-top:14px">${esc(url)}</p>`);
  }
};

/* ---------- column control ---------- */
const COLPRESETS={
  'everything'      : COLS.map(c=>c.k),
  'compact'         : ['pin','rank','name','final_score','signals','market_cap_cr','pe','roce_pct'],
  'ownership'       : ['pin','rank','name','final_score','promoter_pct','fii_pct','dii_pct','num_shareholders'],
  'capex & guidance': ['pin','rank','name','final_score','pe','cwip_cr','cwip_pct_net_block','guidance_pct','signals'],
};
function buildColPop(){
  const pop=document.getElementById('colpop');
  pop.innerHTML='<div class="prow">'+Object.keys(COLPRESETS).map(k=>`<button data-preset="${k}">${k}</button>`).join('')+'</div>'
    + COLS.filter(c=>c.k!=='name').map(c=>{
        const lab = c.k==='rank' ? 'Rank' : c.k==='pin' ? '★ Watchlist' : String(c.l);
        return `<label><input type="checkbox" data-col="${c.k}"${HIDDEN.has(c.k)?'':' checked'}> ${lab}</label>`;
      }).join('');
  pop.querySelectorAll('[data-col]').forEach(cb=>{
    cb.onchange=()=>{ if(cb.checked) HIDDEN.delete(cb.dataset.col); else HIDDEN.add(cb.dataset.col); render(); };
  });
  pop.querySelectorAll('[data-preset]').forEach(b=>{
    b.onclick=()=>{ const keep=new Set(COLPRESETS[b.dataset.preset]);
      HIDDEN=new Set(COLS.map(c=>c.k).filter(k=>!keep.has(k))); buildColPop(); render(); };
  });
}
document.getElementById('colbtn').onclick=e=>{
  e.stopPropagation(); document.getElementById('colpop').classList.toggle('on');
};
document.addEventListener('click',e=>{
  if(!e.target.closest('.popwrap')) document.getElementById('colpop').classList.remove('on');
});

/* ---------- CSV of exactly what is on screen ---------- */
const CSVCOLS=[
  ['Rank','rank'],['Company','name'],['Ticker','code'],
  ['Screen', d=>SCREENS[d.screen].lab],['Theme', d=>base(d)],['Sector','sector'],
  ['Score','final_score'],['Rubric','rubric'],['Claim grade','claim_grade'],
  ['Market cap cr','market_cap_cr'],['P/E','pe'],
  ['CWIP cr','cwip_cr'],['CWIP % net block','cwip_pct_net_block'],['Guidance %','guidance_pct'],
  ['Promoter %','promoter_pct'],['FII %','fii_pct'],['DII %','dii_pct'],['Retail holders','num_shareholders'],
  ['ROCE %','roce_pct'],['ROE %','roe_pct'],
  ['High P/E + heavy CWIP', d=>d.capex_overhang?'yes':''],
  ['Guides above 15%', d=>d.guidance_over15?'yes':''],
  ['PAT turned positive', d=>d.pat_turnaround?'yes':''],
  ['Watchlisted', d=>WATCH.has(d.code)?'yes':''],
  ['Screener.in', d=>scrURL(d)],
];
function csvCell(v){
  if(v===null||v===undefined) return '';
  const t=String(v);
  return /[",\r\n]/.test(t) ? '"'+t.replace(/"/g,'""')+'"' : t;
}
/* Freshness. A file cannot refresh itself — it has no server behind it and, once locked,
   it is encrypted and offline. It also must not carry a token that would trigger a rebuild,
   because the shipped copy is the copy other people hold, and a secret inside it is a
   secret you have given away. So the honest thing is for the board to know how old it is,
   say so plainly when that starts to matter, and hand over the one command that fixes it. */
(function(){
  const btn=document.getElementById('fresh'), out=document.getElementById('freshtxt');
  if(!btn) return;
  const CMD='python scripts/refresh_data.py && python -m app.seed';
  const stamp=(typeof BUILD_STAMP!=='undefined')?BUILD_STAMP:null;
  let age=null;
  if(stamp&&stamp.scanned_on){
    const d=new Date(stamp.scanned_on+'T00:00:00Z');
    if(!isNaN(d)) age=Math.floor((Date.now()-d.getTime())/864e5);
  }
  /* weekly means a scan inside 7 days is current; 8-13 is a missed run; 14+ is unattended */
  const state = age===null ? 'never' : age<=7 ? 'ok' : age<=13 ? 'due' : 'stale';
  out.textContent = age===null ? 'never scanned' : age===0 ? 'scanned today' : `scanned ${age}d ago`;
  if(state==='due')   btn.classList.add('due');
  if(state==='stale'||state==='never') btn.classList.add('stale');

  btn.onclick=()=>{
    const said = state==='never'
      ? 'The weekly scan has not run against this build yet, so nothing here has been checked for new listings.'
      : state==='ok'
        ? `Last scan finished ${age===0?'today':age+' day'+(age===1?'':'s')+' ago'}. That is inside the weekly cycle, so nothing is overdue.`
        : `Last scan finished ${age} days ago. The weekly job either did not run or could not finish.`;
    const q=(typeof CANDIDATES!=='undefined')?CANDIDATES.filter(c=>!c.verdict).length:0;
    openModal(`<h3>Data freshness</h3>
      <p class="mp">${said}${stamp?` At that point the queue held <b>${stamp.queue_open}</b> name${stamp.queue_open===1?'':'s'} and <b>${stamp.profiles}</b> prospectus profile${stamp.profiles===1?'':'s'} had been written.${q!==stamp.queue_open?` It now shows <b>${q}</b>.`:''}`:''}</p>
      <p class="mp">Run the pass yourself from the project folder:</p>
      <p><code id="freshcmd">${CMD}</code>
         <button class="btn" id="freshcopy" style="margin-left:8px">Copy</button></p>
      <p class="mp" style="color:var(--muted)">It re-pulls each company's figures from screener.in / Trendlyne, re-scores, and re-seeds the
      database the API reads from. It does not add anything to the board on its own — promotion
      of a new candidate stays a decision you make.</p>`);
    const c=document.getElementById('freshcopy');
    if(c) c.onclick=()=>{ navigator.clipboard?.writeText(CMD).then(()=>flash(c,'✓ copied'),()=>flash(c,'copy failed')); };
  };
})();

/* The listings queue. Kept out of the table on purpose: these names have no score, no
   rubric and no verified claim, and the board's whole value is that everything on it
   has been looked at. So the queue is a basket you open, not rows you scroll past. */
(function(){
  const btn=document.getElementById('ipoq'), n=document.getElementById('ipoqn');
  if(!btn) return;
  const open=(CANDIDATES||[]).filter(c=>!c.verdict);
  n.textContent = open.length ? open.length : '';
  if(!open.length) btn.classList.add('quiet');
  btn.onclick=()=>{
    if(!open.length){
      openModal(`<h3>Candidates queue</h3>
        <p class="mp">Nothing waiting. The weekly discovery pipeline compares exchange symbol lists
        against the last snapshot for new listings, and sweeps the SME/small-cap/mid-cap
        universe for names not yet on this board — but a name only reaches this queue once
        there's sourced evidence of a real moat: import substitution, a stated India
        market-share or leading-manufacturer claim, being the only (or one of very few)
        Indian makers of something, or a genuinely niche, non-commodity segment. Sector
        plausibility alone never clears that bar. An empty queue means nothing cleared it
        this week — not that the pipeline failed.</p>`);
      return;
    }
    /* count(o) -> "moat 2, import_substitution 1" from a scan() bucket like
       {moat:{count:2,...}, import_substitution:{count:1,...}}; profile-company.mjs
       writes exactly this shape into c.profile.claims / .risk_factors. */
    const line=o=>o?Object.entries(o).map(([k,v])=>`${k.replace(/_/g,' ')} ${v.count}`).join(', '):'';
    const rows=open.map((c,i)=>{
      const p=c.profile;
      const boardTag=c.board&&c.board!=='NSE'?` · ${esc(c.board)}`:'';
      const claimsLine=p?line(p.claims):'';
      const riskLine=p?line(p.risk_factors):'';
      const profileBit=p
        ? `<button class="btn" data-cand="${i}" style="padding:2px 8px;font-size:.85em">profile ↗</button>`
        : '<span class="nd">not yet profiled</span>';
      return `<tr>
        <td><b>${esc(c.name)}</b><span class="tc" style="margin-left:8px">${esc(c.sym)}${boardTag}</span></td>
        <td>${esc(c.listed||c.seen_on||'—')}</td>
        <td>${c.hint?esc(c.hint):'<span class="nd">—</span>'}</td>
        <td>${p?`<span class="tc">${esc(claimsLine||'no claims found')}${riskLine?' · risk: '+esc(riskLine):''}</span>`:'<span class="nd">—</span>'}</td>
        <td>${profileBit} <a class="nm" href="https://www.screener.in/company/${encodeURIComponent(c.sym)}/" target="_blank" rel="noopener">screener ↗</a></td>
      </tr>`;
    }).join('');
    openModal(`<h3>Candidates queue — ${open.length} awaiting a verdict</h3>
      <p class="mp">New listings and already-listed SME/small-cap/mid-cap names with <b>sourced
      moat evidence</b> — an import-substitution story, a stated India market-share or
      leading-manufacturer claim, being the only (or one of very few) Indian makers of
      something, or a genuinely niche segment — found either in the company's own prospectus
      or in that week's Screener/Trendlyne research pass. The sector shown is still a
      <b>guess from the company's name</b>, not a finding. Where a prospectus has been read, the
      claims/risk columns are <b>company-stated</b> — extracted from the filing's own words, not
      verified against any outside source. Nothing here is scored or on the board.</p>
      <div style="overflow:auto;max-height:60vh"><table class="cmp">
        <thead><tr><th>Company</th><th>Listed</th><th>Sector guess</th><th>Prospectus (company-stated)</th><th>Links</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`);
    mbody.querySelectorAll('[data-cand]').forEach(b=>{
      b.onclick=()=>{
        const c=open[+b.dataset.cand], p=c.profile;
        const bucket=(title,o,note)=>{
          const entries=Object.entries(o||{});
          if(!entries.length) return '';
          const items=entries.map(([k,v])=>`<li><b>${esc(k.replace(/_/g,' '))}</b> (${v.count})<ul>${
            (v.evidence||[]).map(e=>`<li class="tc">"${esc(e.sentence)}"</li>`).join('')
          }</ul></li>`).join('');
          return `<h4 style="margin:14px 0 4px">${title}</h4><p class="mp" style="color:var(--muted)">${note}</p><ul>${items}</ul>`;
        };
        openModal(`<h3>${esc(c.name)} <span class="tc">${esc(c.sym)}</span></h3>
          <p class="mp">Source: <a class="nm" href="${esc(p.source.url)}" target="_blank" rel="noopener">${esc(p.source.document)}${p.source.filed?' ('+esc(p.source.filed)+')':''} ↗</a>
          · graded <b>${esc(p.grade)}</b> — the company's own words, not verified.</p>
          ${bucket('Claims', p.claims, 'What the company says about itself — moat, accreditation, import substitution, IP.')}
          ${bucket('Risk factors', p.risk_factors, 'Disclosed because it legally must be — usually worth more than the strengths section.')}
          ${bucket('Objects of the issue', p.objects_of_the_issue, 'What the raised capital is actually for.')}
          <p class="mp" style="margin-top:14px"><button class="btn" id="backtoqueue">← back to queue</button></p>`);
        const back=document.getElementById('backtoqueue');
        if(back) back.onclick=()=>btn.onclick();
      };
    });
  };
})();

/* ASME Certified — a standalone reference list, unrelated to everything
   else on this page. Names and certificate types come from ASME's own CA
   Connect directory (caconnect.asme.org/directory); each was then matched
   by name against NSE's and BSE's own listed-equity feeds, so only names
   an exchange confirms are actually listed survive here. None of this was
   run through this board's gates or scored on any rubric — the button
   just hands back the names, tickers, exchange(s) and how long each has
   held an ASME certificate. */
(function(){
  const btn=document.getElementById('asmeBtn'), n=document.getElementById('asmeCount');
  /* the pipeline's list (via /api/asme) when it loaded, else the bundled snapshot */
  const list=(ASME.companies&&ASME.companies.length)?ASME.companies:(window.ASME_CERTIFIED||[]);
  if(!btn) return;
  if(n) n.textContent = list.length;
  const exBadge = ex => `<span class="badge b-${ex==='NSE-SME'?'sme':ex.toLowerCase()}">${ex==='NSE-SME'?'NSE SME':ex}</span>`;
  const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const fmtDate = iso => { const [y,m,d]=iso.split('-').map(Number); return `${d} ${MONTHS[m-1]} ${y}`; };

  /* "U, U2, U3" all mean "Pressure Vessels" at different classes — grouping
     by what a code actually certifies (rather than just listing codes) is
     what makes this readable instead of alphabet soup. Falls back to the
     bare code if ASME ever issues one window.ASME_CERT_TYPES doesn't have
     yet, rather than hiding it. */
  const CERT_TYPES=window.ASME_CERT_TYPES||{};
  function certsLine(certs){
    const byDesc=new Map();
    certs.forEach(code=>{
      const desc=CERT_TYPES[code]||code;
      if(!byDesc.has(desc)) byDesc.set(desc, []);
      byDesc.get(desc).push(code);
    });
    return [...byDesc.entries()].map(([desc,codes])=>`${desc} (${codes.join(', ')})`).join(' &middot; ');
  }

  const SORTS = {
    name:  {label:'Name',           fn:(a,b)=>a.name.localeCompare(b.name)},
    newest:{label:'Newest certificate first',   fn:(a,b)=>String(b.latest||b.since).localeCompare(String(a.latest||a.since))},
    oldest:{label:'Oldest certification first', fn:(a,b)=>String(a.since).localeCompare(String(b.since))},
  };

  function cardHtml(c){
    const screenerSym = (c.symbol||'').replace(/-[A-Z]$/,''); // drop a trailing series letter (e.g. "-B") for the link only
    return `<div class="asme-card">
      <div class="asme-name">${esc(c.name)}${c.board_code?' <b class="asme-tag">On board</b>':''}</div>
      <div class="asme-meta">
        ${c.exch.map(exBadge).join('')}
        <span class="tc asme-sym">${esc(c.symbol)}</span>
        <a class="asme-link" href="https://www.screener.in/company/${encodeURIComponent(screenerSym)}/" target="_blank" rel="noopener">screener &#8599;</a>
      </div>
      <div class="asme-since" title="Earliest ASME certificate on file, ${esc(c.since)}">&#9878; ASME-certified since ${fmtDate(c.since)}${c.latest&&c.latest!==c.since?` · latest ${fmtDate(String(c.latest).slice(0,10))}`:''}</div>
      ${c.cert_details ? `<table class="asme-certs-table"><thead><tr><th>Certificate type</th><th>First received</th><th>Latest issued</th></tr></thead><tbody>${asmeCertRows(c)}</tbody></table>`
                       : `<div class="asme-certs" title="ASME certificate type(s) on file, by code">&#128220; ${certsLine(c.certs)}</div>`}
    </div>`;
  }

  function render(sort){
    const rows=[...list].sort(SORTS[sort].fn).map(cardHtml).join('');
    const grid=document.querySelector('#mbody .asme-grid');
    if(grid) grid.innerHTML=rows;
    document.querySelectorAll('#mbody .asme-sort .btn').forEach(b=>b.classList.toggle('on', b.dataset.sort===sort));
  }

  btn.onclick=()=>{
    const sortBtns=Object.entries(SORTS).map(([k,v])=>
      `<button class="btn" data-sort="${k}">${v.label}</button>`).join('');
    openModal(`<h3>&#9878; ASME Certified — ${list.length} companies</h3>
      <p class="mp">Active ASME certificate holders in India, per ASME's own
      <a class="nm" href="https://caconnect.asme.org/directory/" target="_blank" rel="noopener">CA Connect directory</a>,
      matched by name against NSE's and BSE's own listed-equity feeds — only
      names an exchange confirms are listed made this cut. Refreshed by the
      weekly scan${ASME.scanned_on?` (last run ${esc(ASME.scanned_on)})`:''}. Companies that are
      also on your board are marked <b class="asme-tag">On board</b>; the rest are not scored.</p>
      <div class="asme-sort">Sort by ${sortBtns}</div>
      <div class="asme-grid"></div>`);
    document.querySelectorAll('#mbody .asme-sort .btn').forEach(b=>{
      b.onclick=()=>render(b.dataset.sort);
    });
    render('newest');
  };
})();

document.getElementById('csv').onclick=()=>{
  const rows=CURRENT;
  if(!rows.length){ flash(document.getElementById('csv'),'nothing to export'); return; }
  const lines=[CSVCOLS.map(c=>csvCell(c[0])).join(',')];
  rows.forEach(d=>lines.push(CSVCOLS.map(c=>csvCell(typeof c[1]==='function'?c[1](d):d[c[1]])).join(',')));
  const blob=new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url; a.download='microcap-screener-'+rows.length+'-companies.csv';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),4000);
  flash(document.getElementById('csv'),'✓ '+rows.length+' rows');
};

/* ---------- keyboard driving ---------- */
function markCursor(){
  const tb=document.getElementById('tbody');
  [...tb.children].forEach(tr=>tr.classList.toggle('cursor', +tr.dataset.i===CUR));
}
function moveCur(delta){
  if(!CURRENT.length) return;
  CUR = CUR<0 ? (delta>0?0:CURRENT.length-1) : Math.min(CURRENT.length-1, Math.max(0, CUR+delta));
  markCursor();
  const tr=document.querySelector('tbody tr.cursor');
  if(tr) tr.scrollIntoView({block:'nearest'});
}
function stepDrawer(delta){
  const n=DRAWERI+delta;
  if(DRAWERI<0 || n<0 || n>=CURRENT.length) return;
  CUR=n; markCursor();
  const tr=document.querySelector('tbody tr.cursor');
  if(tr) tr.scrollIntoView({block:'nearest'});
  openDrawer(CURRENT[n]);
}
function showKeys(){
  const k=(...keys)=>keys.map(x=>`<span class="kbd">${x}</span>`).join('');
  openModal(`<h3>Driving the board from the keyboard</h3>
    <p class="sub">Nothing here needs the mouse.</p>
    <div class="klist">
      <div>${k('/')}<i>Jump into the search box</i></div>
      <div>${k('Esc')}<i>Clear the search, or close what is open</i></div>
      <div>${k('j','↓')}<i>Move down a row</i></div>
      <div>${k('k','↑')}<i>Move up a row</i></div>
      <div>${k('Enter')}<i>Open the scorecard for that row</i></div>
      <div>${k('←','→')}<i>Previous / next company, scorecard open</i></div>
      <div>${k('w')}<i>Pin or unpin the highlighted row</i></div>
      <div>${k('c')}<i>Compare everything pinned</i></div>
      <div>${k('?')}<i>This list</i></div>
    </div>
    <p class="sub" style="margin-top:20px">Elsewhere on the page: the stat tiles at the top are buttons — clicking one filters the board to the names behind that number. So are the bars in <b>Score distribution by theme</b>. Every point on both scatter plots opens its company.</p>`);
}
document.getElementById('keys').onclick=showKeys;
/* A tile shows only the companies behind its number, right under the tiles.
   The themes tile opens the Themes page; clicking the open tile again closes it. */
document.getElementById('tiles').onclick=e=>{
  const b=e.target.closest('[data-tile]'); if(!b) return;
  const t=b.dataset.tile;
  if(t==='themes'){ setView('themes'); return; }
  const same = TILE===t;
  clearFilters();
  if(!same){ TILE=t; if(t!=='all') TG[t]=true; }
  syncTgButtons(); render();
  if(!same) document.getElementById('results-head').scrollIntoView({behavior:'smooth',block:'start'});
};
addEventListener('keydown',e=>{
  if(e.key==='Escape' && modal.classList.contains('on')){ closeModal(); return; }
  const ae=document.activeElement||{};
  if(/^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName||'')) return;
  if(e.ctrlKey||e.metaKey||e.altKey) return;
  if(drawer.classList.contains('on') && (e.key==='ArrowRight'||e.key==='ArrowLeft')){
    e.preventDefault(); stepDrawer(e.key==='ArrowRight'?1:-1); return;
  }
  if(modal.classList.contains('on')) return;
  if(e.key==='j'||e.key==='ArrowDown'){ e.preventDefault(); moveCur(1); }
  else if(e.key==='k'||e.key==='ArrowUp'){ e.preventDefault(); moveCur(-1); }
  else if(e.key==='Enter' && CUR>=0 && CURRENT[CUR]){ e.preventDefault(); openDrawer(CURRENT[CUR]); }
  else if(e.key==='w' && CUR>=0 && CURRENT[CUR]){ e.preventDefault(); togglePin(CURRENT[CUR].code); }
  else if(e.key==='c' && WATCH.size){ e.preventDefault(); openCompare(); }
  else if(e.key==='?'){ e.preventDefault(); showKeys(); }
});

/* ---------- remember the theme the reader chose ---------- */
(function(){
  if(!tbtn) return;
  let stored=null; try{ stored=localStorage.getItem('dms.theme'); }catch(e){}
  if(stored==='dark'||stored==='light'){
    document.documentElement.dataset.theme=stored;
    tbtn.textContent = stored==='dark' ? '◑ Light' : '◐ Dark';
  }
  const flip=tbtn.onclick;
  tbtn.onclick=()=>{ flip(); try{ localStorage.setItem('dms.theme',document.documentElement.dataset.theme); }catch(e){} };
})();

/* ==================================================================
   Chart gallery
   A daily candlestick card for every company on the board. The card list
   is DATA itself, so a company that reaches the board is in the gallery on
   the next page load; its candles come from /api/charts, which the daily
   GitHub Action refreshes from the NSE/BSE bhavcopy (and which fetches a
   company live if it arrived since that run). Candles load lazily as the
   cards scroll into view; filters and sorting use the small stats index.
   ================================================================== */
const GAL_STATUS={high:['At 52-week high','st-high'], near:['Near 52-week high','st-near'],
                  up:['Uptrend','st-up'], flat:['Sideways','st-flat'], down:['Downtrend','st-down']};
const GAL_SORTS={
  high :{l:'Sort: closest to 52-week high', s:s=>s.from_high_pct, dir:-1},
  chg  :{l:"Sort: day's change",            s:s=>s.chg_pct,       dir:-1},
  vol  :{l:'Sort: volume vs average',       s:s=>s.vol_ratio,     dir:-1},
  mcap :{l:'Sort: market cap',              d:d=>nz(d.market_cap_cr), dir:-1},
  score:{l:'Sort: score',                   d:d=>nz(d.final_score),   dir:-1},
  name :{l:'Sort: name',                    d:d=>String(d.name||''),  dir:1},
};
const GMONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const GSERIES=new Map(), GPROM=new Map(), GQUEUE=[];
let GAL=null, GALLOADING=null, GACTIVE=0, GOBS=null;
document.getElementById('gallery-count').textContent=DATA.length;
GALLOADING = fetchJSON('/api/charts').catch(()=>({companies:{}})).then(j=>{
  GAL=j; buildGalControls();
  const up=document.getElementById('nav-upd');
  if(j.latest_session){ up.innerHTML=`<i></i><span>Updated · ${galDay(j.latest_session)}</span>`; up.title=`Prices updated to the ${galDay(j.latest_session)} session`; up.hidden=false; }
});

const galStats = d => (GAL && GAL.companies[d.code]) || (GSERIES.get(d.code)||{}).stats || null;
const galDay = iso => { const [y,m,dd]=String(iso).split('-').map(Number); return `${dd} ${GMONTHS[m-1]} ${y}`; };
const galPx = v => v==null ? '—' : '₹'+(+v).toLocaleString('en-IN',{maximumFractionDigits:2});
const galTicker = (d,s) => s ? s.symbol : (d.nse_code && !/^\d+$/.test(d.nse_code) ? d.nse_code : d.code);

async function openGallery(){
  if(!GAL){
    document.getElementById('ggrid').innerHTML='<p class="view-hint">Loading charts…</p>';
    GALLOADING = GALLOADING || fetchJSON('/api/charts').catch(()=>({companies:{}})).then(j=>{ GAL=j; buildGalControls(); });
    await GALLOADING;
  }
  if(VIEW==='gallery') renderGallery();
}

function buildGalControls(){
  const tr=document.getElementById('gtrend'), th=document.getElementById('gtheme'), so=document.getElementById('gsort');
  const byStatus={}, byTheme={};
  DATA.forEach(d=>{ const s=galStats(d), k=s?s.status:'none'; byStatus[k]=(byStatus[k]||0)+1; byTheme[base(d)]=(byTheme[base(d)]||0)+1; });
  tr.innerHTML='<option value="">Trend: all</option>'
    + Object.entries(GAL_STATUS).map(([k,[l]])=>`<option value="${k}">${l} (${byStatus[k]||0})</option>`).join('')
    + (byStatus.none?`<option value="none">No chart yet (${byStatus.none})</option>`:'');
  th.innerHTML='<option value="">Theme: all</option>'
    + Object.entries(byTheme).sort((a,b)=>b[1]-a[1]).map(([t,n])=>`<option value="${esc(t)}">${esc(shortT(t))} (${n})</option>`).join('');
  so.innerHTML=Object.entries(GAL_SORTS).map(([k,v])=>`<option value="${k}">${v.l}</option>`).join('');
  document.getElementById('gallery-asof').textContent = GAL.latest_session ? 'Last session: '+galDay(GAL.latest_session) : '';
  let t=0;
  document.getElementById('gq').oninput=()=>{ clearTimeout(t); t=setTimeout(renderGallery,120); };
  [tr,th,so].forEach(el=>el.onchange=renderGallery);
  document.getElementById('gclear').onclick=()=>{ document.getElementById('gq').value=''; tr.value=''; th.value=''; so.value='high'; renderGallery(); };

  const grid=document.getElementById('ggrid');
  grid.onclick=e=>{
    const pin=e.target.closest('[data-gpin]');
    if(pin){ e.stopPropagation(); togglePin(pin.dataset.gpin); galPinButtons(pin.dataset.gpin); return; }
    const card=e.target.closest('[data-gcode]'); if(card) openChart(card.dataset.gcode);
  };
  grid.onkeydown=e=>{ const card=e.target.closest('[data-gcode]'); if(card && (e.key==='Enter'||e.key===' ')){ e.preventDefault(); openChart(card.dataset.gcode); } };
}

function galRows(){
  const q=document.getElementById('gq').value.trim().toLowerCase();
  const tr=document.getElementById('gtrend').value, th=document.getElementById('gtheme').value;
  const so=GAL_SORTS[document.getElementById('gsort').value]||GAL_SORTS.high;
  const val=d=>{ if(so.d) return so.d(d); const s=galStats(d); return s ? so.s(s) : null; };
  return DATA.filter(d=>{
    if(th && base(d)!==th) return false;
    const s=galStats(d);
    if(tr==='none' ? !!s : (tr && (!s || s.status!==tr))) return false;
    if(q && !(d._n+' '+(s?String(s.symbol).toLowerCase():'')).includes(q)) return false;
    return true;
  }).sort((a,b)=>{
    const x=val(a), y=val(b);
    if(x==null && y==null) return String(a.name).localeCompare(String(b.name));
    if(x==null) return 1; if(y==null) return -1;
    return (typeof x==='string' ? x.localeCompare(y) : x-y)*so.dir;
  });
}

function renderGallery(){
  if(!GAL) return;
  const rows=galRows(), grid=document.getElementById('ggrid');
  document.getElementById('gcount').textContent=`${rows.length} of ${DATA.length}`;
  grid.innerHTML = rows.length ? rows.map(galCard).join('')
    : '<p class="view-hint">No chart matches — clear the search or pick another filter.</p>';
  if(GOBS) GOBS.disconnect();
  GOBS=new IntersectionObserver(es=>es.forEach(e=>{
    if(e.isIntersecting){ GOBS.unobserve(e.target); galFetch(e.target.dataset.gchart); }
  }),{rootMargin:'500px 0px'});
  grid.querySelectorAll('[data-gchart]').forEach(el=>{ if(!GSERIES.has(el.dataset.gchart)) GOBS.observe(el); });
}

function galCard(d){
  const s=galStats(d), st=s && GAL_STATUS[s.status], on=WATCH.has(d.code);
  return `<article class="gcard" data-gcode="${esc(d.code)}" tabindex="0" aria-label="Open the chart for ${esc(d.name)}">
    <div class="gc-head"><span class="gc-tick">${esc(galTicker(d,s))}</span><span class="gc-name" title="${esc(d.name)}">${esc(d.name)}</span>${st?`<span class="gc-badge ${st[1]}">${st[0]}</span>`:''}</div>
    <div class="gc-chart" data-gchart="${esc(d.code)}">${galThumb(d.code)}</div>
    <div class="gc-stats">${galStatLine(d.code, s)}</div>
    <div class="gc-foot"><span class="gc-theme">${esc(shortT(base(d)))}</span><button type="button" class="gc-pin${on?' on':''}" data-gpin="${esc(d.code)}" title="${on?'Remove from':'Add to'} watchlist" aria-label="Star ${esc(d.name)}">${on?'★':'☆'}</button></div>
  </article>`;
}
function galThumb(code){
  const ser=GSERIES.get(code);
  if(!ser) return '<span class="gc-skel"></span>';
  if(ser.missing || !ser.rows || !ser.rows.length) return '<span class="gc-empty">No price data yet — the chart appears after the next daily update</span>';
  return candleSVG(ser.rows,{w:320,h:160,sessions:125});
}
function galStatLine(code, s){
  if(!s) return GSERIES.get(code) ? '<span class="nd">Waiting for exchange data</span>' : '<span class="nd">Loading…</span>';
  const chg = s.chg_pct==null ? '' : `<span class="${s.chg_pct>=0?'up':'dn'}">${s.chg_pct>=0?'+':''}${s.chg_pct.toFixed(2)}%</span>`;
  const hi = s.status==='high' ? '<span class="gc-chip">52w high</span>' : `<span>${Math.abs(s.from_high_pct).toFixed(1)}% off high</span>`;
  return `${chg}<span>${galPx(s.last)}</span>${s.vol_ratio==null?'':`<span>vol ${s.vol_ratio}×</span>`}${hi}`;
}
function galPinButtons(code){
  const on=WATCH.has(code);
  document.querySelectorAll(`[data-gpin="${CSS.escape(code)}"]`).forEach(b=>{
    b.classList.toggle('on',on); b.textContent=on?'★':'☆'; b.title=(on?'Remove from':'Add to')+' watchlist';
  });
  const mp=document.getElementById('cm-pin');
  if(mp && mp.dataset.code===code) mp.innerHTML = on ? '★ In watchlist' : '☆ Add to watchlist';
}

function galFetch(code){
  if(!GPROM.has(code)) GPROM.set(code, new Promise(resolve=>{ GQUEUE.push([code,resolve]); galPump(); }));
  return GPROM.get(code);
}
function galPump(){
  while(GACTIVE<6 && GQUEUE.length){
    const [code,resolve]=GQUEUE.shift();
    GACTIVE++;
    fetch('/api/charts/'+encodeURIComponent(code)).then(r=>r.ok?r.json():null).catch(()=>null).then(j=>{
      GACTIVE--;
      GSERIES.set(code, j || {missing:true});
      galPaint(code); resolve(); galPump();
    });
  }
}
function galPaint(code){
  const card=document.querySelector(`#ggrid [data-gcode="${CSS.escape(code)}"]`);
  if(!card) return;
  const d=DATA.find(x=>x.code===code);
  if(GAL && !GAL.companies[code]){ card.outerHTML=galCard(d); return; }  /* arrived live: badge + stats too */
  card.querySelector('.gc-chart').innerHTML=galThumb(code);
}

/* Candles, 50/200-day averages, volume and the 52-week high, as one SVG.
   The averages are computed over the whole year and then windowed, so the
   200-day line is real from its first visible point rather than warming up. */
/* o: {w,h,sessions, axis, ma (default true), hi52 (default true), volColor,
       pivot: price for a dashed pivot line, box: {start,end,low,high,label} base box} */
function candleSVG(rows,o){
  const W=o.w, H=o.h, axis=!!o.axis, padR=axis?62:2, padT=axis?14:5, padB=axis?24:3;
  const useMA=o.ma!==false, useHi=o.hi52!==false;
  const closes=rows.map(r=>r[4]);
  const ma=n=>{ let sum=0; return closes.map((c,i)=>{ sum+=c; if(i>=n) sum-=closes[i-n]; return i>=n-1 ? sum/n : null; }); };
  const start=Math.max(0, rows.length-o.sessions);
  const vis=rows.slice(start), m50=ma(50).slice(start), m200=ma(200).slice(start);
  const hi52=Math.max(...rows.map(r=>r[2]));
  let lo=Math.min(...vis.map(r=>r[3])), hi=Math.max(...vis.map(r=>r[2]));
  if(useMA) m50.concat(m200).forEach(v=>{ if(v!=null){ lo=Math.min(lo,v); hi=Math.max(hi,v); } });
  if(o.pivot) hi=Math.max(hi,o.pivot);
  const showHi = useHi && hi52<=hi*1.12;
  if(showHi) hi=Math.max(hi,hi52);
  const pad=(hi-lo)*0.05 || hi*0.02 || 1; hi+=pad; lo=Math.max(0,lo-pad);
  const inner=H-padT-padB, volH=inner*0.2, priceH=inner-volH-6;
  const plotW=W-padR, step=plotW/vis.length, cw=Math.max(0.8, Math.min(8, step*0.66));
  const y=p=>padT+(hi-p)/(hi-lo)*priceH;
  const X=i=>i*step+step/2, f=n=>n.toFixed(2);
  const vmax=Math.max(1,...vis.map(r=>r[5]));
  let s=`<svg viewBox="0 0 ${W} ${H}" class="cs${axis?' cs-full':''}" role="img" aria-label="Daily candlestick chart">`;
  if(axis){
    for(let i=0;i<=4;i++){
      const p=lo+(hi-lo)*i/4, yy=y(p);
      s+=`<line x1="0" x2="${plotW}" y1="${f(yy)}" y2="${f(yy)}" class="cs-grid"/><text x="${plotW+8}" y="${f(yy+4)}" class="cs-tx">${(+p).toLocaleString('en-IN',{maximumFractionDigits:p<100?2:0})}</text>`;
    }
  }
  vis.forEach((r,i)=>{ const vh=r[5]/vmax*volH; s+=`<rect x="${f(X(i)-cw/2)}" y="${f(H-padB-vh)}" width="${f(cw)}" height="${f(Math.max(vh,0.3))}" class="cs-vol${o.volColor?(r[4]>=r[1]?' up':' dn'):''}"/>`; });
  if(o.box){
    const i0=vis.findIndex(r=>r[0]>=o.box.start), i1=(()=>{ let k=-1; vis.forEach((r,i)=>{ if(r[0]<=o.box.end) k=i; }); return k; })();
    if(i0>=0 && i1>=i0){
      const x0=X(i0)-step/2, x1=X(i1)+step/2, yt=y(o.box.high), yb=y(o.box.low);
      s+=`<rect x="${f(x0)}" y="${f(yt)}" width="${f(x1-x0)}" height="${f(Math.max(1,yb-yt))}" class="cs-box"/>`;
      if(o.box.label){
        /* keep the label inside the plot: right-align it when the box hugs the right edge */
        const half=o.box.label.length*3.2, mid=(x0+x1)/2;
        const [lx,anchor] = mid+half>plotW-2 ? [Math.min(x1,plotW)-2,'end'] : mid-half<2 ? [Math.max(x0,0)+2,'start'] : [mid,'middle'];
        s+=`<text x="${f(lx)}" y="${f(yt-4)}" text-anchor="${anchor}" class="cs-boxlab">${esc(o.box.label)}</text>`;
      }
    }
  }
  if(showHi) s+=`<line x1="0" x2="${plotW}" y1="${f(y(hi52))}" y2="${f(y(hi52))}" class="cs-hi"/>`;
  let upW='',dnW='',upB='',dnB='';
  vis.forEach((r,i)=>{
    const x=X(i), up=r[4]>=r[1], top=y(Math.max(r[1],r[4])), bh=Math.max(0.9,Math.abs(y(r[1])-y(r[4])));
    const wick=`M${f(x)} ${f(y(r[2]))}V${f(y(r[3]))}`, body=`M${f(x-cw/2)} ${f(top)}h${f(cw)}v${f(bh)}h${f(-cw)}Z`;
    if(up){ upW+=wick; upB+=body; } else { dnW+=wick; dnB+=body; }
  });
  s+=`<path d="${upW}" class="cs-wick up"/><path d="${dnW}" class="cs-wick dn"/><path d="${upB}" class="cs-body up"/><path d="${dnB}" class="cs-body dn"/>`;
  const line=(arr,cls)=>{ let d=''; arr.forEach((v,i)=>{ if(v!=null) d+=(d?'L':'M')+f(X(i))+' '+f(y(v)); }); return d?`<path d="${d}" class="${cls}"/>`:''; };
  if(useMA) s+=line(m50,'cs-ma50')+line(m200,'cs-ma200');
  if(o.pivot){
    const yp=y(o.pivot), lab=`pivot ₹${(+o.pivot).toLocaleString('en-IN',{maximumFractionDigits:2})}`;
    s+=`<line x1="0" x2="${plotW}" y1="${f(yp)}" y2="${f(yp)}" class="cs-pivot"/><g class="cs-pivlab"><rect x="2" y="${f(yp-15)}" width="${lab.length*6.1+10}" height="13" rx="2"/><text x="7" y="${f(yp-5)}">${lab}</text></g>`;
  }
  if(axis && o.isoAxis){
    const every=Math.max(1,Math.round(vis.length/5));
    vis.forEach((r,i)=>{ if(i%every===Math.floor(every/2)) s+=`<text x="${f(X(i))}" y="${H-6}" class="cs-tx" text-anchor="middle">${r[0]}</text>`; });
    s+=`<line id="cs-cross" x1="0" x2="0" y1="${padT}" y2="${H-padB}" class="cs-cross" style="display:none"/>`;
  } else if(axis){
    let last='';
    vis.forEach((r,i)=>{ const m=r[0].slice(0,7); if(m!==last){ if(last && i>2) s+=`<text x="${f(X(i))}" y="${H-6}" class="cs-tx" text-anchor="middle">${GMONTHS[+r[0].slice(5,7)-1]}</text>`; last=m; } });
    s+=`<line id="cs-cross" x1="0" x2="0" y1="${padT}" y2="${H-padB}" class="cs-cross" style="display:none"/>`;
  }
  return s+'</svg>';
}

function openChart(code){
  const d=DATA.find(x=>x.code===code); if(!d) return;
  if(!GSERIES.has(code)){
    openModal('<p class="view-hint">Loading chart…</p>');
    galFetch(code).then(()=>{ if(modal.classList.contains('on')) openChart(code); });
    return;
  }
  const ser=GSERIES.get(code), s=(ser && ser.stats) || galStats(d), st=s && GAL_STATUS[s.status];
  const on=WATCH.has(code);
  const W=1000, H=430, padR=62;
  const has=ser && !ser.missing && ser.rows && ser.rows.length;
  const vis=has ? ser.rows.slice(-250) : [];
  const read=r=>`<b>${galDay(r[0])}</b> · O ${galPx(r[1])} · H ${galPx(r[2])} · L ${galPx(r[3])} · C ${galPx(r[4])} · Vol ${fmtI(r[5])}`;
  const kv=(k,v)=>`<div><span>${k}</span><b>${v}</b></div>`;
  openModal(`
    <div class="cm-head"><span class="gc-tick">${esc(galTicker(d,s))}</span><h3>${esc(d.name)}</h3>${st?`<span class="gc-badge ${st[1]}">${st[0]}</span>`:''}
      <span class="tc">${s?esc(s.exchange)+' · ':''}${esc(shortT(base(d)))}</span></div>
    ${has ? `<div class="cm-read" id="cm-read">${read(vis[vis.length-1])}</div>
      <div class="cm-chart" id="cm-chart">${candleSVG(ser.rows,{w:W,h:H,sessions:250,axis:true})}</div>
      <div class="cm-legend"><span><i class="lg lg-50"></i>50-day average</span><span><i class="lg lg-200"></i>200-day average</span><span><i class="lg lg-hi"></i>52-week high</span><span><i class="lg lg-vol"></i>Volume</span></div>`
    : '<p class="view-hint">No price data for this company yet. The chart appears automatically after the next daily update.</p>'}
    ${s ? `<div class="kv cm-kv">
      ${kv('Last close', galPx(s.last))}
      ${kv("Day's change", s.chg_pct==null?'—':`<span class="${s.chg_pct>=0?'up':'dn'}">${s.chg_pct>=0?'+':''}${s.chg_pct.toFixed(2)}%</span>`)}
      ${kv('52-week high', galPx(s.high52))}
      ${kv('52-week low', galPx(s.low52))}
      ${kv('From 52-week high', s.from_high_pct==null?'—':s.from_high_pct.toFixed(1)+'%')}
      ${kv('50-day average', galPx(s.dma50))}
      ${kv('200-day average', galPx(s.dma200))}
      ${kv('Volume vs 50-day avg', s.vol_ratio==null?'—':s.vol_ratio+'×')}
      ${kv('As of', galDay(s.asof))}
    </div>` : ''}
    <div class="cm-acts">
      <button class="btn" id="cm-card" type="button">Open scorecard</button>
      <button class="btn" id="cm-report" type="button">Research report</button>
      <button class="btn" id="cm-pin" data-code="${esc(code)}" type="button">${on?'★ In watchlist':'☆ Add to watchlist'}</button>
      <a class="btn" href="${scrURL(d)}" target="_blank" rel="noopener">screener.in ↗</a>
    </div>
    <p class="cm-src">${ser && ser.source==='live' ? 'Fetched live because this company joined the board after the last daily update; tonight\'s run replaces it with exchange data.' : 'Source: NSE / BSE end-of-day bhavcopy, refreshed automatically every trading day.'} Prices are not adjusted for splits or bonus issues.</p>`);
  document.getElementById('cm-card').onclick=()=>{ closeModal(); CURRENT=GAL?galRows():[d]; openDrawer(d); };
  document.getElementById('cm-report').onclick=()=>openReport(code);
  document.getElementById('cm-pin').onclick=()=>{ togglePin(code); galPinButtons(code); };
  const box=document.getElementById('cm-chart');
  if(box){
    const svg=box.querySelector('svg'), cross=svg.querySelector('#cs-cross'), out=document.getElementById('cm-read');
    const step=(W-padR)/vis.length;
    svg.onmousemove=e=>{
      const rect=svg.getBoundingClientRect(), x=(e.clientX-rect.left)/rect.width*W;
      if(x>W-padR){ cross.style.display='none'; return; }
      const i=Math.max(0,Math.min(vis.length-1,Math.floor(x/step)));
      const cx=(i*step+step/2).toFixed(2);
      cross.setAttribute('x1',cx); cross.setAttribute('x2',cx); cross.style.display='';
      out.innerHTML=read(vis[i]);
    };
    svg.onmouseleave=()=>{ cross.style.display='none'; out.innerHTML=read(vis[vis.length-1]); };
  }
}

/* ==================================================================
   Market view — stage screen
   Built from /api/market/setups, which the daily chart job writes (see
   backend/app/setups.py): every board company's stage (Forming, Fresh
   breakouts, Climbing, Played out), its measures, and what changed since
   the last close. NIFTY 50 / SENSEX sit in the bar, live through Zerodha
   when connected and delayed otherwise.
   ================================================================== */
const BP_STAGES=[
  {k:'forming',  label:'Forming',         hint:'resting before a breakout',             title:'Forming — on watch',
   desc:'Strong stocks pausing in a tight, quiet range just under a ceiling, their trading drying up as they coil. A close above the pivot on heavy volume sets one off.'},
  {k:'fresh',    label:'Fresh breakouts', hint:'cleared the pivot in the last 5 sessions', title:'Fresh breakouts',
   desc:'Closed above the pivot on at least 1.4× their usual volume within the last five sessions, and still holding above the stop.'},
  {k:'climbing', label:'Climbing',        hint:'broke out earlier, still rising',       title:'Climbing',
   desc:'Broke out more than five sessions ago and still in the trade: never 8% under the pivot, never a close under the 50-day average.'},
  {k:'played',   label:'Played out',      hint:'this year\'s breakouts, stopped or trailed out', title:'Played out',
   desc:'Broke out this year, then either fell 8% under the pivot (stopped) or closed under the 50-day average (trailed out).'},
];
/* the IPO base screen reuses the stages; only the words change */
const BP_IPO_TEXT={
  forming: {hint:'IPO base, resting under the post-listing high', title:'IPO bases — on watch',
            desc:'Companies listed in the last two years, holding a base of three weeks or more under their post-listing high. The ones within 5% of it are flagged "on the verge"; a close above it on heavy volume is the breakout.'},
  fresh:   {hint:'cleared the post-listing high in the last 5 sessions', title:'Fresh IPO base breakouts',
            desc:'Recent listings that closed above their post-listing high on at least 1.4× usual volume within the last five sessions.'},
  climbing:{hint:'broke out of the IPO base, still rising', title:'Climbing out of the IPO base',
            desc:'Broke out of the IPO base more than five sessions ago and still in the trade.'},
  played:  {hint:'IPO base breakouts this year, stopped or trailed out', title:'IPO base breakouts — played out',
            desc:'Broke out of the IPO base this year, then fell 8% under the pivot or closed under the moving average.'},
};
const BP_SORTS={
  rs:   {l:'RS rating',              k:a=>a.rs,            dir:-1},
  pivot:{l:'Now vs pivot (%)',       k:a=>a.now_vs_pivot,  dir:-1},
  atr:  {l:'Tightening (ATR ratio)', k:a=>a.atr_ratio,     dir:1},
  dry:  {l:'Volume dry-up',          k:a=>a.vol_dryup,     dir:1},
  ud:   {l:'Up/down volume, net',    k:a=>a.updown,        dir:-1},
  age:  {l:'Base age',               k:a=>bpBase(a) && bpBase(a).weeks, dir:-1},
  high: {l:'From 52-week high',      k:a=>a.from_high,     dir:1},
  mcap: {l:'Market cap',             d:d=>nz(d.market_cap_cr), dir:-1},
  listed:{l:'Listing date (newest)', k:a=>a.listed?Date.parse(a.listed):null, dir:-1, ipo:true},
  bodate:{l:'Breakout date (newest)', k:a=>a.breakouts&&a.breakouts[0]?Date.parse(a.breakouts[0].date):null, dir:-1, ipo:true},
  bogain:{l:'Gain since breakout (%)', k:a=>a.breakouts&&a.breakouts[0]?a.breakouts[0].gain_pct:null, dir:-1, ipo:true},
};
const BP_INFO={
  rs:'Relative strength, 1–99: weighted 3/6/9/12-month return ranked against every liquid NSE stock. 90 means it beat 90% of them.',
  pivot:'Last close against the pivot — the ceiling of the base. Negative means still under it.',
  atr:'10-day average true range divided by the 50-day. Under 1× means the daily swings are tightening.',
  dry:'10-day average volume divided by the 50-day. Under 1× means trading is drying up.',
  ud:'Volume on up days minus volume on down days, over total volume, last 50 sessions. Above 0 means buyers are heavier.',
  high:'How far the last close is under the 52-week high.',
  squat:'Traded above the pivot but closed back under it in the last 10 sessions.',
  poke:'Closed above the pivot without breakout volume, then fell back under it.',
};
/* IPO base screen: companies whose latest IPO base breakout falls in a window */
const BP_BWINS=[['1w','1 week',7],['1m','1 month',30],['3m','3 months',91],['6m','6 months',182],['1y','1 year',365]];
const BP={data:null, loading:null, screen:'vcp', stage:'forming', bwin:null, view:'cards', all:false, idx:null, kite:null, timers:{}, obs:null};
/* one company as the current screen sees it: shared measures + that screen's stage fields */
const bpView = a => BP.screen==='ipo' ? (a.ipo ? {...a, ...a.ipo} : null) : a;
const bpStageText = k => { const x=BP_STAGES.find(y=>y.k===k); return BP.screen==='ipo' ? {...x, ...BP_IPO_TEXT[k]} : x; };
const bpBase = a => a.base || (a.breakout && {...a.breakout.base, pivot:a.breakout.pivot});
const bpInr = (v,d=2) => v==null ? '—' : '₹'+(+v).toLocaleString('en-IN',{maximumFractionDigits:d});
const bpPct = (v,d=2) => v==null ? '—' : `${v>=0?'+':''}${(+v).toFixed(d)}%`;
const bpVol = v => v==null ? '—' : v>=1e7 ? (v/1e7).toFixed(1)+'Cr' : v>=1e5 ? (v/1e5).toFixed(1)+'L' : v>=1e3 ? (v/1e3).toFixed(1)+'K' : String(v);
const bpSym = d => d.nse_code && !/^\d+$/.test(d.nse_code) ? d.nse_code : d.code;
const bpByCode = {}; DATA.forEach(d=>{ bpByCode[d.code]=d; });

function openMarket(){
  bpStatus(); bpIndices();
  if(!BP.data){
    document.getElementById('bp-results').innerHTML='<p class="bp-empty">Loading the scan…</p>';
    BP.loading = BP.loading || fetchJSON('/api/market/setups').catch(()=>({stocks:{},feed:[],counts:{},market_breakouts:[]})).then(j=>{ BP.data=j; bpControls(); });
    BP.loading.then(()=>{ if(VIEW==='market') bpRender(); });
  } else bpRender();
}
function bpSchedule(name, fn, ms){
  clearTimeout(BP.timers[name]);
  BP.timers[name]=setTimeout(()=>{ if(VIEW!=='market') return; if(document.hidden){ bpSchedule(name,fn,ms); return; } fn(); }, ms);
}
document.addEventListener('visibilitychange',()=>{ if(!document.hidden && VIEW==='market'){ bpStatus(); bpIndices(); } });

function bpControls(){
  const th=document.getElementById('bp-theme'), so=document.getElementById('bp-sort'), sc=document.getElementById('bp-screen');
  th.innerHTML='<option value="">All themes</option>'+THEMES.map(t=>`<option value="${esc(t)}">${esc(shortT(t))}</option>`).join('');
  const fillSorts=()=>{
    so.innerHTML=Object.entries(BP_SORTS).filter(([,v])=>!v.ipo||BP.screen==='ipo').map(([k,v])=>`<option value="${k}">${v.l}</option>`).join('');
    /* IPO base: closest to its breakout first */
    so.value = BP.screen==='ipo' ? 'pivot' : 'rs';
  };
  fillSorts();
  sc.onchange=()=>{ BP.screen=sc.value; BP.bwin=null; BP.all=false; fillSorts(); bpMarketChip(); bpRender(); };
  let t=0;
  th.onchange=()=>{ BP.all=false; bpRender(); };
  so.onchange=()=>bpRender();
  document.getElementById('bp-find').oninput=()=>{ clearTimeout(t); t=setTimeout(()=>{ BP.all=false; bpRender(); },120); };
  document.getElementById('bp-cards-btn').onclick=()=>{ BP.view='cards'; bpRender(); };
  document.getElementById('bp-list-btn').onclick=()=>{ BP.view='list'; bpRender(); };
  document.getElementById('bp-csv').onclick=bpCsv;
  document.getElementById('bp-how').onclick=bpHow;
  const j=BP.data;
  document.getElementById('bp-upd').innerHTML = j.as_of ? `<i></i>Updated · ${galDay(j.as_of)}` : '';
  bpMarketChip();
}
function bpMarketChip(){
  const j=BP.data, mw=document.getElementById('bp-mw');
  const n=((BP.screen==='ipo'?j.market_breakouts_ipo:j.market_breakouts)||[]).length;
  mw.hidden=!j.as_of;
  mw.innerHTML=`↗ ${n} ${BP.screen==='ipo'?'IPO base ':''}broke out market-wide ›`;
  mw.onclick=bpMarketWide;
}

/* the board companies that pass the theme + find filters */
function bpPool(){
  const th=document.getElementById('bp-theme').value, q=document.getElementById('bp-find').value.trim().toLowerCase();
  return Object.entries(BP.data.stocks||{}).map(([code,a])=>[code,bpView(a)]).filter(([code,v])=>{
    const d=bpByCode[code]; if(!d || !v) return false;
    if(th && base(d)!==th) return false;
    if(q && !d._n.includes(q)) return false;
    return true;
  });
}
function bpRows(){
  const so=BP_SORTS[document.getElementById('bp-sort').value]||BP_SORTS.rs;
  const val=([code,a])=>so.d ? so.d(bpByCode[code]) : so.k(a);
  const inList = BP.bwin ? (([,a])=>bpInWindow(a,BP.bwin)) : (([,a])=>a.stage===BP.stage);
  return bpPool().filter(inList).sort((x,y)=>{
    const a=val(x), b=val(y);
    if(a==null && b==null) return 0; if(a==null) return 1; if(b==null) return -1;
    return (a-b)*so.dir;
  });
}

function bpRender(){
  if(!BP.data) return;
  const pool=bpPool(), counts={};
  pool.forEach(([,a])=>{ counts[a.stage]=(counts[a.stage]||0)+1; });
  document.getElementById('bp-stages').innerHTML=BP_STAGES.map(x=>bpStageText(x.k)).map(s=>`
    <button type="button" class="bp-stage${s.k===BP.stage&&!BP.bwin?' on':''}" data-bp-stage="${s.k}">
      <span class="bp-tap">tap to view ›</span><span class="stage-ic">${icon({forming:'coil',fresh:'rocket',climbing:'mountain',played:'done'}[s.k])}</span><b>${counts[s.k]||0}</b><span>${s.label}</span><small>${s.hint}</small>
    </button>`).join('');
  document.querySelector('.bp-hero h2').textContent = BP.screen==='ipo' ? 'Catch recent listings breaking out of their IPO base.' : "Spot the board's next leaders.";
  document.querySelector('.bp-hero .bp-sub').textContent = BP.screen==='ipo'
    ? `${pool.length} board companies listed in the last two years, scanned after each trading day. Here are the ones basing under their post-listing high. Pick a stage.`
    : 'Every company on the board is scanned after each trading day. Here are the few actually setting up. Pick a stage.';
  document.querySelectorAll('[data-bp-stage]').forEach(b=>b.onclick=()=>{
    BP.stage=b.dataset.bpStage; BP.all=false;
    if(BP.bwin){ BP.bwin=null; document.getElementById('bp-sort').value='pivot'; }
    bpRender();
  });
  bpBwinBar(pool);

  const codes=new Set(pool.map(([c])=>c));
  const feed=(BP.data.feed||[]).filter(f=>codes.has(f.code) && (f.screen||'vcp')===BP.screen);
  document.getElementById('bp-feed').innerHTML=`<h3><i>⟲</i>What changed since last close?</h3>
    ${feed.length ? feed.slice(0,3).map(bpFeedItem).join('') : '<p class="bp-empty" style="padding:14px">Nothing changed stage on the board in the last session.</p>'}
    ${feed.length>3?`<button type="button" class="bp-link" id="bp-full-feed">Show the full feed (${feed.length}) ›</button>`:''}`;
  document.querySelectorAll('#bp-feed [data-bp-code]').forEach(b=>b.onclick=()=>openChart(b.dataset.bpCode));
  const ff=document.getElementById('bp-full-feed'); if(ff) ff.onclick=()=>bpFullFeed(feed);

  const win=BP.bwin && BP_BWINS.find(w=>w[0]===BP.bwin);
  const stage=win ? {label:`breakouts in the last ${win[1]}`, title:`IPO base breakouts — last ${win[1]}`,
    desc:`Recent listings whose latest IPO base breakout — a close above the post-listing high on at least 1.4× usual volume — came in the last ${win[1]} (to ${galDay(BP.data.as_of)}). Each card says how far it has run since and whether it is still in the trade.`} : bpStageText(BP.stage);
  const rows=bpRows();
  document.getElementById('bp-list-title').textContent=stage.title;
  document.getElementById('bp-list-n').textContent=rows.length;
  document.getElementById('bp-desc').textContent=stage.desc;
  document.getElementById('bp-cards-btn').classList.toggle('on',BP.view==='cards');
  document.getElementById('bp-list-btn').classList.toggle('on',BP.view==='list');
  const box=document.getElementById('bp-results');
  if(!rows.length){ box.innerHTML=`<p class="bp-empty">No board company is in "${esc(stage.label)}" ${document.getElementById('bp-theme').value||document.getElementById('bp-find').value?'with these filters':'right now'}.</p>`; return; }

  if(BP.view==='list'){
    box.innerHTML=`<div class="bp-table-wrap"><table class="bp-table"><thead><tr>
      <th>Company</th><th>Price</th><th>Chg</th><th>RS</th><th>vs pivot</th><th>Tightening</th><th>Dry-up</th><th>Up/down</th><th>Base (wks)</th><th>From 52w high</th><th>M-cap ₹cr</th></tr></thead><tbody>
      ${rows.map(([code,a])=>{ const d=bpByCode[code], b=bpBase(a); return `<tr data-bp-code="${esc(code)}">
        <td><b>${esc(d.name)}</b> <span class="bpc-sym">${esc(bpSym(d))}</span></td>
        <td>${bpInr(a.last.c)}</td><td class="${a.last.chg_pct>=0?'up':'dn'}">${bpPct(a.last.chg_pct)}</td>
        <td>${a.rs??'—'}</td><td>${a.now_vs_pivot==null?'—':bpPct(a.now_vs_pivot,1)}</td>
        <td>${a.atr_ratio==null?'—':a.atr_ratio.toFixed(2)+'×'}</td><td>${a.vol_dryup==null?'—':a.vol_dryup.toFixed(2)+'×'}</td>
        <td>${a.updown==null?'—':a.updown.toFixed(2)}</td><td>${b?b.weeks:'—'}</td>
        <td>${a.from_high==null?'—':a.from_high+'%'}</td><td>${fmtI(d.market_cap_cr)}</td></tr>`; }).join('')}
      </tbody></table></div>`;
    box.querySelectorAll('[data-bp-code]').forEach(tr=>tr.onclick=()=>openChart(tr.dataset.bpCode));
    return;
  }

  const shown=BP.all?rows:rows.slice(0,6);
  box.innerHTML=`<div class="bp-cards">${shown.map(([code,a])=>bpCard(code,a)).join('')}</div>
    ${rows.length>6?`<button type="button" class="bp-more" id="bp-more">${BP.all?'Show fewer':`Show all ${rows.length} ${stage.label.toLowerCase()} ›`}</button>`:''}`;
  const more=document.getElementById('bp-more'); if(more) more.onclick=()=>{ BP.all=!BP.all; bpRender(); };
  box.querySelectorAll('[data-bp-tech]').forEach(b=>b.onclick=()=>openChart(b.dataset.bpTech));
  box.querySelectorAll('[data-bp-star]').forEach(b=>b.onclick=()=>{ togglePin(b.dataset.bpStar); const on=WATCH.has(b.dataset.bpStar); b.classList.toggle('on',on); b.textContent=on?'★':'☆'; });
  box.querySelectorAll('[data-bp-theme]').forEach(b=>b.onclick=()=>{ document.getElementById('bp-theme').value=b.dataset.bpTheme; BP.all=false; bpRender(); });
  box.querySelectorAll('[data-bp-share]').forEach(b=>b.onclick=()=>bpShare(b));
  if(BP.obs) BP.obs.disconnect();
  BP.obs=new IntersectionObserver(es=>es.forEach(e=>{ if(e.isIntersecting){ BP.obs.unobserve(e.target); bpDrawChart(e.target); } }),{rootMargin:'400px 0px'});
  box.querySelectorAll('[data-bp-chart]').forEach(el=>{ el.onclick=()=>openChart(el.dataset.bpChart); BP.obs.observe(el); });
}

/* latest IPO base breakout within the window, counted in calendar days to the scan date */
function bpInWindow(a, key){
  const w=BP_BWINS.find(x=>x[0]===key), bo=a.breakouts&&a.breakouts[0];
  if(!w || !bo) return false;
  return (Date.parse(BP.data.as_of)-Date.parse(bo.date))/864e5 <= w[2];
}
function bpBwinBar(pool){
  const bar=document.getElementById('bp-bwins');
  bar.hidden = BP.screen!=='ipo';
  if(bar.hidden){ bar.innerHTML=''; return; }
  bar.innerHTML=`<span class="bp-bwins-lab">IPO base breakouts in the last</span>${BP_BWINS.map(([k,l])=>{
      const n=pool.filter(([,a])=>bpInWindow(a,k)).length;
      return `<button type="button" class="bp-bwin${BP.bwin===k?' on':''}" data-bp-bwin="${k}">${l} <span>${n}</span></button>`;
    }).join('')}`;
  bar.querySelectorAll('[data-bp-bwin]').forEach(b=>b.onclick=()=>{
    BP.bwin = BP.bwin===b.dataset.bpBwin ? null : b.dataset.bpBwin;
    BP.all=false;
    document.getElementById('bp-sort').value = BP.bwin ? 'bodate' : 'pivot';
    bpRender();
    if(BP.bwin) document.getElementById('bp-list-title').scrollIntoView({behavior:'smooth',block:'start'});
  });
}

function bpFeedItem(f){
  return `<button type="button" class="bp-fi" data-bp-code="${esc(f.code)}"><span><b>${esc(f.name||f.code)}</b> ${esc(f.text)}</span>
    <span class="bp-fpx">${bpInr(f.close)} <span class="${(f.chg_pct||0)>=0?'up':'dn'}">${bpPct(f.chg_pct,1)}</span></span></button>`;
}

function bpCard(code,a){
  const d=bpByCode[code], l=a.last, b=bpBase(a), on=WATCH.has(code), bo=a.breakout;
  const m=(label,info,val)=>`<div><span>${label}<i class="bpc-i" title="${esc(info)}">i</i></span><b>${val}</b></div>`;
  const ipo=BP.screen==='ipo' && a.listed, what=ipo?'the IPO base':'the pivot';
  let note=a.note||'';
  const wb = BP.bwin && a.breakouts && a.breakouts[0];
  if(wb){
    const st = wb.status==='active' ? 'still in the trade' : wb.status==='rolled' ? 'broke out again from a later base'
             : `${wb.status==='stopped'?'stopped out':'trailed out'} ${_bpDay(wb.exit.date)} (${bpPct(wb.exit.result_pct,1)})`;
    note=`Broke out of the IPO base ${_bpDay(wb.date)} (${wb.sessions_ago} session${wb.sessions_ago===1?'':'s'} ago) on ${wb.vol_x}× volume · ${bpPct(wb.gain_pct,1)} since, best ${bpPct(wb.best_pct,1)} · ${st}`;
  } else if(!note && bo){
    if(a.stage==='fresh') note=`Broke out of ${what} ${_bpDay(bo.date)} on ${bo.vol_x}× usual volume — ${bpPct(bo.gain_pct,1)} from the pivot`;
    else if(a.stage==='climbing') note=`Broke out of ${what} ${_bpDay(bo.date)} — ${bpPct(bo.gain_pct,1)} since, ${bo.sessions} sessions in`;
    else if(a.stage==='played' && bo.exit) note=`Broke out ${_bpDay(bo.date)} → ${bo.exit.reason==='stopped'?'stopped out':'trailed out'} ${_bpDay(bo.exit.date)} (${bpPct(bo.exit.result_pct,1)})`;
  }
  const flags=(a.verge?[`<span class="bpc-flag verge" title="Forming and within 5% of the pivot">🔥 on the verge — ${Math.abs(a.now_vs_pivot)}% under ${ipo?'the post-listing high':'the pivot'}</span>`]:[])
    .concat((a.flags||[]).map(fl=>fl==='squat'
      ? `<span class="bpc-flag" title="${esc(BP_INFO.squat)}">⚑ squat</span>`
      : `<span class="bpc-flag bad" title="${esc(BP_INFO.poke)}">✕ failed poke</span>`)).join('');
  const listedLine = ipo ? `<div class="bpc-listed">Listed ${galDay(a.listed)} · ${a.sessions_listed} sessions ago · post-listing high ${bpInr(a.listing_high)} (${a.from_listing_high}% above today)</div>` : '';
  return `<article class="bpc">
    <div class="bpc-top">
      <div style="min-width:0">
        <div class="bpc-title"><span class="bpc-name">${esc(d.name)}</span><span class="bpc-sym">${esc(bpSym(d))}</span>
          <button type="button" class="bpc-star${on?' on':''}" data-bp-star="${esc(code)}" title="${on?'Remove from':'Add to'} watchlist">${on?'★':'☆'}</button></div>
        <div class="bpc-ind"><button type="button" data-bp-theme="${esc(base(d))}">${esc(shortT(base(d)))}</button>${d.industry||d.sector?` · ${esc(d.industry||d.sector)}`:''}</div>
      </div>
      <div class="bpc-right"><button type="button" class="bpc-tech" data-bp-tech="${esc(code)}">📈 Tech chart ›</button>
        <div class="bpc-px">${bpInr(l.c)} <span class="${l.chg_pct>=0?'up':'dn'}">(${bpPct(l.chg_pct)})</span></div></div>
    </div>
    ${listedLine}
    <div class="bpc-ohlc"><b>${esc(a.asof)}</b><span>O ${(+l.o).toFixed(2)}</span><span>H ${(+l.h).toFixed(2)}</span><span>L ${(+l.l).toFixed(2)}</span><span>C ${(+l.c).toFixed(2)}</span><span class="${l.chg_pct>=0?'up':'dn'}">(${bpPct(l.chg_pct)})</span><span>Vol ${bpVol(l.v)}</span><span>RS ${a.rs??'—'}</span></div>
    <div class="bpc-chart" data-bp-chart="${esc(code)}" title="Open the full chart"><span class="gc-skel"></span></div>
    <div class="bpc-m">
      ${m('RS rating',BP_INFO.rs,a.rs??'—')}
      ${m('Now vs pivot (%)',BP_INFO.pivot,a.now_vs_pivot==null?'—':bpPct(a.now_vs_pivot,1))}
      ${m('Tightening (ATR ratio)',BP_INFO.atr,a.atr_ratio==null?'—':a.atr_ratio.toFixed(2)+'×')}
      ${m('Volume dry-up',BP_INFO.dry,a.vol_dryup==null?'—':a.vol_dryup.toFixed(2)+'×')}
      ${m('Up/down volume, net',BP_INFO.ud,a.updown==null?'—':a.updown.toFixed(2))}
      ${m('From 52-week high',BP_INFO.high,a.from_high==null?'—':a.from_high+'%')}
    </div>
    ${flags?`<div class="bpc-flags">${flags}</div>`:''}
    ${note?`<div class="bpc-note">${esc(note)}</div>`:''}
    <div class="bpc-foot"><button type="button" class="bp-btn" data-bp-share="${esc(code)}">⤴ Share</button>
      <span class="bpc-foot-r">${b?`<span class="bpc-stage">${b.weeks}-week base · ${esc(bpInr(b.pivot||a.pivot))} pivot</span>`:''}<span class="bpc-mood ${a.mood==='Powering up'?'up':'dn'}">${a.mood==='Powering up'?'▲':'▼'} ${esc(a.mood)}</span></span></div>
  </article>`;
}
function _bpDay(iso){ const [y,mo,dd]=String(iso).split('-').map(Number); return `${dd} ${GMONTHS[mo-1]}`; }

function bpDrawChart(el){
  const code=el.dataset.bpChart, a=bpView(BP.data.stocks[code]);
  galFetch(code).then(()=>{
    const ser=GSERIES.get(code);
    if(!ser || ser.missing || !ser.rows){ el.innerHTML='<span class="gc-empty">No price data</span>'; return; }
    const wb = BP.bwin && a.breakouts && a.breakouts[0];
    const b = wb ? {...wb.base, pivot:wb.pivot} : bpBase(a);
    /* an IPO base card shows the stock's life since listing (up to ~14 months) */
    const ipoCard = BP.screen==='ipo' && a.listed;
    const sessions = ipoCard ? Math.min(ser.rows.length, 300) : 125;
    el.innerHTML=candleSVG(ser.rows,{w:600,h:230,sessions,axis:true,isoAxis:true,ma:false,hi52:false,volColor:true,
      pivot:wb?wb.pivot:a.pivot, box:b?{start:b.start,end:b.end,low:b.low,high:b.pivot||a.pivot,label:`${ipoCard?'IPO base ':''}${b.weeks} wks`}:null});
  });
}

async function bpShare(btn){
  const code=btn.dataset.bpShare, a=bpView(BP.data.stocks[code]), d=bpByCode[code];
  const stage=a.stage && bpStageText(a.stage);
  const text=`${d.name} (${bpSym(d)}) — ${stage?stage.label:'no setup'} · ${bpInr(a.last.c)} ${bpPct(a.last.chg_pct)} · pivot ${bpInr(a.pivot)} (${a.now_vs_pivot==null?'—':bpPct(a.now_vs_pivot,1)}) · RS ${a.rs??'—'} · ${galDay(a.asof)}`;
  try{ await navigator.clipboard.writeText(text); flash(btn,'✓ Copied'); }
  catch(e){ openModal(`<h3>Share</h3><p class="quote" style="margin-top:12px">${esc(text)}</p>`); }
}

function bpCsv(){
  const rows=bpRows(); if(!rows.length) return;
  const head=['Company','Symbol','Theme','Screen','Stage','On the verge','Listed','Latest breakout','Breakout status','Gain since breakout %','Close','Change %','RS','Pivot','Now vs pivot %','ATR ratio','Volume dry-up','Up/down net','Base weeks','From 52w high %','Market cap cr','As of'];
  const lines=[head.join(',')].concat(rows.map(([code,a])=>{ const d=bpByCode[code], b=bpBase(a);
    return [d.name,bpSym(d),shortT(base(d)),BP.screen==='ipo'?'IPO base':'VCP',a.stage,a.verge?'yes':'',a.listed||'',(a.breakouts&&a.breakouts[0]||{}).date||'',(a.breakouts&&a.breakouts[0]||{}).status||'',(a.breakouts&&a.breakouts[0]||{}).gain_pct??'',a.last.c,a.last.chg_pct,a.rs,a.pivot,a.now_vs_pivot,a.atr_ratio,a.vol_dryup,a.updown,b&&b.weeks,a.from_high,d.market_cap_cr,a.asof].map(csvCell).join(','); }));
  const url=URL.createObjectURL(new Blob(['﻿'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a'); link.href=url; link.download=`market-view-${BP.screen}-${BP.bwin?'breakouts-'+BP.bwin:BP.stage}-${BP.data.as_of}.csv`;
  document.body.appendChild(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),4000);
}

function bpFullFeed(feed){
  openModal(`<h3>What changed since last close — ${galDay(BP.data.as_of)}</h3>
    <p class="mp">Stage changes on the board in the latest session, then new 52-week highs and lows.</p>
    <div style="margin-top:14px">${feed.map(bpFeedItem).join('')}</div>`);
  mbody.querySelectorAll('[data-bp-code]').forEach(b=>b.onclick=()=>openChart(b.dataset.bpCode));
}

function bpMarketWide(){
  const ipo=BP.screen==='ipo', list=(ipo?BP.data.market_breakouts_ipo:BP.data.market_breakouts)||[];
  openModal(`<h3>↗ ${list.length} ${ipo?'IPO base ':''}broke out market-wide — ${galDay(BP.data.as_of)}</h3>
    <p class="mp">${ipo
      ? `Every liquid NSE stock (${fmtI(BP.data.universe)} with over ₹1 crore traded a day) listed in the last two years that closed above its post-listing high, after a base of three weeks or more, on at least 1.4× its usual volume in the latest session.`
      : `Every liquid NSE stock (${fmtI(BP.data.universe)} with over ₹1 crore traded a day) that closed above its base's pivot on at least 1.4× its usual volume in the latest session, while in an uptrend.`} Stocks also on your board are marked.</p>
    ${list.length?`<div class="bp-table-wrap" style="margin-top:14px"><table class="bp-table"><thead><tr><th>Company</th><th>Close</th><th>Chg</th><th>Pivot</th><th>Above pivot</th><th>Volume</th></tr></thead><tbody>
      ${list.map(x=>`<tr${x.on_board?' data-bp-mw="1"':''}><td><b>${esc(x.name)}</b> <span class="bpc-sym">${esc(x.symbol)}</span>${x.on_board?' <span class="asme-tag">On board</span>':''}</td>
        <td>${bpInr(x.close)}</td><td class="${x.chg_pct>=0?'up':'dn'}">${bpPct(x.chg_pct)}</td><td>${bpInr(x.pivot)}</td><td>${bpPct(x.above_pct,1)}</td><td>${x.vol_x}×</td></tr>`).join('')}
      </tbody></table></div>`:'<p class="bp-empty" style="margin-top:14px">No breakouts market-wide in the latest session.</p>'}`);
}

function bpHow(){
  openModal(`<h3>How these are found</h3>
    <p class="mp">After every trading day the chart job rebuilds each board company's split-adjusted daily candles from the NSE/BSE closing files and runs the same fixed rules over them. Nothing is picked by hand.</p>
    <table class="cmp" style="margin-top:12px"><tbody>
      <tr><td><b>Base &amp; pivot</b></td><td>The highest high of the last ~6 months is the pivot. It is a base when that high is at least 3 weeks old, the pullback from it is at most 35%, and the stock ran up at least 25% into it.</td></tr>
      <tr><td><b>Breakout</b></td><td>The first close above the pivot on at least 1.4× the 50-day average volume.</td></tr>
      <tr><td><b>Exit</b></td><td>Stopped when the price trades 8% under the pivot; trailed out on a close under the 50-day average.</td></tr>
      <tr><td><b>Forming</b></td><td>In a base, within 15% under the pivot, above the 200-day average with the 50-day above the 200-day — or building a new base after an earlier breakout.</td></tr>
      <tr><td><b>Fresh / Climbing</b></td><td>Broke out and not exited: within the last 5 sessions, or earlier.</td></tr>
      <tr><td><b>Played out</b></td><td>Broke out this year and has since been stopped or trailed out.</td></tr>
      <tr><td><b>IPO base screen</b></td><td>Companies listed in the last two years, dated by the first session any of their identifiers (ISIN, NSE or BSE ticker) appears in the exchange files, so renames, splits and a later second listing are not counted as an IPO. The pivot is the highest price since listing; a base needs at least 3 weeks under it and at most a 50% pullback. Usual volume ignores the first 5 sessions after listing.</td></tr>
      <tr><td><b>On the verge</b></td><td>Forming and within 5% of the pivot.</td></tr>
      <tr><td><b>RS rating</b></td><td>${esc(BP_INFO.rs)}</td></tr>
      <tr><td><b>Powering up / Cooling off</b></td><td>Last close above / below the close five sessions earlier.</td></tr>
    </tbody></table>`);
}

/* ---- NIFTY 50 / SENSEX chips and the Zerodha connection ---- */
async function bpStatus(){
  try{ BP.kite=await fetchJSON('/api/kite/status'); }catch(e){ BP.kite={configured:false,connected:false}; }
  const k=BP.kite, box=document.getElementById('bp-conn');
  if(k.connected) box.innerHTML=`<span class="bp-upd"><i></i>NIFTY &amp; SENSEX live via Zerodha${k.user?' · '+esc(k.user):''}</span>`;
  else if(k.configured) box.innerHTML=`<span>Index prices delayed · </span><button class="bp-link" id="bp-connect" type="button">Connect Zerodha</button>`;
  else box.innerHTML='<span title="Set KITE_API_KEY, KITE_API_SECRET and KITE_ADMIN_KEY on the server">Index prices delayed (Zerodha not set up)</span>';
  const c=document.getElementById('bp-connect'); if(c) c.onclick=bpConnect;
  bpSchedule('status', bpStatus, 5*60*1000);
}
function bpConnect(){
  openModal(`<h3>Connect Zerodha</h3>
    <p class="mp">Zerodha asks for a login once a day; the session lasts until 6 a.m. the next morning. Enter the admin key set on the server (KITE_ADMIN_KEY) to continue to Zerodha's login page.</p>
    <form id="bp-key-form" class="bp-key"><input id="bp-key" type="password" autocomplete="current-password" placeholder="Admin key" class="bp-find" required>
    <button class="bp-mw" type="submit">Continue to Zerodha</button></form>`);
  const f=document.getElementById('bp-key-form'), inp=document.getElementById('bp-key');
  inp.focus();
  f.onsubmit=e=>{ e.preventDefault(); location.href='/api/kite/login?key='+encodeURIComponent(inp.value); };
}
async function bpIndices(){
  let j; try{ j=await fetchJSON('/api/market/indices'); }catch(e){ j=null; }
  const box=document.getElementById('bp-idx');
  if(j){
    const prev=BP.idx; BP.idx=j;
    const live=j.source==='zerodha';
    box.innerHTML=j.items.map(it=>{
      const p=prev && prev.items.find(x=>x.key===it.key);
      const tick=p && p.last!=null && it.last!=null && it.last!==p.last ? (it.last>p.last?' tick-up':' tick-dn') : '';
      const tip=`${it.name} · ${live?(j.market_open?'live':'Zerodha, market closed'):'delayed'} · O ${it.open==null?'—':mvNum(it.open)} H ${it.high==null?'—':mvNum(it.high)} L ${it.low==null?'—':mvNum(it.low)} · prev close ${it.prev_close==null?'—':mvNum(it.prev_close)}`;
      return `<span class="bp-ix${tick}" title="${esc(tip)}"><i class="${live&&j.market_open?'live':''}"></i><b>${esc(it.name)}</b><span>${it.last==null?'—':mvNum(it.last)}</span><small class="${(it.change||0)>=0?'up':'dn'}">${it.change_pct==null?'':bpPct(it.change_pct)}</small></span>`;
    }).join('');
  }
  bpSchedule('indices', bpIndices, j && j.source==='zerodha' && j.market_open ? 3000 : 60000);
}
const mvNum=(v,d=2)=>v==null?'—':(+v).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});

/* ==================================================================
   Account menu: who is logged in, log out, and (for admins) approving
   the people who asked for access.
   ================================================================== */
var ME=null;   /* the signed-in user, once /api/auth/me answers */
async function initUserMenu(){
  let me=null;
  try{ me=await fetchJSON('/api/auth/me'); }catch(e){ return; }
  if(!me || !me.user) return;                 /* accounts off (local development) */
  const u=me.user, box=document.getElementById('nav-user');
  ME=u;
  const initial=(u.name||u.email||'?').trim().charAt(0).toUpperCase();
  box.hidden=false;
  box.innerHTML=`<button type="button" class="nav-user-btn" id="nav-user-btn" aria-haspopup="menu" aria-expanded="false">
      <span class="nav-avatar">${esc(initial)}</span><span class="nav-user-name">${esc(u.name||u.email)}</span><span class="nav-pending" id="nav-pending" hidden></span>
    </button>
    <div class="nav-menu" id="nav-menu" role="menu" hidden>
      <div class="nav-menu-head"><span class="nav-avatar lg">${esc(initial)}</span><div><b>${esc(u.name||'Signed in')}</b><small>${esc(u.email)}${u.is_admin?' · Admin':''}</small></div></div>
      ${u.is_admin?`<button type="button" role="menuitem" id="nav-manage">${icon('building')}Manage users <span class="nav-pending" id="nav-pending-2" hidden></span></button>`:''}
      <button type="button" role="menuitem" id="nav-logout">${icon('flag')}Log out</button>
    </div>`;
  const btn=document.getElementById('nav-user-btn'), menu=document.getElementById('nav-menu');
  btn.onclick=e=>{ e.stopPropagation(); menu.hidden=!menu.hidden; btn.setAttribute('aria-expanded', String(!menu.hidden)); };
  document.addEventListener('click',e=>{ if(!box.contains(e.target)){ menu.hidden=true; btn.setAttribute('aria-expanded','false'); } });
  document.getElementById('nav-logout').onclick=async ()=>{
    try{ await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'}); }catch(e){}
    location.href='/login';
  };
  if(u.is_admin){
    document.getElementById('nav-manage').onclick=()=>{ menu.hidden=true; openUsers(); };
    refreshPending();
    setInterval(refreshPending, 5*60*1000);
  }
}
async function refreshPending(){
  let j; try{ j=await fetchJSON('/api/admin/users'); }catch(e){ return; }
  ['nav-pending','nav-pending-2'].forEach(id=>{ const el=document.getElementById(id); if(el){ el.textContent=j.pending; el.hidden=!j.pending; } });
  return j;
}
const USER_STATUS={pending:['Waiting for approval','st-pending'], approved:['Approved','st-approved'], rejected:['Rejected','st-rejected'], disabled:['Disabled','st-disabled']};
async function openUsers(){
  openModal('<p class="view-hint">Loading accounts…</p>');
  const j=await refreshPending(); if(!j) return;
  const when=iso=>iso?new Date(iso).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}):'—';
  const acts=u=>{
    if(u.is_owner) return '<span class="user-owner" title="Set by ADMIN_EMAIL / AUTH_USERNAME on the server">Main admin · managed in server settings</span>';
    if(u.status==='pending') return `<button class="btn on" data-uact="approve" data-uid="${u.id}">Approve</button><button class="btn" data-uact="reject" data-uid="${u.id}">Reject</button>`;
    if(u.status==='approved') return `${u.is_admin?`<button class="btn" data-uact="remove-admin" data-uid="${u.id}">Remove admin</button>`:`<button class="btn" data-uact="make-admin" data-uid="${u.id}">Make admin</button>`}<button class="btn" data-uact="disable" data-uid="${u.id}">Disable</button>`;
    return `<button class="btn" data-uact="enable" data-uid="${u.id}">${u.status==='rejected'?'Approve':'Enable'}</button><button class="btn danger" data-udel="${u.id}">Delete</button>`;
  };
  mbody.innerHTML=`<h3>Manage users</h3>
    <p class="mp">${j.pending?`<b>${j.pending}</b> ${j.pending===1?'person is':'people are'} waiting for approval. `:'No one is waiting for approval. '}New sign-ups can't log in until you approve them; disabling or rejecting someone logs them out straight away.</p>
    <div class="users-list">${j.users.map(u=>{ const st=USER_STATUS[u.status]||[u.status,'']; return `
      <div class="user-row">
        <span class="nav-avatar lg">${esc((u.name||u.email).charAt(0).toUpperCase())}</span>
        <div class="user-info"><b>${esc(u.name||'—')}${u.is_admin?' <span class="user-admin">Admin</span>':''}</b><small>${esc(u.email)}</small>
          <small>Signed up ${when(u.created_at)}${u.last_login?` · last login ${when(u.last_login)}`:''}</small></div>
        <span class="user-status ${st[1]}">${st[0]}</span>
        <div class="user-acts">${acts(u)}</div>
      </div>`; }).join('')}</div>`;
  mbody.querySelectorAll('[data-uact]').forEach(b=>b.onclick=async ()=>{
    b.disabled=true;
    const r=await fetch(`/api/admin/users/${b.dataset.uid}/${b.dataset.uact}`,{method:'POST',credentials:'same-origin'});
    if(!r.ok){ const d=await r.json().catch(()=>({})); alert(d.detail||'That did not work.'); }
    openUsers();
  });
  mbody.querySelectorAll('[data-udel]').forEach(b=>b.onclick=async ()=>{
    if(!confirm('Delete this account permanently?')) return;
    const r=await fetch(`/api/admin/users/${b.dataset.udel}`,{method:'DELETE',credentials:'same-origin'});
    if(!r.ok){ const d=await r.json().catch(()=>({})); alert(d.detail||'That did not work.'); }
    openUsers();
  });
}
/* ================================================================
   Research Reports
   One report for every company on the board, composed from its research
   record (business, moat, import substitution, ownership, financials,
   capex and guidance, score, risks, verification), its chart and its
   Market view stage. Nothing is typed up per report, so a company that
   joins the board -- including the daily auto-screen's -- has a report the
   moment it arrives, and every report moves with the daily data refresh.
   ================================================================ */
const RP={code:null, q:'', theme:'', kind:'', sort:'score', list:[], built:false};
const RP_KIND={deep:'Deep dive', research:'Research note', screen:'Screening note', triage:'Triage note', auto:'Auto-screened'};
const rpKind = d => d.screen==='auto' ? 'auto' : /deep/i.test(d.depth||'') ? 'deep'
  : /triage/i.test(d.depth||'')||d.screen==='v4-triage' ? 'triage' : /screening/i.test(d.depth||'')||d.screen==='v3-screen' ? 'screen' : 'research';
const RP_SORTS={
  score:{l:'Sort: highest score', f:(a,b)=>(nz(b.final_score)??-1)-(nz(a.final_score)??-1)},
  newest:{l:'Sort: newest on the board', f:(a,b)=>String(b.added_on||'').localeCompare(String(a.added_on||''))||(nz(b.final_score)??-1)-(nz(a.final_score)??-1)},
  name:{l:'Sort: company name', f:(a,b)=>String(a.name).localeCompare(String(b.name))},
  mcap:{l:'Sort: smallest market cap', f:(a,b)=>(nz(a.market_cap_cr)??1e12)-(nz(b.market_cap_cr)??1e12)},
};
const rpDate = iso => iso && /^\d{4}-\d\d-\d\d/.test(iso) ? galDay(iso.slice(0,10)) : (iso||'—');
/* the first sentence or two of a research note, for summaries */
function rpLead(s, max){
  s=String(s||'').trim(); if(!s) return '';
  const parts=s.split(/(?<=[.!?])\s+(?=[A-Z₹(“"])/);
  let out=parts[0];
  if(out.length<90 && parts[1]) out+=' '+parts[1];
  max=max||260;
  return out.length>max ? out.slice(0,max).replace(/\s+\S*$/,'')+'…' : out;
}
const rpPct = v => nz(v)===null ? '—' : fmt(v)+'%';
const rpChg = v => nz(v)===null ? '' : ` <span class="${v>0?'up':v<0?'dn':'nd'}">(${v>0?'+':''}${fmt(v,2)} pts, 1y)</span>`;
const rpSearchText = d => d._rp || (d._rp=[d.name,d.code,d.nse_code,d.bse_code,d.theme,d.sector,d.industry,d.business,d.moat_note,
  d.import_substitution,d.risk_note,d.why_obscure].filter(Boolean).join(' ').toLowerCase());

function openReports(){
  if(!DR.index){
    if(!RP.code) document.getElementById('rpgrid').innerHTML='<p class="view-hint">Loading reports…</p>';
    drLoadIndex().then(()=>{ if(VIEW==='reports') openReports(); });
    return;
  }
  if(!RP.built) rpBuildControls();
  if(RP.code && DATA.some(d=>d.code===RP.code)) rpRenderDoc(RP.code);
  else { RP.code=null; rpRenderLib(); }
}
function openReport(code){
  if(RP.code!==code){ DR.period=null; DR.tab='deep'; }
  RP.code=code;
  closeDrawer(); closeModal();
  setView('reports',{nav:'reports-link', keep:true});
  window.scrollTo({top:0});
}
function rpBuildControls(){
  RP.built=true;
  const th=document.getElementById('rptheme'), kd=document.getElementById('rpkind'), so=document.getElementById('rpsort'), q=document.getElementById('rpq');
  th.innerHTML='<option value="">All themes</option>'+THEMES.map(t=>`<option value="${esc(t)}">${esc(shortT(t))} (${DATA.filter(d=>base(d)===t).length})</option>`).join('');
  const kinds=Object.keys(RP_KIND).filter(k=>DATA.some(d=>rpKind(d)===k));
  const nDeep=DATA.filter(d=>DR.index&&DR.index[d.code]).length;
  kd.innerHTML='<option value="">All report types</option>'+(nDeep?`<option value="deep">Deep-dive available (${nDeep})</option>`:'')+kinds.map(k=>`<option value="${k}">${RP_KIND[k]} (${DATA.filter(d=>rpKind(d)===k).length})</option>`).join('');
  so.innerHTML=Object.entries(RP_SORTS).map(([k,v])=>`<option value="${k}">${v.l}</option>`).join('');
  let timer=null;
  q.oninput=()=>{ clearTimeout(timer); timer=setTimeout(()=>{ RP.q=q.value.trim().toLowerCase(); rpRenderLib(); },140); };
  th.onchange=()=>{ RP.theme=th.value; rpRenderLib(); };
  kd.onchange=()=>{ RP.kind=kd.value; rpRenderLib(); };
  so.onchange=()=>{ RP.sort=so.value; rpRenderLib(); };
  document.getElementById('rpclear').onclick=()=>{ q.value=''; th.value=''; kd.value=''; so.value='score'; RP.q=''; RP.theme=''; RP.kind=''; RP.sort='score'; rpRenderLib(); };
  document.getElementById('rpgrid').onclick=e=>{
    const pin=e.target.closest('[data-rppin]');
    if(pin){ e.stopPropagation(); togglePin(pin.dataset.rppin); rpRenderLib(); return; }
    const card=e.target.closest('[data-rp]'); if(card) openReport(card.dataset.rp);
  };
  document.getElementById('rpgrid').onkeydown=e=>{
    const card=e.target.closest('[data-rp]'); if(card && (e.key==='Enter'||e.key===' ')){ e.preventDefault(); openReport(card.dataset.rp); }
  };
}
function rpFiltered(){
  const terms=RP.q ? RP.q.split(/\s+/).filter(Boolean) : [];
  return DATA.filter(d=>(!RP.theme||base(d)===RP.theme) && (!RP.kind||(RP.kind==='deep'?!!(DR.index&&DR.index[d.code]):rpKind(d)===RP.kind)) && terms.every(t=>rpSearchText(d).includes(t)))
             .sort(RP_SORTS[RP.sort].f);
}
function rpCard(d){
  const k=rpKind(d), on=WATCH.has(d.code);
  const tags=[`<span class="rp-tag rp-k-${k}">${RP_KIND[k]}</span>`];
  const deep=DR.index&&DR.index[d.code];
  if(deep) tags.unshift(`<span class="rp-tag rp-deep" title="Quarterly deep-dive report, updated ${esc((deep.generated_at||'').slice(0,10))}">Deep dive · ${esc(deep.period_label)}</span>`);
  if(d.import_sub_verdict) tags.push(`<span class="rp-tag rp-v-${esc(d.import_sub_verdict)}">Import sub: ${esc(d.import_sub_verdict)}</span>`);
  if(d.claim_grade) tags.push(`<span class="rp-tag ${CG[d.claim_grade]||'cg-none'}">Claim: ${esc(d.claim_grade)}</span>`);
  if(ASME_BY_CODE[d.code]) tags.push('<span class="rp-tag rp-asme">ASME</span>');
  return `<article class="rp-card" data-rp="${esc(d.code)}" tabindex="0" aria-label="Research report: ${esc(d.name)}">
    <div class="rp-card-top"><span class="theme-ic">${themeIcon(base(d))}</span><span class="rp-card-theme">${esc(shortT(base(d)))}</span>
      <button type="button" class="rp-pin${on?' on':''}" data-rppin="${esc(d.code)}" title="${on?'Remove from':'Add to'} watchlist">${on?'★':'☆'}</button></div>
    <h3>${esc(d.name)}</h3>
    <div class="rp-card-sub">${esc(d.code)}${d.industry?' · '+esc(d.industry):''} · ₹${fmtI(d.market_cap_cr)} cr</div>
    <p class="rp-card-thesis">${esc(rpLead(d.moat_note||d.business,170))}</p>
    <div class="rp-tags">${tags.join('')}</div>
    <div class="rp-card-foot"><span class="rp-card-score"><b>${d.final_score==null?'—':fmt(d.final_score)}</b>/100</span><span class="rp-read">Read report ›</span></div>
  </article>`;
}
function rpRenderLib(){
  document.getElementById('rp-lib').hidden=false;
  document.getElementById('rp-doc').hidden=true;
  document.getElementById('reports-heading').hidden=false;
  const rows=rpFiltered(); RP.list=rows;
  document.getElementById('rpcount').textContent = rows.length===DATA.length
    ? `${DATA.length} research reports — one for every company on the board`
    : `${rows.length} of ${DATA.length} reports`;
  const note=document.getElementById('rp-note');
  if(note){ const n=DATA.filter(d=>d.added_on && d.added_on===META.build_new).length; note.textContent = n ? `${n} new report${n>1?'s':''} ${galDay(META.build_new)}` : ''; }
  document.getElementById('rpgrid').innerHTML = rows.length ? rows.map(rpCard).join('')
    : '<p class="view-hint">No report matches. Clear the search or pick another theme.</p>';
  syncURL();
}

/* ---------- one report ---------- */
function rpScoreRing(v){
  const pct=nz(v)===null?0:Math.max(0,Math.min(100,v)), r=34, c=2*Math.PI*r;
  return `<svg class="rp-ring" viewBox="0 0 84 84" aria-hidden="true"><circle cx="42" cy="42" r="${r}" class="rp-ring-bg"/>
    <circle cx="42" cy="42" r="${r}" class="rp-ring-fg" stroke-dasharray="${(c*pct/100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 42 42)"/>
    <text x="42" y="46" text-anchor="middle" class="rp-ring-tx">${nz(v)===null?'—':fmt(v)}</text></svg>`;
}
function rpPatChart(d){
  const p=(d.pat_periods||[]).filter(x=>Array.isArray(x)&&nz(x[1])!==null);
  if(p.length<2) return '';
  const W=560,H=190,padB=34,padT=18, vals=p.map(x=>+x[1]), mx=Math.max(0,...vals), mn=Math.min(0,...vals), span=(mx-mn)||1;
  const y=v=>padT+(mx-v)/span*(H-padT-padB), step=W/p.length, bw=Math.min(46,step*0.6);
  let s=`<svg viewBox="0 0 ${W} ${H}" class="rp-pat" role="img" aria-label="Net profit by period"><line x1="0" x2="${W}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" class="rp-pat-zero"/>`;
  p.forEach(([lab,v],i)=>{
    const x=i*step+step/2, y0=y(0), yv=y(v), top=Math.min(y0,yv), h=Math.max(1,Math.abs(yv-y0));
    s+=`<rect x="${(x-bw/2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="3" class="rp-pat-bar ${v<0?'neg':''}"/>`
     +`<text x="${x.toFixed(1)}" y="${(v<0?yv+12:yv-5).toFixed(1)}" text-anchor="middle" class="rp-pat-v">${(+v).toLocaleString('en-IN',{maximumFractionDigits:1})}</text>`
     +`<text x="${x.toFixed(1)}" y="${H-10}" text-anchor="middle" class="rp-pat-l">${esc(String(lab))}</text>`;
  });
  return s+'</svg>';
}
function rpOwnership(d){
  const parts=[['Promoter',d.promoter_pct,'o-pro'],['FII',d.fii_pct,'o-fii'],['DII',d.dii_pct,'o-dii'],['Public',d.public_pct,'o-pub']].filter(x=>nz(x[1])!==null&&x[1]>0);
  const total=parts.reduce((a,x)=>a+(+x[1]),0)||1;
  return `<div class="rp-own-bar">${parts.map(([l,v,c])=>`<span class="${c}" style="width:${(v/total*100).toFixed(2)}%" title="${l} ${fmt(v,2)}%"></span>`).join('')}</div>
    <ul class="rp-own-list">${parts.map(([l,v,c])=>`<li><i class="${c}"></i>${l}<b>${fmt(v,2)}%</b></li>`).join('')}</ul>`;
}
function rpRow(k,v){ return v===''||v==null ? '' : `<tr><th>${k}</th><td>${v}</td></tr>`; }
function rpBlock(n,title,body,cls){ return body ? `<section class="rp-block ${cls||''}"><h2><span class="rp-n">${n}</span>${title}</h2>${body}</section>` : ''; }
function rpPara(s){ return s ? `<p>${esc(s)}</p>` : ''; }

function rpTakeaways(d){
  const out=[];
  if(d.moat_note) out.push(['Moat', rpLead(d.moat_note,230)]);
  if(d.import_substitution && !/^none found/i.test(d.import_substitution))
    out.push(['Import substitution', (d.import_sub_verdict?`${d.import_sub_verdict[0].toUpperCase()+d.import_sub_verdict.slice(1)} — `:'')+rpLead(d.import_substitution,200)]);
  const fin=[nz(d.roce_pct)!==null?`ROCE ${fmt(d.roce_pct)}%`:'', nz(d.roe_pct)!==null?`ROE ${fmt(d.roe_pct)}%`:'', nz(d.opm_pct)!==null?`operating margin ${fmt(d.opm_pct)}%`:'',
    nz(d.sales_growth_3y_pct)!==null?`3-year sales growth ${fmt(d.sales_growth_3y_pct,0)}%`:'', nz(d.pe)!==null?`P/E ${fmt(d.pe)}`:''].filter(Boolean);
  if(fin.length) out.push(['Numbers', fin.join(' · ')]);
  const inst=(nz(d.fii_pct)||0)+(nz(d.dii_pct)||0);
  out.push(['Ownership', `Promoter ${rpPct(d.promoter_pct)}, institutions ${fmt(inst,2)}%, ${fmtI(d.num_shareholders)} shareholders — Tier ${d.tier||'—'}`]);
  if(d.risk_note) out.push(['Main risk', rpLead(d.risk_note,220)]);
  const st=BP.data && BP.data.stocks && BP.data.stocks[d.code];
  if(st && st.stage){ const x=BP_STAGES.find(s=>s.k===st.stage); if(x) out.push(['Chart', `Market view stage: ${x.label}${st.rs!=null?` · RS rating ${st.rs}`:''}${st.mood?` · ${st.mood}`:''}`]); }
  return `<ul class="rp-take">${out.map(([k,v])=>`<li><b>${k}</b><span>${esc(v)}</span></li>`).join('')}</ul>`;
}

function rpRenderDoc(code){
  const d=DATA.find(x=>x.code===code); if(!d){ RP.code=null; return rpRenderLib(); }
  if(DR.tab!=='board' && DR.index && DR.index[code]) return drOpen(d);
  document.getElementById('rp-lib').hidden=true;
  document.getElementById('reports-heading').hidden=true;
  const doc=document.getElementById('rp-doc'); doc.hidden=false;
  const list=RP.list.length && RP.list.includes(d) ? RP.list : [...DATA].sort(RP_SORTS.score.f);
  const i=list.indexOf(d), prev=list[i-1], next=list[i+1];
  const k=rpKind(d), scr=SCREENS[d.screen]||{lab:d.screen||'',full:''}, on=WATCH.has(d.code);
  const inst=(nz(d.fii_pct)||0)+(nz(d.dii_pct)||0);
  const asme=ASME_BY_CODE[d.code];
  const warns=(d.warnings||[]).filter(Boolean), pen=(d.penalty_detail||[]).filter(Boolean), gates=(d.gate_failures||[]).filter(Boolean);
  const exch=[d.nse_code&&!/^\d+$/.test(d.nse_code)?`NSE: ${esc(d.nse_code)}`:'', d.bse_code?`BSE: ${esc(d.bse_code)}`:''].filter(Boolean).join(' · ') || esc(d.code);
  const kpi=(l,v,sub)=>`<div class="rp-kpi"><span>${l}</span><b>${v}</b>${sub?`<small>${sub}</small>`:''}</div>`;
  const quals=(d.qualifies_as||[]).filter(Boolean);

  const fins = [
    rpRow('ROCE', rpPct(d.roce_pct)), rpRow('ROE', rpPct(d.roe_pct)), rpRow('Operating margin', nz(d.opm_pct)===null?'':rpPct(d.opm_pct)),
    rpRow('Sales growth, 3 years', nz(d.sales_growth_3y_pct)===null?'':fmt(d.sales_growth_3y_pct,0)+'% a year'),
    rpRow('Profit growth, 3 years', nz(d.profit_growth_3y_pct)===null?'':fmt(d.profit_growth_3y_pct,0)+'% a year'),
    rpRow('Debt / equity', nz(d.debt_equity)===null?'':fmt(d.debt_equity,2)), rpRow('P/E', fmt(d.pe)),
  ].join('');
  const pat=rpPatChart(d);
  const capex = [
    rpRow('Capital work in progress', nz(d.cwip_cr)===null?'':`₹${fmtI(d.cwip_cr)} cr${nz(d.cwip_prev_cr)!==null?` (a year earlier ₹${fmtI(d.cwip_prev_cr)} cr)`:''}`),
    rpRow('Net block', nz(d.net_block_cr)===null?'':`₹${fmtI(d.net_block_cr)} cr`),
    rpRow('CWIP / net block', nz(d.cwip_pct_net_block)===null?'':fmt(d.cwip_pct_net_block,1)+'%'),
    rpRow('Guided growth', nz(d.guidance_pct)===null?'':fmt(d.guidance_pct,0)+'%'),
  ].join('');
  const flags=[d.capex_overhang&&'High P/E with heavy CWIP', d.capex_heavy&&'CWIP ≥ 25% of net block', d.guidance_over15&&'Management guides above 15%', d.pat_turnaround&&'Profit turned positive'].filter(Boolean);

  doc.innerHTML=`
  <div class="rp-toolbar">
    <button type="button" class="theme-back" id="rp-back">‹ All reports</button>
    <span class="rp-pos">${i>=0?`${i+1} of ${list.length}`:''}</span>
    <span class="rp-tools">
      <button type="button" class="btn" id="rp-prev" ${prev?'':'disabled'} title="${prev?esc(prev.name):''}">‹ Previous</button>
      <button type="button" class="btn" id="rp-next" ${next?'':'disabled'} title="${next?esc(next.name):''}">Next ›</button>
      <button type="button" class="btn" id="rp-pin">${on?'★ In watchlist':'☆ Watchlist'}</button>
      <button type="button" class="btn" id="rp-card">Scorecard</button>
      <button type="button" class="btn rp-print" id="rp-print">Print / save PDF</button>
    </span>
  </div>

  ${DR.index&&DR.index[d.code]?`<div class="dr-tabs" role="tablist"><button type="button" role="tab" id="dr-deep" aria-selected="false">Deep-dive report · ${esc(DR.index[d.code].period_label)}</button><button type="button" role="tab" class="on" aria-selected="true">Board research summary</button></div>`:''}
  <header class="rp-cover">
    <div class="rp-cover-main">
      <span class="rp-eyebrow">RESEARCH REPORT · ${esc(shortT(base(d)).toUpperCase())}</span>
      <h1>${esc(d.name)}</h1>
      <div class="rp-cover-sub">${exch}${d.industry?' · '+esc(d.industry):d.sector?' · '+esc(d.sector):''}${d.cap_tier?' · '+esc(d.cap_tier)+'-cap':''}</div>
      <div class="rp-cover-meta">
        <span class="rp-tag rp-k-${k}">${RP_KIND[k]}</span>
        <span class="badge ${scr.cls||''}" title="${esc(scr.full||'')}">${esc(scr.lab||'')}</span>
        ${d.claim_grade?`<span class="cg ${CG[d.claim_grade]||'cg-none'}">claim: ${esc(d.claim_grade)}</span>`:''}
        ${d.verified?`<span class="rp-tag rp-ver-${esc(d.verified)}">verification: ${esc(d.verified)}</span>`:''}
        ${asme?'<span class="rp-tag rp-asme">ASME certified</span>':''}
        <span class="rp-dateline">On the board since ${rpDate(d.added_on)}</span>
      </div>
    </div>
    <div class="rp-cover-score">${rpScoreRing(d.final_score)}<span>${d.rubric==='v4'?'Score (v4 rubric)':'Composite score'}</span>
      <small>${d.rank?`Rank ${d.rank} of ${DATA.filter(x=>x.rank).length}`:d.screen==='auto'?'Auto-screen score':'Unranked'} · Tier ${d.tier||'—'}</small></div>
  </header>

  <div class="rp-kpis">
    ${kpi('Price', d.price?'₹'+fmtI(d.price):'—')}
    ${kpi('Market cap', '₹'+fmtI(d.market_cap_cr)+' cr')}
    ${kpi('P/E', fmt(d.pe))}
    ${kpi('ROCE', rpPct(d.roce_pct))}
    ${kpi('ROE', rpPct(d.roe_pct))}
    ${kpi('Promoter', rpPct(d.promoter_pct))}
    ${kpi('FII + DII', (nz(d.fii_pct)===null&&nz(d.dii_pct)===null)?'—':fmt(inst,2)+'%')}
    ${kpi('Shareholders', fmtI(d.num_shareholders))}
  </div>

  ${d.screen==='auto'?`<div class="rp-callout">This company was added by the daily auto-screen: it passes the board's gates and states a moat in its own words. The numbers are screener.in's and nobody has researched it by hand yet — read this report as a lead, not a verdict.</div>`:''}
  ${d.triage_verdict?`<div class="rp-callout warn"><b>Triage verdict:</b> ${esc(d.triage_verdict)}</div>`:''}

  <section class="rp-block rp-summary"><h2>Key takeaways</h2>${rpTakeaways(d)}</section>

  <div class="rp-cols">
    <div class="rp-main">
      ${rpBlock(1,'Business', rpPara(d.business) + (d.theme_detail&&d.theme_detail!==d.theme?`<p class="rp-muted">${esc(d.theme_detail)}</p>`:''))}
      ${rpBlock(2,'Moat and competitive position',
        rpPara(d.moat_note)
        + (d.pricing_power_note?`<h3>Pricing power</h3>${rpPara(d.pricing_power_note)}`:'')
        + (quals.length?`<div class="rp-quals">${quals.map(q=>`<span>${esc(q)}</span>`).join('')}</div>`:''))}
      ${rpBlock(3,'Import substitution',
        (d.import_sub_verdict?`<p class="rp-verdict rp-v-${esc(d.import_sub_verdict)}">Verdict: <b>${esc(d.import_sub_verdict)}</b></p>`:'') + rpPara(d.import_substitution))}
      ${rpBlock(4,'Why the market overlooks it', rpPara(d.why_obscure) + (d.liquidity_note?`<h3>Liquidity</h3>${rpPara(d.liquidity_note)}`:''))}
      ${rpBlock(5,'Financials and earnings trend',
        `<table class="rp-table">${fins}</table>`
        + (pat?`<h3>Net profit by period (₹ cr)</h3><div class="rp-pat-wrap">${pat}</div>${d.period_type?`<p class="rp-muted">${esc(d.period_type)}</p>`:''}`:'')
        + (d.pat_turn_note?rpPara(d.pat_turn_note):''))}
      ${rpBlock(6,'Capex and guidance',
        (capex?`<table class="rp-table">${capex}</table>`:'')
        + (flags.length?`<div class="rp-quals">${flags.map(f=>`<span>${f}</span>`).join('')}</div>`:'')
        + (d.cwip_note?rpPara(d.cwip_note):'')
        + (d.guidance_quote?`<blockquote class="rp-quote">“${esc(d.guidance_quote)}”${d.guidance_source?`<cite>${esc(d.guidance_source)}</cite>`:''}</blockquote>`:'')
        + (!capex&&!d.cwip_note&&!d.guidance_quote?'<p class="rp-muted">The capex and guidance pass has not been run for this company yet.</p>':''))}
      <section class="rp-block"><h2><span class="rp-n">7</span>Price action</h2>
        <div class="rp-chart" id="rp-chart"><p class="view-hint">Loading chart…</p></div>
        <div class="rp-stage" id="rp-stage"></div>
      </section>
      ${rpBlock(8,'Risks and red flags',
        rpPara(d.risk_note)
        + (warns.length?`<ul class="rp-warns">${warns.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:'')
        + (gates.length?`<h3>Board gates it does not clear</h3><ul class="rp-warns">${gates.map(g=>`<li>${esc(g)}</li>`).join('')}</ul>`:''), 'rp-risk')}
      ${rpBlock(9,'Verification and sources',
        (d.verified?`<p class="rp-verdict rp-ver-${esc(d.verified)}">Verification: <b>${esc(d.verified)}</b></p>`:'')
        + rpPara(d.verify_comment) + (d.data_note?`<p class="rp-muted">${esc(d.data_note)}</p>`:'')
        + ((d.evidence_sources||[]).length?`<p>Evidence: ${(d.evidence_sources||[]).map(u=>`<a class="lnk" href="${esc(u)}" target="_blank" rel="noopener">${/\.pdf/i.test(u)?'annual report':'screener.in profile'} ↗</a>`).join(' ')}</p>`:'')
        + (!d.verified&&!d.verify_comment&&!d.data_note&&!(d.evidence_sources||[]).length?'<p class="rp-muted">No separate verification pass is recorded for this company.</p>':''))}
    </div>

    <aside class="rp-side">
      <section class="rp-block"><h2>Scorecard</h2>
        ${pillarsFor(d).map(([l,key,mx])=>{ const v=nz(d[key]); return `<div class="rp-pill"><span>${l}</span><b>${v===null?'—':fmt(v)}<small>/${mx}</small></b><i><em style="width:${v===null?0:Math.max(0,Math.min(100,v/mx*100)).toFixed(1)}%"></em></i></div>`; }).join('')}
        <table class="rp-table rp-mini">
          ${rpRow('Raw score', fmt(d.score))}${rpRow('Confidence-adjusted', nz(d.adj_score)===null?'':fmt(d.adj_score))}
          ${rpRow('Risk penalty', nz(d.risk_penalty)===null?'':'−'+fmt(d.risk_penalty))}${rpRow('Final', `<b>${fmt(d.final_score)}</b>`)}
          ${rpRow('Data completeness', nz(d.completeness)===null?'':d.completeness+'%')}
        </table>
        ${pen.length?`<ul class="rp-pen">${pen.map(p=>`<li>${esc(p)}</li>`).join('')}</ul>`:''}
        ${d.score_rationale?`<p class="rp-muted">${esc(d.score_rationale)}</p>`:''}
      </section>
      <section class="rp-block"><h2>Ownership</h2>
        ${rpOwnership(d)}
        <table class="rp-table rp-mini">
          ${rpRow('Promoter', rpPct(d.promoter_pct)+rpChg(d.promoter_chg_1y_pct))}
          ${rpRow('FII', rpPct(d.fii_pct)+rpChg(d.fii_chg_1y_pct))}
          ${rpRow('DII', rpPct(d.dii_pct))}
          ${rpRow('Promoter pledge', nz(d.promoter_pledge_pct)===null?'':fmt(d.promoter_pledge_pct,2)+'%')}
          ${rpRow('Shareholders', fmtI(d.num_shareholders))}
        </table>
      </section>
      ${asme?`<section class="rp-block"><h2>ASME certificates</h2><table class="asme-certs-table rp-table rp-mini"><thead><tr><th>Type</th><th>First</th><th>Latest</th></tr></thead><tbody>${asmeCertRows(asme)}</tbody></table></section>`:''}
      <section class="rp-block rp-links"><h2>Go deeper</h2>
        <a class="lnk" href="${scrURL(d)}" target="_blank" rel="noopener">screener.in ↗</a>
        <a class="lnk" href="${scrURL(d)}#documents" target="_blank" rel="noopener">Annual reports &amp; filings ↗</a>
        ${exURL(d)?`<a class="lnk" href="${exURL(d)}" target="_blank" rel="noopener">${d.nse_code&&!/^\d+$/.test(d.nse_code)?'NSE':'BSE'} quote ↗</a>`:''}
        <a class="lnk" href="https://www.google.com/search?q=${encodeURIComponent(d.name+' India company news')}" target="_blank" rel="noopener">News ↗</a>
        <button type="button" class="lnk" id="rp-chart-open">Full chart</button>
      </section>
    </aside>
  </div>

  <footer class="rp-foot">
    Compiled automatically from this dashboard's research record for ${esc(d.name)} (numbers refreshed daily from screener.in; prices from the NSE / BSE end-of-day files).
    Report generated ${galDay(new Date().toISOString().slice(0,10))}. For research only — not investment advice.
  </footer>`;

  document.getElementById('rp-back').onclick=()=>{ RP.code=null; rpRenderLib(); window.scrollTo({top:0}); };
  const ddeep=document.getElementById('dr-deep');
  if(ddeep) ddeep.onclick=()=>{ DR.tab='deep'; rpRenderDoc(d.code); window.scrollTo({top:0}); };
  if(prev) document.getElementById('rp-prev').onclick=()=>openReport(prev.code);
  if(next) document.getElementById('rp-next').onclick=()=>openReport(next.code);
  document.getElementById('rp-pin').onclick=()=>{ togglePin(d.code); const b=document.getElementById('rp-pin'); b.textContent=WATCH.has(d.code)?'★ In watchlist':'☆ Watchlist'; };
  document.getElementById('rp-card').onclick=()=>{ CURRENT=list; openDrawer(d); };
  document.getElementById('rp-print').onclick=()=>window.print();
  document.getElementById('rp-chart-open').onclick=()=>openChart(d.code);
  syncURL();
  rpPaintChart(d);
  rpPaintStage(d);
}
function rpPaintChart(d){
  const box=document.getElementById('rp-chart'); if(!box) return;
  if(!GSERIES.has(d.code)){ galFetch(d.code).then(()=>{ if(RP.code===d.code && VIEW==='reports') rpPaintChart(d); }); return; }
  const ser=GSERIES.get(d.code), s=(ser&&ser.stats)||(GAL&&GAL.companies[d.code])||null;
  if(!ser || ser.missing || !ser.rows || !ser.rows.length){ box.innerHTML='<p class="view-hint">No price data for this company yet. It appears after the next daily update.</p>'; return; }
  const st=s && GAL_STATUS[s.status];
  box.innerHTML = candleSVG(ser.rows,{w:900,h:320,sessions:250,axis:true})
    + `<div class="rp-chart-stats">${[
        ['Last close', galPx(s&&s.last)], ['52-week high', galPx(s&&s.high52)], ['52-week low', galPx(s&&s.low52)],
        ['From 52-week high', s&&s.from_high_pct!=null?s.from_high_pct.toFixed(1)+'%':'—'], ['50-day avg', galPx(s&&s.dma50)], ['200-day avg', galPx(s&&s.dma200)],
        ['Trend', st?st[0]:'—'], ['As of', s&&s.asof?galDay(s.asof):'—']
      ].map(([k,v])=>`<div><span>${k}</span><b>${v}</b></div>`).join('')}</div>`;
}
function rpPaintStage(d){
  const box=document.getElementById('rp-stage'); if(!box) return;
  if(!BP.data){
    BP.loading = BP.loading || fetchJSON('/api/market/setups').catch(()=>({stocks:{},feed:[],counts:{},market_breakouts:[]})).then(j=>{ BP.data=j; bpControls(); });
    BP.loading.then(()=>{ if(RP.code===d.code && VIEW==='reports'){ rpPaintStage(d); const t=document.querySelector('.rp-summary'); if(t) t.innerHTML='<h2>Key takeaways</h2>'+rpTakeaways(d); } });
    return;
  }
  const st=BP.data.stocks && BP.data.stocks[d.code];
  if(!st){ box.innerHTML=''; return; }
  const lab=k=>{ const x=BP_STAGES.find(s=>s.k===k); return x?x.label:null; };
  const bits=[];
  if(lab(st.stage)) bits.push(`<span class="rp-tag rp-k-deep">Stage: ${lab(st.stage)}</span>`);
  if(st.ipo && lab(st.ipo.stage)) bits.push(`<span class="rp-tag rp-k-auto">IPO base: ${lab(st.ipo.stage)}</span>`);
  if(st.verge) bits.push('<span class="rp-tag rp-asme">On the verge of a breakout</span>');
  const kv=[['RS rating', st.rs!=null?st.rs:'—'], ['Momentum', st.mood||'—'], ['Pivot', st.pivot?galPx(st.pivot):'—'],
            ['Now vs pivot', st.now_vs_pivot!=null?(st.now_vs_pivot>0?'+':'')+fmt(st.now_vs_pivot)+'%':'—']];
  box.innerHTML=`<h3>Market view</h3><div class="rp-quals">${bits.join('')||'<span>No active setup</span>'}</div>
    <div class="rp-chart-stats">${kv.map(([k,v])=>`<div><span>${k}</span><b>${v}</b></div>`).join('')}</div>`;
}


/* ================================================================
   Deep-dive reports (scripts/deep_reports.py): quarterly forensic
   research reports. Numbers and valuation are computed by code from
   screener.in statements; the analysis is written from the company's
   annual report, earnings calls, presentation and rating rationale, with
   every claim labelled [F] fact / [MC] management claim / [AI] inference /
   [E] estimate and cited to its document.
   ================================================================ */
const DR={index:null, loading:null, cache:new Map(), tab:'deep', period:null};
/* mirrors backend/scripts/deep_report/sections.py; 'statements' and 'sources' are built here,
   'summary' holds the summary plus changes_since_last */
const DR_ORDER=[
  ['summary','Summary'],['executive_summary','Executive investment summary'],['company_history','Company history'],
  ['business_model','Understanding the business model'],['products','Product-level analysis'],['segments','Segment-wise revenue analysis'],
  ['revenue_bridge','Revenue bridge'],['export_domestic','Export vs domestic'],['customer_forensics','Customer forensics'],
  ['client_analysis','Global client analysis'],['order_book','Order book forensics'],['business_line_deep_dives','Business-line deep dives'],
  ['capacity','Capacity analysis'],['capex_forensics','Capex forensics'],['raw_materials','Raw material economics'],
  ['margin_analysis','Margin analysis'],['unit_economics','Unit economics'],['financial_forensics','Financial forensics'],
  ['statements','Financial statements'],['working_capital','Working capital forensics'],['balance_sheet_quality','Balance sheet quality'],
  ['accounting_quality','Accounting quality'],['group_structure_rpt','Subsidiaries, group structure and related parties'],
  ['management','Management and promoter analysis'],['competitive_landscape','Competitive landscape'],['moat','Competitive moat'],
  ['industry_structure','Industry structure'],['structural_themes','Structural themes'],['guidance_tracker','Management guidance tracker'],
  ['earnings_quality','Earnings quality'],['forecast','Forecast model'],['scenarios','Bull / base / bear scenarios'],
  ['valuation','Valuation'],['priced_in','What is already priced in'],['catalysts','Catalysts'],['risks_matrix','Risks matrix'],
  ['thesis_breakers','Thesis breakers'],['monitoring_dashboard','Quarterly monitoring dashboard'],['red_flag_checklist','Red-flag checklist'],
  ['management_questions','Twenty-five management questions'],['investment_scorecard','Investment scorecard'],
  ['final_thesis','Final investment thesis'],['final_conclusion','Final conclusion'],['quality_control','Quality control — second-pass review'],
  ['sources','Source appendix'],
];
const DR_LABEL={F:['F','Fact — directly evidenced by a document or the statements'],MC:['MC','Management claim — not independently verified'],
  AI:['AI','Analyst inference — the report\'s own interpretation'],E:['E','Estimate — the report\'s own calculation or projection']};

function drLoadIndex(){
  if(DR.index) return Promise.resolve(DR.index);
  DR.loading = DR.loading || fetchJSON('/api/reports').catch(()=>({})).then(j=>{ DR.index=j||{}; return DR.index; });
  return DR.loading;
}
function drFetch(code, period){
  const key=code+'|'+(period||'');
  if(!DR.cache.has(key)) DR.cache.set(key, fetchJSON('/api/reports/'+encodeURIComponent(code)+(period?'/'+encodeURIComponent(period):'')).catch(()=>null));
  return DR.cache.get(key);
}

/* text with [F]/[MC]/[AI]/[E] chips and "(AR2026 p.45)" citations linked to the document */
function drText(s, sources){
  let h=esc(s||'');
  h=h.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
  h=h.replace(/\[(F|MC|AI|E)\]/g,(m,k)=>`<abbr class="dr-lab dr-lab-${k.toLowerCase()}" title="${DR_LABEL[k][1]}">${k}</abbr>`);
  const ids=(sources||[]).map(x=>x.id).sort((a,b)=>b.length-a.length);
  if(ids.length){
    h=h.replace(/\(([^()]{2,80})\)/g,(m,inner)=>{
      const parts=inner.split(/\s*;\s*/);
      let linked=false;
      const out=parts.map(part=>{
        const id=ids.find(i=>part.startsWith(esc(i)));
        if(!id) return part;
        const src=sources.find(x=>x.id===id);
        const pg=(part.match(/p\.?\s*(\d+)/)||[])[1];
        const url=src && src.url ? src.url+(pg && /\.pdf/i.test(src.url)?'#page='+pg:'') : null;
        linked=true;
        return url?`<a class="dr-cite" href="${esc(url)}" target="_blank" rel="noopener" title="${esc(src.title||id)}">${part}</a>`:`<span class="dr-cite">${part}</span>`;
      });
      return linked?`<span class="dr-cites">(${out.join('; ')})</span>`:m;
    });
  }
  return h;
}
function drBlocks(blocks, sources){
  return (blocks||[]).map(b=>{
    if(b.type==='bullets') return `<ul class="dr-ul">${(b.items||[]).map(i=>`<li>${drText(i,sources)}</li>`).join('')}</ul>`;
    if(b.type==='table') return `<div class="dr-tablewrap">${b.caption?`<div class="dr-cap">${drText(b.caption,sources)}</div>`:''}<table class="dr-table">
        <thead><tr>${(b.columns||[]).map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>${(b.rows||[]).map(r=>`<tr>${r.map(c=>`<td>${drText(String(c==null?'':c),sources)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    if(b.type==='callout') return `<div class="dr-callout dr-${esc(b.tone||'info')}">${drText(b.text,sources)}</div>`;
    return `<p>${drText(b.text,sources)}</p>`;
  }).join('');
}
function drSectionHTML(sec, sources){
  return (sec.subsections||[]).map(s=>`<h3>${esc(s.title)}</h3>${drBlocks(s.blocks,sources)}`).join('');
}

/* ---------- numbers: tables and charts built from the quant pack ---------- */
const drN=(v,d=0)=>v==null||isNaN(v)?'—':(v<0?'−':'')+Math.abs(+v).toLocaleString('en-IN',{maximumFractionDigits:d,minimumFractionDigits:d});
const drP=(v,d=1)=>v==null||isNaN(v)?'—':(+v).toFixed(d)+'%';
function drGrid(cols, rows, opts){
  opts=opts||{};
  return `<div class="dr-tablewrap">${opts.caption?`<div class="dr-cap">${opts.caption}</div>`:''}<table class="dr-table dr-num">
    <thead><tr><th>${opts.first||''}</th>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead>
    <tbody>${rows.filter(r=>r && (r[1]||[]).some(v=>v!=null)).map(([lab,vals,f,cls])=>`<tr class="${cls||''}"><th>${lab}</th>${vals.map(v=>`<td>${(f||drN)(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function drBars(labels, series, opts){
  /* series: [{name, values, cls}] grouped bars, negatives below the axis */
  opts=opts||{};
  const W=720, H=opts.h||220, padB=30, padT=16, padL=6;
  const all=series.flatMap(s=>s.values).filter(v=>v!=null);
  if(all.length<2) return '';
  const mx=Math.max(0,...all), mn=Math.min(0,...all), span=(mx-mn)||1;
  const y=v=>padT+(mx-v)/span*(H-padT-padB), step=(W-padL)/labels.length, gw=step*0.72, bw=gw/series.length;
  let s=`<svg viewBox="0 0 ${W} ${H}" class="dr-chart" role="img" aria-label="${esc(opts.title||'chart')}"><line x1="0" x2="${W}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" class="dr-axis"/>`;
  labels.forEach((lab,i)=>{
    const x0=padL+i*step+(step-gw)/2;
    series.forEach((se,k)=>{
      const v=se.values[i]; if(v==null) return;
      const top=Math.min(y(0),y(v)), h=Math.max(1,Math.abs(y(v)-y(0)));
      s+=`<rect x="${(x0+k*bw+1).toFixed(1)}" y="${top.toFixed(1)}" width="${Math.max(1,bw-2).toFixed(1)}" height="${h.toFixed(1)}" rx="2" class="dr-bar ${se.cls}${v<0?' neg':''}"><title>${esc(se.name)} ${esc(lab)}: ${drN(v)}</title></rect>`;
    });
    if(labels.length<=14 || i%2===0) s+=`<text x="${(padL+i*step+step/2).toFixed(1)}" y="${H-10}" text-anchor="middle" class="dr-tick">${esc(String(lab).replace(/^([A-Z][a-z]{2}) 20(\d\d)$/,'$1 $2'))}</text>`;
  });
  return `<div class="dr-chartwrap">${s}</svg><div class="dr-legend">${series.map(se=>`<span><i class="dr-bar ${se.cls}"></i>${esc(se.name)}</span>`).join('')}</div></div>`;
}
function drStatements(q){
  const a=q.annual, d=q.derived, c=a.columns, qq=q.quarterly;
  const ttm=a.ttm;
  const plCols=ttm?[...c,'TTM']:c, withTTM=(arr,k)=>ttm?[...arr,ttm[k]]:arr;
  return `
    <h3>Profit & loss <span class="dr-muted">Rs crore · ${esc(q.basis)} · [F] screener.in</span></h3>
    ${drGrid(plCols,[
      ['Sales',withTTM(a.sales,'sales'),null,'b'],['EBITDA',withTTM(a.ebitda,'ebitda')],['EBITDA margin',withTTM(a.opm,'opm'),v=>drP(v,0)],
      ['Other income',withTTM(a.other_income,'other_income')],['Interest',withTTM(a.interest,'interest')],['Depreciation',withTTM(a.depreciation,'depreciation')],
      ['Profit before tax',withTTM(a.pbt,'pbt')],['Tax rate',ttm?[...a.tax_rate,null]:a.tax_rate,v=>drP(v,0)],['Net profit',withTTM(a.pat,'pat'),null,'b'],
      ['EPS (Rs)',withTTM(a.eps,'eps'),v=>drN(v,1)]])}
    ${drBars(c,[{name:'Sales',values:a.sales,cls:'s1'},{name:'Net profit',values:a.pat,cls:'s2'}],{title:'Sales and net profit'})}
    <h3>Balance sheet <span class="dr-muted">[F]</span></h3>
    ${drGrid(c,[['Equity (capital + reserves)',a.equity,null,'b'],['Borrowings',a.borrowings],['Other liabilities',a.other_liabilities],['Total assets',a.total_assets,null,'b'],
      ['Fixed assets',a.fixed_assets],['Capital work in progress',a.cwip],['Investments',a.investments],['Other assets',a.other_assets]])}
    <h3>Cash flow and earnings quality <span class="dr-muted">[F] / [E]</span></h3>
    ${drGrid(c,[['Cash from operations',a.cfo,null,'b'],['Cash from investing',a.cfi],['Cash from financing',a.cff],['Free cash flow',a.fcf,null,'b'],
      ['Capex (CFO − FCF) [E]',a.capex],['CFO / net profit [E]',d.cfo_pat,v=>v==null?'—':(+v).toFixed(2)+'x'],['Capex / sales [E]',d.capex_sales,v=>drP(v,0)]])}
    ${drBars(c,[{name:'Cash from operations',values:a.cfo,cls:'s1'},{name:'Net profit',values:a.pat,cls:'s2'}],{title:'Operating cash flow vs profit'})}
    <h3>Returns, leverage and working capital <span class="dr-muted">[F] / [E]</span></h3>
    ${drGrid(c,[['ROCE',a.roce,v=>drP(v,0),'b'],['ROE [E]',d.roe,v=>drP(v,0)],['Debt / equity [E]',d.debt_equity,v=>v==null?'—':(+v).toFixed(2)+'x'],
      ['Interest cover [E]',d.interest_cover,v=>v==null?'—':(+v).toFixed(1)+'x'],['Debtor days',a.debtor_days],['Inventory days',a.inventory_days],
      ['Days payable',a.payable_days],['Cash conversion cycle',a.ccc],['Working capital days',a.wc_days],['Fixed-asset turnover [E]',d.fixed_asset_turnover,v=>v==null?'—':(+v).toFixed(2)+'x']])}
    <p class="dr-muted">Growth [E]: ${Object.entries(d.growth||{}).map(([k,v])=>`${esc(k.replace(/_/g,' '))} ${drP(v,0)}`).join(' · ')||'—'} · 3-year CFO/profit ${d.cumulative&&d.cumulative.cfo_pat_3y!=null?d.cumulative.cfo_pat_3y+'x':'—'} · 5-year ${d.cumulative&&d.cumulative.cfo_pat_5y!=null?d.cumulative.cfo_pat_5y+'x':'—'}</p>
    ${qq&&qq.columns&&qq.columns.length?`<h3>Last ${qq.columns.length} results periods <span class="dr-muted">[F]</span></h3>
    ${drGrid(qq.columns,[['Sales',qq.sales,null,'b'],['Sales YoY',qq.sales_yoy,v=>drP(v,0)],['EBITDA',qq.ebitda],['EBITDA margin',qq.opm,v=>drP(v,0)],
      ['Other income',qq.other_income],['Net profit',qq.pat,null,'b'],['Profit YoY',qq.pat_yoy,v=>drP(v,0)],['EPS (Rs)',qq.eps,v=>drN(v,2)]])}
    ${drBars(qq.columns,[{name:'Sales',values:qq.sales,cls:'s1'},{name:'Net profit',values:qq.pat,cls:'s2'}],{title:'Quarterly sales and profit',h:190})}`:''}`;
}
function drShareholding(q){
  const sh=q.shareholding; if(!sh||!sh.columns||!sh.columns.length) return '';
  const cols=sh.columns.slice(-8), n=sh.columns.length;
  const row=(k,f)=>[k,(sh.rows[k]||[]).slice(n-cols.length),f];
  return `<h3>Shareholding pattern <span class="dr-muted">[F] screener.in</span></h3>${drGrid(cols,[row('Promoters',v=>drP(v,2)),row('FIIs',v=>drP(v,2)),row('DIIs',v=>drP(v,2)),row('Public',v=>drP(v,2)),row('No. of Shareholders')])}`;
}
function drValuation(q, price){
  const v=q.valuation||{}, dcf=v.dcf||{};
  let h=`<h3>Where the price sits today <span class="dr-muted">[F] / [E]</span></h3>
    <div class="dr-kpis">${[['Price',v.price?'₹'+drN(v.price,0):'—'],['Market cap',v.market_cap?'₹'+drN(v.market_cap)+' cr':'—'],[`P/E (${esc(v.earnings_basis||'TTM')})`,v.pe_ttm!=null?v.pe_ttm+'x':'—'],
      ['EV / EBITDA',v.ev_ebitda_ttm!=null?v.ev_ebitda_ttm+'x':'—'],['EV / sales',v.ev_sales_ttm!=null?v.ev_sales_ttm+'x':'—'],['Price / book',v.pb!=null?v.pb+'x':'—'],
      ['52-week range',v.low_52w&&v.high_52w?`₹${drN(v.low_52w)}–${drN(v.high_52w)}`:'—'],['From 52-week high',drP(v.from_high_pct,0)]].map(([k,x])=>`<div><span>${k}</span><b>${x}</b></div>`).join('')}</div>
    <p class="dr-muted">${esc(v.net_debt_note||'')}</p>`;
  if(!dcf.available){ return h+`<div class="dr-callout dr-warn">DCF not shown: ${esc(dcf.reason||'not enough data')}</div>`; }
  const sc=dcf.scenarios, inp=dcf.inputs;
  const bar=['bear','base','bull'].map(k=>({k,v:sc[k].per_share}));
  const maxv=Math.max(price||0,...bar.map(b=>b.v||0))*1.08||1;
  h+=`<h3>DCF fair value — bear / base / bull <span class="dr-muted">[E] computed, not a price target</span></h3>
    <div class="dr-fv">${bar.map(b=>`<div class="dr-fv-row"><span>${b.k[0].toUpperCase()+b.k.slice(1)}</span><i><em class="fv-${b.k}" style="width:${Math.max(0,(b.v||0)/maxv*100).toFixed(1)}%"></em></i><b>${b.v!=null?'₹'+drN(b.v,0):'—'}</b><small>${dcf.upside_pct[b.k]!=null?(dcf.upside_pct[b.k]>0?'+':'')+dcf.upside_pct[b.k]+'% vs price':''}</small></div>`).join('')}
      <div class="dr-fv-row dr-fv-price"><span>Price</span><i><em style="width:${((price||0)/maxv*100).toFixed(1)}%"></em></i><b>₹${drN(price,0)}</b><small>today</small></div></div>
    ${drGrid(['Bear','Base','Bull'],[
      ['Starting sales growth',['bear','base','bull'].map(k=>sc[k].assumptions.start_growth),v=>drP(v,1)],
      ['Growth by year 5',['bear','base','bull'].map(k=>sc[k].assumptions.growth_by_year5),v=>drP(v,1)],
      ['EBITDA margin',['bear','base','bull'].map(k=>sc[k].assumptions.ebitda_margin),v=>drP(v,1)],
      ['Enterprise value (Rs cr)',['bear','base','bull'].map(k=>sc[k].ev)],['Equity value (Rs cr)',['bear','base','bull'].map(k=>sc[k].equity_value)],
      ['Per share (Rs)',['bear','base','bull'].map(k=>sc[k].per_share),v=>drN(v,0),'b']],{first:'Scenario'})}
    ${drGrid(sc.base.projection.map(p=>'Year '+p.year),[['Sales growth',sc.base.projection.map(p=>p.growth),v=>drP(v,1)],['Sales',sc.base.projection.map(p=>p.sales)],
      ['EBITDA',sc.base.projection.map(p=>p.ebitda)],['Free cash flow to firm',sc.base.projection.map(p=>p.fcff),null,'b'],['Present value',sc.base.projection.map(p=>p.pv)]],{first:'Base case',caption:'Base-case projection (Rs crore)'})}
    <p class="dr-muted">Method: ${esc(dcf.method)} Inputs: WACC ${inp.wacc}% (risk-free ${inp.rf}% + beta ${inp.beta} × ERP ${inp.erp}% = cost of equity ${inp.cost_of_equity}%; debt ${inp.debt_weight}% at ${inp.cost_of_debt_post_tax}% post-tax), terminal growth ${inp.terminal_growth}%, tax ${inp.tax_rate}%, base margin ${inp.ebitda_margin_base}% (median of three years), starting growth ${inp.starting_growth_base}% (${esc(inp.growth_basis)}), D&A ${inp.da_pct_sales}% of sales, capex = ${esc(inp.capex_rule||'')} (${inp.fixed_asset_turnover}x), working capital ${inp.wc_days} days.</p>
    <h3>Sensitivity — base-case value per share <span class="dr-muted">[E]</span></h3>
    ${drGrid(dcf.sensitivity.g.map(g=>'Terminal g '+g+'%'),dcf.sensitivity.wacc.map((w,i)=>['WACC '+w+'%',dcf.sensitivity.values[i],v=>v==null?'—':'₹'+drN(v,0)]))}
    <h3>What the price already assumes — reverse DCF <span class="dr-muted">[E]</span></h3>
    <div class="dr-callout ${dcf.reverse_dcf.implied_5y_sales_cagr!=null&&dcf.reverse_dcf.implied_5y_sales_cagr>dcf.reverse_dcf.base_case_5y_cagr_equivalent+10?'dr-bad':'dr-info'}">
      ${dcf.reverse_dcf.implied_5y_sales_cagr!=null?`Today's price needs about <b>${dcf.reverse_dcf.implied_5y_sales_cagr}% a year</b> sales growth for five years (at the base margin), against <b>${dcf.reverse_dcf.base_case_5y_cagr_equivalent}%</b> in the base case.`:''} ${esc(dcf.reverse_dcf.note)}</div>`;
  return h;
}
function drFlags(q){
  const fl=q.red_flags||[]; if(!fl.length) return '';
  return `<h3>Rule-based checks from the statements</h3><ul class="dr-flags">${fl.map(f=>`<li class="fl-${f.level}"><b>${esc(f.title)}</b> ${esc(f.detail)} <abbr class="dr-lab dr-lab-${f.label.replace(/\W/g,'').toLowerCase()}">${f.label.replace(/\W/g,'')}</abbr></li>`).join('')}</ul>`;
}

function drRender(d, rep, entry){
  const doc=document.getElementById('rp-doc');
  const src=rep.sources||[], q=rep.quant, v=q.valuation||{};
  const secs=Object.fromEntries((rep.sections||[]).map(s=>[s.id,s]));
  const list=RP.list.length && RP.list.includes(d) ? RP.list : [...DATA].sort(RP_SORTS.score.f);
  const i=list.indexOf(d), prev=list[i-1], next=list[i+1], on=WATCH.has(d.code);
  const periods=rep.periods||[rep.period];
  const fv=entry&&entry.fair_value;
  const sm=rep.summary||{};
  const parts=[];
  const add=(id,title,html)=>{ if(html && html.trim()) parts.push([id,title,html]); };
  add('summary','Summary',`
    ${sm.one_line?`<p class="dr-lede">${drText(sm.one_line,src)}</p>`:''}
    <div class="dr-sumgrid">
      <div class="dr-sum good"><h4>The bull case</h4><ul>${(sm.bull_points||[]).map(x=>`<li>${drText(x,src)}</li>`).join('')}</ul></div>
      <div class="dr-sum bad"><h4>The bear case</h4><ul>${(sm.bear_points||[]).map(x=>`<li>${drText(x,src)}</li>`).join('')}</ul></div>
      <div class="dr-sum watch"><h4>Watch next quarter</h4><ul>${(sm.watch_points||[]).map(x=>`<li>${drText(x,src)}</li>`).join('')}</ul></div>
    </div>
    ${secs.changes_since_last?`<h3>What changed since the last report</h3>${drSectionHTML(secs.changes_since_last,src)}`:''}`);
  DR_ORDER.forEach(([id,title])=>{
    if(id==='summary'||id==='sources') return;
    if(id==='statements') return add(id,title,drStatements(q));
    if(id==='valuation') return add(id,(secs.valuation&&secs.valuation.title)||title,drValuation(q,v.price)+(secs.valuation?`<h3 class="dr-sep">Interpretation</h3>${drSectionHTML(secs.valuation,src)}`:''));
    const s=secs[id]; let html=s?drSectionHTML(s,src):'';
    if(id==='management') html+=drShareholding(q);
    if(id==='red_flag_checklist') html=drFlags(q)+html;
    add(id,(s&&s.title)||title,html);
  });
  add('sources','Sources',`<ol class="dr-sources">${src.map(s=>`<li><b>${esc(s.id)}</b> — ${s.url?`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title||s.id)} ↗</a>`:esc(s.title||'')}${s.date?` · ${esc(s.date)}`:''}</li>`).join('')}</ol>
    <p class="dr-muted">Labels: <abbr class="dr-lab dr-lab-f">F</abbr> fact · <abbr class="dr-lab dr-lab-mc">MC</abbr> management claim, not verified · <abbr class="dr-lab dr-lab-ai">AI</abbr> analyst inference · <abbr class="dr-lab dr-lab-e">E</abbr> estimate. Numbers from screener.in (${esc(rep.basis)}); analysis generated ${esc((rep.generated_at||'').slice(0,10))}${rep.model_id?` with ${esc(rep.model_id)}`:''} from the documents listed, checked by rules against invented figures — but it is machine-written: verify anything you act on against the source.</p>`);

  doc.innerHTML=`
  <div class="rp-toolbar">
    <button type="button" class="theme-back" id="rp-back">‹ All reports</button>
    <span class="rp-pos">${i>=0?`${i+1} of ${list.length}`:''}</span>
    <span class="rp-tools">
      <button type="button" class="btn" id="rp-prev" ${prev?'':'disabled'} title="${prev?esc(prev.name):''}">‹ Previous</button>
      <button type="button" class="btn" id="rp-next" ${next?'':'disabled'} title="${next?esc(next.name):''}">Next ›</button>
      <button type="button" class="btn" id="rp-pin">${on?'★ In watchlist':'☆ Watchlist'}</button>
      ${periods.length>1?`<select class="gal-select dr-period" id="dr-period" aria-label="Report quarter">${periods.map(p=>`<option value="${esc(p)}"${p===rep.period?' selected':''}>${esc(p.replace(/^FY(\d\d)-(Q\d)$/,'$2 FY$1'))}${p===periods[0]?' (latest)':''}</option>`).join('')}</select>`:''}
      ${entry&&entry.pdf&&rep.period===entry.period?`<a class="btn" href="${esc(entry.pdf)}" target="_blank" rel="noopener">Download PDF</a>`:''}
      <button type="button" class="btn rp-print" id="rp-print">Print / save PDF</button>
    </span>
  </div>
  <div class="dr-tabs" role="tablist"><button type="button" role="tab" class="on" aria-selected="true">Deep-dive report · ${esc(rep.period_label)}</button><button type="button" role="tab" id="dr-board" aria-selected="false">Board research summary</button></div>
  <header class="rp-cover dr-cover">
    <div class="rp-cover-main">
      <span class="rp-eyebrow">DEEP-DIVE RESEARCH REPORT · ${esc(rep.period_label)} · ${esc(shortT(base(d)).toUpperCase())}</span>
      <h1>${esc(rep.name||d.name)}</h1>
      <div class="rp-cover-sub">${[d.nse_code&&!/^\d+$/.test(d.nse_code)?'NSE: '+esc(d.nse_code):'',d.bse_code?'BSE: '+esc(d.bse_code):''].filter(Boolean).join(' · ')||esc(d.code)} · results to ${esc(rep.results_quarter)} · ${esc(rep.basis)}</div>
      <div class="rp-cover-meta"><span class="rp-dateline">Updated ${esc((rep.generated_at||'').slice(0,10))} · ${src.length} sources · refreshed automatically after each quarter's results</span></div>
    </div>
    <div class="dr-cover-fv">
      <span>DCF fair value [E]</span>
      ${fv&&rep.period===entry.period?`<div class="dr-cover-trio">${['bear','base','bull'].map(k=>`<div><small>${k}</small><b>${fv[k]!=null?'₹'+drN(fv[k],0):'—'}</b></div>`).join('')}</div>`:(v.dcf&&v.dcf.available?`<div class="dr-cover-trio">${['bear','base','bull'].map(k=>`<div><small>${k}</small><b>₹${drN(v.dcf.scenarios[k].per_share,0)}</b></div>`).join('')}</div>`:'<b>—</b>')}
      <small>Price ₹${drN(v.price,0)} · not a price target, no rating</small>
    </div>
  </header>
  <div class="dr-layout">
    <nav class="dr-toc" aria-label="Report sections"><b>Contents</b><ol>${parts.map(([id,t])=>`<li><a href="#dr-${id}" data-dr="${id}">${esc(t)}</a></li>`).join('')}</ol></nav>
    <div class="dr-body">${parts.map(([id,t,h],n)=>`<section class="rp-block dr-sec" id="dr-${id}"><h2><span class="rp-n">${n+1}</span>${esc(t)}</h2>${h}</section>`).join('')}
      <footer class="rp-foot">For research only — not investment advice, not a recommendation to buy or sell, and not from a SEBI-registered research analyst. Fair values are mechanical DCF outputs under the stated assumptions, not price targets.</footer>
    </div>
  </div>`;
  document.getElementById('rp-back').onclick=()=>{ RP.code=null; DR.period=null; rpRenderLib(); window.scrollTo({top:0}); };
  if(prev) document.getElementById('rp-prev').onclick=()=>{ DR.period=null; openReport(prev.code); };
  if(next) document.getElementById('rp-next').onclick=()=>{ DR.period=null; openReport(next.code); };
  document.getElementById('rp-pin').onclick=()=>{ togglePin(d.code); document.getElementById('rp-pin').textContent=WATCH.has(d.code)?'★ In watchlist':'☆ Watchlist'; };
  document.getElementById('rp-print').onclick=()=>window.print();
  document.getElementById('dr-board').onclick=()=>{ DR.tab='board'; rpRenderDoc(d.code); window.scrollTo({top:0}); };
  const sel=document.getElementById('dr-period');
  if(sel) sel.onchange=()=>{ DR.period=sel.value===periods[0]?null:sel.value; rpRenderDoc(d.code); };
  doc.querySelectorAll('[data-dr]').forEach(a=>a.onclick=e=>{ e.preventDefault(); const t=document.getElementById('dr-'+a.dataset.dr); if(t) window.scrollTo({top:t.getBoundingClientRect().top+scrollY-80,behavior:'smooth'}); });
  syncURL();
}
function drOpen(d){
  const doc=document.getElementById('rp-doc');
  document.getElementById('rp-lib').hidden=true;
  document.getElementById('reports-heading').hidden=true;
  doc.hidden=false;
  const entry=DR.index&&DR.index[d.code];
  doc.innerHTML='<p class="view-hint">Loading the deep-dive report…</p>';
  drFetch(d.code, DR.period).then(rep=>{
    if(RP.code!==d.code || VIEW!=='reports') return;
    if(!rep){ DR.tab='board'; rpRenderDoc(d.code); return; }
    drRender(d, rep, entry);
    document.body.dataset.drReady='1';
  });
}


initUserMenu();
watchSync();

BUSY=true; buildColPop(); applyState(); BUSY=false;
setView(VIEW,{keep:true});

}

boot().catch(function(err){
  console.error(err);
  if (err && err.message === "Login required") return;   /* already on its way to /login */
  document.body.innerHTML =
    '<div style="max-width:640px;margin:80px auto;padding:24px;font:15px/1.5 system-ui;text-align:center">'
    + '<h2 style="margin-bottom:8px">Could not load the screener</h2>'
    + '<p style="color:#666">The backend API did not respond as expected ('
    + String((err && err.message) || err).replace(/[&<>]/g, function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c];})
    + '). Make sure the API is running and reachable at <code>/api/companies</code>, then reload.</p></div>';
});
