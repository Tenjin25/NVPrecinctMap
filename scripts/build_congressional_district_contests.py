import csv
import json
import re
from collections import defaultdict
from pathlib import Path


OE_DIR = Path("data/openelections")
OUT_DIR = Path("data/district_contests")


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


def normalize_party(party: str) -> str:
    p = clean(party).lower()
    if p in ("dem", "democratic"):
        return "DEM"
    if p in ("rep", "republican", "gop"):
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


def aggregate_rows(rows: list[dict], group_key_name: str, ctype: str) -> dict:
    grouped = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    dem_name = defaultdict(lambda: defaultdict(int))
    rep_name = defaultdict(lambda: defaultdict(int))

    for row in rows:
        group_key = clean(row.get(group_key_name, ""))
        if not group_key:
            continue
        p = normalize_party(row.get("party", ""))
        cand = normalize_candidate_name(ctype, row.get("candidate", ""))
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

    pkey_to_cd = {}
    for r in by_type.get("us_house", []):
        pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
        d = clean(r.get("district", "")).zfill(2)
        if pkey[0] and pkey[1] and d:
            pkey_to_cd[pkey] = d

    pkey_to_sldl = {}
    for r in by_type.get("state_assembly", []):
        pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
        d = clean(r.get("district", ""))
        if pkey[0] and pkey[1] and d:
            pkey_to_sldl[pkey] = d

    pkey_to_sldu = {}
    for r in by_type.get("state_senate", []):
        pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
        d = clean(r.get("district", ""))
        if pkey[0] and pkey[1] and d:
            pkey_to_sldu[pkey] = d

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
        results = aggregate_rows(trows, "district", t)
        payload = {
            "year": year,
            "scope": "congressional",
            "contest_type": t,
            "meta": {"source": "nv_openelections_precinct", "nongeo_allocation_mode": "precinct_map"},
            "general": {"results": results},
        }
        out.append((f"congressional_{t}_{year}.json", payload))

    # State Assembly scope
    for t in ("state_assembly",) + common_types:
        source_rows = by_type.get(t, [])
        if not source_rows:
            continue
        if t == "state_assembly":
            trows = [dict(r) for r in source_rows if clean(r.get("district", ""))]
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
        results = aggregate_rows(trows, "district", t)
        payload = {
            "year": year,
            "scope": "state_house",
            "contest_type": t,
            "meta": {"source": "nv_openelections_precinct", "nongeo_allocation_mode": "precinct_map"},
            "general": {"results": results},
        }
        out.append((f"state_house_{t}_{year}.json", payload))

    # State Senate scope
    for t in ("state_senate",) + common_types:
        source_rows = by_type.get(t, [])
        if not source_rows:
            continue
        if t == "state_senate":
            trows = [dict(r) for r in source_rows if clean(r.get("district", ""))]
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
        results = aggregate_rows(trows, "district", t)
        payload = {
            "year": year,
            "scope": "state_senate",
            "contest_type": t,
            "meta": {"source": "nv_openelections_precinct", "nongeo_allocation_mode": "precinct_map"},
            "general": {"results": results},
        }
        out.append((f"state_senate_{t}_{year}.json", payload))

    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []

    allowed_years = {2022, 2024}
    for p in sorted(OE_DIR.glob("*__nv__general__precinct.csv")):
        if parse_year(p) not in allowed_years:
            continue
        generated = build_for_year(p)
        for fname, payload in generated:
            y = int(payload["year"])
            scope = payload["scope"]
            ctype = payload["contest_type"]
            keep = (
                (scope == "congressional" and ctype == "us_house") or
                (scope == "state_house" and ctype == "state_assembly") or
                (scope == "state_senate" and ctype == "state_senate")
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
    (OUT_DIR / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_DIR / 'manifest.json'} entries={len(manifest)}")


if __name__ == "__main__":
    main()
