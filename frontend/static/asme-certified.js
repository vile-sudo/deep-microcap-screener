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

   `since` is the earliest `issuedDate` ASME's API returns across every
   certificate row that folds into this company (so Thermax Limited's
   `since` is the oldest of its own row and its two merged divisions,
   not just the plain "Thermax Limited" row's own date) — pulled from
   the same API call as everything else, same day.
   ================================================================ */

/* What each `certs` code actually certifies — copied from the "Certificate
   Type" dropdown on ASME's own search form at caconnect.asme.org/directory,
   restricted to the codes that actually appear below. ASME issues several
   near-duplicate codes per scope (U/U2/U3 are all "Pressure Vessels" at
   different classes; N/NA/NPT/NS are all nuclear-component variants) —
   that's ASME's own scheme, not a simplification made here. */
window.ASME_CERT_TYPES = {
  N:   'Nuclear Components',
  NA:  'Nuclear Installation and Shop Assembly',
  NPT: 'Nuclear Partials',
  NS:  'Nuclear Components',
  PP:  'Pressure Piping',
  S:   'Power Boiler',
  T:   'Transport Tanks',
  U:   'Pressure Vessels',
  U2:  'Pressure Vessels',
  U3:  'Pressure Vessels',
  UM:  'Miniature Pressure Vessels',
};
window.ASME_CERTIFIED = [
  {name:"ADOR WELDING LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"ADOR", since:"2008-07-30"},
  {name:"AEROFLEX INDUSTRIES LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"AEROFLEX", since:"2025-07-16"},
  {name:"Artson Limited", certs:["S","U","U2"], exch:["BSE"], symbol:"ARTSON", since:"2012-05-10"},
  {name:"ATV PROJECTS INDIA LIMITED", certs:["S","U"], exch:["BSE"], symbol:"ATVPR", since:"2024-01-23"},
  {name:"BEW Engineering Limited", certs:["U"], exch:["NSE-SME"], symbol:"BEWLTD", since:"2016-09-02"},
  {name:"BGR Energy Systems Ltd", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"BGRENERGY", since:"2005-06-03"},
  {name:"Bharat Heavy Electricals Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"BHEL", since:"1981-06-15"},
  {name:"Blue Star Limited", certs:["U"], exch:["BSE","NSE"], symbol:"BLUESTARCO", since:"2016-04-19"},
  {name:"CRYOGENIC OGS LIMITED", certs:["U"], exch:["BSE"], symbol:"CRYOGENIC", since:"2026-05-04"},
  {name:"Dee Development Engineers Limited", certs:["PP","S","U","U2"], exch:["BSE","NSE"], symbol:"DEEDEV", since:"2011-12-27"},
  {name:"ELGI EQUIPMENTS LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"ELGIEQUIP", since:"2011-10-20"},
  {name:"EXPO ENGINEERING AND PROJECTS LIMITED", certs:["U"], exch:["BSE"], symbol:"EXPOEAPL", since:"2025-12-09"},
  {name:"GE Power India Limited", certs:["S","U"], exch:["BSE","NSE"], symbol:"GVPIL", since:"2020-12-15"},
  {name:"GMM Pfaudler Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"GMMPFAUDLR", since:"1998-05-22"},
  {name:"Graphite India Limited", certs:["U"], exch:["BSE","NSE"], symbol:"GRAPHITE", since:"2014-09-26"},
  {name:"Hitachi Energy India Limited", certs:["U"], exch:["BSE","NSE"], symbol:"POWERINDIA", since:"2021-10-07"},
  {name:"HLE Glascoat Limited", certs:["U"], exch:["BSE","NSE"], symbol:"HLEGLAS", since:"2002-12-23"},
  {name:"Inox India Limited", certs:["T","U","U2","UM"], exch:["BSE","NSE"], symbol:"INOXINDIA", since:"2000-03-02"},
  {name:"Isgec Heavy Engineering Limited", certs:["PP","S","T","U","U2"], exch:["BSE","NSE"], symbol:"ISGEC", since:"1994-05-24"},
  {name:"Jindal Steel Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"JINDALSTEL", since:"2010-11-16"},
  {name:"JNK INDIA LTD.", certs:["S","U"], exch:["BSE","NSE"], symbol:"JNKINDIA", since:"2024-04-24"},
  {name:"KILBURN ENGINEERING LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"KLBRENG-B", since:"2022-12-16"},
  {name:"Kirloskar Pneumatic Company Limited", certs:["U"], exch:["BSE","NSE"], symbol:"KIRLPNU", since:"2025-01-27"},
  {name:"Larsen & Toubro Limited", certs:["N","NA","NPT","NS","PP","S","U","U2","U3"], exch:["BSE","NSE"], symbol:"LT", since:"1988-10-03"},
  {name:"Laxmipati Engineering Works Limited", certs:["U"], exch:["BSE"], symbol:"LAXMIPATI", since:"2021-11-08"},
  {name:"LINDE INDIA LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"LINDEINDIA", since:"2009-04-13"},
  {name:"Lloyds Engineering Works Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"LLOYDSENGG", since:"2017-12-26"},
  {name:"LOYAL equipments limited.", certs:["U","U2"], exch:["BSE"], symbol:"LOYAL", since:"2005-06-07"},
  {name:"Mazda Limited", certs:["U"], exch:["BSE","NSE"], symbol:"MAZDA", since:"2005-06-27"},
  {name:"PATELS AIRTEMP (INDIA) LIMITED", certs:["S","U","U2"], exch:["BSE"], symbol:"PATELSAI", since:"2002-12-23"},
  {name:"PENNAR INDUSTRIES LIMITED", certs:["S","U"], exch:["BSE","NSE"], symbol:"PENIND", since:"2024-10-07"},
  {name:"PRAJ Industries Limited", certs:["S","U","U2","U3"], exch:["BSE","NSE"], symbol:"PRAJIND", since:"2007-09-28"},
  {name:"Sealmatic India Limited", certs:["U"], exch:["BSE"], symbol:"SEALMATIC", since:"2024-01-04"},
  {name:"Tata Steel Limited", certs:["U","U2"], exch:["BSE","NSE"], symbol:"TATASTEEL", since:"2025-12-19"},
  {name:"TEMPSENS INSTRUMENTS INDIA LIMITED", certs:["U"], exch:["BSE","NSE"], symbol:"TEMPSENS", since:"2022-08-17"},
  {name:"Texmaco Rail & Engineering Limited", certs:["U"], exch:["BSE","NSE"], symbol:"TEXRAIL", since:"2026-04-06"},
  {name:"THE ANUP ENGINEERING LIMITED", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"ANUP", since:"2005-11-16"},
  {name:"The K.C.P Limited", certs:["S","U","U2"], exch:["BSE","NSE"], symbol:"KCP", since:"2021-05-21"},
  {name:"Thermax Limited", certs:["S","U"], exch:["BSE","NSE"], symbol:"THERMAX", since:"1987-12-30"},
  {name:"United Heat Transfer Limited", certs:["U"], exch:["NSE-SME"], symbol:"UHTL", since:"2022-06-09"}
];
