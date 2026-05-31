import csv
import json
from collections import defaultdict
from pathlib import Path

import shapefile


ROOT = Path("data")
CROSS = ROOT / "crosswalks"
CROSS.mkdir(parents=True, exist_ok=True)

NEUTRAL_PATH = ROOT / "neutral NV.geojson"


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
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


def normalize_polygon_coords(geom):
    t = geom.get("type")
    coords = geom.get("coordinates", [])
    if t == "Polygon":
        return [coords]
    if t == "MultiPolygon":
        return coords
    return []


def load_neutral():
    d = json.loads(NEUTRAL_PATH.read_text(encoding="utf-8"))
    out = []
    for f in d.get("features", []):
        p = f.get("properties", {})
        did = str(p.get("id", p.get("NAME", ""))).strip()
        polys = normalize_polygon_coords(f.get("geometry", {}))
        # bbox prefilter
        xmin = ymin = float("inf")
        xmax = ymax = float("-inf")
        for poly in polys:
            for ring in poly:
                for x, y in ring:
                    xmin = min(xmin, x)
                    ymin = min(ymin, y)
                    xmax = max(xmax, x)
                    ymax = max(ymax, y)
        out.append({"neutral_id": did, "name": str(p.get("NAME", did)), "polys": polys, "bbox": (xmin, ymin, xmax, ymax)})
    return out


def point_in_neutral(x, y, neutral):
    xmin, ymin, xmax, ymax = neutral["bbox"]
    if not (xmin <= x <= xmax and ymin <= y <= ymax):
        return False
    for poly in neutral["polys"]:
        # exterior ring only for assignment robustness
        if not poly:
            continue
        ext = poly[0]
        if point_in_ring(x, y, ext):
            return True
    return False


def assign_shapefile(shp_path, id_field, out_name):
    neutral = load_neutral()
    r = shapefile.Reader(str(shp_path))
    fields = [f[0] for f in r.fields[1:]]
    rows = []
    unmatched = 0
    counts = defaultdict(int)

    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict() if hasattr(sr.record, "as_dict") else {fields[i]: sr.record[i] for i in range(len(fields))}
        src_id = str(rec.get(id_field, "")).strip()
        if not src_id:
            continue

        x = y = None
        # try standard centroid fields first
        for lat_key, lon_key in [("INTPTLAT20", "INTPTLON20"), ("INTPTLAT10", "INTPTLON10"), ("INTPTLAT00", "INTPTLON00")]:
            lat = str(rec.get(lat_key, "")).replace("+", "").strip()
            lon = str(rec.get(lon_key, "")).replace("+", "").strip()
            try:
                y = float(lat)
                x = float(lon)
                break
            except Exception:
                pass
        if x is None or y is None:
            b = sr.shape.bbox
            x = (b[0] + b[2]) / 2.0
            y = (b[1] + b[3]) / 2.0

        assigned = ""
        for n in neutral:
            if point_in_neutral(x, y, n):
                assigned = n["neutral_id"]
                break
        if not assigned:
            unmatched += 1
        else:
            counts[assigned] += 1
        rows.append([src_id, assigned, "centroid"])

    out_path = CROSS / out_name
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([id_field.lower(), "neutral_id", "assign_method"])
        w.writerows(rows)

    share_path = CROSS / out_name.replace(".csv", "_shares.csv")
    with share_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["neutral_id", "unit_count", "share_of_units"])
        total = sum(counts.values()) or 1
        for k in sorted(counts):
            c = counts[k]
            w.writerow([k, c, f"{c/total:.10f}"])

    return out_path, share_path, len(rows), unmatched


def main():
    jobs = [
        (ROOT / "census/tl_2022_32_tabblock20/tl_2022_32_tabblock20.shp", "GEOID20", "neutral_to_tabblock20.csv"),
        (ROOT / "census/tl_2010_32_tabblock00/tl_2010_32_tabblock00.shp", "BLKIDFP00", "neutral_to_tabblock00.csv"),
        (ROOT / "census/tl_2020_32_vtd20/tl_2020_32_vtd20.shp", "GEOID20", "neutral_to_vtd20.csv"),
        (ROOT / "census/tl_2012_32_vtd10/tl_2012_32_vtd10.shp", "GEOID10", "neutral_to_vtd10.csv"),
    ]
    for shp, idf, out in jobs:
        p1, p2, n, um = assign_shapefile(shp, idf, out)
        print(f"Wrote {p1} rows={n} unmatched={um}")
        print(f"Wrote {p2}")


if __name__ == "__main__":
    main()
