import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


OE_DIR = Path("data/openelections")
OUT_DIR = Path("data/contests")


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


def parse_date_from_name(path: Path) -> str:
    m = re.match(r"^(\d{8})__", path.name)
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
    if p in ("rep", "republican"):
        return "REP"
    return ""

def normalize_candidate_name(contest_type: str, candidate: str) -> str:
    cand = clean(candidate)
    if contest_type != "president":
        return cand
    return re.split(r"\s+(?:and|&)\s+|/|;", cand, maxsplit=1, flags=re.IGNORECASE)[0].strip()


def competitiveness_color(margin_pct: float) -> str:
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
            return "#08519c"
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


def pick_top_candidate(candidate_votes: dict[str, int]) -> str:
    if not candidate_votes:
        return ""
    return sorted(candidate_votes.items(), key=lambda kv: (-kv[1], kv[0].lower()))[0][0]


def build_rows_for_contest(rows: list[dict], year: int, contest_type: str) -> dict:
    by_precinct = defaultdict(lambda: {"dem_votes": 0, "rep_votes": 0, "other_votes": 0})
    dem_cand_votes = defaultdict(lambda: defaultdict(int))
    rep_cand_votes = defaultdict(lambda: defaultdict(int))
    statewide_dem = defaultdict(int)
    statewide_rep = defaultdict(int)

    for row in rows:
        county = clean(row.get("county", ""))
        precinct = clean(row.get("precinct", ""))
        key = f"{county} - {precinct}" if county else precinct
        party = normalize_party(row.get("party", ""))
        candidate = normalize_candidate_name(contest_type, row.get("candidate", ""))
        votes = to_int(row.get("votes", ""))

        if party == "DEM":
            by_precinct[key]["dem_votes"] += votes
            dem_cand_votes[key][candidate] += votes
            statewide_dem[candidate] += votes
        elif party == "REP":
            by_precinct[key]["rep_votes"] += votes
            rep_cand_votes[key][candidate] += votes
            statewide_rep[candidate] += votes
        else:
            by_precinct[key]["other_votes"] += votes

    default_dem = pick_top_candidate(statewide_dem)
    default_rep = pick_top_candidate(statewide_rep)

    out_rows = []
    dem_total = rep_total = other_total = 0
    for key in sorted(by_precinct):
        dem = int(by_precinct[key]["dem_votes"])
        rep = int(by_precinct[key]["rep_votes"])
        other = int(by_precinct[key]["other_votes"])
        total = dem + rep + other
        margin = rep - dem
        margin_pct = ((margin / total) * 100.0) if total else 0.0
        winner = "REP" if margin > 0 else "DEM" if margin < 0 else "TIE"
        dem_candidate = pick_top_candidate(dem_cand_votes[key]) or default_dem
        rep_candidate = pick_top_candidate(rep_cand_votes[key]) or default_rep

        dem_total += dem
        rep_total += rep
        other_total += other

        out_rows.append(
            {
                "year": int(year),
                "county": key,
                "dem_votes": dem,
                "rep_votes": rep,
                "other_votes": other,
                "total_votes": total,
                "dem_candidate": dem_candidate,
                "rep_candidate": rep_candidate,
                "margin": margin,
                "margin_pct": round(margin_pct, 4),
                "winner": winner,
                "color": competitiveness_color(margin_pct),
            }
        )

    payload = {
        "year": int(year),
        "contest_type": contest_type,
        "meta": {
            "source": "nv_openelections_precinct",
            "office": {
                "us_senate": "US SENATE",
                "us_house": "US HOUSE",
                "president": "PRESIDENT",
                "state_senate": "STATE SENATE",
                "state_assembly": "STATE ASSEMBLY",
                "governor": "GOVERNOR",
                "lieutenant_governor": "LIEUTENANT GOVERNOR",
                "attorney_general": "ATTORNEY GENERAL",
                "treasurer": "TREASURER",
                "controller": "CONTROLLER",
            }.get(contest_type, contest_type.upper()),
            "nongeo_allocation_mode": "none",
            "dem_total": dem_total,
            "rep_total": rep_total,
            "other_total": other_total,
            "total_votes": dem_total + rep_total + other_total,
            "major_party_contested": dem_total > 0 and rep_total > 0,
        },
        "rows": out_rows,
    }
    return payload


def build_for_file(path: Path) -> list[dict]:
    election_date = parse_date_from_name(path)
    year = int(election_date[:4])

    by_contest = defaultdict(list)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            contest_type = normalize_contest_type(row.get("office", ""))
            if contest_type:
                by_contest[contest_type].append(row)

    entries = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if year == 2022:
        allowed = {
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
    elif year == 2024:
        allowed = {"president", "us_senate", "us_house", "state_senate", "state_assembly"}
    else:
        allowed = {"president", "us_senate", "governor", "lieutenant_governor", "attorney_general", "treasurer", "controller"}

    for contest_type in (
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
        if contest_type not in allowed:
            continue
        rows = by_contest.get(contest_type, [])
        if not rows:
            continue
        payload = build_rows_for_contest(rows, year, contest_type)
        out_name = f"{contest_type}_{year}.json"
        out_path = OUT_DIR / out_name
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        entries.append(
            {
                "year": year,
                "contest_type": contest_type,
                "file": out_name,
                "rows": len(payload["rows"]),
            }
        )
        print(f"Wrote {out_path} ({len(payload['rows'])} rows)")

    return entries


def main():
    parser = argparse.ArgumentParser(description="Build NC-format contest JSONs from NV OpenElections precinct files.")
    parser.add_argument("--years", nargs="*", default=[], help="Years to include. If omitted, include all available years.")
    args = parser.parse_args()

    years = set(args.years or [])
    files = sorted(
        p
        for p in OE_DIR.glob("*__nv__general__precinct.csv")
        if (not years or parse_date_from_name(p)[:4] in years)
    )
    if not files:
        raise FileNotFoundError(f"No NV OpenElections files matched years: {sorted(years) if years else 'ALL'}")

    manifest_entries = []
    for path in files:
        manifest_entries.extend(build_for_file(path))

    manifest_entries.sort(key=lambda x: (x["year"], x["contest_type"]))
    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps({"files": manifest_entries}, indent=2), encoding="utf-8")
    print(f"Wrote {manifest_path}")


if __name__ == "__main__":
    main()
