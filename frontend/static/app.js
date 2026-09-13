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
  const r = await fetch(url);
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
  'v4-triage':'triage','v5-new':'v5 new sweep','v6-new':'v6 new sectors','v7-new':'v7 depth expansion','user':'requested added on request','deep-sweep':'Deep Sweep'}[s]||'';}
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

const TG = {overhang:false, heavycap:false, guide15:false, guideany:false, turn:false, haslens:false, ipo:false, asme:false,
            nolens:false, watch:false,
            nosme:false, nopledge:false, realsub:false, cheap:false, ongate:false};
/* The watchlist is the one piece of state that belongs to the reader rather than to the
   data, so it is kept in localStorage and survives a reload. */
let WATCH = new Set();
try{ WATCH = new Set(JSON.parse(localStorage.getItem('dms.watch')||'[]')); }catch(e){}
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
const VIEWS=['overview','themes','market','watchlist','companies','filters','gallery','method'];
const NAV={'overview-link':'overview','themes-link':'themes','market-link':'market','watchlist-link':'watchlist',
           'companies-link':'companies','filters-link':'filters','gallery-link':'gallery'};
const LENSES=['overhang','heavycap','guide15','guideany','turn','haslens','ipo','asme'];
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
  {k:'rank',  l:'#',        f:d=>d.rank},
  {k:'name',  l:'Company',  f:d=>`<a class="nm" href="${scrURL(d)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Open on screener.in">${hl(d.name)} <span class="ext">↗</span></a><span class="tc">${hl(d.code||'')}${d.tier===2?' · Tier 2':''}${d.added_on===BUILD_NEW?' · <b class="newbadge">NEW</b>':''}${ASME_BY_CODE[d.code]?' · <b class="asme-tag" title="Holds an active ASME certificate">ASME</b>':''}</span>`},
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
     <div class="thm" style="margin-bottom:6px">${base(d)} · ${d.final_score===null?'unranked':'rank '+d.rank}<span class="tierbadge">${d.tier===1?'Tier 1 · institutions present':'Tier 2 · no institutions yet'}</span></div>
     <h2 style="margin:0 0 3px;font-size:19px">${hl(d.name)}</h2>
     <div class="tc">${d.code||''}${d.industry?' · '+esc(d.industry):''}</div>
     <div style="margin-top:9px">
       <span class="badge ${SCREENS[d.screen].cls}" title="${SCREENS[d.screen].full}">${SCREENS[d.screen].lab}</span>
       ${d.claim_grade?`<span class="cg ${CG[d.claim_grade]||'cg-none'}" style="margin-right:4px">claim: ${esc(d.claim_grade)}</span>`:''}
       ${d.has_lens_data?sigBadges(d,false):'<span class="pend" style="font-size:11px">capex / guidance pass pending</span>'}
     </div>
     <div class="links">
       <a class="lnk" href="${scrURL(d)}" target="_blank" rel="noopener">screener.in ↗</a>
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
function saveWatch(){ try{ localStorage.setItem('dms.watch', JSON.stringify([...WATCH])); }catch(e){} }
function togglePin(code){
  if(!code) return;
  if(WATCH.has(code)) WATCH.delete(code); else WATCH.add(code);
  saveWatch(); render();
}
function syncTgButtons(){
  document.querySelectorAll('[data-tg]').forEach(b=>b.classList.toggle('on', !!TG[b.dataset.tg]));
  document.querySelectorAll('[data-tile]').forEach(b=>b.classList.toggle('on', b.dataset.tile===TILE));
}
/* ---------- my watchlist ----------
   A shelf above the board for the names you pinned, so returning to them is a
   glance rather than a hunt. It replaces the floating tray the pins used to get:
   same actions, but it holds still and shows the numbers.

   The pins are localStorage, which means per-browser and per-device. That is the
   one real limit, so the section says so and hands you a link that carries the
   list to another machine. */
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
        <button class="btn" id="wllink" title="Copies a link that carries this watchlist — open it on another device to get the same pins">&#128279; Copy watchlist link</button>
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
  document.getElementById('wllink').onclick=async ()=>{
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
  if(TILE) p.set('tile',TILE);
  if(QUERY) p.set('q',QUERY);
  if(NEWSINCE) p.set('new',NEWSINCE);
  if(activeThemes.size<THEMES.length) p.set('t',[...activeThemes].map(t=>THEMES.indexOf(t)).join(','));
  const tg=Object.keys(TG).filter(k=>TG[k]); if(tg.length) p.set('tg',tg.join(','));
  const sl=SL.map((s,i)=>[s,i]).filter(([s])=> s.inv ? s.v<s.max : s.v>s.min).map(([s,i])=>i+':'+s.v);
  if(sl.length) p.set('sl',sl.join(','));
  if(sortKey!=='final_score'||sortDir!==-1) p.set('sort',sortKey+':'+sortDir);
  if(HIDDEN.size) p.set('hide',[...HIDDEN].join(','));
  if(WATCH.size) p.set('w',[...WATCH].join(','));
  const h=p.toString();
  try{ history.replaceState(null,'',location.pathname+location.search+(h?'#'+h:'')); }catch(e){}
}
function applyState(){
  const raw=location.hash.replace(/^#/,''); if(!raw) return;
  const p=new URLSearchParams(raw);
  const get=k=>p.get(k);
  if(VIEWS.includes(get('v'))) VIEW=get('v');
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
  if(j.latest_session){ up.innerHTML=`<i></i>Updated · ${galDay(j.latest_session)}`; up.hidden=false; }
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
      <button class="btn" id="cm-pin" data-code="${esc(code)}" type="button">${on?'★ In watchlist':'☆ Add to watchlist'}</button>
      <a class="btn" href="${scrURL(d)}" target="_blank" rel="noopener">screener.in ↗</a>
    </div>
    <p class="cm-src">${ser && ser.source==='live' ? 'Fetched live because this company joined the board after the last daily update; tonight\'s run replaces it with exchange data.' : 'Source: NSE / BSE end-of-day bhavcopy, refreshed automatically every trading day.'} Prices are not adjusted for splits or bonus issues.</p>`);
  document.getElementById('cm-card').onclick=()=>{ closeModal(); CURRENT=GAL?galRows():[d]; openDrawer(d); };
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

BUSY=true; buildColPop(); applyState(); BUSY=false;
setView(VIEW,{keep:true});

}

boot().catch(function(err){
  console.error(err);
  document.body.innerHTML =
    '<div style="max-width:640px;margin:80px auto;padding:24px;font:15px/1.5 system-ui;text-align:center">'
    + '<h2 style="margin-bottom:8px">Could not load the screener</h2>'
    + '<p style="color:#666">The backend API did not respond as expected ('
    + String((err && err.message) || err).replace(/[&<>]/g, function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c];})
    + '). Make sure the API is running and reachable at <code>/api/companies</code>, then reload.</p></div>';
});
