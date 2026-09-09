"use strict";
/* ================================================================
   ASME Certified — a standalone reference list, unrelated to the
   board's own 382 companies.

   Source, step 1: ASME's own "CA Connect" certificate-holder directory
   (https://caconnect.asme.org/directory/), searched for Country = India,
   Status = Active. Pulled as a one-time snapshot on 2026-09-09 via the
   directory's own search API — 638 certificate records, collapsed to
   389 companies (a company can hold several certificate types, or the
   same type at several plants; those are merged into the `certs` list
   rather than repeated).

   Source, step 2: those 389 names were then matched by (normalized)
   name against NSE's own listed-equity feeds — the main board
   (nsearchives.nseindia.com/content/equities/EQUITY_L.csv) and the SME
   board (.../emerge/corporates/content/SME_EQUITY_L.csv) — and BSE's
   active-scrip API (api.bseindia.com/BseIndiaAPI/api/ListofScripData),
   all pulled the same day. 40 names survive here; the other 349 —
   almost entirely Private Limited companies and LLPs, which cannot be
   listed at all — were dropped. `exch` records which board(s) matched
   and `symbol` is that listing's ticker.

   A second, deliberately paranoid pass re-checked every dropped name
   for near-misses a strict match would swallow: an "(I)" ASME wrote
   where the exchange spelled out "India" (caught Patels Airtemp), and
   ASME rows that are one specific plant/division of an already-listed
   parent rather than a separately incorporated company — recognized
   only where the ASME name itself says so explicitly (e.g. "Dee
   Development Engineers Limited, Plant-2", "TATA STEEL LTD. - Growth
   Shop", or a division named "(A Division of Thermax Limited)").
   Those merge into the parent's single row rather than adding a
   duplicate card; Thermax Limited's `certs` here is the union of its
   own row and its two Chemicals & Hydrogenation Group divisions.
   Token-overlap and edit-distance passes surfaced nothing else real —
   just noise from generic words ("Energy", "Raj", "Creative") shared
   with unrelated listed companies, which was discarded.

   This file is intentionally NOT wired into the API, the database, or
   DATA/THEMES in app.js. Nothing here was cross-checked against the
   board above, nothing here is scored, and the moat rubric does not
   apply to any name on this list — it is exactly what ASME's directory
   says, restricted to names an exchange also confirms are listed.
   There is no automation to refresh either snapshot; re-run both
   searches by hand if a later one is needed.
   ================================================================ */
window.ASME_CERTIFIED = [
  {name:"ADOR WELDING LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"ADOR"},
  {name:"AEROFLEX INDUSTRIES LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"AEROFLEX"},
  {name:"Artson Limited", certs:["S","U","U2"], exch:["BSE"], symbol:"ARTSON"},
  {name:"ATV PROJECTS INDIA LIMITED", certs:["S","U"], exch:["BSE"], symbol:"ATVPR"},
  {name:"BEW Engineering Limited", certs:["U"], exch:["NSE-SME"], symbol:"BEWLTD"},
  {name:"BGR Energy Systems Ltd", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"BGRENERGY"},
  {name:"Bharat Heavy Electricals Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"BHEL"},
  {name:"Blue Star Limited", certs:["U"], exch:["BSE","NSE"], symbol:"BLUESTARCO"},
  {name:"CRYOGENIC OGS LIMITED", certs:["U"], exch:["BSE"], symbol:"CRYOGENIC"},
  {name:"Dee Development Engineers Limited", certs:["PP","S","U","U2"], exch:["BSE","NSE"], symbol:"DEEDEV"},
  {name:"ELGI EQUIPMENTS LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"ELGIEQUIP"},
  {name:"EXPO ENGINEERING AND PROJECTS LIMITED", certs:["U"], exch:["BSE"], symbol:"EXPOEAPL"},
  {name:"GE Power India Limited", certs:["S","U"], exch:["BSE","NSE"], symbol:"GVPIL"},
  {name:"GMM Pfaudler Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"GMMPFAUDLR"},
  {name:"Graphite India Limited", certs:["U"], exch:["BSE","NSE"], symbol:"GRAPHITE"},
  {name:"Hitachi Energy India Limited", certs:["U"], exch:["BSE","NSE"], symbol:"POWERINDIA"},
  {name:"HLE Glascoat Limited", certs:["U"], exch:["BSE","NSE"], symbol:"HLEGLAS"},
  {name:"Inox India Limited", certs:["T","U","U2","UM"], exch:["BSE","NSE"], symbol:"INOXINDIA"},
  {name:"Isgec Heavy Engineering Limited", certs:["PP","S","T","U","U2"], exch:["BSE","NSE"], symbol:"ISGEC"},
  {name:"Jindal Steel Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"JINDALSTEL"},
  {name:"JNK INDIA LTD.", certs:["S","U"], exch:["BSE","NSE"], symbol:"JNKINDIA"},
  {name:"KILBURN ENGINEERING LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"KLBRENG-B"},
  {name:"Kirloskar Pneumatic Company Limited", certs:["U"], exch:["BSE","NSE"], symbol:"KIRLPNU"},
  {name:"Larsen & Toubro Limited", certs:["N","NA","NPT","NS","PP","S","U","U2","U3"], exch:["BSE","NSE"], symbol:"LT"},
  {name:"Laxmipati Engineering Works Limited", certs:["U"], exch:["BSE"], symbol:"LAXMIPATI"},
  {name:"LINDE INDIA LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"LINDEINDIA"},
  {name:"Lloyds Engineering Works Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"LLOYDSENGG"},
  {name:"LOYAL equipments limited.", certs:["U","U2"], exch:["BSE"], symbol:"LOYAL"},
  {name:"Mazda Limited", certs:["U"], exch:["BSE","NSE"], symbol:"MAZDA"},
  {name:"PATELS AIRTEMP (INDIA) LIMITED", certs:["S","U","U2"], exch:["BSE"], symbol:"PATELSAI"},
  {name:"PENNAR INDUSTRIES LIMITED", certs:["S","U"], exch:["BSE","NSE"], symbol:"PENIND"},
  {name:"PRAJ Industries Limited", certs:["S","U","U2","U3"], exch:["BSE","NSE"], symbol:"PRAJIND"},
  {name:"Sealmatic India Limited", certs:["U"], exch:["BSE"], symbol:"SEALMATIC"},
  {name:"Tata Steel Limited", certs:["U","U2"], exch:["BSE","NSE"], symbol:"TATASTEEL"},
  {name:"TEMPSENS INSTRUMENTS INDIA LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"TEMPSENS"},
  {name:"Texmaco Rail & Engineering Limited", certs:["U"], exch:["BSE","NSE"], symbol:"TEXRAIL"},
  {name:"THE ANUP ENGINEERING LIMITED", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"ANUP"},
  {name:"The K.C.P Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"KCP"},
  {name:"Thermax Limited", certs:["S","U"], exch:["BSE","NSE"], symbol:"THERMAX"},
  {name:"United Heat Transfer Limited", certs:["U"], exch:["NSE-SME"], symbol:"UHTL"}
];
