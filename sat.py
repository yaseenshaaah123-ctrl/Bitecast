"""Satellite layers for a place, read one small window at a time from free cloud-optimised files: no account,
no bulk download. Cached per place in data/sat/<key>.json; the model uses them when they are there and
works exactly as before when they are not.

- Water: JRC Global Surface Water 1984-2021 (Pekel et al. 2016, Nature; 30 m, Landsat). Every patch of
  open water, with how many months a year it holds water. It finds ponds and seasonal pools that nobody
  has mapped on OpenStreetMap; anything OSM already has is left to OSM (it knows the names and the types).
- People: Meta High Resolution Settlement Layer (Data for Good; 30 m). People per pixel, so "people nearby"
  counts people where OpenStreetMap has few homes mapped.

    python sat.py              # fetch for every cached place that has none yet
    python sat.py coimbra      # just these
"""
import json
import math
import os
import sys
from datetime import date
from pathlib import Path

# GDAL: read only the byte ranges needed, never list the remote directories
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.vrt")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "30")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "2")

import numpy as np

DIR = Path(__file__).parent / "data" / "sat"
GSW = "https://storage.googleapis.com/global-surface-water/downloads2021/seasonality/seasonality_{tile}v1_4_2021.tif"
HRSL = "https://dataforgood-fb-data.s3.amazonaws.com/hrsl-cogs/hrsl_general/hrsl_general-latest.vrt"
MARGIN_M = 500           # beyond the study circle, so edge features still see the people around them
MIN_PIXELS = 2           # a single 30 m pixel of water is too often a roof or a shadow
MAX_PATCHES = 400        # the biggest ones, if a place is all lakes
AGG = 3                  # people summed over 3x3 pixels (~90 m) before caching: plenty for a 300 m radius


def path(key):
    return DIR / f"{key}.json"


def cached(key):
    try:
        return json.loads(path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _bounds(c, r):
    dlat = r / 110_540
    dlon = r / (111_320 * math.cos(math.radians(c["lat"])))
    return c["lon"] - dlon, c["lat"] - dlat, c["lon"] + dlon, c["lat"] + dlat


def _gsw_tiles(b):
    """JRC tiles are 10 x 10 degrees, named by their top-left corner (e.g. 10W_50N)."""
    out = set()
    for lon in (b[0], b[2]):
        for lat in (b[1], b[3]):
            x, y = math.floor(lon / 10) * 10, math.floor(lat / 10) * 10 + 10
            out.add(f"{abs(x)}{'E' if x >= 0 else 'W'}_{abs(y)}{'N' if y >= 0 else 'S'}")
    return sorted(out)


def _read(urls, b):
    """One window over one or more files (a place on a tile edge), as (array, transform). None if no file
    covers it (open ocean has no JRC tile)."""
    import rasterio
    from rasterio.errors import RasterioIOError
    from rasterio.merge import merge
    srcs = []
    for u in urls:
        try:
            srcs.append(rasterio.open("/vsicurl/" + u))
        except RasterioIOError:
            continue
    if not srcs:
        return None
    try:
        arr, tr = merge(srcs, bounds=b, nodata=0 if srcs[0].nodata is None else srcs[0].nodata)
        return arr[0], tr
    finally:
        for s in srcs:
            s.close()


def water_patches(c, b):
    """Open-water patches with their months of water a year: [{months, permanent, coords}], biggest first."""
    from rasterio import features
    got = _read([GSW.format(tile=t) for t in _gsw_tiles(b)], b)
    if got is None:
        return []
    months, tr = got
    months = np.where(months == 255, 0, months).astype("uint8")   # 255 = no data
    wet = months > 0
    shapes = [(g, v) for g, v in features.shapes(wet.astype("uint8"), mask=wet, transform=tr, connectivity=8)]
    if not shapes:
        return []
    labels = features.rasterize([(g, i + 1) for i, (g, _) in enumerate(shapes)], out_shape=months.shape,
                                transform=tr, dtype="int32")
    n = np.bincount(labels.ravel(), minlength=len(shapes) + 1)
    perm = np.bincount(labels.ravel(), weights=(months == 12).ravel(), minlength=len(shapes) + 1)
    total = np.bincount(labels.ravel(), weights=months.ravel(), minlength=len(shapes) + 1)
    out = []
    for i, (g, _) in enumerate(shapes, start=1):
        if n[i] < MIN_PIXELS:
            continue
        ring = [[round(x, 5), round(y, 5)] for x, y in g["coordinates"][0]]
        out.append({"pixels": int(n[i]), "months": round(float(total[i] / n[i]), 1),
                    "permanent": round(float(perm[i] / n[i]), 2), "coords": ring})
    out.sort(key=lambda p: -p["pixels"])
    return out[:MAX_PATCHES]


def people_grid(b):
    """People per ~90 m cell over the window, or None where the layer has no data."""
    got = _read([HRSL], b)
    if got is None:
        return None
    pop, tr = got
    pop = np.nan_to_num(pop.astype("float64"), nan=0.0)
    if pop.sum() <= 0:
        return None
    rows, cols = pop.shape[0] // AGG, pop.shape[1] // AGG
    agg = pop[:rows * AGG, :cols * AGG].reshape(rows, AGG, cols, AGG).sum(axis=(1, 3))
    return {"lat0": round(tr.f, 6), "lon0": round(tr.c, 6), "dlat": round(-tr.e * AGG, 8), "dlon": round(tr.a * AGG, 8),
            "rows": rows, "cols": cols, "people": [int(round(v)) for v in agg.ravel()]}


def fetch(key, c):
    """Fetch and cache both layers for a place. Each is independent: one failing leaves the other."""
    b = _bounds(c, c["radius_m"] + MARGIN_M)
    out = {"key": key, "fetched": date.today().isoformat(), "center": [c["lat"], c["lon"]],
           "radius_m": c["radius_m"], "sources": {}, "water": [], "people": None}
    try:
        out["water"] = [{"id": f"sat/{i + 1}", **p} for i, p in enumerate(water_patches(c, b))]
        out["sources"]["water"] = "JRC Global Surface Water 1984-2021, seasonality (Pekel et al. 2016)"
    except Exception as e:
        out["sources"]["water_error"] = str(e)[:200]
    try:
        out["people"] = people_grid(b)
        out["sources"]["people"] = "Meta High Resolution Settlement Layer (Data for Good)"
    except Exception as e:
        out["sources"]["people_error"] = str(e)[:200]
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = path(key).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path(key))
    return out


if __name__ == "__main__":
    import time
    import places                          # registers every cached place
    from cities import ANYWHERE, CITIES
    assert _gsw_tiles((-8.5, 40.1, -8.3, 40.3)) == ["10W_50N"]
    assert _gsw_tiles((-51.2, -6.8, -51.1, -6.7)) == ["60W_0N"]
    assert _gsw_tiles((-0.1, 51.4, 0.1, 51.6)) == ["0E_60N", "10W_60N"]
    keys = sys.argv[1:] or [k for k in [*CITIES, *ANYWHERE] if cached(k) is None]
    for key in keys:
        c = CITIES.get(key) or ANYWHERE[key]
        t = time.time()
        d = fetch(key, c)
        pop = sum(d["people"]["people"]) if d["people"] else None
        print(f"{key}: {len(d['water'])} water patches, people {pop}, {time.time() - t:.0f} s"
              + "".join(f"  [{k}: {v}]" for k, v in d["sources"].items() if k.endswith("error")), flush=True)
