import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from build_nv_scope_aggregates import (
    build_precinct_match_probes,
    clean,
    contest_type,
    load_precinct_to_geoid20,
    load_vtd20_to_cd,
    normalize_candidate_name,
    parse_year,
    pick_top_name,
    resolve_party,
    to_int,
    competitiveness_color,
)


OE_DIR = Path("data/openelections")
OUT_PATH = Path("data/nv_congressional_aggregated.json")


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
    precinct_to_geoid20 = load_precinct_to_geoid20()
    vtd20_to_cd = load_vtd20_to_cd()
    congressional = {"results_by_year": {}}

    for path in sorted(OE_DIR.glob("*__nv__general__precinct.csv")):
        year = parse_year(path)
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

        by_type = defaultdict(list)
        for row in rows:
            t = contest_type(row.get("office", ""))
            if t:
                by_type[t].append(row)

        pkey_to_cd = {}
        for r in by_type.get("us_house", []):
            pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
            cd = clean(r.get("district", "")).zfill(2)
            if pkey[0] and pkey[1] and cd:
                pkey_to_cd[pkey] = cd

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

    OUT_PATH.write_text(json.dumps(congressional, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
