import csv
import argparse
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import shapefile
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from build_nv_scope_aggregates import (
    build_precinct_match_probes,
    load_precinct_to_geoid20,
    resolve_party,
)


OE_DIR = Path("data/openelections")
OUT_DIR = Path("data/district_contests")
CROSSWALKS_DIR = Path("data/crosswalks")
VTD20_SHP = Path("data/census/tl_2020_32_vtd20/tl_2020_32_vtd20.shp")
TABBLOCK20_SHP = Path("data/census/tl_2022_32_tabblock20/tl_2022_32_tabblock20.shp")


def clean(v: str) -> str:
    return (v or "").strip()


def normalize_legislative_district(v: str) -> str:
    district = clean(v)
    if re.fullmatch(r"\d+", district):
        return str(int(district))
    return district


def build_district_match_probes(county: str, precinct: str, year: int) -> list[tuple[str, str]]:
    probes = list(build_precinct_match_probes(county, precinct))
    if year != 2018:
        return probes

    # The 2018 export often zero-pads numeric precinct tokens and identifies
    # split precincts as "25-2" where VTD20 uses the parent code "25".
    # These aliases are accepted only later when the VTD index resolves them
    # to one unique geography.
    seen = set(probes)
    for county_key, precinct_key in list(probes):
        unpadded = re.sub(r"\b0+(\d+)\b", r"\1", precinct_key)
        for candidate in (unpadded,):
            key = (county_key, candidate)
            if candidate and key not in seen:
                seen.add(key)
                probes.append(key)
        split_match = re.match(r"^0*(\d+)\s+\d+(?:\s|$)", unpadded)
        if split_match:
            key = (county_key, str(int(split_match.group(1))))
            if key not in seen:
                seen.add(key)
                probes.append(key)
    return probes


def fill_unmatched_with_county_mode(
    pkey_to_district: dict[tuple[str, str], str],
    all_pkeys: set[tuple[str, str]],
) -> None:
    counts = defaultdict(lambda: defaultdict(int))
    for (county, _), district in pkey_to_district.items():
        if county and district:
            counts[county][district] += 1
    county_mode = {
        county: min(districts, key=lambda district: (-districts[district], district))
        for county, districts in counts.items()
        if districts
    }
    for pkey in all_pkeys:
        if pkey not in pkey_to_district and pkey[0] in county_mode:
            pkey_to_district[pkey] = county_mode[pkey[0]]


def to_int(v: str) -> int:
    s = clean(v).replace(",", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def parse_year(path: Path) -> int:
    m = re.match(r"^(\d{4})\d{4}__nv__general__precinct\.csv$", path.name)
    if not m:
        raise ValueError(path.name)
    return int(m.group(1))


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


def normalize_party(party: str) -> str:
    p = clean(party).lower()
    if p.startswith("dem"):
        return "DEM"
    if p.startswith("rep") or "gop" in p:
        return "REP"
    return ""


def normalize_person_name(candidate: str) -> str:
    cand = clean(candidate)
    if not cand:
        return ""
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
    return re.split(r"\s+(?:and|&)\s+|/|;", cand, maxsplit=1, flags=re.IGNORECASE)[0].strip()


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


def load_vtd20_to_district(path: Path) -> dict[str, str]:
    out = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            geoid = clean(r.get("geoid20"))
            d = clean(r.get("district_id"))
            if geoid and d:
                out[geoid] = d
    return out


@lru_cache(maxsize=1)
def load_tabblock20_derived_vtd20_districts() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Assign each VTD20 to the district containing most of its Census tabblocks."""
    direct_maps = {
        "cd": load_vtd20_to_district(CROSSWALKS_DIR / "vtd20_to_cd118.csv"),
        "sldl": load_vtd20_to_district(CROSSWALKS_DIR / "vtd20_to_sldl.csv"),
        "sldu": load_vtd20_to_district(CROSSWALKS_DIR / "vtd20_to_sldu.csv"),
    }
    block_maps = {
        "cd": load_vtd20_to_district(CROSSWALKS_DIR / "tabblock20_to_cd118.csv"),
        "sldl": load_vtd20_to_district(CROSSWALKS_DIR / "tabblock20_to_sldl.csv"),
        "sldu": load_vtd20_to_district(CROSSWALKS_DIR / "tabblock20_to_sldu.csv"),
    }

    vtd_reader = shapefile.Reader(str(VTD20_SHP))
    vtd_fields = [f[0] for f in vtd_reader.fields[1:]]
    vtd_geometries = []
    vtd_geoids = []
    for sr in vtd_reader.iterShapeRecords():
        record = {vtd_fields[i]: sr.record[i] for i in range(len(vtd_fields))}
        vtd_geometries.append(shape(sr.shape.__geo_interface__))
        vtd_geoids.append(clean(str(record.get("GEOID20", ""))))
    tree = STRtree(vtd_geometries)

    counts = {
        scope: defaultdict(lambda: defaultdict(int))
        for scope in block_maps
    }
    block_reader = shapefile.Reader(str(TABBLOCK20_SHP))
    block_fields = [f[0] for f in block_reader.fields[1:]]
    for record_values in block_reader.iterRecords():
        record = {block_fields[i]: record_values[i] for i in range(len(block_fields))}
        block_geoid = clean(str(record.get("GEOID20", "")))
        try:
            point = Point(float(record["INTPTLON20"]), float(record["INTPTLAT20"]))
        except (KeyError, TypeError, ValueError):
            continue
        hits = tree.query(point, predicate="intersects")
        if len(hits) == 0:
            continue
        vtd_geoid = vtd_geoids[int(hits[0])]
        for scope, district_by_block in block_maps.items():
            district = clean(district_by_block.get(block_geoid, ""))
            if district:
                counts[scope][vtd_geoid][district] += 1

    out = {}
    for scope, by_vtd in counts.items():
        assignments = {}
        for vtd_geoid, district_counts in by_vtd.items():
            direct = direct_maps[scope].get(vtd_geoid, "")
            assignments[vtd_geoid] = min(
                district_counts,
                key=lambda district: (
                    -district_counts[district],
                    0 if district == direct else 1,
                    district,
                ),
            )
        out[scope] = assignments
    return out["cd"], out["sldl"], out["sldu"]


def aggregate_rows(rows: list[dict], group_key_name: str, ctype: str, year: int) -> dict:
    grouped = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    dem_name = defaultdict(lambda: defaultdict(int))
    rep_name = defaultdict(lambda: defaultdict(int))

    for row in rows:
        group_key = clean(row.get(group_key_name, ""))
        if not group_key:
            continue
        p = resolve_party(str(year), ctype, row.get("party", ""), row.get("candidate", ""))
        cand = normalize_candidate_name(ctype, row.get("candidate", ""))
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
    for key in sorted(grouped, key=lambda x: (0, int(str(x))) if str(x).isdigit() else (1, str(x))):
        dem = grouped[key]["dem"]
        rep = grouped[key]["rep"]
        other = grouped[key]["other"]
        total = dem + rep + other
        margin = rep - dem
        margin_pct = (margin / total * 100.0) if total else 0.0
        out[str(key)] = {
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


def build_for_year(path: Path) -> list[tuple[str, dict]]:
    year = parse_year(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    by_type = defaultdict(list)
    for row in rows:
        t = contest_type(row.get("office", ""))
        if t:
            by_type[t].append(row)

    precinct_to_geoid20 = load_precinct_to_geoid20()
    if year == 2018:
        vtd20_to_cd, vtd20_to_sldl, vtd20_to_sldu = load_tabblock20_derived_vtd20_districts()
    else:
        vtd20_to_cd = load_vtd20_to_district(CROSSWALKS_DIR / "vtd20_to_cd118.csv")
        vtd20_to_sldl = load_vtd20_to_district(CROSSWALKS_DIR / "vtd20_to_sldl.csv")
        vtd20_to_sldu = load_vtd20_to_district(CROSSWALKS_DIR / "vtd20_to_sldu.csv")

    pkey_to_cd = {}
    for r in by_type.get("us_house", []):
        pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
        d = clean(r.get("district", "")).zfill(2)
        if pkey[0] and pkey[1] and d:
            pkey_to_cd[pkey] = d

    pkey_to_sldl = {}
    for r in by_type.get("state_assembly", []):
        pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
        d = normalize_legislative_district(r.get("district", ""))
        if pkey[0] and pkey[1] and d:
            pkey_to_sldl[pkey] = d

    pkey_to_sldu = {}
    for r in by_type.get("state_senate", []):
        pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
        d = normalize_legislative_district(r.get("district", ""))
        if pkey[0] and pkey[1] and d:
            pkey_to_sldu[pkey] = d

    all_pkeys = {(clean(r.get("county", "")), clean(r.get("precinct", ""))) for r in rows}
    for county, precinct in all_pkeys:
        pkey = (county, precinct)
        probes = build_district_match_probes(county, precinct, year)
        geoid = ""
        for pr in probes:
            if pr in precinct_to_geoid20:
                geoid = precinct_to_geoid20[pr]
                break
        if not geoid:
            continue
        # Reallocate 2018 statewide contests onto the enacted 2022 district
        # layers. Same-year office labels remain a fallback for precincts that
        # cannot be matched through the supplied tabblock-derived crosswalk.
        prefer_crosswalk = year == 2018
        if geoid in vtd20_to_cd and (prefer_crosswalk or pkey not in pkey_to_cd):
            pkey_to_cd[pkey] = clean(vtd20_to_cd[geoid]).zfill(2)
        if geoid in vtd20_to_sldl and (prefer_crosswalk or pkey not in pkey_to_sldl):
            pkey_to_sldl[pkey] = normalize_legislative_district(vtd20_to_sldl[geoid])
        if geoid in vtd20_to_sldu and (prefer_crosswalk or pkey not in pkey_to_sldu):
            pkey_to_sldu[pkey] = normalize_legislative_district(vtd20_to_sldu[geoid])

    if year == 2018:
        fill_unmatched_with_county_mode(pkey_to_cd, all_pkeys)
        fill_unmatched_with_county_mode(pkey_to_sldl, all_pkeys)
        fill_unmatched_with_county_mode(pkey_to_sldu, all_pkeys)

    common_types = (
        "president",
        "us_senate",
        "governor",
        "lieutenant_governor",
        "attorney_general",
        "treasurer",
        "controller",
        "us_house",
    )

    out = []

    # Congressional scope
    for t in common_types:
        source_rows = by_type.get(t, [])
        if not source_rows:
            continue
        if t == "us_house":
            trows = []
            for r in source_rows:
                d = clean(r.get("district", "")).zfill(2)
                if not d:
                    continue
                rc = dict(r)
                rc["district"] = d
                trows.append(rc)
            if not trows:
                continue
        else:
            trows = []
            for r in source_rows:
                pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
                d = pkey_to_cd.get(pkey, "")
                if not d:
                    continue
                rc = dict(r)
                rc["district"] = d
                trows.append(rc)
            if not trows:
                continue
        results = aggregate_rows(trows, "district", t, year)
        payload = {
            "year": year,
            "scope": "congressional",
            "contest_type": t,
            "meta": {
                "source": "nv_openelections_precinct",
                "nongeo_allocation_mode": (
                    "vtd20_tabblock20_majority_crosswalk_with_county_fallback"
                    if year == 2018 and t != "us_house"
                    else "precinct_map"
                ),
            },
            "general": {"results": results},
        }
        out.append((f"congressional_{t}_{year}.json", payload))

    # State Assembly scope
    for t in ("state_assembly",) + common_types:
        source_rows = by_type.get(t, [])
        if not source_rows:
            continue
        if t == "state_assembly":
            trows = []
            for r in source_rows:
                d = normalize_legislative_district(r.get("district", ""))
                if not d:
                    continue
                rc = dict(r)
                rc["district"] = d
                trows.append(rc)
        else:
            trows = []
            for r in source_rows:
                pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
                d = pkey_to_sldl.get(pkey, "")
                if not d:
                    continue
                rc = dict(r)
                rc["district"] = d
                trows.append(rc)
        if not trows:
            continue
        results = aggregate_rows(trows, "district", t, year)
        payload = {
            "year": year,
            "scope": "state_house",
            "contest_type": t,
            "meta": {
                "source": "nv_openelections_precinct",
                "nongeo_allocation_mode": (
                    "vtd20_tabblock20_majority_crosswalk_with_county_fallback"
                    if year == 2018 and t != "state_assembly"
                    else "precinct_map"
                ),
            },
            "general": {"results": results},
        }
        out.append((f"state_house_{t}_{year}.json", payload))

    # State Senate scope
    for t in ("state_senate",) + common_types:
        source_rows = by_type.get(t, [])
        if not source_rows:
            continue
        if t == "state_senate":
            trows = []
            for r in source_rows:
                d = normalize_legislative_district(r.get("district", ""))
                if not d:
                    continue
                rc = dict(r)
                rc["district"] = d
                trows.append(rc)
        else:
            trows = []
            for r in source_rows:
                pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
                d = pkey_to_sldu.get(pkey, "")
                if not d:
                    continue
                rc = dict(r)
                rc["district"] = d
                trows.append(rc)
        if not trows:
            continue
        results = aggregate_rows(trows, "district", t, year)
        payload = {
            "year": year,
            "scope": "state_senate",
            "contest_type": t,
            "meta": {
                "source": "nv_openelections_precinct",
                "nongeo_allocation_mode": (
                    "vtd20_tabblock20_majority_crosswalk_with_county_fallback"
                    if year == 2018 and t != "state_senate"
                    else "precinct_map"
                ),
            },
            "general": {"results": results},
        }
        out.append((f"state_senate_{t}_{year}.json", payload))

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Nevada district contest slices.")
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        help="Rebuild only these election years and preserve other manifest entries.",
    )
    args = parser.parse_args()
    selected_years = set(args.years or [])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []

    manifest_path = OUT_DIR / "manifest.json"
    if selected_years and manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])
        manifest.extend(e for e in existing if int(e.get("year", 0)) not in selected_years)

    for p in sorted(OE_DIR.glob("*__nv__general__precinct.csv")):
        if selected_years and parse_year(p) not in selected_years:
            continue
        generated = build_for_year(p)
        for fname, payload in generated:
            y = int(payload["year"])
            scope = payload["scope"]
            ctype = payload["contest_type"]
            keep = (
                # Keep U.S. House confined to the recent congressional selector;
                # 2018 contributes its statewide contests, but not U.S. House.
                (
                    scope == "congressional"
                    and (
                        (ctype == "us_house" and y in {2022, 2024})
                        or (y == 2018 and ctype != "us_house")
                    )
                ) or
                (
                    scope == "state_house"
                    and ctype != "us_house"
                    and (ctype != "state_assembly" or y >= 2022)
                ) or
                (
                    scope == "state_senate"
                    and ctype != "us_house"
                    and (ctype != "state_senate" or y >= 2022)
                )
            )
            if not keep:
                continue
            out = OUT_DIR / fname
            out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            districts = len((payload.get("general", {}).get("results", {}) or {}))
            manifest.append(
                {
                    "year": y,
                    "scope": scope,
                    "contest_type": ctype,
                    "file": fname,
                    "districts": districts,
                }
            )

    manifest.sort(key=lambda x: (x["year"], x["scope"], x["contest_type"]))
    manifest_path.write_text(json.dumps({"files": manifest}, indent=2), encoding="utf-8")
    print(f"Wrote {manifest_path} entries={len(manifest)}")


if __name__ == "__main__":
    main()
