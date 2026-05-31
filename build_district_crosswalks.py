import csv
from collections import defaultdict
from pathlib import Path

import shapefile


ROOT = Path("data")
CROSS = ROOT / "crosswalks"
CROSS.mkdir(parents=True, exist_ok=True)


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) if (yj - yi) else 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def shape_parts(shape):
    pts = shape.points
    parts = list(shape.parts) + [len(pts)]
    return [pts[parts[i] : parts[i + 1]] for i in range(len(parts) - 1)]


def load_districts(shp_path: Path, id_field: str):
    r = shapefile.Reader(str(shp_path))
    fields = [f[0] for f in r.fields[1:]]
    out = []
    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict() if hasattr(sr.record, "as_dict") else {fields[i]: sr.record[i] for i in range(len(fields))}
        did = str(rec.get(id_field, "")).strip()
        if not did:
            continue
        out.append({"id": did, "bbox": sr.shape.bbox, "parts": shape_parts(sr.shape)})
    return out


def get_centroid_from_record(rec, shape, suffix):
    lat = str(rec.get(f"INTPTLAT{suffix}", "")).replace("+", "").strip()
    lon = str(rec.get(f"INTPTLON{suffix}", "")).replace("+", "").strip()
    try:
        y = float(lat)
        x = float(lon)
        return x, y
    except Exception:
        b = shape.bbox
        return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


def assign_units_to_district(unit_shp: Path, unit_id_field: str, suffix: str, districts, out_csv: Path):
    r = shapefile.Reader(str(unit_shp))
    fields = [f[0] for f in r.fields[1:]]
    rows = []
    unmatched = 0
    counts = defaultdict(int)

    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict() if hasattr(sr.record, "as_dict") else {fields[i]: sr.record[i] for i in range(len(fields))}
        uid = str(rec.get(unit_id_field, "")).strip()
        if not uid:
            continue
        x, y = get_centroid_from_record(rec, sr.shape, suffix)
        assigned = ""
        for d in districts:
            xmin, ymin, xmax, ymax = d["bbox"]
            if not (xmin <= x <= xmax and ymin <= y <= ymax):
                continue
            hit = False
            for ring in d["parts"]:
                if point_in_ring(x, y, ring):
                    hit = True
                    break
            if hit:
                assigned = d["id"]
                break
        if not assigned:
            unmatched += 1
        else:
            counts[assigned] += 1
        rows.append([uid, assigned, "centroid"])

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([unit_id_field.lower(), "district_id", "assign_method"])
        w.writerows(rows)

    shares_csv = out_csv.with_name(out_csv.stem + "_shares.csv")
    with shares_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["district_id", "unit_count", "share_of_units"])
        total = sum(counts.values()) or 1
        for k in sorted(counts):
            c = counts[k]
            w.writerow([k, c, f"{c/total:.10f}"])

    return len(rows), unmatched, shares_csv


def main():
    # Missing neutral tabblock10 shares file from prior step
    n10 = CROSS / "neutral_to_tabblock10.csv"
    if n10.exists():
        counts = defaultdict(int)
        total = 0
        with n10.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                nid = (row.get("neutral_id") or "").strip()
                if nid:
                    counts[nid] += 1
                total += 1
        out = CROSS / "neutral_to_tabblock10_shares.csv"
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["neutral_id", "unit_count", "share_of_units"])
            denom = sum(counts.values()) or 1
            for k in sorted(counts):
                c = counts[k]
                w.writerow([k, c, f"{c/denom:.10f}"])
        print(f"Wrote {out}")

    districts = {
        "cd118": (
            load_districts(ROOT / "census/tl_2022_32_cd118/tl_2022_32_cd118.shp", "CD118FP"),
            "cd118_id",
        ),
        "sldl": (
            load_districts(ROOT / "census/tl_2022_32_sldl/tl_2022_32_sldl.shp", "SLDLST"),
            "sldl_id",
        ),
        "sldu": (
            load_districts(ROOT / "census/tl_2022_32_sldu/tl_2022_32_sldu.shp", "SLDUST"),
            "sldu_id",
        ),
    }

    units = [
        ("tabblock20", ROOT / "census/tl_2022_32_tabblock20/tl_2022_32_tabblock20.shp", "GEOID20", "20"),
        ("tabblock10", ROOT / "census/tl_2010_32_tabblock10/tl_2010_32_tabblock10.shp", "GEOID10", "10"),
        ("tabblock00", ROOT / "census/tl_2010_32_tabblock00/tl_2010_32_tabblock00.shp", "BLKIDFP00", "00"),
        ("vtd20", ROOT / "census/tl_2020_32_vtd20/tl_2020_32_vtd20.shp", "GEOID20", "20"),
        ("vtd10", ROOT / "census/tl_2012_32_vtd10/tl_2012_32_vtd10.shp", "GEOID10", "10"),
    ]

    for unit_name, unit_shp, unit_id, suffix in units:
        for dist_name, (dist_polys, _) in districts.items():
            out = CROSS / f"{unit_name}_to_{dist_name}.csv"
            n, um, shares = assign_units_to_district(unit_shp, unit_id, suffix, dist_polys, out)
            print(f"Wrote {out} rows={n} unmatched={um}")
            print(f"Wrote {shares}")


if __name__ == "__main__":
    main()
