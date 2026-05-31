import csv
import re
from pathlib import Path
from collections import defaultdict


INPUT_FILES = [
    "data/2004 Statewide General Election (CSV Format).csv",
    "data/2006 Statewide General Election (CSV Format).csv",
    "data/2008 Statewide General Election (CSV Format).csv",
    "data/2010 Statewide General Election (CSV Format).csv",
    "data/2012 Statewide General Election (CSV Format).csv",
    "data/2014 General Election Results (CSV Format).csv",
    "data/2016 General Election Results (CSV Format).csv",
    "data/2020 General Election Precinct-Level Results.csv",
    "data/2022 General Election Results by Precinct.csv",
    "data/2024StatewideGeneralElectionResults.csv",
]

OUT_DIR = Path("data/openelections")
REFERENCE_DIR = OUT_DIR / "reference"
REFERENCE_FILES = [OUT_DIR / "_oe_reference_2022.csv"]
OVERRIDES_FILE = OUT_DIR / "party_overrides.csv"


KNOWN_PARTIES = {
    # 2024
    "harris, kamala d.": "DEM",
    "trump, donald j.": "REP",
    "kennedy jr., robert f.": "IND",
    "oliver, chase": "LIB",
    "stein, jill": "GRN",
    "amodei, mark e.": "REP",
    "wilson, gregory t.": "DEM",
    "rosen, jacky": "DEM",
    "brown, sam": "REP",
    "biden, joseph r. jr.": "DEM",
    "trump, donald j.": "REP",
    "obama, barack": "DEM",
    "romney, mitt": "REP",
    "clinton, hillary": "DEM",
    "kaine, tim": "DEM",
    "pence, mike": "REP",
    "bush, george w.": "REP",
    "kerry, john f.": "DEM",
    "biden, joseph r": "DEM",
    "jorgensen, jo": "LIB",
    "blankenship, don": "CON",
    "goodman, robert b": "DEM",
    "vanderbeek, david l": "LIB",
    "johnson, gary": "LIB",
    "goode, virgil": "CON",
    "hansen, janine": "IAP",
    "knecht, ron": "REP",
    "miller, ross": "DEM",
    "wallin, kim": "DEM",
    "krolicki, brian k": "REP",
    "seeback, tiffany g": "IAP",
    "seeback, tiffany gholson": "IAP",
    "hammond, scott t": "REP",
    "becker, april": "REP",
    "becker, april m": "REP",
    "buck, carrie": "REP",
    "buck, carrie a": "REP",
    "spearman, patricia p": "DEM",
    "spearman, patricia pat": "DEM",
    "hafen, ii, gregory": "REP",
    "hafen, gregory ii": "REP",
    "jones, iii, walter boo": "NPP",
    "jones, walter iii": "NPP",
    "carter, ii, max": "DEM",
    "carter, max ii": "DEM",
    # 2022 (sampled common statewide names)
    "ford, aaron d.": "DEM",
    "chattah, sigal": "REP",
    "lombardo, joe": "REP",
    "sisolak, steve": "DEM",
    "aguilar, francisco \"cisco\"": "DEM",
    "marchant, jim": "REP",
    "conine, zach": "DEM",
    "fiore, michele": "REP",
    "cortez masto, catherine": "DEM",
    "laxalt, adam paul": "REP",
}

ELECTION_DATES = {
    "2004": "20041102",
    "2006": "20061107",
    "2008": "20081104",
    "2010": "20101102",
    "2012": "20121106",
    "2014": "20141104",
    "2016": "20161108",
    "2020": "20201103",
    "2022": "20221108",
    "2024": "20241105",
}

PARTISAN_OFFICE_HINTS = (
    "president",
    "u.s. senate",
    "u.s. house",
    "u.s representative",
    "u.s. representative",
    "governor",
    "lieutenant governor",
    "attorney general",
    "secretary of state",
    "state treasurer",
    "state controller",
)

YEAR_SPECIFIC_PARTY = {
    "2012": {
        "obama, barack": "DEM",
        "romney, mitt": "REP",
        "heller, dean": "REP",
        "shelley berkley": "DEM",
        "berkley, shelley": "DEM",
        "amodei, mark e": "REP",
        "amodei, mark e.": "REP",
        "marshall, samuel p": "DEM",
        "marshall, samuel p.": "DEM",
        "angles, richard": "IAP",
        "none of these candidates": "",
        "butch otter": "REP",
    },
    "2014": {
        "sandoval, brian": "REP",
        "goodman, bob": "DEM",
        "hutchison, mark": "REP",
        "jones-vargas, lucy flores": "DEM",
        "flores, lucy": "DEM",
        "laxalt, adam paul": "REP",
        "segarbic, ross c": "DEM",
        "segarbic, ross c.": "DEM",
        "cegavske, barbara k": "REP",
        "cavnar, kate marshall": "DEM",
        "marshall, kate": "DEM",
        "dan schwartz": "REP",
        "schwartz, dan": "REP",
        "nevens, kim wallin": "DEM",
        "reid, steven j.": "IND",
        "none of these candidates": "",
    },
    "2024": {
        "harris, kamala d.": "DEM",
        "trump, donald j.": "REP",
        "kennedy jr., robert f.": "IND",
        "oliver, chase": "LIB",
        "stein, jill": "GRN",
        "none of these candidates": "",
        "rosen, jacky": "DEM",
        "brown, sam": "REP",
        "amodei, mark e.": "REP",
        "wilson, gregory t.": "DEM",
        "lee, susie": "DEM",
        "obrien, drew johnson": "REP",
        "horsford, steven": "DEM",
        "fleischmann, john": "REP",
        "tituss, dina": "DEM",
        "wolfe, flemming larsen": "REP",
        "harris, kamala d and walz, tim": "DEM",
        "trump, donald j and vance, jd": "REP",
        "oliver, chase and ter maat, michael": "LIB",
        "skousen, joel and combs, rik": "CON",
        "rosen, jacky s": "DEM",
        "cunningham, chris": "LIB",
        "hansen, janine": "IAP",
        "chapman, lynn": "IAP",
        "havlicek, david": "LIB",
        "kidd, greg": "IND",
        "titus, dina": "DEM",
        "amodei, mark e": "REP",
        "robertson, mark": "REP",
        "lee, john": "REP",
        "johnson, drew": "REP",
        "best, russell": "IAP",
        "goossen, david": "LIB",
        "hoge, william": "IAP",
        "quince, ron ron q": "LIB",
        "ferreira, tim tj": "IAP",
        "tachiquin, javi trujillo": "LIB",
    },
}


def clean(s: str) -> str:
    return (s or "").strip()


def norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", clean(s).lower())


def candidate_key(s: str) -> str:
    v = norm_key(s)
    v = v.replace('"', "")
    v = re.sub(r"\bincumbent\b", "", v)
    v = re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b", "", v)
    v = re.sub(r"[^a-z0-9,\s\-]", "", v)
    v = re.sub(r"\s+", " ", v).strip(" ,")
    return v


KNOWN_PARTIES_NORM = {candidate_key(k): v for k, v in KNOWN_PARTIES.items()}
YEAR_SPECIFIC_PARTY_NORM = {
    y: {candidate_key(k): v for k, v in m.items()} for y, m in YEAR_SPECIFIC_PARTY.items()
}
PARTY_FULL_NAME = {
    "DEM": "Democratic",
    "REP": "Republican",
    "LIB": "Libertarian",
    "LPN": "Libertarian",
    "GRN": "Green",
    "IND": "Independent",
    "IAP": "Independent American",
    "NPP": "Nonpartisan",
    "CON": "Constitution",
}


def candidate_variants(s: str):
    """
    Return multiple normalized variants so party lookup survives label differences:
    - "Last, First M." vs "First M Last"
    - punctuation/initial differences
    """
    base = candidate_key(s)
    if not base:
        return []
    out = {base}
    if "," in base:
        parts = [p.strip() for p in base.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            out.add(f"{parts[1]} {parts[0]}".strip())
            rhs = parts[1].split()
            if rhs:
                out.add(f"{parts[0]}, {rhs[0]}".strip())
                out.add(f"{rhs[0]} {parts[0]}".strip())
    else:
        toks = base.split()
        if len(toks) >= 2:
            out.add(f"{toks[-1]}, {' '.join(toks[:-1])}".strip())
    # Variant with single-letter middle initials removed.
    cleaned = []
    for t in base.replace(",", " ").split():
        if len(t) == 1:
            continue
        cleaned.append(t)
    if cleaned:
        simple = " ".join(cleaned)
        out.add(simple)
        toks = simple.split()
        if len(toks) >= 2:
            out.add(f"{toks[-1]}, {' '.join(toks[:-1])}".strip())
    return [v for v in out if v]


def candidate_skeleton(s: str) -> str:
    """
    Coarse identity key for override fallback: first+last only.
    Helps bridge nickname/middle-initial variants (e.g., Philip "PK" O'Neill).
    """
    c = candidate_key(s)
    if not c:
        return ""
    if "," in c:
        parts = [p.strip() for p in c.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            first = parts[1].split()[0] if parts[1].split() else ""
            return f"{parts[0]}|{first}".strip("|")
    toks = c.split()
    if len(toks) >= 2:
        return f"{toks[-1]}|{toks[0]}".strip("|")
    return c


def office_key(s: str) -> str:
    s = norm_key(s)
    s = s.replace("u.s. representative in congress", "u.s. house")
    s = s.replace("united states senator", "u.s. senate")
    s = s.replace("president and vice president of the united states", "president")
    return s


def is_partisan_office(office: str) -> bool:
    o = office_key(office)
    return any(h in o for h in PARTISAN_OFFICE_HINTS)


def parse_year_from_filename(path: str) -> str:
    m = re.search(r"(20\d{2})", Path(path).name)
    return m.group(1) if m else ""


def load_reference_party_maps():
    by_year_office_candidate = defaultdict(dict)
    by_year_county_precinct_office_candidate = defaultdict(dict)
    by_year_candidate_party = defaultdict(dict)
    if not REFERENCE_DIR.exists():
        return by_year_office_candidate, by_year_county_precinct_office_candidate

    ref_paths = sorted(REFERENCE_DIR.glob("*__nv__general__*.csv"))
    for rf in REFERENCE_FILES:
        if rf.exists():
            ref_paths.append(rf)
    for p in ref_paths:
        year = parse_year_from_filename(p.name)
        if not year and "2022" in p.name:
            year = "2022"
        try:
            with p.open("r", encoding="utf-8", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    party = clean(row.get("party", ""))
                    if not party:
                        continue
                    county = norm_key(row.get("county", ""))
                    precinct = norm_key(row.get("precinct", ""))
                    office_raw = clean(row.get("office", ""))
                    district_raw = clean(row.get("district", ""))
                    office = office_key(office_raw)
                    candidate = candidate_key(row.get("candidate", ""))
                    if not office or not candidate:
                        continue

                    office_keys = {office}
                    if district_raw:
                        office_keys.add(office_key(f"{office_raw}, District {district_raw}"))
                    for offk in office_keys:
                        k1 = (offk, candidate)
                        if k1 in by_year_office_candidate[year]:
                            if by_year_office_candidate[year][k1] != party:
                                by_year_office_candidate[year][k1] = ""
                        else:
                            by_year_office_candidate[year][k1] = party

                    if county or precinct:
                        for offk in office_keys:
                            k2 = (county, precinct, offk, candidate)
                            by_year_county_precinct_office_candidate[year][k2] = party
                    if candidate in by_year_candidate_party[year]:
                        if by_year_candidate_party[year][candidate] != party:
                            by_year_candidate_party[year][candidate] = ""
                    else:
                        by_year_candidate_party[year][candidate] = party
        except Exception:
            continue

    return by_year_office_candidate, by_year_county_precinct_office_candidate, by_year_candidate_party


def load_party_overrides():
    by_year_office_candidate = defaultdict(dict)
    by_year_office_skeleton = defaultdict(dict)
    if not OVERRIDES_FILE.exists():
        return by_year_office_candidate, by_year_office_skeleton
    with OVERRIDES_FILE.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            year = clean(row.get("year", ""))
            office = office_key(row.get("office", ""))
            candidate = candidate_key(row.get("candidate", ""))
            party = clean(row.get("party", ""))
            if not year or not office or not candidate or not party:
                continue
            by_year_office_candidate[year][(office, candidate)] = party
            sk = candidate_skeleton(row.get("candidate", ""))
            if sk:
                k = (office, sk)
                prev = by_year_office_skeleton[year].get(k, "")
                if not prev:
                    by_year_office_skeleton[year][k] = party
                elif prev != party:
                    by_year_office_skeleton[year][k] = ""
    return by_year_office_candidate, by_year_office_skeleton


def detect_header(rows):
    for i, row in enumerate(rows):
        vals = [norm_key(c) for c in row]
        if "jurisdiction" in vals and any(v.startswith("precinct") for v in vals) and "votes" in vals:
            return i, row
    raise ValueError("Could not find header row.")


def column_index_map(header):
    canon = [norm_key(h).replace(" ", "") for h in header]
    idx = {}
    for i, h in enumerate(canon):
        if h == "jurisdiction":
            idx["county"] = i
        elif h.startswith("precinct"):
            idx["precinct"] = i
        elif h in ("contest", "race"):
            idx["office"] = i
        elif h in ("selection", "candidate"):
            idx["candidate"] = i
        elif h == "votes":
            idx["votes"] = i
    missing = [k for k in ("county", "precinct", "office", "candidate", "votes") if k not in idx]
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")
    return idx


def infer_party(
    year: str,
    county: str,
    precinct: str,
    office: str,
    district: str,
    candidate: str,
    ref_oc: dict,
    ref_cpo: dict,
    ref_cand: dict,
    global_party_by_office_candidate: dict,
    global_party_by_candidate: dict,
    overrides: dict,
    override_skeletons: dict,
) -> str:
    cvars = candidate_variants(candidate)
    c = cvars[0] if cvars else ""
    if c in ("yes", "no", "none of these candidates", ""):
        return ""
    if "question" in norm_key(office):
        return ""
    office_candidates = [office_key(office)]
    if district:
        office_candidates.append(office_key(f"{office}, District {district}"))
    for offk in office_candidates:
        for cv in cvars:
            po = overrides.get(year, {}).get((offk, cv), "")
            if po:
                return po
    sks = {candidate_skeleton(v) for v in cvars}
    sks = {s for s in sks if s}
    for offk in office_candidates:
        for sk in sks:
            po = override_skeletons.get(year, {}).get((offk, sk), "")
            if po:
                return po
    for offk in office_candidates:
        for cv in cvars:
            k2 = (norm_key(county), norm_key(precinct), offk, cv)
            p2 = ref_cpo.get(year, {}).get(k2, "")
            if p2:
                return p2
    for offk in office_candidates:
        for cv in cvars:
            k1 = (offk, cv)
            p1 = ref_oc.get(year, {}).get(k1, "")
            if p1:
                return p1
    for cv in cvars:
        pc = ref_cand.get(year, {}).get(cv, "")
        if pc:
            return pc
    for offk in office_candidates:
        for cv in cvars:
            pg = global_party_by_office_candidate.get((offk, cv), "")
            if pg:
                return pg
    for cv in cvars:
        pgc = global_party_by_candidate.get(cv, "")
        if pgc:
            return pgc
    for cv in cvars:
        if cv in KNOWN_PARTIES_NORM:
            return KNOWN_PARTIES_NORM[cv]
    ys = YEAR_SPECIFIC_PARTY_NORM.get(year, {})
    if is_partisan_office(office):
        for cv in cvars:
            p = ys.get(cv, "")
            if p:
                return p
    # Extract explicit party tags when present in candidate labels (e.g., "NAME (REP)").
    raw = norm_key(candidate)
    m = re.search(r"\((dem|rep|lib|grn|ind|iap)\)", raw)
    if m and is_partisan_office(office):
        return m.group(1).upper()
    return ""


def normalize_office(office: str) -> str:
    o = clean(office)
    low = o.lower()
    if "u.s. representative in congress" in low:
        return "U.S. House"
    if "united states senator" in low:
        return "U.S. Senate"
    if "president and vice president of the united states" in low:
        return "President"
    o = re.sub(r",?\s+(district|dist\.?)\s*[0-9A-Za-z\-]+$", "", o, flags=re.I)
    # Some early-year files include county suffixes (e.g., "Mayor CARSON CITY").
    o = re.sub(r"^mayor\s+[a-z\s]+$", "Mayor", o, flags=re.I)
    # Normalize SHOUTING labels while preserving mixed-case values.
    if o and o == o.upper():
        o = o.title()
        o = o.replace("U.S.", "U.S")
    return o


def normalize_district(office: str) -> str:
    m = re.search(r"\b(?:district|dist\.?)\s*([0-9A-Za-z\-]+)\b", office, flags=re.I)
    return m.group(1) if m else ""


def parse_votes(v: str):
    s = clean(v)
    if s == "*":
        return ""
    s = s.replace(",", "")
    try:
        return str(int(float(s)))
    except Exception:
        return ""


def normalize_precinct(precinct: str) -> str:
    p = clean(precinct)
    p = re.sub(r"^[A-Za-z\s]+-\s*Precinct\s*", "", p, flags=re.I)
    p = re.sub(r"^Precinct\s*", "", p, flags=re.I)
    return p


def normalize_candidate(candidate: str) -> str:
    c = clean(candidate).replace('""""', '"')
    base_l = norm_key(c)
    if base_l in ("yes", "no", "none of these candidates", ""):
        return c.title()
    if c and c == c.upper():
        c = c.title()
        c = c.replace("U.S.", "U.S")
    # Standardize person names as "First M. Last", including paired tickets.
    if " and " in norm_key(c):
        parts = re.split(r"\s+and\s+", c, flags=re.I)
        norm_parts = [normalize_candidate(p) for p in parts]
        return " and ".join(norm_parts)
    raw = c.replace('"', "").strip()
    # Repair malformed suffix-leading labels like "Ii, G. Hafen" -> "G. Hafen II".
    m_suffix_lead = re.match(r"^(ii|iii|iv|jr|sr)\.?,\s*(.+)$", raw, flags=re.I)
    if m_suffix_lead:
        suffix = m_suffix_lead.group(1).upper().replace("JR", "Jr.").replace("SR", "Sr.")
        rhs = m_suffix_lead.group(2).strip()
        if suffix in ("II", "III", "IV"):
            raw = f"{rhs} {suffix}"
        else:
            raw = f"{rhs} {suffix}"
    first = ""
    middle = ""
    last = ""
    if "," in raw:
        parts = [p.strip() for p in raw.split(",", 1)]
        if len(parts) == 2:
            last = parts[0]
            rhs = parts[1].split()
            if rhs:
                first = rhs[0]
            if len(rhs) > 1:
                middle = rhs[1]
    else:
        toks = raw.split()
        if len(toks) >= 2:
            first = toks[0]
            last = toks[-1]
            if len(toks) > 2:
                middle = toks[1]
    if first and middle and last:
        return f"{first.title()} {middle[0].upper()}. {last.title()}"
    if first and last:
        return f"{first.title()} {last.title()}"
    return c


def convert_one(
    path: str,
    ref_oc: dict,
    ref_cpo: dict,
    ref_cand: dict,
    global_party_by_office_candidate: dict,
    global_party_by_candidate: dict,
    overrides: dict,
    override_skeletons: dict,
):
    year = parse_year_from_filename(path)
    p = Path(path)
    rows = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with p.open("r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    if rows is None:
        raise ValueError(f"Could not decode file: {path}")

    hidx, header = detect_header(rows)
    idx = column_index_map(header)

    out_rows = []
    for row in rows[hidx + 1 :]:
        if not row or not any(clean(c) for c in row):
            continue
        county = clean(row[idx["county"]] if idx["county"] < len(row) else "")
        precinct = normalize_precinct(row[idx["precinct"]] if idx["precinct"] < len(row) else "")
        raw_office = clean(row[idx["office"]] if idx["office"] < len(row) else "")
        office = normalize_office(raw_office)
        raw_candidate = clean(row[idx["candidate"]] if idx["candidate"] < len(row) else "")
        candidate = normalize_candidate(raw_candidate)
        votes = parse_votes(row[idx["votes"]] if idx["votes"] < len(row) else "")
        if not county and not precinct and not office and not candidate:
            continue
        district = normalize_district(raw_office)
        party = infer_party(
            year,
            county,
            precinct,
            office,
            district,
            raw_candidate,
            ref_oc,
            ref_cpo,
            ref_cand,
            global_party_by_office_candidate,
            global_party_by_candidate,
            overrides,
            override_skeletons,
        )
        party = PARTY_FULL_NAME.get(party, party)
        out_rows.append(
            {
                "county": county,
                "precinct": precinct,
                "office": office,
                "district": district,
                "party": party,
                "candidate": candidate,
                "votes": votes,
            }
        )

    # Fill residual blanks for partisan contests when the same office+candidate has
    # a known party elsewhere in this file.
    local_party = {}
    for r in out_rows:
        if not is_partisan_office(r["office"]):
            continue
        if not r["party"]:
            continue
        for cv in candidate_variants(r["candidate"]):
            local_party[(office_key(r["office"]), cv)] = r["party"]
    for r in out_rows:
        if r["party"] or not is_partisan_office(r["office"]):
            continue
        if candidate_key(r["candidate"]) in ("", "none of these candidates", "yes", "no"):
            continue
        for cv in candidate_variants(r["candidate"]):
            p = local_party.get((office_key(r["office"]), cv), "")
            if p:
                r["party"] = p
                break
    for r in out_rows:
        if not is_partisan_office(r["office"]) or not r["party"]:
            continue
        offk = office_key(r["office"])
        for cv in candidate_variants(r["candidate"]):
            global_party_by_office_candidate[(offk, cv)] = r["party"]
            if cv not in global_party_by_candidate:
                global_party_by_candidate[cv] = r["party"]

    election_date = ELECTION_DATES.get(year, f"{year}1101")
    out_name = f"{election_date}__nv__general__precinct.csv"
    out_path = OUT_DIR / out_name
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["county", "precinct", "office", "district", "party", "candidate", "votes"],
        )
        w.writeheader()
        w.writerows(out_rows)
    return out_path, len(out_rows)


def main():
    ref_oc, ref_cpo, ref_cand = load_reference_party_maps()
    overrides, override_skeletons = load_party_overrides()
    global_party_by_office_candidate = {}
    global_party_by_candidate = {}
    for path in INPUT_FILES:
        out_path, n = convert_one(
            path,
            ref_oc,
            ref_cpo,
            ref_cand,
            global_party_by_office_candidate,
            global_party_by_candidate,
            overrides,
            override_skeletons,
        )
        print(f"Wrote {out_path} ({n} rows)")


if __name__ == "__main__":
    main()
