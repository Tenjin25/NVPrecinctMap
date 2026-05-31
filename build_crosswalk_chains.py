import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path


ROOT = Path("data/crosswalks")

TABBLOCK00_TO_VTD00 = ROOT / "tabblock00_to_vtd00.csv"
NHGIS_BLK2000_BLK2010_ZIP = ROOT / "nhgis_blk2000_blk2010_32.zip"
NHGIS_BLK2010_BG2020_ZIP = ROOT / "nhgis_blk2010_bg2020_32.zip"
NHGIS_BLK2020_BG2010_ZIP = ROOT / "nhgis_blk2020_bg2010_32.zip"


def load_zip_csv(zip_path: Path):
    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_name = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
        text = zf.read(csv_name).decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def to_float(s):
    try:
        return float((s or "").strip())
    except Exception:
        return 0.0


def build_vtd00_to_blk2010():
    # blk2000 -> vtd00 (deterministic assignment from tabblock crosswalk)
    blk2000_to_vtd = {}
    with TABBLOCK00_TO_VTD00.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            blk = (row.get("blkidfp00") or "").strip()
            vtd = (row.get("vtdidfp00") or "").strip()
            if blk and vtd:
                blk2000_to_vtd[blk] = vtd

    # blk2000 -> blk2010 (weighted) from NHGIS
    nhgis = load_zip_csv(NHGIS_BLK2000_BLK2010_ZIP)
    out = defaultdict(float)
    for row in nhgis:
        blk2000 = (row.get("blk2000ge") or "").strip()
        blk2010 = (row.get("blk2010ge") or "").strip()
        w = to_float(row.get("weight"))
        if not blk2000 or not blk2010 or w <= 0:
            continue
        if not blk2000.startswith("32") or not blk2010.startswith("32"):
            continue
        vtd = blk2000_to_vtd.get(blk2000, "")
        if not vtd:
            continue
        out[(vtd, blk2010)] += w

    out_path = ROOT / "vtd00_to_blk2010_weighted.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["vtdidfp00", "blk2010ge", "alloc_weight"])
        for (vtd, blk2010), weight in sorted(out.items()):
            w.writerow([vtd, blk2010, f"{weight:.10f}"])
    return out_path, len(out)


def build_vtd00_to_bg2020(vtd_blk2010_path: Path):
    # vtd00 -> blk2010
    vtd_blk = defaultdict(float)
    with vtd_blk2010_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            vtd = (row.get("vtdidfp00") or "").strip()
            blk2010 = (row.get("blk2010ge") or "").strip()
            w = to_float(row.get("alloc_weight"))
            if vtd and blk2010 and w > 0:
                vtd_blk[(vtd, blk2010)] += w

    # blk2010 -> bg2020
    nhgis = load_zip_csv(NHGIS_BLK2010_BG2020_ZIP)
    blk_to_bg = defaultdict(list)
    for row in nhgis:
        blk2010 = (row.get("blk2010ge") or "").strip()
        bg2020 = (row.get("bg2020ge") or "").strip()
        w = to_float(row.get("weight"))
        if blk2010.startswith("32") and bg2020.startswith("32") and w > 0:
            blk_to_bg[blk2010].append((bg2020, w))

    out = defaultdict(float)
    for (vtd, blk2010), w_vb in vtd_blk.items():
        for bg2020, w_bb in blk_to_bg.get(blk2010, []):
            out[(vtd, bg2020)] += (w_vb * w_bb)

    out_path = ROOT / "vtd00_to_bg2020_weighted.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["vtdidfp00", "bg2020ge", "alloc_weight"])
        for (vtd, bg2020), weight in sorted(out.items()):
            w.writerow([vtd, bg2020, f"{weight:.10f}"])
    return out_path, len(out)


def build_blk2020_to_bg2010():
    nhgis = load_zip_csv(NHGIS_BLK2020_BG2010_ZIP)
    out_path = ROOT / "blk2020_to_bg2010_weighted.csv"
    n = 0
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["blk2020ge", "bg2010ge", "alloc_weight"])
        for row in nhgis:
            blk2020 = (row.get("blk2020ge") or "").strip()
            bg2010 = (row.get("bg2010ge") or "").strip()
            wt = to_float(row.get("weight"))
            if not blk2020.startswith("32"):
                continue
            if not bg2010.startswith("32"):
                continue
            if wt <= 0:
                continue
            w.writerow([blk2020, bg2010, f"{wt:.10f}"])
            n += 1
    return out_path, n


def main():
    vtd_blk_path, n1 = build_vtd00_to_blk2010()
    vtd_bg_path, n2 = build_vtd00_to_bg2020(vtd_blk_path)
    blk_bg_path, n3 = build_blk2020_to_bg2010()
    print(f"Wrote {vtd_blk_path} rows={n1}")
    print(f"Wrote {vtd_bg_path} rows={n2}")
    print(f"Wrote {blk_bg_path} rows={n3}")


if __name__ == "__main__":
    main()
