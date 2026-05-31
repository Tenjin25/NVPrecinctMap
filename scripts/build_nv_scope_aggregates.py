import csv
import json
import re
from collections import defaultdict
from pathlib import Path


OE_DIR = Path("data/openelections")
CROSSWALKS_DIR = Path("data/crosswalks")
OUT_DIR = Path("data")


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
    if p in ("dem", "democratic"):
        return "DEM"
    if p in ("rep", "republican", "gop"):
        return "REP"
    return ""


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
        return "treasurer"
    if "controller" in o or "comptroller" in o:
        return "controller"
    if "governor" in o:
        return "governor"
    if "state senate" in o:
        return "state_senate"
    if "state assembly" in o or "state house" in o:
        return "state_assembly"
    return ""


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


def load_cd_to_neutral() -> dict[str, str]:
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

    # Build a one-to-one CD -> neutral assignment so all districts remain represented.
    cds = sorted(overlap.keys())
    neutrals = sorted({nid for counts in overlap.values() for nid in counts.keys()})
    if not cds or not neutrals:
        return {}

    cd_to_neutral = {}
    used_neutral = set()
    used_cd = set()
    candidates = []
    for cd in cds:
        for nid in neutrals:
            score = overlap.get(cd, {}).get(nid, 0)
            if score > 0:
                candidates.append((score, cd, nid))
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

    for score, cd, nid in candidates:
        if cd in used_cd or nid in used_neutral:
            continue
        cd_to_neutral[cd] = nid
        used_cd.add(cd)
        used_neutral.add(nid)

    # Fallback for any unassigned CDs (only if overlap is sparse).
    for cd in cds:
        if cd in cd_to_neutral:
            continue
        for nid, _ in sorted(overlap.get(cd, {}).items(), key=lambda kv: (-kv[1], kv[0])):
            if nid not in used_neutral:
                cd_to_neutral[cd] = nid
                used_neutral.add(nid)
                break
        if cd not in cd_to_neutral:
            cd_to_neutral[cd] = pick_top_name(overlap.get(cd, {}))
    return cd_to_neutral


def aggregate_rows(rows: list[dict], group_key_name: str) -> dict:
    grouped = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    dem_name = defaultdict(lambda: defaultdict(int))
    rep_name = defaultdict(lambda: defaultdict(int))

    for row in rows:
        group_key = clean(row.get(group_key_name, ""))
        if not group_key:
            continue
        p = normalize_party(row.get("party", ""))
        cand = clean(row.get("candidate", ""))
        votes = to_int(row.get("votes", ""))
        if p == "DEM":
            grouped[group_key]["dem"] += votes
            dem_name[group_key][cand] += votes
        elif p == "REP":
            grouped[group_key]["rep"] += votes
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
    cd_to_neutral = load_cd_to_neutral()
    legislative = {"results_by_year": {}}
    congressional = {"results_by_year": {}}
    neutral_congressional = {"results_by_year": {}, "meta": {"cd_to_neutral": cd_to_neutral}}

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

        # Legislative (district field from OE rows)
        year_leg = {}
        for t in ("state_senate", "state_assembly"):
            trows = [r for r in by_type.get(t, []) if clean(r.get("district", ""))]
            if trows:
                year_leg[t] = {"general": {"results": aggregate_rows(trows, "district")}}
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
                    year_cong[t] = {"general": {"results": aggregate_rows(trows, "district")}}
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
                year_cong[t] = {"general": {"results": aggregate_rows(trows, "district")}}
        if year_cong:
            congressional["results_by_year"][year] = year_cong

        # Neutral congressional (map CD -> neutral_id), for same contest set as congressional.
        year_neutral = {}
        for t in congressional_types:
            source_rows = by_type.get(t, [])
            if not source_rows:
                continue
            neutral_rows = []
            if t == "us_house":
                for r in source_rows:
                    cd = clean(r.get("district", "")).zfill(2)
                    nid = cd_to_neutral.get(cd, "")
                    if not nid:
                        continue
                    rc = dict(r)
                    rc["neutral_id"] = nid
                    neutral_rows.append(rc)
            else:
                for r in source_rows:
                    pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
                    cd = pkey_to_cd.get(pkey, "")
                    nid = cd_to_neutral.get(cd, "")
                    if not nid:
                        continue
                    rc = dict(r)
                    rc["neutral_id"] = nid
                    neutral_rows.append(rc)
            if neutral_rows:
                year_neutral[t] = {"general": {"results": aggregate_rows(neutral_rows, "neutral_id")}}
        if year_neutral:
            neutral_congressional["results_by_year"][year] = year_neutral

    leg_path = OUT_DIR / "nv_legislative_aggregated.json"
    cong_path = OUT_DIR / "nv_congressional_aggregated.json"
    neutral_cong_path = OUT_DIR / "nv_neutral_congressional_aggregated.json"

    leg_path.write_text(json.dumps(legislative, indent=2), encoding="utf-8")
    cong_path.write_text(json.dumps(congressional, indent=2), encoding="utf-8")
    neutral_cong_path.write_text(json.dumps(neutral_congressional, indent=2), encoding="utf-8")
    print(f"Wrote {leg_path}")
    print(f"Wrote {cong_path}")
    print(f"Wrote {neutral_cong_path}")


if __name__ == "__main__":
    main()
