import csv
import json
import re
from collections import defaultdict, Counter
from pathlib import Path


OE_DIR = Path("data/openelections")
CROSSWALK_VTD_CD = Path("data/crosswalks/vtd20_to_cd118.csv")
VTD20_SHP = Path("data/census/tl_2020_32_vtd20/tl_2020_32_vtd20.shp")
COUNTY20_SHP = Path("data/census/tl_2020_32_county20/tl_2020_32_county20.shp")
OUT_DIR = Path("data/district_contests")


def clean(s: str) -> str:
    return (s or "").strip()


def to_int(v: str) -> int:
    s = clean(v).replace(",", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def norm(s: str) -> str:
    s = clean(s).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def calc_color(margin_pct: float) -> str:
    abs_margin = abs(margin_pct)
    if margin_pct > 0:
        if abs_margin >= 50:
            return "#67000d"
        if abs_margin >= 20:
            return "#ef3b2c"
        if abs_margin >= 10:
            return "#fc8d59"
        if abs_margin >= 5:
            return "#fdbb84"
        if abs_margin >= 1:
            return "#fdd49e"
        return "#fee8c8"
    if margin_pct < 0:
        if abs_margin >= 50:
            return "#08306b"
        if abs_margin >= 20:
            return "#3182bd"
        if abs_margin >= 10:
            return "#4292c6"
        if abs_margin >= 5:
            return "#6baed6"
        if abs_margin >= 1:
            return "#9ecae1"
        return "#c6dbef"
    return "#f7f7f7"


def is_us_house_office(office: str) -> bool:
    o = clean(office).lower()
    return ("u.s. house" in o) or ("u.s. representative in congress" in o)


def is_us_senate_office(office: str) -> bool:
    o = clean(office).lower()
    return ("u.s. senate" in o) or ("united states senator" in o)


def load_cd_lookup() -> dict[str, str]:
    out = {}
    with CROSSWALK_VTD_CD.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out[clean(row.get("geoid20"))] = clean(row.get("district_id"))
    return out


def county_norm(s: str) -> str:
    x = norm(s)
    return x.replace(" county", "")


def clean_vtd_name(name20: str) -> str:
    s = clean(name20)
    s = re.sub(r"(?i)^precinct\s*(no\.?\s*)?", "", s).strip(" -")
    return s


def load_precinct_to_geoid() -> dict[tuple[str, str], str]:
    import shapefile

    cr = shapefile.Reader(str(COUNTY20_SHP))
    cfields = [f[0] for f in cr.fields[1:]]
    cfp_to_name = {}
    for rec in cr.records():
        d = {cfields[i]: rec[i] for i in range(len(cfields))}
        cfp_to_name[str(d["COUNTYFP20"]).zfill(3)] = d["NAMELSAD20"]

    vr = shapefile.Reader(str(VTD20_SHP))
    vfields = [f[0] for f in vr.fields[1:]]

    idx = defaultdict(set)
    for rec in vr.records():
        d = {vfields[i]: rec[i] for i in range(len(vfields))}
        cfp = str(d["COUNTYFP20"]).zfill(3)
        county = county_norm(cfp_to_name.get(cfp, ""))
        geoid = clean(str(d["GEOID20"]))
        vtdst = clean(str(d["VTDST20"]))
        name20 = clean(str(d["NAME20"]))
        keys = {(county, norm(vtdst.lstrip("0") or "0")), (county, norm(name20)), (county, norm(clean_vtd_name(name20)))}
        m = re.match(r"^(\d+[A-Za-z0-9\-]*)", clean_vtd_name(name20))
        if m:
            keys.add((county, norm(m.group(1))))
        for k in keys:
            idx[k].add(geoid)

    out = {}
    for k, vals in idx.items():
        if len(vals) == 1:
            out[k] = next(iter(vals))
    return out


def parse_year(path: Path) -> int:
    m = re.match(r"^(\d{4})\d{4}__nv__general__precinct\.csv$", path.name)
    if not m:
        raise ValueError(path.name)
    return int(m.group(1))


def build_for_year(path: Path, precinct_to_geoid: dict[tuple[str, str], str], geoid_to_cd: dict[str, str]) -> list[dict]:
    year = parse_year(path)
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    # Build house candidate co-occurrence components to infer precinct-level district clusters.
    precinct_house_candidates = defaultdict(set)
    precinct_house_votes = defaultdict(Counter)
    house_rows = []
    senate_rows = []
    for r in rows:
        office = clean(r.get("office"))
        if is_us_house_office(office):
            key = (clean(r.get("county")), clean(r.get("precinct")))
            cand = clean(r.get("candidate"))
            party = clean(r.get("party")).upper()
            precinct_house_votes[key][cand] += to_int(r.get("votes"))
            # Use only major-party candidate co-occurrence to avoid cross-district
            # bridges from minor-party candidates who run in multiple districts.
            if cand and cand.lower() != "none of these candidates" and party in {"DEM", "REP"}:
                precinct_house_candidates[key].add(cand)
            house_rows.append(r)
        elif is_us_senate_office(office):
            senate_rows.append(r)

    # DSU on candidates
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for cands in precinct_house_candidates.values():
        cands = sorted(cands)
        if len(cands) < 2:
            continue
        base = cands[0]
        for c in cands[1:]:
            union(base, c)

    comp_precincts = defaultdict(list)
    for pkey, cands in precinct_house_candidates.items():
        if not cands:
            continue
        root = find(sorted(cands)[0])
        comp_precincts[root].append(pkey)

    # Map components to CD ids via partial county/precinct -> geoid matching
    comp_cd_votes = defaultdict(Counter)
    for root, pkeys in comp_precincts.items():
        for county, precinct in pkeys:
            probes = [(county_norm(county), norm(precinct))]
            m = re.match(r"^(\d+[A-Za-z0-9\-]*)", clean(precinct))
            if m:
                probes.append((county_norm(county), norm(m.group(1))))
            g = ""
            for pr in probes:
                if pr in precinct_to_geoid:
                    g = precinct_to_geoid[pr]
                    break
            cd = geoid_to_cd.get(g, "")
            if cd:
                comp_cd_votes[root][cd] += 1

    comp_to_cd = {}
    used = set()
    for root, counts in sorted(comp_cd_votes.items(), key=lambda kv: sum(kv[1].values()), reverse=True):
        for cd, _ in counts.most_common():
            if cd not in used:
                comp_to_cd[root] = cd
                used.add(cd)
                break
    # fill remaining with available district ids
    for root in comp_precincts:
        if root not in comp_to_cd:
            for cd in ("01", "02", "03", "04"):
                if cd not in used:
                    comp_to_cd[root] = cd
                    used.add(cd)
                    break

    def _has(cand: str, needle: str) -> bool:
        return needle in cand.lower()

    # Deterministic precinct-level candidate anchors (NC-style congressional intent):
    # prefer known House nominees to avoid tiny crossovers merging components.
    anchored_precinct_cd = {}
    for pkey, cv in precinct_house_votes.items():
        cands = sorted(cv.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        names = [n for n, _ in cands]
        joined = " | ".join(names).lower()
        cd = ""
        if "amodei" in joined:
            cd = "02"
        elif "titus" in joined or "robertson" in joined:
            cd = "01"
        elif "lee, susie" in joined or "johnson, drew" in joined or "becker, april" in joined:
            cd = "03"
        elif "horsford" in joined or "lee, john" in joined or "peters, sam" in joined:
            cd = "04"
        if cd:
            anchored_precinct_cd[pkey] = cd

    precinct_to_cd = {}
    for root, pkeys in comp_precincts.items():
        for p in pkeys:
            precinct_to_cd[p] = anchored_precinct_cd.get(p, comp_to_cd[root])

    def aggregate_contest(contest_rows: list[dict], contest_type: str) -> dict:
        by_d = defaultdict(lambda: {"dem_votes": 0, "rep_votes": 0, "other_votes": 0})
        dem_name = defaultdict(Counter)
        rep_name = defaultdict(Counter)

        for r in contest_rows:
            pkey = (clean(r.get("county")), clean(r.get("precinct")))
            d = precinct_to_cd.get(pkey, "")
            if not d:
                continue
            party = clean(r.get("party")).upper()
            cand = clean(r.get("candidate"))
            votes = to_int(r.get("votes"))
            if party == "DEM":
                by_d[d]["dem_votes"] += votes
                dem_name[d][cand] += votes
            elif party == "REP":
                by_d[d]["rep_votes"] += votes
                rep_name[d][cand] += votes
            else:
                by_d[d]["other_votes"] += votes

        results = {}
        for d in ("01", "02", "03", "04"):
            dem = int(by_d[d]["dem_votes"])
            rep = int(by_d[d]["rep_votes"])
            oth = int(by_d[d]["other_votes"])
            total = dem + rep + oth
            margin = rep - dem
            margin_pct = (margin / total * 100.0) if total else 0.0
            results[str(int(d))] = {
                "dem_votes": dem,
                "rep_votes": rep,
                "other_votes": oth,
                "total_votes": total,
                "dem_candidate": dem_name[d].most_common(1)[0][0] if dem_name[d] else "",
                "rep_candidate": rep_name[d].most_common(1)[0][0] if rep_name[d] else "",
                "margin": margin,
                "margin_pct": round(margin_pct, 2),
                "winner": "REP" if margin > 0 else "DEM" if margin < 0 else "TIE",
                "competitiveness": {"color": calc_color(margin_pct)},
            }

        payload = {
            "year": year,
            "scope": "congressional",
            "contest_type": contest_type,
            "meta": {
                "source": "nv_openelections_precinct+house_component_inference",
                "office": "US HOUSE" if contest_type == "us_house" else "US SENATE",
                "nongeo_allocation_mode": "precinct_component",
                "match_coverage_pct": round((len(precinct_to_cd) / max(1, len(precinct_house_candidates))) * 100.0, 2),
                "matched_precinct_keys": len(precinct_to_cd),
                "total_precinct_keys": len(precinct_house_candidates),
            },
            "general": {"results": results},
        }
        return payload

    out = []
    out.append(("congressional_us_house_%d.json" % year, aggregate_contest(house_rows, "us_house")))
    out.append(("congressional_us_senate_%d.json" % year, aggregate_contest(senate_rows, "us_senate")))
    return out


def main():
    years = {2022, 2024}
    files = sorted(p for p in OE_DIR.glob("*__nv__general__precinct.csv") if parse_year(p) in years)
    geoid_to_cd = load_cd_lookup()
    precinct_to_geoid = load_precinct_to_geoid()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = []
    for p in files:
        generated = build_for_year(p, precinct_to_geoid, geoid_to_cd)
        for fname, payload in generated:
            out = OUT_DIR / fname
            out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            districts = len((payload.get("general", {}).get("results", {}) or {}))
            manifest.append(
                {
                    "year": int(payload["year"]),
                    "scope": "congressional",
                    "contest_type": payload["contest_type"],
                    "file": fname,
                    "districts": districts,
                }
            )
            print(f"Wrote {out} districts={districts}")

    manifest.sort(key=lambda x: (x["year"], x["scope"], x["contest_type"]))
    (OUT_DIR / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
