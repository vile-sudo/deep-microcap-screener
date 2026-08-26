/**
 * lib/sectors.mjs — guess a listing's sector from its company name alone.
 *
 * The exchange feeds (NSE's two CSVs, BSE's scrip master) carry no sector or
 * industry field worth trusting -- BSE's INDUSTRY column is null on every row
 * this was checked against. A name is all there is to go on before a person
 * (or profile-company.mjs, reading the prospectus) looks closer.
 *
 * hint(name) returns one of three things:
 *   - a label from PLAUSIBLE_PATTERNS  -> "this could be the board's kind of company"
 *   - a label from RULED_OUT_PATTERNS  -> "this is a business the board never carries"
 *   - null                             -> the name gave no hint either way
 *
 * scan-listings.mjs turns that into known === true / false / null and decides
 * from there whether a name is worth queuing. Getting this list exactly right
 * is not the point -- it only has to be good enough to keep the queue readable
 * without silently dropping the odd real candidate. Being wrong in the
 * direction of "queued but turns out irrelevant" costs thirty seconds of
 * review; being wrong the other way is invisible.
 */

/* Kept roughly aligned with the sector taxonomy already on the board (see the
   `sector` values in backend/data/companies_raw.json) so a name that would fit
   an existing screen is recognised, not just a generic "industrial" catch-all. */
const PLAUSIBLE_PATTERNS = [
  ['pharma & healthcare', /\b(pharma(?:ceutical)?s?|drugs?|healthcare|hospitals?|diagnostics?|biotech|life ?sciences?|formulations?|medic(?:al|are|ines?)|labs?|bio-?sciences?)\b/i],
  ['chemicals', /\b(chemicals?|petrochem\w*|specialty chem\w*|agrochem\w*|dyes?|pigments?|polymers?|resins?|fluorochem\w*|inorganic|organics?|surfactants?|peroxygens?)\b/i],
  ['defence & aerospace', /\b(defence|defense|aerospace|munitions?|shipyard\w*|shipbuild\w*|ordnance)\b/i],
  ['electronics & electricals', /\b(electronics?|electricals?|electro-?mech\w*|circuits?|semiconductors?|components?|switchgear)\b/i],
  ['precision engineering', /\b(engineering|forgings?|castings?|precision|machining|tools?|bearings?|fasteners?|gears?)\b/i],
  ['auto & mobility', /\b(auto(?:mobile|motive)?s?|motors?|\bev\b|vehicles?|tractors?)\b/i],
  ['industrial & capital goods', /\b(industries|industrial|capital goods|machinery|equipments?|engines?|turbines?|pumps?|valves?|compressors?)\b/i],
  ['textiles & apparel', /\b(textiles?|apparels?|garments?|fabrics?|yarns?|spinning|weaving|hosiery)\b/i],
  ['metals & mining', /\b(steel|metals?|mining|alloys?|smelt\w*|foundry|ferro-?\w*|minerals?)\b/i],
  ['agri & food', /\b(agro|agri\w*|foods?|fmcg|dairy|nutrition|beverages?|proteins?|enzymes?)\b/i],
  ['renewable & power', /\b(solar|renewable|power(?:\b|s\b)|transformers?|energy(?:\b|s\b))\b/i],
  ['packaging & plastics', /\b(packaging|plastics?|polyester|containers?|laminates?)\b/i],
  ['construction & building materials', /\b(cement|construction|infra(?:structure)?|building materials?|ceramics?|tiles?)\b/i],
  ['testing & certification', /\b(testing|certification|calibration|inspection)\b/i],
];

/* Sectors a name can look plausible for but that this board never invests in
   -- ruling these OUT (rather than leaving them "unknown") is what keeps a
   287-row mainboard re-stamp from flooding the queue with finance and IT
   names, which are common enough in a listings feed to dominate an unfiltered
   queue. A name matching neither list here falls through to null, which is
   the "unknown" bucket scan-listings.mjs treats differently for SME vs
   mainboard boards (see the comment there). */
const RULED_OUT_PATTERNS = [
  ['financial services', /\b(finance|financial|nbfc|capital(?:\b|s\b)|leasing|insurance|banks?|housing finance|micro ?finance|chit funds?|asset management|broking|securities)\b/i],
  ['it & software services', /\b(software|technologies?|infotech|it services?|\bbpo\b|\bkpo\b|consultancy|consulting|systems?(?:\b|\.)|solutions?)\b/i],
  ['real estate', /\b(realty|real estate|properties|developers?|estates?)\b/i],
  ['media & entertainment', /\b(media|entertainment|broadcast\w*|publish\w*|films?|studios?|multiplex\w*)\b/i],
  ['retail & hospitality', /\b(retail|stores?|\bmart\b|hospitality|hotels?|resorts?|tours?|travels?|restaurants?)\b/i],
  ['trading & logistics', /\b(trading|traders?|distributors?|logistics|couriers?|transport(?:\b|s\b|ers?\b))\b/i],
];

export function hint(name) {
  if (!name) return null;
  for (const [label, re] of PLAUSIBLE_PATTERNS) if (re.test(name)) return label;
  for (const [label, re] of RULED_OUT_PATTERNS) if (re.test(name)) return label;
  return null;
}

/* The set of labels hint() can return that mean "plausible" -- kept separate
   from RULED_OUT so `PLAUSIBLE.has(hint(name))` distinguishes the two without
   scan-listings.mjs needing to import both pattern lists. */
export const PLAUSIBLE = new Set(PLAUSIBLE_PATTERNS.map(([label]) => label));
