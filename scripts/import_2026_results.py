"""Build 2026 Nevada result slices from Call the Map's published CSV tables.

Input files in data/2026_source are Nevada-only rows from the site's
November 19, 2026 open-data release. Counts originate with state/county
election offices via civicAPI and remain unofficial until state canvass.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "2026_source"
CONTESTS = ROOT / "data" / "contests"
DISTRICTS = ROOT / "data" / "district_contests"

RACE_TYPES = {
    "AG-NV": "attorney_general",
    "CTRL-NV": "controller",
    "GOV-NV": "governor",
    "LG-NV": "lieutenant_governor",
    "SOS-NV": "secretary_of_state",
    "TREAS-NV": "treasurer",
}


def read_csv(name):
    with (SOURCE / name).open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # The upstream CSV replaces the accent in this candidate's name.
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.replace("Guzm�n", "Guzmán")
    return rows


def result(dem, rep, other, dem_name="", rep_name=""):
    total = dem + rep + other
    margin = rep - dem
    return {
        "dem_votes": dem, "rep_votes": rep, "other_votes": other,
        "total_votes": total, "dem_candidate": dem_name,
        "rep_candidate": rep_name, "margin": margin,
        "margin_pct": round(100 * margin / total, 2) if total else 0,
        "winner": "REP" if margin > 0 else ("DEM" if margin < 0 else "TIE"),
    }


def write(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    counties = read_csv("counties.csv")
    races = read_csv("races.csv")
    legislatures = read_csv("legislatures.csv")
    by_race = defaultdict(list)
    for row in counties:
        by_race[row["race_id"]].append(row)
    source_meta = {
        "source": "Call the Map / civicAPI, compiled from Nevada election offices",
        "source_url": "https://www.callthemap.com/results/data",
        "source_read_utc": max(row["source_read_utc"] for row in races),
        "status": "unofficial",
        "geography": "county",
    }
    contest_manifest = json.loads((CONTESTS / "manifest.json").read_text(encoding="utf-8"))
    district_manifest = json.loads((DISTRICTS / "manifest.json").read_text(encoding="utf-8"))
    contest_manifest["files"] = [f for f in contest_manifest["files"] if f["year"] != 2026]
    district_manifest["files"] = [f for f in district_manifest["files"] if f["year"] != 2026]

    for race_id, contest_type in RACE_TYPES.items():
        rows = by_race[race_id]
        assert len(rows) == 17, (race_id, len(rows))
        output = []
        for row in rows:
            assert row["party_a"] == "D" and row["party_b"] == "R"
            dem, rep, other = (int(row[k]) for k in ("votes_a", "votes_b", "votes_other"))
            assert dem + rep + other == int(row["total_votes"])
            output.append({"year": 2026, "county": row["place"].removesuffix(" County"),
                           **result(dem, rep, other, row["candidate_a"], row["candidate_b"])})
        filename = f"{contest_type}_2026.json"
        write(CONTESTS / filename, {
            "year": 2026, "contest_type": contest_type,
            "meta": {**source_meta, "race_id": race_id}, "rows": output,
        })
        contest_manifest["files"].append({"year": 2026, "contest_type": contest_type,
                                          "file": filename, "rows": len(output),
                                          "major_party_contested": True})

    for scope, contest_type, chamber in (("state_house", "state_assembly", "lower"),
                                         ("state_senate", "state_senate", "upper")):
        grouped = defaultdict(list)
        for row in legislatures:
            if row["chamber"] == chamber:
                grouped[row["district"]].append(row)
        results = {}
        for district, candidates in grouped.items():
            dem_rows = [r for r in candidates if r["party"] == "D"]
            rep_rows = [r for r in candidates if r["party"] == "R"]
            dem = sum(int(r["votes"]) for r in dem_rows)
            rep = sum(int(r["votes"]) for r in rep_rows)
            total = int(candidates[0]["total_votes"])
            assert dem + rep <= total
            results[district] = result(dem, rep, total - dem - rep,
                                       dem_rows[0]["candidate"] if dem_rows else "",
                                       rep_rows[0]["candidate"] if rep_rows else "")
        filename = f"{scope}_{contest_type}_2026.json"
        write(DISTRICTS / filename, {"year": 2026, "scope": scope,
               "contest_type": contest_type, "meta": {**source_meta, "geography": "district"},
               "general": {"results": results}})
        district_manifest["files"].append({"year": 2026, "scope": scope,
                                            "contest_type": contest_type,
                                            "file": filename, "districts": len(results)})

    house_results = {}
    for district in range(1, 5):
        race_id = f"NV-{district:02d}"
        candidates = [r for r in races if r["race_id"] == race_id]
        dem_rows = [r for r in candidates if r["party"] == "D"]
        rep_rows = [r for r in candidates if r["party"] == "R"]
        dem = sum(int(r["votes"]) for r in dem_rows)
        rep = sum(int(r["votes"]) for r in rep_rows)
        total = int(candidates[0]["total_votes"])
        assert dem + rep <= total
        house_results[f"{district:02d}"] = result(dem, rep, total - dem - rep,
            dem_rows[0]["candidate"] if dem_rows else "",
            rep_rows[0]["candidate"] if rep_rows else "")
    filename = "congressional_us_house_2026.json"
    write(DISTRICTS / filename, {"year": 2026, "scope": "congressional",
        "contest_type": "us_house", "meta": {**source_meta, "geography": "district"},
        "general": {"results": house_results}})
    district_manifest["files"].append({"year": 2026, "scope": "congressional",
                                        "contest_type": "us_house", "file": filename, "districts": 4})
    write(CONTESTS / "manifest.json", contest_manifest)
    write(DISTRICTS / "manifest.json", district_manifest)


if __name__ == "__main__":
    main()
