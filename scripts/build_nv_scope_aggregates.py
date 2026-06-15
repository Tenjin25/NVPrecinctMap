import csv
import json
import re
import argparse
from collections import defaultdict
from pathlib import Path

import shapefile


OE_DIR = Path("data/openelections")
CROSSWALKS_DIR = Path("data/crosswalks")
VTD20_SHP = Path("data/census/tl_2020_32_vtd20/tl_2020_32_vtd20.shp")
COUNTY20_SHP = Path("data/census/tl_2020_32_county20/tl_2020_32_county20.shp")
OUT_DIR = Path("data")
NEUTRAL_PRECINCT_OVERRIDES = Path("data/crosswalks/precinct_to_neutral_overrides.csv")

# A handful of 2018 statewide OpenElections rows omit the party field for
# major-party candidates. Keep those candidates out of `other` while leaving
# true third-party and NOTC rows untouched.
BLANK_PARTY_CANDIDATE_OVERRIDES: dict[tuple[str, str, str], str] = {
    ("2018", "attorney_general", "aaron ford"): "DEM",
    ("2018", "attorney_general", "wes duncan"): "REP",
    ("2018", "controller", "catherine byrne"): "DEM",
    ("2018", "lieutenant_governor", "michael roberson"): "REP",
    ("2018", "treasurer", "bob beers"): "REP",
}


def clean(v: str) -> str:
    return (v or "").strip()


def to_int(v: str) -> int:
    s = clean(v).replace(",", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def parse_year(path: Path) -> str:
    m = re.match(r"^(\d{4})\d{4}__", path.name)
    if not m:
        raise ValueError(path.name)
    return m.group(1)


def normalize_party(party: str) -> str:
    p = clean(party).lower()
    if p.startswith("dem"):
        return "DEM"
    if p.startswith("rep") or "gop" in p:
        return "REP"
    return ""


def resolve_party(year: str, contest: str, party: str, candidate: str) -> str:
    normalized = normalize_party(party)
    if normalized:
        return normalized
    return BLANK_PARTY_CANDIDATE_OVERRIDES.get(
        (str(year), contest, normalize_person_name(candidate).lower()),
        "",
    )


def contest_type(office: str) -> str:
    o = clean(office).lower()
    if "president" in o:
        return "president"
    if "u.s. house" in o or "u.s. representative in congress" in o:
        return "us_house"
    if "u.s. senate" in o or "united states senator" in o:
        return "us_senate"
    if "lieutenant governor" in o:
        return "lieutenant_governor"
    if "attorney general" in o:
        return "attorney_general"
    if "treasurer" in o:
        # Keep statewide treasurer only; exclude county/local clerk-treasurer offices.
        if "county" in o or "clerk" in o:
            return ""
        if "state treasurer" in o:
            return "treasurer"
        return ""
    if "controller" in o or "comptroller" in o:
        return "controller"
    if "governor" in o:
        return "governor"
    if "state senate" in o:
        return "state_senate"
    if "state assembly" in o or "state house" in o:
        return "state_assembly"
    return ""

def normalize_person_name(candidate: str) -> str:
    cand = clean(candidate)
    if not cand:
        return ""
    # Handle "Last, First" and malformed suffix-first forms like "Ii, G. Hafen".
    if "," in cand:
        left, right = [x.strip() for x in cand.split(",", 1)]
        left_up = left.upper().replace(".", "")
        if left_up in {"II", "III", "IV", "V"}:
            return f"{right} {left_up}".strip()
        return f"{right} {left}".strip()
    return cand

def normalize_candidate_name(contest: str, candidate: str) -> str:
    cand = normalize_person_name(candidate)
    if contest != "president":
        return cand
    # Strip running mate from ticket labels:
    # "Kamala D. Harris and Tim Walz" -> "Kamala D. Harris"
    # "Donald J. Trump / JD Vance" -> "Donald J. Trump"
    cand = re.split(r"\s+(?:and|&)\s+|/|;", cand, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return cand


def competitiveness_color(margin_pct: float) -> str:
    abs_margin = abs(margin_pct)
    if abs_margin < 0.5:
        return "#f7f7f7"
    if margin_pct > 0:
        if abs_margin >= 40:
            return "#67000d"
        if abs_margin >= 20:
            return "#cb181d"
        if abs_margin >= 10:
            return "#ef3b2c"
        if abs_margin >= 1:
            return "#fcae91"
        return "#fee8c8"
    if abs_margin >= 40:
        return "#08306b"
    if abs_margin >= 20:
        return "#3182bd"
    if abs_margin >= 10:
        return "#6baed6"
    if abs_margin >= 1:
        return "#c6dbef"
    return "#e1f5fe"


def pick_top_name(counter: dict[str, int]) -> str:
    if not counter:
        return ""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].lower()))[0][0]


def norm(s: str) -> str:
    s = clean(s).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def county_norm(s: str) -> str:
    return norm(s).replace(" county", "")


def clean_vtd_name(name20: str) -> str:
    s = clean(name20)
    s = re.sub(r"(?i)^precinct\s*(no\.?\s*)?", "", s).strip(" -")
    return s


def build_precinct_match_probes(county: str, precinct: str) -> list[tuple[str, str]]:
    c = county_norm(county)
    p_raw = clean(precinct)
    probes = []

    def add(v: str):
        nv = norm(v)
        if nv:
            probes.append((c, nv))

    add(p_raw)

    m_lead = re.match(r"^(\d+[A-Za-z0-9\-]*)", p_raw)
    if m_lead:
        add(m_lead.group(1))

    m_tail = re.search(r"(\d{3,8})\s*$", p_raw)
    if m_tail:
        tail = m_tail.group(1)
        add(tail)
        if len(tail) > 4 and tail.endswith("00"):
            add(tail[:-2])
        if len(tail) >= 5:
            add(tail[:4])

    compact = re.sub(r"[^0-9A-Za-z]", "", p_raw)
    if compact and compact != p_raw:
        add(compact)

    out = []
    seen = set()
    for pr in probes:
        if pr in seen:
            continue
        seen.add(pr)
        out.append(pr)
    return out


def load_precinct_to_geoid20() -> dict[tuple[str, str], str]:
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
        keys = {
            (county, norm(vtdst.lstrip("0") or "0")),
            (county, norm(name20)),
            (county, norm(clean_vtd_name(name20))),
        }
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


def load_vtd20_to_neutral() -> dict[str, str]:
    # Prefer block-derived weighted assignment when available.
    weighted_path = CROSSWALKS_DIR / "neutral_to_vtd20_weighted.csv"
    if weighted_path.exists():
        best = {}
        with weighted_path.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                geoid = clean(r.get("geoid20"))
                nid = clean(r.get("neutral_id"))
                w_raw = clean(r.get("alloc_weight"))
                try:
                    w = float(w_raw) if w_raw else 0.0
                except Exception:
                    w = 0.0
                if not geoid or not nid:
                    continue
                prev = best.get(geoid)
                if not prev or w > prev[1]:
                    best[geoid] = (nid, w)
        if best:
            return {g: v[0] for g, v in best.items()}

    out = {}
    with (CROSSWALKS_DIR / "neutral_to_vtd20.csv").open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            geoid = clean(r.get("geoid20"))
            nid = clean(r.get("neutral_id"))
            if geoid and nid:
                out[geoid] = nid
    return out


def neutral_vtd20_source_label() -> str:
    if (CROSSWALKS_DIR / "neutral_to_vtd20_weighted.csv").exists():
        return "neutral_to_vtd20_weighted (block-derived)"
    return "neutral_to_vtd20 (direct)"


def load_precinct_to_neutral_overrides() -> dict[tuple[str, str], str]:
    out = {}
    if not NEUTRAL_PRECINCT_OVERRIDES.exists():
        return out
    with NEUTRAL_PRECINCT_OVERRIDES.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            county = clean(r.get("county", ""))
            precinct = clean(r.get("precinct", ""))
            neutral_id = clean(r.get("neutral_id", ""))
            if county and precinct and neutral_id:
                out[(county, precinct)] = neutral_id
    return out

def load_vtd20_to_cd() -> dict[str, str]:
    out = {}
    with (CROSSWALKS_DIR / "vtd20_to_cd118.csv").open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            geoid = clean(r.get("geoid20"))
            cd = clean(r.get("district_id")).zfill(2)
            if geoid and cd:
                out[geoid] = cd
    return out


def load_cd_to_neutral_shares() -> dict[str, list[tuple[str, float]]]:
    vtd_to_cd = {}
    with (CROSSWALKS_DIR / "vtd20_to_cd118.csv").open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            vtd_to_cd[clean(r.get("geoid20"))] = clean(r.get("district_id"))

    overlap = defaultdict(lambda: defaultdict(int))
    with (CROSSWALKS_DIR / "neutral_to_vtd20.csv").open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            vtd = clean(r.get("geoid20"))
            neutral_id = clean(r.get("neutral_id"))
            cd = vtd_to_cd.get(vtd, "")
            if cd and neutral_id:
                overlap[cd][neutral_id] += 1

    cd_to_neutral_shares = {}
    for cd, counts in overlap.items():
        total = sum(counts.values())
        if total <= 0:
            continue
        cd_to_neutral_shares[cd.zfill(2)] = [
            (nid, cnt / total) for nid, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if cnt > 0
        ]
    return cd_to_neutral_shares


def aggregate_rows(rows: list[dict], group_key_name: str, year: str) -> dict:
    grouped = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    dem_name = defaultdict(lambda: defaultdict(int))
    rep_name = defaultdict(lambda: defaultdict(int))

    for row in rows:
        group_key = clean(row.get(group_key_name, ""))
        if not group_key:
            continue
        contest = contest_type(clean(row.get("office", "")))
        p = resolve_party(year, contest, row.get("party", ""), row.get("candidate", ""))
        cand = normalize_candidate_name(contest, row.get("candidate", ""))
        votes = to_int(row.get("votes", ""))
        if p == "DEM":
            grouped[group_key]["dem"] += votes
            if cand:
                dem_name[group_key][cand] += votes
        elif p == "REP":
            grouped[group_key]["rep"] += votes
            if cand:
                rep_name[group_key][cand] += votes
        else:
            grouped[group_key]["other"] += votes

    out = {}
    for key in sorted(grouped):
        dem = grouped[key]["dem"]
        rep = grouped[key]["rep"]
        other = grouped[key]["other"]
        total = dem + rep + other
        margin = rep - dem
        margin_pct = (margin / total * 100.0) if total else 0.0
        out[key] = {
            "dem_votes": dem,
            "rep_votes": rep,
            "other_votes": other,
            "total_votes": total,
            "dem_candidate": pick_top_name(dem_name[key]),
            "rep_candidate": pick_top_name(rep_name[key]),
            "margin": margin,
            "margin_pct": round(margin_pct, 2),
            "winner": "REP" if margin > 0 else "DEM" if margin < 0 else "TIE",
            "competitiveness": {"color": competitiveness_color(margin_pct)},
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rebuild-neutral",
        action="store_true",
        help="Recompute and overwrite nv_neutral_congressional_aggregated.json.",
    )
    args = parser.parse_args()

    cd_to_neutral_shares = load_cd_to_neutral_shares()
    precinct_to_geoid20 = load_precinct_to_geoid20()
    vtd20_to_cd = load_vtd20_to_cd()
    vtd20_to_neutral = load_vtd20_to_neutral()
    precinct_to_neutral_overrides = load_precinct_to_neutral_overrides()
    legislative = {"results_by_year": {}}
    congressional = {"results_by_year": {}}
    neutral_congressional = {
        "results_by_year": {},
        "meta": {"cd_to_neutral_shares": cd_to_neutral_shares, "neutral_source": neutral_vtd20_source_label()},
    }

    for path in sorted(OE_DIR.glob("*__nv__general__precinct.csv")):
        year = parse_year(path)
        rows = []
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

        by_type = defaultdict(list)
        for row in rows:
            t = contest_type(row.get("office", ""))
            if not t:
                continue
            by_type[t].append(row)

        # Precinct -> congressional district map, derived from same-year US House rows.
        pkey_to_cd = {}
        for r in by_type.get("us_house", []):
            pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
            cd = clean(r.get("district", "")).zfill(2)
            if pkey[0] and pkey[1] and cd:
                pkey_to_cd[pkey] = cd

        # Fallback map via precinct -> VTD20 -> CD118 for years where house rows are incomplete/missing.
        all_pkeys = {(clean(r.get("county", "")), clean(r.get("precinct", ""))) for r in rows}
        for county, precinct in all_pkeys:
            pkey = (county, precinct)
            if pkey in pkey_to_cd:
                continue
            probes = build_precinct_match_probes(county, precinct)
            geoid = ""
            for pr in probes:
                if pr in precinct_to_geoid20:
                    geoid = precinct_to_geoid20[pr]
                    break
            cd = vtd20_to_cd.get(geoid, "")
            if cd:
                pkey_to_cd[pkey] = cd

        # Legislative (district field from OE rows)
        year_leg = {}
        for t in ("state_senate", "state_assembly"):
            trows = [r for r in by_type.get(t, []) if clean(r.get("district", ""))]
            if trows:
                year_leg[t] = {"general": {"results": aggregate_rows(trows, "district", year)}}
        if year_leg:
            legislative["results_by_year"][year] = year_leg

        # Congressional by district for all requested contests.
        year_cong = {}
        congressional_types = (
            "president",
            "us_senate",
            "governor",
            "lieutenant_governor",
            "attorney_general",
            "treasurer",
            "controller",
            "us_house",
        )
        for t in congressional_types:
            source_rows = by_type.get(t, [])
            if not source_rows:
                continue
            if t == "us_house":
                trows = []
                for r in source_rows:
                    d = clean(r.get("district", ""))
                    if not d:
                        continue
                    rc = dict(r)
                    rc["district"] = d.zfill(2)
                    trows.append(rc)
                if trows:
                    year_cong[t] = {"general": {"results": aggregate_rows(trows, "district", year)}}
                continue

            # For non-house contests, allocate precincts to CDs via same-year House district map.
            trows = []
            for r in source_rows:
                pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
                cd = pkey_to_cd.get(pkey, "")
                if not cd:
                    continue
                rc = dict(r)
                rc["district"] = cd
                trows.append(rc)
            if trows:
                year_cong[t] = {"general": {"results": aggregate_rows(trows, "district", year)}}
        if year_cong:
            congressional["results_by_year"][year] = year_cong

        if args.rebuild_neutral:
            # Neutral congressional for the same contest set as congressional.
            # Primary path: precinct -> VTD20 -> neutral_id.
            # Backfill path: unmatched precinct rows are allocated by CD->neutral overlap shares (crosswalk-derived).
            pkey_to_neutral = {}
            for county, precinct in {(clean(r.get("county", "")), clean(r.get("precinct", ""))) for r in rows}:
                if (county, precinct) in precinct_to_neutral_overrides:
                    pkey_to_neutral[(county, precinct)] = precinct_to_neutral_overrides[(county, precinct)]
                    continue
                probes = build_precinct_match_probes(county, precinct)
                geoid = ""
                for pr in probes:
                    if pr in precinct_to_geoid20:
                        geoid = precinct_to_geoid20[pr]
                        break
                if geoid and geoid in vtd20_to_neutral:
                    pkey_to_neutral[(county, precinct)] = vtd20_to_neutral[geoid]

            year_neutral = {}
            for t in congressional_types:
                source_rows = by_type.get(t, [])
                if not source_rows:
                    continue
                neutral_rows = []
                total_source = 0
                matched_source = 0
                backfilled_source = 0
                for r in source_rows:
                    pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
                    total_source += 1
                    nid = pkey_to_neutral.get(pkey, "")
                    if nid:
                        matched_source += 1
                        rc = dict(r)
                        rc["neutral_id"] = nid
                        neutral_rows.append(rc)
                        continue

                    cd = pkey_to_cd.get(pkey, "") or clean(r.get("district", "")).zfill(2)
                    shares = cd_to_neutral_shares.get(cd, [])
                    votes_total = to_int(r.get("votes", ""))
                    if not shares or votes_total <= 0:
                        continue

                    backfilled_source += 1
                    remaining = votes_total
                    for i, (share_nid, share_w) in enumerate(shares):
                        if i == len(shares) - 1:
                            alloc = remaining
                        else:
                            alloc = int(round(votes_total * share_w))
                            alloc = max(0, min(remaining, alloc))
                        remaining -= alloc
                        if alloc <= 0:
                            continue
                        rc = dict(r)
                        rc["neutral_id"] = share_nid
                        rc["votes"] = str(alloc)
                        neutral_rows.append(rc)
                if neutral_rows:
                    coverage = (matched_source / total_source * 100.0) if total_source else 0.0
                    allocated_coverage = ((matched_source + backfilled_source) / total_source * 100.0) if total_source else 0.0
                    year_neutral[t] = {
                        "general": {"results": aggregate_rows(neutral_rows, "neutral_id", year)},
                        "meta": {
                            "match_rows": matched_source,
                            "backfilled_rows": backfilled_source,
                            "total_rows": total_source,
                            "match_coverage_pct": round(coverage, 2),
                            "allocated_coverage_pct": round(allocated_coverage, 2),
                            "mapping": "precinct -> VTD20 -> neutral_id (+ CD->neutral overlap-share backfill)"
                        }
                    }
            if year_neutral:
                neutral_congressional["results_by_year"][year] = year_neutral

    leg_path = OUT_DIR / "nv_legislative_aggregated.json"
    cong_path = OUT_DIR / "nv_congressional_aggregated.json"
    neutral_cong_path = OUT_DIR / "nv_neutral_congressional_aggregated.json"

    leg_path.write_text(json.dumps(legislative, indent=2), encoding="utf-8")
    cong_path.write_text(json.dumps(congressional, indent=2), encoding="utf-8")
    if args.rebuild_neutral:
        neutral_cong_path.write_text(json.dumps(neutral_congressional, indent=2), encoding="utf-8")
    print(f"Wrote {leg_path}")
    print(f"Wrote {cong_path}")
    if args.rebuild_neutral:
        print(f"Wrote {neutral_cong_path}")
    else:
        print(f"Left existing {neutral_cong_path} unchanged (use --rebuild-neutral to overwrite)")

    # Write high-impact unmatched precinct rows report for targeted override cleanup.
    report_rows = []
    for path in sorted(OE_DIR.glob("*__nv__general__precinct.csv")):
        year = parse_year(path)
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        vote_by_pkey = defaultdict(int)
        for r in rows:
            pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
            vote_by_pkey[pkey] += to_int(r.get("votes", ""))
        for (county, precinct), votes in vote_by_pkey.items():
            if (county, precinct) in precinct_to_neutral_overrides:
                continue
            matched = False
            probes = build_precinct_match_probes(county, precinct)
            for pr in probes:
                geoid = precinct_to_geoid20.get(pr, "")
                if geoid and geoid in vtd20_to_neutral:
                    matched = True
                    break
            if not matched:
                report_rows.append({"year": int(year), "county": county, "precinct": precinct, "votes": votes})
    report_rows.sort(key=lambda r: (r["year"], -r["votes"], r["county"], r["precinct"]))
    report_path = OUT_DIR / "openelections" / "_neutral_unmatched_precinct_rows.csv"
    with report_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["year", "county", "precinct", "votes"])
        w.writeheader()
        w.writerows(report_rows)
    print(f"Wrote {report_path} rows={len(report_rows)}")


if __name__ == "__main__":
    main()
