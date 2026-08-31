# Weekly discovery — judgement pass, 31 August 2026

## The short version

74 candidates were open when this pass started. 13 of them already carried
moat evidence (5 from the prospectus reader, 8 from the earlier pass today),
so 61 were genuinely mine to research. Of those 61 I ruled out 46, set
`moat_signal: true` on 7 new names, and left 8 open with an honest "nothing
stands out yet" note.

20 candidates now carry moat evidence and reach the dashboard queue. Nothing
was promoted to the board, nothing was published, and `companies_raw.json`
was not touched.

**Data availability, which shaped this pass.** Trendlyne's paid channels were
quota-exhausted again, exactly as in the earlier pass: the document search,
stock overview and multi-stock parameter tools returned
`Maximum weighted channel limit exceeded [code:1002]` on every attempt,
including through the second Trendlyne connector, which shares the same
account quota. Only `search_entities` — the cheap name-to-sector lookup —
answered, and it answered reliably, which is what made the sector work below
possible. Screener's MCP server returns financial statements but no business
description for these symbols. So for company text I went to Screener's own
web pages and to company filings and websites, the same route the earlier
pass used. Every piece of evidence recorded names where it came from.

One discipline note worth recording: search-engine summaries asserted
"India's largest manufacturer" for United Drilling Tools and "world's largest
camphor manufacturer" for Mangalam Organics, but when I fetched the
companies' own pages, UDT claims only "one of the world's leading" and
Mangalam's site claims nothing of the kind. I recorded what the primary
sources actually say, not the paraphrase. Two claims that looked like finds
did not survive that check.

---

## Set `moat_signal: true` — 7 new names

**Innovassynth Technologies (India) Ltd — BSE 533315 — flagged `promising`**
The clearest find of the pass, and both automated sector guesses were wrong:
the scan's regex read "Technologies" as IT services, Trendlyne files it under
Others / Investment Companies. It is actually a CRDMO in pharmaceutical
intermediates, oligonucleotide building blocks and specialty chemicals —
market cap about ₹1,312 crore on sales of ₹146 crore. Its own site states
"Innovassynth is the pioneer in offering commercial-scale Nucleosides from
India, globally", and the product list (protected nucleosides,
phosphoramidites, DMT-Cl, GalNAc delivery platforms, ADC linkers, plus
ALD/CVD semiconductor precursors) is a field with very few Indian makers.
Hits the only-Indian-company and niche criteria.

**Hindusthan Insulators & Industries Ltd — BSE 539984 — flagged `unclear`**
Electro-porcelain high-tension insulators, ₹1,002 crore market cap on ₹390
crore of sales. The group's division page claims "leading manufacturer of
overhead conductors and electro porcelain high tension insulators", "First
company in India to manufacture 500KV conductor", BIS certification to 800 KV
and exports to 33+ countries. Flagged unclear because those claims sit on the
Hindusthan Group site covering a combined conductors-and-insulators division,
and I could not establish this pass whether the conductor business sits inside
the listed entity — Screener's description of the listed company mentions only
insulators. That ambiguity is written into the candidate's own record.

**Mangalam Organics Ltd — BSE 514418 — flagged `unclear`**
Screener's company page states it is "the world's largest manufacturer of
Camphor", and the company site confirms the pine-chemicals and terpene-resin
portfolio. Leadership plus niche, both sourced. Unclear rather than promising
because the leadership claim is not matched by pricing power: operating margin
swung 38% → -0.6% → 14% across FY21–FY26 while borrowings went from ₹20 crore
to ₹359 crore, which reads as commodity pass-through.

**Simplex Castings Ltd — BSE 513472 — flagged `unclear`**
May 2026 RDSO approval covering cast steel side frames, bolsters, centre pivot
assemblies and CASNUB 22HS/22RFT bogies — a regulator-gated railway product
with a short approved-supplier list. The same announcement says the company
"previously commanded an estimated 60-70 percent market share" in cast bogies.
Unclear because the approval is at prototype-testing stage, that share figure
is explicitly historical, and the revenue attached to it is management's own
projection.

**Sukhjit Starch & Chemicals Ltd — BSE 524542 — flagged `unclear`**
Its FY2022-23 annual report calls it "India's oldest and third-largest
producer of starch for more than seven decades" — a specific ranking, which is
what the market-share criterion asks for. Unclear because the annual-report
PDF refuses direct fetches (HTTP 403), so the quote is carried from the search
index rather than verified at source, and because a number-three position in
maize milling is not by itself evidence of pricing power.

**United Drilling Tools Ltd — BSE 522014 — flagged `unclear`**
Holds API monogram licences 5CT, 5L, 5B, 7-1, 7-2, 19G1 and 19G2 plus ISO
13679 for oilfield casing, drill pipe and gas-lift equipment, supplying ONGC
and Oil India. The certification stack is a genuine qualification barrier in a
small field, carrying the niche criterion. Unclear because no India
market-share figure exists in any source reached, the company's own pages
claim only "one of the world's leading", and ROCE is 10.4%.

**Shish Industries Ltd — BSE 540693 — flagged `unclear`**
Screener's key points record that it "was the first to develop and patent
5-ply Polypropylene Corrugated Sheets", created the Carmika reflective
insulation product and pioneered insulated water tank covers in India.
Unclear: a single company-supplied claim with no share figure behind it, on a
₹10.5 share.

---

## Ruled out — 46

**Wrong sector — the name fooled the scan (23)**

- **A2Z Infra Engineering** (533292) — "Engineering" guessed precision engineering; it is an EPC and maintenance-services contractor.
- **IL&FS Engineering and Construction** (532907) — same regex artefact; a civil EPC contractor in the distressed IL&FS group.
- **W. S. Industries** (504220) — made porcelain insulators from 1965, but Screener says the listed entity now "operates primarily in Infra segment" as an EPC contractor. The manufacturing worth testing is gone.
- **Brahmaputra Infrastructure** (535693) — EPC plus real-estate development, including a shopping mall.
- **B.R.Goyal Infrastructure** (544335) — road EPC with RMC, toll collection, residential plotting and a windmill.
- **Suraj Industries** (526211) — "Industries" guessed industrial goods; Trendlyne says commodity trading and distribution.
- **Pajson Agro India** (544657) — same, under an agri name.
- **Oswal Agro Mills** (500317) — same, despite "Mills".
- **N D Metal Industries** (512024) — imports and trades copper, brass, zinc, tin and aluminium scrap.
- **Satani Bearings** (505703) — listed as Deccan Bearings; Screener says it *trades* bearings rather than making them.
- **Lynx Machinery & Commercials** (505320) — warehousing, trading and investments, halted FY24; now a Kolkata land development.
- **KD Green Industries** (512595) — resolves to Manbro Industries, an import-export trading shell across food, pharma and packaging.
- **Lancer Container Lines** (539841) — "Container" guessed packaging; it is a shipping-container logistics line.
- **Uday Jewellery Industries** (539518) — "Industries" guessed industrial goods; it is gems and jewellery.
- **Harmony Capital Service** (530055) — the finance hint was right. Trendlyne's "Specialty Chemicals" tag for this symbol is wrong; Screener says investing and trading in shares and securities.
- **Polytex India** (512481) — polymer-sounding name, but it is a lender whose RBI registration was cancelled in June 2024.
- **Colab Platforms** (542866) — "stock-in-trade and IT-services economics"; the manufacturing lines are still aspirational.
- **Cityon Systems (India)** (780013) — "engaged in trading activities".
- **Woodsvilla** (526959) — hospitality; it owns a resort in Ranikhet.
- **Revati Media** (524504) — buying and selling movie rights.
- **Starbeam Ventures** (539175) — films and entertainment.
- **Laser Diamonds** (531164) — now Turner Industries; trading of diamonds.
- **Encash Entertainment** (538684) — entertainment plus fashion garments.

**Right sector, but no moat evidence after looking (23)**

- **Donear Industries** (512519) — commodity suiting and shirting fabric on 600 looms, plus rental property.
- **Vishal Fabrics** (538598) — denim dyeing and processing, largely job-work, in the Chiripal group.
- **Shiva Cement** (532323) — commodity cement for eastern India, JSW subsidiary, negative book value.
- **Systematic Industries** (544541) — commodity steel wire drawing; real operations and 17% ROE, but no leadership or share claim anywhere.
- **Parmeshwar Metal** (544330) — copper wire rod from recycled scrap; "leading manufacturer" traces only to IPO marketing copy.
- **Raasi Refractories** (502271) — refractory bricks; ₹8.78 crore market cap, negative book value, 99.8% of promoter holding pledged.
- **Chase Bright Steel** (504671) — bright bars; book value ₹-86.6, price feed stale since 2012.
- **Carnation Industries** (530609) — sanitary and water-distribution castings; exited insolvency Nov 2024, sales ₹1.70 crore.
- **Iykot Hitech Toolroom** (522245) — sales ₹1.54 crore, three-year ROE -57.6%, promoter stake down from 66% to 37%.
- **Integra Switchgear** (517423) — makes MCBs, but sales are ₹0.03 crore; dormant in a crowded field.
- **TMT (India)** (522171) — carries a real first-in-India paper-machinery claim, but in the past tense: the entity is now a shell on ₹0.05 crore of sales.
- **Master Chemicals** (506867) — ₹0.78 crore market cap, zero sales, no business stated beyond its memorandum.
- **Oswal Overseas** (531065) — commodity sugar, sales down 94%, insolvency settlement July 2026.
- **Parmax Pharma** (540359) — small bulk-drug and intermediates maker, crowded and contract-priced.
- **Advik Laboratories** (531686) — now MPS Pharmaa; zero sales, BSE trading suspended August 2026 over unpaid listing fees.
- **Naturo Indiabull** (543579) — ₹4.68 crore market cap after pivots between healthcare, FMCG, fertilizers and logistics.
- **Clenon Enterprises** (517564) — post-insolvency G.R.Cables shell "venturing into crude oil, water processing equipment, agro products".
- **Bharat Textiles & Proofing** (531029) — tarpaulin, HDPE and coated canvas; commodity.
- **Cresanto Global** (531207) — flexible packaging as a declared intention; sales ₹0.05 crore.
- **Progrex Ventures** (531265) — manufacturing suspended since FY16 after the fixed assets were sold.
- **Kiran Print Pack** (531413) — capital-issue and label printing on ₹0.98 crore of sales.
- **RCC Cements** (531825) — dormant, zero sales, object clause recently amended to add consumer electronics.
- **Pithampur Poly Products** (530683) — FIBC and woven sacks; sales ₹0.59 crore, book value ₹-15.2.

---

## Still open, nothing found yet — 8

These were researched properly this pass and produced no sourced hit on the
five criteria. They stay open at `moat_signal: null` and will not reach the
dashboard until a later pass finds something.

- **GTV Engineering** (539479) — high-tech steel fabrication subcontractor. ROCE 30.4% and 90% five-year profit CAGR are unusually good for the trade, which is why I did not rule it out, but no source names a product, share or niche behind those returns.
- **Cospower Engineering** (543172) — HT/LT power capacitors, APFC panels and turnkey substations to 132 kV, ROE 39.8%. The only ranking found is "among the top 10 in the country" on a trade directory — too weak and too thin a source to record.
- **B&B Triplewall Containers** (543668) — "first company in India to put up an automated plant with a BHS corrugator and BOBST FFG" is a claim about imported plant equipment, not about leading a product or holding a share, so it maps onto none of the five criteria. Its prospectus profile only records that it has *no* registered trademarks.
- **Gujarat Apollo Industries** (522217) — crushing and screening equipment. Its "pioneer of several construction technology" claim attaches to the asphalt business that ran through the Ammann JV, not to what the listed company sells now.
- **Jenburkt Pharmaceuticals** (524731) — 85 branded formulations, ROCE 27%. Sticky model, but a branded-generics book is a common Indian structure and no therapy leadership or share figure was found.
- **Aarti Surfactants** (543210) — surfactants demerged from Aarti Industries; ROE 5.27%, several larger Indian players, no claim found.
- **Haleos Labs** (540679) — APIs and intermediates, demerged from SMS Pharmaceuticals; no molecule leadership or exclusivity claim found.
- **CONTAINE Technologies** (751131) — actually an electronics maker (vehicle speed limiters, AIS-140/NavIC trackers), not the IT shop the regex guessed. Regulation-gated products worth testing, but Screener carries no certification, customer or leadership detail. **Also flagged in its record: the queue's symbol 751131 does not match the actively quoted scrip code 543606** — worth checking before anyone acts on this entry.

---

## Needing a person because the sources had nothing — 0

Unlike the earlier pass, no candidate had to be left untested for lack of
data. `search_entities` plus Screener's web pages reached every one of the 61.
What was *not* reachable was filing text — annual reports, investor
presentations, earnings calls — for any of them. That is the gap that keeps
six of the eight "still open" names unresolved, and it is the gap a pass run
with Trendlyne's document channel available should close first.

## Housekeeping

- Queue rewritten in place: 233 entries, order and all untouched fields preserved, 12 prospectus profiles intact.
- The 5 prospectus-derived `moat_signal: true` entries (SECL, AUGMONT, TEMPSENS, Jinkushal, Sarveshwar) were left exactly as the profile reader wrote them, as instructed.
- `node update-weekly.mjs --stamp-only` was run so `candidates_raw.json` and `build_stamp.json` reflect these verdicts.
- Nothing was published. `publish-candidates.cmd` remains a person's call.
