import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import shapefile
from shapely import make_valid
from shapely.geometry import shape
from shapely.strtree import STRtree


ROOT = Path("data")
CROSS = ROOT / "crosswalks"
CROSS.mkdir(parents=True, exist_ok=True)

NEUTRAL_SHP_PATH = ROOT / "district-shapes" / "POLYGON.shp"
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


def _bbox_for_polys(polys):
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for poly in polys:
        for ring in poly:
            for x, y in ring:
                xmin = min(xmin, x)
                ymin = min(ymin, y)
                xmax = max(xmax, x)
                ymax = max(ymax, y)
    return (xmin, ymin, xmax, ymax)


def load_neutral():
    # Prefer the user-provided DRA shapefile when available.
    if NEUTRAL_SHP_PATH.exists():
        r = shapefile.Reader(str(NEUTRAL_SHP_PATH))
        fields = [f[0] for f in r.fields[1:]]
        out = []
        for sr in r.iterShapeRecords():
            rec = sr.record.as_dict() if hasattr(sr.record, "as_dict") else {fields[i]: sr.record[i] for i in range(len(fields))}
            did_raw = rec.get("id", rec.get("NAME", ""))
            did = str(int(float(did_raw))) if str(did_raw).strip() else ""
            if not did:
                continue
            geom = shape(sr.shape.__geo_interface__)
            if not geom.is_valid:
                geom = make_valid(geom)
            if geom.is_empty:
                continue
            out.append({"neutral_id": did, "name": str(rec.get("NAME", did)), "geom": geom})
        return out

    # Fallback to existing GeoJSON path.
    d = json.loads(NEUTRAL_PATH.read_text(encoding="utf-8"))
    out = []
    for f in d.get("features", []):
        p = f.get("properties", {})
        did = str(p.get("id", p.get("NAME", ""))).strip()
        geom = shape(f.get("geometry", {}))
        if not geom.is_valid:
            geom = make_valid(geom)
        if not did or geom.is_empty:
            continue
        out.append({"neutral_id": did, "name": str(p.get("NAME", did)), "geom": geom})
    return out


def assign_shapefile(shp_path, id_field, out_name):
    neutral = load_neutral()
    neutral_geoms = [n["geom"] for n in neutral]
    neutral_id_by_wkb = {n["geom"].wkb: n["neutral_id"] for n in neutral}
    tree = STRtree(neutral_geoms)

    r = shapefile.Reader(str(shp_path))
    fields = [f[0] for f in r.fields[1:]]
    rows_main = []
    rows_weighted = []
    unmatched = 0.0
    full_matches = 0
    splits = 0
    max_share_sum = 0.0
    entropy_sum = 0.0
    assign_method_counts = Counter()
    counts = defaultdict(float)

    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict() if hasattr(sr.record, "as_dict") else {fields[i]: sr.record[i] for i in range(len(fields))}
        src_id = str(rec.get(id_field, "")).strip()
        if not src_id:
            continue

        src_geom = shape(sr.shape.__geo_interface__)
        if not src_geom.is_valid:
            src_geom = make_valid(src_geom)
        if src_geom.is_empty:
            rows_main.append([src_id, "", "unmatched", ""])
            unmatched += 1.0
            assign_method_counts["unmatched"] += 1
            continue

        src_area = src_geom.area
        hit_idx = tree.query(src_geom)
        pieces = []
        if src_area > 0 and len(hit_idx) > 0:
            for i in hit_idx:
                h = neutral_geoms[int(i)]
                inter = src_geom.intersection(h)
                if inter.is_empty:
                    continue
                inter_area = inter.area
                if inter_area <= 0:
                    continue
                nid = neutral_id_by_wkb[h.wkb]
                pieces.append((nid, inter_area / src_area))

        if pieces:
            agg = defaultdict(float)
            for nid, share in pieces:
                agg[nid] += share
            norm = sum(agg.values()) or 1.0
            weighted = sorted(((nid, share / norm) for nid, share in agg.items()), key=lambda x: x[1], reverse=True)
            top_nid, top_share = weighted[0]
            method = "overlap_full" if len(weighted) == 1 else "overlap_split"
            if len(weighted) == 1:
                full_matches += 1
            else:
                splits += 1
            assign_method_counts[method] += 1
            max_share_sum += top_share
            ent = 0.0
            for _, s in weighted:
                if s > 0:
                    ent -= s * math.log(s)
            entropy_sum += ent
            rows_main.append([src_id, top_nid, method, f"{top_share:.10f}"])
            for nid, share in weighted:
                rows_weighted.append([src_id, nid, f"{share:.10f}", method])
                counts[nid] += share
        else:
            # Fallback: centroid-in-polygon to avoid dropping tiny slivers.
            c = src_geom.centroid
            centroid_idx = tree.query(c)
            assigned = ""
            for i in centroid_idx:
                h = neutral_geoms[int(i)]
                if h.covers(c):
                    assigned = neutral_id_by_wkb[h.wkb]
                    break
            if assigned:
                rows_main.append([src_id, assigned, "centroid_fallback", "1.0000000000"])
                rows_weighted.append([src_id, assigned, "1.0000000000", "centroid_fallback"])
                counts[assigned] += 1.0
                full_matches += 1
                max_share_sum += 1.0
                assign_method_counts["centroid_fallback"] += 1
            else:
                rows_main.append([src_id, "", "unmatched", ""])
                unmatched += 1.0
                assign_method_counts["unmatched"] += 1

    out_path = CROSS / out_name
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([id_field.lower(), "neutral_id", "assign_method", "top_share"])
        w.writerows(rows_main)

    weighted_path = CROSS / out_name.replace(".csv", "_weighted.csv")
    with weighted_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([id_field.lower(), "neutral_id", "alloc_weight", "assign_method"])
        w.writerows(rows_weighted)

    share_path = CROSS / out_name.replace(".csv", "_shares.csv")
    with share_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["neutral_id", "allocated_weight", "share_of_total_weight"])
        total = sum(counts.values()) or 1
        for k in sorted(counts):
            c = counts[k]
            w.writerow([k, c, f"{c/total:.10f}"])

    total_units = len(rows_main) or 1
    qa_path = CROSS / out_name.replace(".csv", "_qa.csv")
    with qa_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerow(["source_shapefile", str(shp_path)])
        w.writerow(["source_units", len(rows_main)])
        w.writerow(["matched_units", len(rows_main) - int(unmatched)])
        w.writerow(["unmatched_units", int(unmatched)])
        w.writerow(["unmatched_rate", f"{unmatched/total_units:.10f}"])
        w.writerow(["full_match_units", full_matches])
        w.writerow(["split_units", splits])
        w.writerow(["split_rate", f"{splits/total_units:.10f}"])
        w.writerow(["avg_top_share", f"{max_share_sum/max(1, (len(rows_main) - int(unmatched))):.10f}"])
        w.writerow(["avg_entropy", f"{entropy_sum/max(1, (len(rows_main) - int(unmatched))):.10f}"])
        for method, c in sorted(assign_method_counts.items()):
            w.writerow([f"assign_method_{method}", c])

    return out_path, weighted_path, share_path, qa_path, len(rows_main), int(unmatched)


def main():
    jobs = [
        (ROOT / "census/tl_2022_32_tabblock20/tl_2022_32_tabblock20.shp", "GEOID20", "neutral_to_tabblock20.csv"),
        (ROOT / "census/tl_2010_32_tabblock00/tl_2010_32_tabblock00.shp", "BLKIDFP00", "neutral_to_tabblock00.csv"),
        (ROOT / "census/tl_2020_32_vtd20/tl_2020_32_vtd20.shp", "GEOID20", "neutral_to_vtd20.csv"),
        (ROOT / "census/tl_2012_32_vtd10/tl_2012_32_vtd10.shp", "GEOID10", "neutral_to_vtd10.csv"),
    ]
    for shp, idf, out in jobs:
        p1, pw, p2, pqa, n, um = assign_shapefile(shp, idf, out)
        print(f"Wrote {p1} rows={n} unmatched={um}")
        print(f"Wrote {pw}")
        print(f"Wrote {p2}")
        print(f"Wrote {pqa}")


if __name__ == "__main__":
    main()
