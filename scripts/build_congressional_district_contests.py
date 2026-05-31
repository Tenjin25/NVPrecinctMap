import json
from pathlib import Path


DATA_DIR = Path("data")
OUT_DIR = DATA_DIR / "district_contests"
CONGRESSIONAL_AGG = DATA_DIR / "nv_congressional_aggregated.json"
LEGISLATIVE_AGG = DATA_DIR / "nv_legislative_aggregated.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_slice(scope: str, contest_type: str, year: int, node: dict) -> str:
    fname = f"{scope}_{contest_type}_{year}.json"
    payload = {
        "year": int(year),
        "scope": scope,
        "contest_type": contest_type,
        "meta": {
            "source": "nv_scope_aggregates",
            "nongeo_allocation_mode": "precomputed",
        },
        "general": {"results": node.get("general", {}).get("results", {})},
    }
    (OUT_DIR / fname).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return fname


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []

    cong = load_json(CONGRESSIONAL_AGG)
    for year_str, contests in (cong.get("results_by_year", {}) or {}).items():
        year = int(year_str)
        for contest_type, node in (contests or {}).items():
            fname = write_slice("congressional", contest_type, year, node or {})
            districts = len((node or {}).get("general", {}).get("results", {}) or {})
            manifest.append(
                {
                    "year": year,
                    "scope": "congressional",
                    "contest_type": contest_type,
                    "file": fname,
                    "districts": districts,
                }
            )

    leg = load_json(LEGISLATIVE_AGG)
    for year_str, contests in (leg.get("results_by_year", {}) or {}).items():
        year = int(year_str)
        for contest_type, node in (contests or {}).items():
            scope = "state_senate" if contest_type == "state_senate" else "state_house"
            fname = write_slice(scope, contest_type, year, node or {})
            districts = len((node or {}).get("general", {}).get("results", {}) or {})
            manifest.append(
                {
                    "year": year,
                    "scope": scope,
                    "contest_type": contest_type,
                    "file": fname,
                    "districts": districts,
                }
            )

    manifest.sort(key=lambda x: (x["year"], x["scope"], x["contest_type"]))
    (OUT_DIR / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_DIR / 'manifest.json'} entries={len(manifest)}")


if __name__ == "__main__":
    main()
