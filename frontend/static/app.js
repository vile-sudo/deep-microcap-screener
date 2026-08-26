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
  const [DATA, META] = await Promise.all([
    fetchJSON("/api/companies"),
    fetchJSON("/api/meta"),
  ]);

  const BUILD = META.build || {version: "\u2014", built: "\u2014", history: []};
  const SCREENS = META.screens || {};
  const SHORT = META.short || {};
  const BUILD_STAMP = META.build_stamp;
  const CANDIDATES = META.candidates || [];
  const BUILD_NEW = META.build_new;

  /* Keep the header build stamp and page chrome sourced from the API
     instead of hand-edited on every publish. */
  document.title = "Deep Microcap Screener \u2014 " + DATA.length + " companies";
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
  })();

const THEMES = [...new Set(DATA.map(d=>(d.theme||'').split(' / ')[0]))];
/* search index is built here rather than shipped in the data — same result, ~400KB smaller file */
const QFIELDS=['name','code','nse_code','bse_code','theme','sector','industry','business','moat_note',
  'import_substitution','why_obscure','risk_note','pricing_power_note','guidance_quote','cwip_note',
  'claim_grade','triage_verdict','liquidity_note','verify_comment','theme_detail'];
DATA.forEach(d=>{
  d._q = (QFIELDS.map(k=>d[k]||'').join(' ') + ' ' + (d.warnings||[]).join(' ')
          + ' ' + SCREENS_LABEL(d.screen)).toLowerCase();
});
function SCREENS_LABEL(s){return {'v3-deep':'v3 deep','v3-screen':'v3 screen','v4-moat':'v4 moat monopoly',
  'v4-triage':'triage','v5-new':'v5 new sweep','v6-new':'v6 new sectors','v7-new':'v7 depth expansion','user':'requested added on request'}[s]||'';}
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

/* ---------- stat tiles ---------- */
(function(){
  const n = k => DATA.filter(d=>d[k]).length;
  const pending = DATA.filter(d=>!d.has_lens_data).length;
  const t = [
    [String(DATA.length),'Companies on the board','89 v3 · 58 v4 · 11 triage · 144 from three 2026 sweeps · 8 requested',null,'reset'],
    ['18','Themes covered','Every one now has real depth — the thinnest holds 5 names, the largest 64',"var(--s6)"],
    [String(n('capex_overhang')),'&#9873; High P/E + heavy CWIP','P/E &gt; 40 and CWIP &#8805; 15% of net block',"var(--crit)",'overhang'],
    [String(n('guidance_over15')),'&#9650; Management guides &gt; 15%','A further '+(n('guidance_flag')-n('guidance_over15'))+' made an unquantified forward statement',"var(--good-ink)",'guide15'],
    [String(n('pat_turnaround')),'&#8635; PAT turned positive','Latest period profitable after a loss in the prior three',"var(--s1)",'turn'],
    [String(pending),'Awaiting the capex pass','CWIP, guidance and quarterly PAT not yet pulled for these',"var(--muted)",'nolens'],
  ];
  /* Every tile that maps onto a filter is a button - the number you just read is the
     quickest route to the names behind it. */
  document.getElementById('tiles').innerHTML = t.map(([v,k,nn,c,tg])=>{
    const tag = tg ? 'button' : 'div';
    const at  = tg ? ` class="tile" data-tile="${tg}" title="${tg==='reset'?'Clear every filter and show the whole board':'Filter the board down to these names'}"` : ' class="tile"';
    /* A bare count leaves the reader doing arithmetic against 310. The share bar puts
       the denominator back without spending a second number on it. The first tile IS
       the denominator, and the themes tile counts themes rather than companies, so
       neither gets one. */
    const num = Number(String(v).replace(/[^0-9.]/g,''));
    const share = (tg && tg!=='reset' && isFinite(num) && DATA.length) ? (num/DATA.length)*100 : null;
    const bar = share===null ? '' : `<div class="tbar" title="${num} of ${DATA.length} companies — ${share.toFixed(share<1?1:0)}%"><i style="width:${Math.max(share,1.2).toFixed(1)}%;background:${c||'var(--ink2)'}"></i></div>`;
    return `<${tag}${at}><div class="v"${c?` style="color:${c}"`:''}>${v}</div><div class="k">${k}</div>${bar}<div class="n">${nn}</div></${tag}>`;
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

const TG = {overhang:false, heavycap:false, guide15:false, guideany:false, turn:false, haslens:false, ipo:false,
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
/* ---------- filter chips ---------- */
const themeBox = document.getElementById('themes');
THEMES.forEach(t=>{
  const b=document.createElement('button');
  b.className='chip on'; b.dataset.t=t;
  b.innerHTML=`<span class="mk"></span>${shortT(t)}`;
  b.onclick=()=>{
    if(activeThemes.has(t)&&activeThemes.size===THEMES.length){activeThemes=new Set([t]);}
    else if(activeThemes.has(t)){activeThemes.delete(t); if(!activeThemes.size)activeThemes=new Set(THEMES);}
    else activeThemes.add(t);
    syncChips(); render();
  };
  themeBox.appendChild(b);
});
function syncChips(){
  [...themeBox.children].forEach(c=>c.classList.toggle('on', activeThemes.has(c.dataset.t)));
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

document.querySelectorAll('[data-tg]').forEach(b=>{
  b.onclick=()=>{ TG[b.dataset.tg]=!TG[b.dataset.tg]; b.classList.toggle('on',TG[b.dataset.tg]); render(); };
});
/* ---------- search ---------- */
const qIn=document.getElementById('q'), qWrap=document.getElementById('swrap');
function setQuery(v){
  qIn.value=v; QUERY=v.trim().toLowerCase();
  QTERMS = QUERY ? QUERY.split(/\s+/).filter(Boolean) : [];
  qWrap.classList.toggle('has', !!QUERY);
  render();
}
/* a keystroke used to rebuild all 310 rows and redraw all three charts; at that
   size the page visibly stalls mid-word, so settle briefly before re-rendering */
let qTimer=0;
qIn.oninput=()=>{ clearTimeout(qTimer); qTimer=setTimeout(()=>setQuery(qIn.value),110); };
document.getElementById('qclear').onclick=()=>{setQuery(''); qIn.focus();};
addEventListener('keydown',e=>{
  if(e.key==='/' && document.activeElement!==qIn){ e.preventDefault(); qIn.focus(); }
  if(e.key==='Escape' && document.activeElement===qIn && QUERY){ setQuery(''); }
});
const SUGG=['torpedo','CRGO','NABL','DRDO','sole Indian','debunked','import substitution',
            'switchgear','forging','submarine','data centre','railway','pricing power','SME'];
document.getElementById('sugg').innerHTML =
  SUGG.map(s=>`<button data-q="${s}">${s}</button>`).join('') +
  `<button data-new="2026-08-20">&#9733; new in this build (${DATA.filter(d=>d.added_on==='2026-08-20').length})</button>`;
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
  if(QTERMS.length && !QTERMS.every(w=>d._q.includes(w))) return false;
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
  {k:'name',  l:'Company',  f:d=>`<a class="nm" href="${scrURL(d)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Open on screener.in">${hl(d.name)} <span class="ext">↗</span></a><span class="tc">${hl(d.code||'')}${d.tier===2?' · Tier 2':''}${d.added_on===BUILD_NEW?' · <b class="newbadge">NEW</b>':''}</span>`},
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
function render(){
  if(BUSY) return;                       /* batched updates render once, at the end */
  buildHead();
  const cols=VIS();
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
    ? `<b>${rows.length}</b> match${rows.length===1?'':'es'} for “${esc(QUERY)}”`
    : (NEWSINCE?`<b>${rows.length}</b> added in this build`:`${DATA.length} companies · <b>/</b> to search · <b>?</b> for shortcuts`);
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
tbtn.onclick=()=>{
  const dark=document.documentElement.dataset.theme==='dark';
  document.documentElement.dataset.theme=dark?'light':'dark';
  tbtn.textContent=dark?'◐ Dark':'◑ Light';
  render();
};
if(matchMedia('(prefers-color-scheme: dark)').matches){ document.documentElement.dataset.theme='dark'; tbtn.textContent='◑ Light'; }

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
  document.querySelectorAll('[data-tile]').forEach(b=>b.classList.toggle('on', !!TG[b.dataset.tile]));
}
/* ---------- my watchlist ----------
   A shelf above the board for the names you pinned, so returning to them is a
   glance rather than a hunt. It replaces the floating tray the pins used to get:
   same actions, but it holds still and shows the numbers.

   The pins are localStorage, which means per-browser and per-device. That is the
   one real limit, so the section says so and hands you a link that carries the
   list to another machine. */
let WLOPEN = true;
try{ WLOPEN = localStorage.getItem('dms.wlopen') !== '0'; }catch(e){}
let WLARM = 0;   /* Clear is two-step: a stray click must not take the whole list */

function renderWatchlist(){
  const box=document.getElementById('wl');
  const codes=[...WATCH];
  document.getElementById('wcount').textContent = codes.length ? '· '+codes.length : '';
  document.getElementById('watchbtn').disabled = !codes.length && !TG.watch;

  if(!codes.length){
    WLARM=0;
    box.innerHTML = `<p class="wlhint"><b>My watchlist</b> — nothing pinned yet. `
      + `Click the ☆ beside any company, in the table or on its card, to keep it here.</p>`;
    return;
  }

  const byCode={}; DATA.forEach(d=>{ byCode[d.code]=d; });
  const head=`<div class="wlhead">
      <button class="wlcar" id="wlcar" aria-expanded="${WLOPEN}" title="${WLOPEN?'Collapse':'Expand'} the watchlist">${WLOPEN?'▼':'►'}</button>
      <h2>My watchlist <span class="tc">${codes.length} ${codes.length===1?'company':'companies'}</span></h2>
      <div class="wlacts">
        <button class="btn${TG.watch?' on':''}" id="wlonly" title="Filter the board below to these names">Show only these</button>
        <button class="btn" id="wlcmp" title="Put them side by side (c)">Compare ${codes.length}</button>
        <button class="btn" id="wllink" title="Copies a link that carries this watchlist — open it on another device to get the same pins">&#128279; Copy watchlist link</button>
        <button class="btn" id="wlclr">${WLARM?'Click again to clear':'Clear'}</button>
      </div>
    </div>`;

  let body='';
  if(WLOPEN){
    body = `<div class="wltab"><table>
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
  }
  box.innerHTML = head + body;

  document.getElementById('wlcar').onclick=()=>{
    WLOPEN=!WLOPEN;
    try{ localStorage.setItem('dms.wlopen', WLOPEN?'1':'0'); }catch(e){}
    renderWatchlist();
  };
  document.getElementById('wlonly').onclick=()=>{ TG.watch=!TG.watch; syncTgButtons(); render(); };
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
    tr.onclick=()=>{ const d=byCode[tr.dataset.wl]; if(d) openDrawer(d); };
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

/* ---------- reset: now clears the search, tiers and the NEW pill too ---------- */
document.getElementById('reset').onclick=()=>{
  BUSY=true;
  SL.forEach((s,i)=>{ s.v = s.inv? s.max : s.min; const el=document.getElementById('si'+i); el.value=s.v; el.dispatchEvent(new Event('input')); });
  Object.keys(TG).forEach(k=>TG[k]=false);
  activeThemes=new Set(THEMES); syncChips();
  NEWSINCE=null; const nb=document.querySelector('[data-new]'); if(nb) nb.style.borderStyle='dashed';
  sortKey='final_score'; sortDir=-1;
  syncTgButtons();
  BUSY=false;
  setQuery('');
};

/* ---------- shareable view state ----------
   Every choice a reader makes goes into the URL fragment, so a filtered view can
   be sent to someone else (or bookmarked) and reopened exactly as it was. */
function syncURL(){
  const p=new URLSearchParams();
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
        universe for names not yet on this board, queuing anything whose sector could plausibly
        be an import-substitution story. An empty queue means nothing new turned up —
        not that the pipeline failed.</p>`);
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
      <p class="mp">Symbols that appeared on the NSE mainboard, Emerge/SME or BSE lists since the
      last snapshot, in a sector where import substitution is at least possible. The sector is a
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
document.getElementById('tiles').onclick=e=>{
  const b=e.target.closest('[data-tile]'); if(!b) return;
  if(b.dataset.tile==='reset'){ document.getElementById('reset').click(); return; }
  TG[b.dataset.tile]=!TG[b.dataset.tile]; syncTgButtons(); render();
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
  let stored=null; try{ stored=localStorage.getItem('dms.theme'); }catch(e){}
  if(stored==='dark'||stored==='light'){
    document.documentElement.dataset.theme=stored;
    tbtn.textContent = stored==='dark' ? '◑ Light' : '◐ Dark';
  }
  const flip=tbtn.onclick;
  tbtn.onclick=()=>{ flip(); try{ localStorage.setItem('dms.theme',document.documentElement.dataset.theme); }catch(e){} };
})();

BUSY=true; buildColPop(); applyState(); BUSY=false;
render();

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
