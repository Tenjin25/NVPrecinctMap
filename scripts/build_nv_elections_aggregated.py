import csv
import json
import re
from collections import defaultdict
from pathlib import Path


OE_DIR = Path("data/openelections")
OUT_PATH = Path("data/nv_elections_aggregated.json")
CROSSWALKS_DIR = Path("data/crosswalks")


def clean(value: str) -> str:
    return (value or "").strip()


def to_int(value: str) -> int:
    s = clean(value).replace(",", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def parse_year_from_name(path: Path) -> str:
    m = re.match(r"^(\d{4})\d{4}__", path.name)
    if not m:
        raise ValueError(f"Unexpected OpenElections filename: {path.name}")
    return m.group(1)


def normalize_contest_type(office: str) -> str:
    low = clean(office).lower()
    if "president" in low:
        return "president"
    if "u.s. senate" in low or "united states senator" in low:
        return "us_senate"
    if "u.s. house" in low or "u.s. representative in congress" in low:
        return "us_house"
    if "state senate" in low:
        return "state_senate"
    if "state assembly" in low or "state house" in low:
        return "state_assembly"
    if "lieutenant governor" in low:
        return "lieutenant_governor"
    if "attorney general" in low:
        return "attorney_general"
    if "treasurer" in low:
        return "treasurer"
    if "controller" in low or "comptroller" in low:
        return "controller"
    if "governor" in low:
        return "governor"
    return ""


def normalize_party(party: str) -> str:
    p = clean(party).lower()
    if p in ("dem", "democratic"):
        return "DEM"
    if p in ("rep", "republican", "gop"):
        return "REP"
    return ""


def competitiveness_color(margin_pct: float) -> str:
    abs_margin = abs(margin_pct)
    if abs_margin < 0.5:
        return "#f7f7f7"
    if margin_pct > 0:
        if abs_margin >= 40:
            return "#67000d"
        if abs_margin >= 30:
            return "#a50f15"
        if abs_margin >= 20:
            return "#cb181d"
        if abs_margin >= 10:
            return "#ef3b2c"
        if abs_margin >= 5.5:
            return "#fb6a4a"
        if abs_margin >= 1:
            return "#fcae91"
        return "#fee8c8"
    if abs_margin >= 40:
        return "#08306b"
    if abs_margin >= 30:
        return "#08519c"
    if abs_margin >= 20:
        return "#3182bd"
    if abs_margin >= 10:
        return "#6baed6"
    if abs_margin >= 5.5:
        return "#9ecae1"
    if abs_margin >= 1:
        return "#c6dbef"
    return "#e1f5fe"


def pick_top_candidate(candidate_votes: dict[str, int]) -> str:
    if not candidate_votes:
        return ""
    return sorted(candidate_votes.items(), key=lambda kv: (-kv[1], kv[0].lower()))[0][0]


def load_crosswalk_share_rows(filename: str) -> list[dict]:
    path = CROSSWALKS_DIR / filename
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_crosswalk_context() -> dict:
    return {
        "sources": {
            "vtd20_to_cd118": "data/crosswalks/vtd20_to_cd118.csv",
            "vtd20_to_sldu": "data/crosswalks/vtd20_to_sldu.csv",
            "vtd20_to_sldl": "data/crosswalks/vtd20_to_sldl.csv",
            "neutral_to_vtd20": "data/crosswalks/neutral_to_vtd20.csv",
        },
        "district_shares": {
            "cd118": load_crosswalk_share_rows("vtd20_to_cd118_shares.csv"),
            "sldu": load_crosswalk_share_rows("vtd20_to_sldu_shares.csv"),
            "sldl": load_crosswalk_share_rows("vtd20_to_sldl_shares.csv"),
        },
    }


def build_contest_results(rows: list[dict]) -> dict:
    by_precinct = defaultdict(lambda: {"dem_votes": 0, "rep_votes": 0, "other_votes": 0})
    dem_cand_votes = defaultdict(lambda: defaultdict(int))
    rep_cand_votes = defaultdict(lambda: defaultdict(int))
    statewide_dem = defaultdict(int)
    statewide_rep = defaultdict(int)
    district_votes = defaultdict(lambda: defaultdict(int))

    for row in rows:
        county = clean(row.get("county", ""))
        precinct = clean(row.get("precinct", ""))
        precinct_key = f"{county} - {precinct}" if county else precinct
        party = normalize_party(row.get("party", ""))
        candidate = clean(row.get("candidate", ""))
        votes = to_int(row.get("votes", ""))

        if party == "DEM":
            by_precinct[precinct_key]["dem_votes"] += votes
            dem_cand_votes[precinct_key][candidate] += votes
            statewide_dem[candidate] += votes
        elif party == "REP":
            by_precinct[precinct_key]["rep_votes"] += votes
            rep_cand_votes[precinct_key][candidate] += votes
            statewide_rep[candidate] += votes
        else:
            by_precinct[precinct_key]["other_votes"] += votes

        district = clean(row.get("district", ""))
        if district:
            district_votes[precinct_key][district] += votes

    default_dem = pick_top_candidate(statewide_dem)
    default_rep = pick_top_candidate(statewide_rep)

    results = {}
    for precinct_key in sorted(by_precinct):
        dem_votes = int(by_precinct[precinct_key]["dem_votes"])
        rep_votes = int(by_precinct[precinct_key]["rep_votes"])
        other_votes = int(by_precinct[precinct_key]["other_votes"])
        total_votes = dem_votes + rep_votes + other_votes
        margin = rep_votes - dem_votes
        margin_pct = (margin / total_votes * 100.0) if total_votes else 0.0
        winner = "REP" if margin > 0 else "DEM" if margin < 0 else "TIE"
        dem_candidate = pick_top_candidate(dem_cand_votes[precinct_key]) or default_dem
        rep_candidate = pick_top_candidate(rep_cand_votes[precinct_key]) or default_rep
        district = pick_top_candidate(district_votes[precinct_key])

        results[precinct_key] = {
            "dem_votes": dem_votes,
            "rep_votes": rep_votes,
            "other_votes": other_votes,
            "total_votes": total_votes,
            "dem_candidate": dem_candidate,
            "rep_candidate": rep_candidate,
            "margin": margin,
            "margin_pct": round(margin_pct, 2),
            "winner": winner,
            "competitiveness": {"color": competitiveness_color(margin_pct)},
        }
        if district:
            results[precinct_key]["district"] = district
    return results


def allowed_contests_for_year(year: str) -> set[str]:
    if year == "2022":
        return {
            "us_house",
            "us_senate",
            "governor",
            "lieutenant_governor",
            "attorney_general",
            "treasurer",
            "controller",
            "state_senate",
            "state_assembly",
        }
    if year == "2024":
        return {"president", "us_senate", "us_house", "state_senate", "state_assembly"}
    return {"president", "us_senate", "governor", "lieutenant_governor", "attorney_general", "treasurer", "controller"}


def main() -> None:
    files = sorted(OE_DIR.glob("*__nv__general__precinct.csv"))
    if not files:
        raise FileNotFoundError(f"No OpenElections CSV files found under {OE_DIR}")

    aggregated = {
        "results_by_year": {},
        "crosswalk_context": load_crosswalk_context(),
    }

    for path in files:
        year = parse_year_from_name(path)
        by_contest = defaultdict(list)
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                contest = normalize_contest_type(row.get("office", ""))
                if contest:
                    by_contest[contest].append(row)

        allowed = allowed_contests_for_year(year)
        year_data = {}
        for contest in (
            "president",
            "us_house",
            "us_senate",
            "governor",
            "lieutenant_governor",
            "attorney_general",
            "treasurer",
            "controller",
            "state_senate",
            "state_assembly",
        ):
            if contest not in allowed:
                continue
            rows = by_contest.get(contest, [])
            if not rows:
                continue
            year_data[contest] = {"general": {"results": build_contest_results(rows)}}

        if year_data:
            aggregated["results_by_year"][year] = year_data
            print(f"{year}: {', '.join(sorted(year_data.keys()))}")

    OUT_PATH.write_text(json.dumps(aggregated, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
