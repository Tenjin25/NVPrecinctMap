import csv
import math
import re
from collections import defaultdict
from pathlib import Path

import shapefile


ROOT = Path(".")
OE_DIR = ROOT / "data" / "openelections"
CROSSWALKS_DIR = ROOT / "data" / "crosswalks"
CAL_DIR = ROOT / "data" / "neutral calibration"
OUT_OVERRIDES = CROSSWALKS_DIR / "precinct_to_neutral_overrides.csv"

COUNTY20_SHP = ROOT / "data" / "census" / "tl_2020_32_county20" / "tl_2020_32_county20.shp"
VTD20_SHP = ROOT / "data" / "census" / "tl_2020_32_vtd20" / "tl_2020_32_vtd20.shp"

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


def to_float(v: str) -> float:
    s = clean(v)
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


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
    p_norm = norm(p_raw)
    probes = []

    def add(v: str):
        nv = norm(v)
        if nv:
            probes.append((c, nv))

    # full token
    add(p_raw)
    # leading numeric token (e.g., "5046 WARD")
    m_lead = re.match(r"^(\d+[A-Za-z0-9\-]*)", p_raw)
    if m_lead:
        add(m_lead.group(1))
    # trailing numeric token (e.g., "RENO-VERDI 5022")
    m_tail = re.search(r"(\d{3,8})\s*$", p_raw)
    if m_tail:
        tail = m_tail.group(1)
        add(tail)
        # trim common "00" suffix artifacts from legacy exports (e.g., 504600 -> 5046)
        if len(tail) > 4 and tail.endswith("00"):
            add(tail[:-2])
        # also try first 4 digits for long numeric tokens
        if len(tail) >= 5:
            add(tail[:4])
    # remove separators and retry (e.g., 50-22)
    compact = re.sub(r"[^0-9A-Za-z]", "", p_raw)
    if compact and compact != p_raw:
        add(compact)

    # de-duplicate while preserving order
    out = []
    seen = set()
    for pr in probes:
        if pr in seen:
            continue
        seen.add(pr)
        out.append(pr)
    return out


def party_bucket(party: str) -> str:
    p = clean(party).lower()
    if p in ("dem", "democratic"):
        return "D"
    if p in ("rep", "republican", "gop"):
        return "R"
    return "O"


def contest_type(office: str) -> str:
    o = clean(office).lower()
    if "president" in o:
        return "president"
    if "u.s. house" in o or "u.s. representative in congress" in o:
        return "us_house"
    if "u.s. senate" in o or "united states senator" in o:
        return "us_senate"
    return ""


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
    weighted = CROSSWALKS_DIR / "neutral_to_vtd20_weighted.csv"
    if weighted.exists():
        best = {}
        with weighted.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                geoid = clean(r.get("geoid20"))
                nid = clean(r.get("neutral_id"))
                w = to_float(r.get("alloc_weight"))
                if not geoid or not nid:
                    continue
                prev = best.get(geoid)
                if not prev or w > prev[1]:
                    best[geoid] = (nid, w)
        if best:
            return {k: v[0] for k, v in best.items()}

    out = {}
    with (CROSSWALKS_DIR / "neutral_to_vtd20.csv").open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            geoid = clean(r.get("geoid20"))
            nid = clean(r.get("neutral_id"))
            if geoid and nid:
                out[geoid] = nid
    return out


def load_cd_to_neutral_shares() -> dict[str, list[tuple[str, float]]]:
    # derive from vtd20->cd118 and neutral_to_vtd20
    vtd_to_cd = {}
    with (CROSSWALKS_DIR / "vtd20_to_cd118.csv").open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            geoid = clean(r.get("geoid20"))
            cd = clean(r.get("district_id")).zfill(2)
            if geoid and cd:
                vtd_to_cd[geoid] = cd
    overlap = defaultdict(lambda: defaultdict(int))
    with (CROSSWALKS_DIR / "neutral_to_vtd20.csv").open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            vtd = clean(r.get("geoid20"))
            nid = clean(r.get("neutral_id"))
            cd = vtd_to_cd.get(vtd, "")
            if cd and nid:
                overlap[cd][nid] += 1

    out = {}
    for cd, counts in overlap.items():
        total = sum(counts.values())
        if total <= 0:
            continue
        out[cd.zfill(2)] = [(nid, cnt / total) for nid, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if cnt > 0]
    return out


def detect_dra_contest_from_filename(name: str) -> str:
    n = name.lower()
    if "pres" in n or "president" in n:
        return "president"
    if "senate" in n:
        return "us_senate"
    if "house" in n or "congress" in n:
        return "us_house"
    return ""


def detect_dra_year_from_filename(name: str) -> str:
    m = re.search(r"(20\d{2})", name)
    return m.group(1) if m else ""


def load_dra_targets() -> dict[tuple[str, str], dict[str, tuple[float, float, float]]]:
    out = {}
    for path in sorted(CAL_DIR.glob("district-statistics*.csv")):
        fname = path.name
        year = detect_dra_year_from_filename(fname)
        contest = detect_dra_contest_from_filename(fname)
        if not year or not contest:
            continue
        targets = {}
        with path.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                nid = clean(r.get("ID", "")).strip('"')
                if nid not in {"1", "2", "3", "4"}:
                    continue
                dem = to_float(r.get("Dem"))
                rep = to_float(r.get("Rep"))
                oth = to_float(r.get("Oth"))
                targets[nid] = (dem, rep, oth)
        if targets:
            out[(year, contest)] = targets
    return out


def load_oe_rows_by_year() -> dict[str, list[dict]]:
    out = {}
    for p in sorted(OE_DIR.glob("*__nv__general__precinct.csv")):
        m = re.match(r"^(\d{4})\d{4}__", p.name)
        if not m:
            continue
        year = m.group(1)
        with p.open("r", encoding="utf-8", newline="") as f:
            out[year] = list(csv.DictReader(f))
    return out


def build_scenarios():
    precinct_to_geoid20 = load_precinct_to_geoid20()
    vtd20_to_neutral = load_vtd20_to_neutral()
    cd_to_neutral_shares = load_cd_to_neutral_shares()
    dra_targets = load_dra_targets()
    rows_by_year = load_oe_rows_by_year()

    scenarios = []
    all_unmatched = set()
    candidates_by_pkey = defaultdict(set)
    influence_votes = defaultdict(int)

    for (year, contest), target_shares in dra_targets.items():
        rows = rows_by_year.get(year, [])
        if not rows:
            continue

        by_type = defaultdict(list)
        for r in rows:
            t = contest_type(r.get("office", ""))
            if t:
                by_type[t].append(r)

        source = by_type.get(contest, [])
        if not source:
            continue

        pkey_to_cd = {}
        for r in by_type.get("us_house", []):
            pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
            cd = clean(r.get("district", "")).zfill(2)
            if pkey[0] and pkey[1] and cd:
                pkey_to_cd[pkey] = cd

        fixed = defaultdict(lambda: {"D": 0, "R": 0, "O": 0})
        unmatched_vote = defaultdict(lambda: {"D": 0, "R": 0, "O": 0})

        for r in source:
            pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
            votes = to_int(r.get("votes", ""))
            if votes <= 0:
                continue
            b = party_bucket(r.get("party", ""))

            geoid = ""
            probes = build_precinct_match_probes(pkey[0], pkey[1])
            for pr in probes:
                if pr in precinct_to_geoid20:
                    geoid = precinct_to_geoid20[pr]
                    break

            nid = vtd20_to_neutral.get(geoid, "") if geoid else ""
            if nid:
                fixed[nid][b] += votes
                continue

            unmatched_vote[pkey][b] += votes

        # Build candidate set for unmatched pkeys
        for pkey, v in unmatched_vote.items():
            cd = pkey_to_cd.get(pkey, "")
            shares = cd_to_neutral_shares.get(cd, [])
            if not shares:
                continue
            cands = [nid for nid, _ in shares]
            for nid in cands:
                candidates_by_pkey[pkey].add(nid)
            all_unmatched.add(pkey)
            influence_votes[pkey] += v["D"] + v["R"] + v["O"]

        # keep only pkeys with candidates
        unmatched_vote = {k: v for k, v in unmatched_vote.items() if k in all_unmatched}

        scenarios.append(
            {
                "year": year,
                "contest": contest,
                "targets": target_shares,
                "fixed": fixed,
                "unmatched_vote": unmatched_vote,
                "pkey_to_cd": pkey_to_cd,
            }
        )

    return scenarios, candidates_by_pkey, influence_votes


def make_initial_assignment(candidates_by_pkey: dict[tuple[str, str], set[str]]) -> dict[tuple[str, str], str]:
    out = {}
    if OUT_OVERRIDES.exists():
        with OUT_OVERRIDES.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                pkey = (clean(r.get("county", "")), clean(r.get("precinct", "")))
                nid = clean(r.get("neutral_id", ""))
                if pkey in candidates_by_pkey and nid in candidates_by_pkey[pkey]:
                    out[pkey] = nid
    for pkey, cands in candidates_by_pkey.items():
        if pkey not in out and cands:
            out[pkey] = sorted(cands)[0]
    return out


def scenario_error(scenario, assignment) -> float:
    district_totals = defaultdict(lambda: {"D": 0, "R": 0, "O": 0})
    for nid, v in scenario["fixed"].items():
        for k in ("D", "R", "O"):
            district_totals[nid][k] += v.get(k, 0)
    for pkey, v in scenario["unmatched_vote"].items():
        nid = assignment.get(pkey, "")
        if not nid:
            continue
        for k in ("D", "R", "O"):
            district_totals[nid][k] += v.get(k, 0)

    err = 0.0
    for nid in ("1", "2", "3", "4"):
        t = district_totals.get(nid, {"D": 0, "R": 0, "O": 0})
        total = t["D"] + t["R"] + t["O"]
        if total <= 0:
            continue
        dem = t["D"] / total
        rep = t["R"] / total
        oth = t["O"] / total
        target = scenario["targets"].get(nid)
        if not target:
            continue
        td, tr, to = target
        err += (dem - td) ** 2 + (rep - tr) ** 2 + (oth - to) ** 2
    return err


def total_error(scenarios, assignment) -> float:
    return sum(scenario_error(s, assignment) for s in scenarios)


def calibrate():
    scenarios, candidates_by_pkey, influence_votes = build_scenarios()
    if not scenarios:
        raise SystemExit("No calibration scenarios found.")
    assignment = make_initial_assignment(candidates_by_pkey)

    pkeys = sorted(candidates_by_pkey.keys(), key=lambda p: (-influence_votes.get(p, 0), p[0], p[1]))
    best = total_error(scenarios, assignment)
    print(f"Initial objective={best:.8f} pkeys={len(pkeys)} scenarios={len(scenarios)}")

    for pass_ix in range(8):
        changed = 0
        for pkey in pkeys:
            current = assignment.get(pkey, "")
            cands = sorted(candidates_by_pkey.get(pkey, []))
            if len(cands) <= 1:
                continue
            best_local = current
            best_local_score = best
            for nid in cands:
                if nid == current:
                    continue
                assignment[pkey] = nid
                sc = total_error(scenarios, assignment)
                if sc + 1e-12 < best_local_score:
                    best_local_score = sc
                    best_local = nid
            assignment[pkey] = best_local
            if best_local != current:
                changed += 1
                best = best_local_score
        print(f"Pass {pass_ix + 1}: changed={changed} objective={best:.8f}")
        if changed == 0:
            break

    # Write overrides
    rows = [{"county": p[0], "precinct": p[1], "neutral_id": assignment[p]} for p in sorted(assignment)]
    with OUT_OVERRIDES.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["county", "precinct", "neutral_id"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {OUT_OVERRIDES} rows={len(rows)}")

    # Print scenario fit summary
    for s in scenarios:
        err = scenario_error(s, assignment)
        print(f"Fit {s['year']} {s['contest']}: error={err:.8f}")


if __name__ == "__main__":
    calibrate()
