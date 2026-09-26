"""Fetch and cache weather (Open-Meteo) and OSM features (Overpass) for the BiteCast cities.

Usage: python fetch.py [city ...]     (no args = all cities; re-running overwrites)
Writes data/weather/{city}.csv and data/osm/{city}.json so the app runs from its cache.
"""
import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from cities import CITIES, grid_points

DATA = Path(__file__).parent / "data"
HEADERS = {"User-Agent": "BiteCast hackathon project (IEEE OneAquaHealth Hackathon 2026)"}

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
# Worldwide mirrors, in order of reliability when checked (2026-09-22, a query in central Karachi): both of
# the first two answered with the same count. Public Overpass instances go down or rate-limit without
# warning, so the fetcher rotates and retries; overpass-api.de publishes free slots at /api/status.
# Never add a regional instance (overpass.osm.ch holds Switzerland only): for anywhere else it answers
# 200 with no elements, which looks exactly like a place with no water.
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]

START = date(2024, 1, 1)  # history start: two full mosquito seasons of record
# Checked 2026-09-19: both the archive and the forecast endpoint serve all four variables below.
# If a single row still comes back without a mean, it is filled with (tmax + tmin) / 2.
DAILY = {"temperature_2m_mean": "tmean", "temperature_2m_max": "tmax",
         "temperature_2m_min": "tmin", "precipitation_sum": "precip"}
# The archive lags real time by a few days; the forecast call's past days cover that gap.
PAST_DAYS, FORECAST_DAYS = 14, 16  # forecast_days=16 = today + 15 (Open-Meteo max is 16)
# A refresh re-asks only the last month: Open-Meteo counts every 2 weeks x location as one call, so the whole
# record since START for 9 cells is ~650 calls and a few refreshes used up the free 10,000 a day.
REFETCH_DAYS = 31
# If Open-Meteo still refuses (its free limit is per day), a place looked up anywhere takes its past weather
# from NASA POWER instead: free, no key, ~3 days behind. Its grid is ~50 km and it ran 0.7-2.4 C off
# Open-Meteo and missed some heavy-rain days (checked on Coimbra, Oslo and Lahore, 2025), so it is only
# the fallback, shifted to match Open-Meteo's own last 14 days, which still arrive with the forecast.
POWER = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_FALLBACK = True     # preload.py turns it off: a place cached ahead of time waits for the best weather


def start_for(c):
    """How far back the weather goes. The research and reference places keep the whole record since START
    (the validation uses it); a place looked up anywhere needs last year and this one: fewer calls."""
    return START if not c.get("anywhere") else max(START, date(date.today().year - 1, 1, 1))


def weather_cells(key):
    """How many grid cells a cached weather file has (0 if none)."""
    path = DATA / "weather" / f"{key}.csv"
    return int(pd.read_csv(path, usecols=["cell"])["cell"].max()) + 1 if path.exists() else 0

# Enough to tell a stormwater basin from a park lake, and a mosquito pond from a fountain or fish tank.
WATER_TAGS = ["name", "waterway", "natural", "water", "landuse", "intermittent", "seasonal",
              "tunnel", "covered", "layer", "location", "width",
              "amenity", "man_made", "leisure", "reservoir_type", "basin"]
EXPOSURE_MARGIN_M = 500  # exposure searched wider so edge features still see their neighbours


_mirror = 0  # start where the last successful call left off: a throttled mirror shouldn't be retried first


def request_json(method, urls, attempts=8, backoff=20, timeout=90, accept=None, **kw):
    """HTTP with retries, rotating through mirror urls on errors, 429 and 5xx.
    Public Overpass servers were seen returning 429/504 for minutes at a time, hence the patience.
    accept(url, json) -> False rejects a well-formed answer (e.g. suspiciously empty) and tries the next url."""
    global _mirror
    for i in range(attempts):
        url = urls[(_mirror + i) % len(urls)]
        try:
            r = requests.request(method, url, headers=HEADERS, timeout=timeout, **kw)
            if r.status_code == 200:
                j = r.json()
                # Overpass reports query timeouts / memory errors as HTTP 200 with a "remark".
                # Open-Meteo answers a multi-point request with a list, which carries no remark.
                remark = j.get("remark", "") if isinstance(j, dict) else ""
                if "error" not in remark:
                    if accept is None or accept(url, j):
                        _mirror = (_mirror + i) % len(urls)
                        return j
                    print(f"  {url}: empty answer, asking another mirror", flush=True)
                    continue   # not a rate limit: no need to wait before the next mirror
                print(f"  {url}: {j['remark'][:120]}")
            else:
                print(f"  {url}: HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            print(f"  {url}: {e!r}")
        time.sleep(min(backoff * (i + 1), 90))
    raise RuntimeError(f"{attempts} attempts failed for {urls}")


# ---------------------------------------------------------------- weather

def daily_frames(j):
    """Open-Meteo returns one object for a single point and a list for several: always give back a list."""
    for loc in (j if isinstance(j, list) else [j]):
        df = pd.DataFrame(loc["daily"]).rename(columns=DAILY)
        yield df.set_index(pd.to_datetime(df.pop("time"))).astype(float)


def cached_history(key, cells, start):
    """The observed days already on disk, one frame per cell, if the file matches this grid and runs
    unbroken from start (or earlier); else None (fetch everything)."""
    path = DATA / "weather" / f"{key}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    df = df[df["kind"] == "observed"]
    if sorted(df["cell"].unique()) != list(range(cells)):
        return None
    out = []
    for i in range(cells):
        cell = df[df["cell"] == i].set_index("date")[["tmean", "tmax", "tmin", "precip"]]
        if cell.empty or cell.index[0].date() > start or len(cell) != (cell.index[-1] - cell.index[0]).days + 1:
            return None
        out.append(cell)
    return out


def fetch_weather(key, c, full=False):
    """One row per (day, grid cell). Cell 0 is the centre; the rest sample the place's own spread,
    which is 1-2 °C in a hilly city — more than a day of mosquito development.
    Unless full, days already on disk are kept and only the last REFETCH_DAYS are asked for again
    (full=True when the grid itself changed)."""
    today = date.today()
    end = today + timedelta(days=FORECAST_DAYS - 1)
    pts = grid_points(c)
    start = start_for(c)
    old = None if full else cached_history(key, len(pts), start)
    since = start
    if old is not None:     # never leave a gap, however long the place went unrefreshed
        start = min(start, old[0].index[0].date())   # an older record on disk is kept, not cut
        since = max(start, min(today - timedelta(days=REFETCH_DAYS), old[0].index[-1].date() + timedelta(days=1)))
    anywhere = bool(c.get("anywhere"))
    fallback = POWER_FALLBACK and anywhere   # the research cities never mix sources: they wait for Open-Meteo
    base = {"latitude": ",".join(str(p[0]) for p in pts), "longitude": ",".join(str(p[1]) for p in pts),
            "daily": ",".join(DAILY), "timezone": "auto"}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:      # the two halves of the record, at the same time
        # a place looked up anywhere: someone is waiting, and NASA POWER stands behind, so don't sit out a
        # refusal (Open-Meteo's limit is per day; retrying for a minute won't lift it)
        fa = ex.submit(request_json, "GET", [ARCHIVE], **({"attempts": 2, "backoff": 3} if anywhere else {}),
                       params={**base, "start_date": since.isoformat(), "end_date": today.isoformat()})
        ff = ex.submit(request_json, "GET", [FORECAST], params={
            **base, "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS})
        forecasts = list(daily_frames(ff.result()))
        try:
            archives = list(daily_frames(fa.result()))
        except RuntimeError as e:
            if not fallback:
                raise
            print(f"  {key}: Open-Meteo archive refused ({e}); past weather from NASA POWER", flush=True)
            archives = power_archives(pts, since, today, forecasts)
    assert len(archives) == len(forecasts) == len(pts), (len(archives), len(forecasts), len(pts))

    cells, last = [], None
    for i, (arch, fc) in enumerate(zip(archives, forecasts)):
        if old is not None:          # history from disk, the recent weeks fresh
            arch = pd.concat([old[i][old[i].index < pd.Timestamp(since)], arch])
        df = arch.combine_first(fc)  # archive wins wherever it has a value
        df = df.reindex(pd.date_range(start, end, name="date"))
        have = df.dropna(how="all").index
        if len(have):                            # Open-Meteo sometimes has no day 16 for a place
            last = have[-1] if last is None else min(last, have[-1])
        cells.append(df)
    if last is not None and last < cells[0].index[-1]:
        print(f"  {key}: forecast ends {last.date()}, trimming {(cells[0].index[-1] - last).days} empty day(s)")

    out_rows = []
    for i, df in enumerate(cells):
        df = (df.loc[:last] if last is not None else df).copy()
        df["tmean"] = df["tmean"].fillna((df["tmax"] + df["tmin"]) / 2)
        missing = int(df.isna().sum().sum())
        if missing:
            print(f"  WARNING {key} cell {i}: {missing} missing values; temps forward-filled, precip -> 0")
            temps = ["tmean", "tmax", "tmin"]
            df[temps] = df[temps].ffill().bfill()
            df["precip"] = df["precip"].fillna(0)
        assert len(df) >= (end - start).days - 1 and not df.isna().any().any()
        df = df.round(1)
        df["cell"] = i
        df["kind"] = ["observed" if d.date() < today else "forecast" for d in df.index]
        df.index = df.index.strftime("%Y-%m-%d")
        out_rows.append(df[["cell", "tmean", "tmax", "tmin", "precip", "kind"]])

    out = DATA / "weather" / f"{key}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".csv.tmp")          # atomic: a refresh must never be half-read by a request
    pd.concat(out_rows).to_csv(tmp)
    os.replace(tmp, out)


def power_archives(pts, since, today, forecasts):
    """Past weather from NASA POWER, one frame per grid cell. One request (its ~50 km grid cell covers the
    whole place); each cell's temperatures are shifted by the mean difference from Open-Meteo's own past
    days for that cell, so the record doesn't jump where the two sources meet. Rain is used as it is."""
    j = request_json("GET", [POWER], attempts=3, params={
        "parameters": "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR", "community": "AG", "format": "JSON",
        "latitude": pts[0][0], "longitude": pts[0][1], "time-standard": "LST",
        "start": since.strftime("%Y%m%d"), "end": today.strftime("%Y%m%d")})
    p = pd.DataFrame(j["properties"]["parameter"]).rename(
        columns={"T2M": "tmean", "T2M_MAX": "tmax", "T2M_MIN": "tmin", "PRECTOTCORR": "precip"})
    p.index = pd.to_datetime(p.index, format="%Y%m%d")
    p = p.where(p > -900).dropna(how="all")          # -999 = not yet available
    out = []
    for fc in forecasts:
        past = fc[fc.index < pd.Timestamp(today)]
        both = p.index.intersection(past.dropna(subset=["tmean"]).index)
        shift = float((past.loc[both, "tmean"] - p.loc[both, "tmean"]).mean()) if len(both) >= 5 else 0.0
        cell = p.copy()
        cell[["tmean", "tmax", "tmin"]] += shift
        out.append(cell)
    return out


# ---------------------------------------------------------------- OSM

# Limit: water relations (multipolygon lakes / riverbanks) are skipped; big rivers still show up
# as waterway=river centre lines. Add relation outer rings if lake shores ever matter.
WATER_Q = """[out:json][timeout:180];
(
  way["waterway"~"^(river|stream|canal|ditch|drain)$"]({a});
  way["natural"="water"]({a});
  way["landuse"~"^(basin|reservoir)$"]({a});
);
out tags geom;"""

EXPOSURE_Q = """[out:json][timeout:180];
(
  way["landuse"="residential"]({a});
  node["leisure"~"^(park|playground)$"]({a});
  way["leisure"~"^(park|playground)$"]({a});
  node["amenity"~"^(school|kindergarten)$"]({a});
  way["amenity"~"^(school|kindergarten)$"]({a});
);
out tags geom;
(
  relation["leisure"~"^(park|playground)$"]({a});
  relation["amenity"~"^(school|kindergarten)$"]({a});
);
out tags center;"""

# One request instead of two, for a person waiting on a new place: the water and the homes/schools in a
# single query, split by tags afterwards (WATER_TAGS below). Halves the round trips and the query slots
# we take from the volunteer servers. Both use the wider (exposure) area; habitat.py clips water to the
# study circle anyway.
COMBINED_Q = """[out:json][timeout:180];
(
  way["waterway"~"^(river|stream|canal|ditch|drain)$"]({a});
  way["natural"="water"]({a});
  way["landuse"~"^(basin|reservoir)$"]({a});
  way["landuse"="residential"]({a});
  node["leisure"~"^(park|playground)$"]({a});
  way["leisure"~"^(park|playground)$"]({a});
  node["amenity"~"^(school|kindergarten)$"]({a});
  way["amenity"~"^(school|kindergarten)$"]({a});
);
out tags geom;
(
  relation["leisure"~"^(park|playground)$"]({a});
  relation["amenity"~"^(school|kindergarten)$"]({a});
);
out tags center;"""


def is_water(tags):
    """Water in the combined query's answer; everything else is a place where people are."""
    return ("waterway" in tags or tags.get("natural") == "water"
            or tags.get("landuse") in ("basin", "reservoir"))


def overpass(query, attempts=8, qtimeout=180, backoff=20):
    empty = set()

    def accept(url, j):
        # A town with no mapped water or homes at all is rare; a mirror that lacks the region is not.
        # Believe "nothing here" only once two different mirrors say so.
        if j.get("elements"):
            return True
        empty.add(url)
        return len(empty) >= 2

    # a person may be waiting (anywhere mode): don't hold a dead connection far past the query's own limit
    j = request_json("POST", OVERPASS, attempts=attempts, backoff=backoff, timeout=min(90, qtimeout + 15),
                     accept=accept, data={"data": query.replace("timeout:180", f"timeout:{qtimeout}")})
    time.sleep(5)  # be polite to the shared public servers
    return j["elements"]


HEDGE_AFTER_S = 12    # ask the next mirror too if the current one has not answered by then
MIN_GAP_S = 3         # never start two requests closer together than this (the servers are volunteers')


def _post_overpass(url, query, timeout):
    r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    j = r.json()
    if "error" in j.get("remark", ""):
        raise RuntimeError(j["remark"][:120])
    return j


def overpass_hedged(query, qtimeout=60, deadline=120):
    """For a person waiting on a new place. Ask one mirror; if it fails, or has not answered within
    HEDGE_AFTER_S, ask the next one as well and take the first good answer. The extra load stays small: a
    second mirror is only asked when the first is slow or failing. Sequential retries used to wait out
    each failure in turn (Islamabad: 169 s through two 504s and a hung connection).
    An empty answer is believed only once two mirrors agree (a mirror may not hold the region)."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    global _mirror
    q = query.replace("timeout:180", f"timeout:{qtimeout}")
    pool = ThreadPoolExecutor(max_workers=len(OVERPASS))
    running, empty, errors = {}, set(), []
    t0 = last = time.monotonic()
    turn = _mirror                                   # start where the last good answer came from
    launched = 0
    try:
        while time.monotonic() - t0 < deadline:
            now = time.monotonic()
            idle = [u for u in OVERPASS if u not in running.values()]
            due = not running or now - last >= HEDGE_AFTER_S
            gap = min(20, MIN_GAP_S * (1 + len(errors)))  # back off as failures pile up
            if idle and due and (launched == 0 or now - last >= gap):
                url = OVERPASS[turn % len(OVERPASS)]
                turn += 1
                if url in idle:
                    running[pool.submit(_post_overpass, url, q, qtimeout + 15)] = url
                    last, launched = now, launched + 1
                continue
            done, _ = wait(list(running), timeout=0.5, return_when=FIRST_COMPLETED) if running else (set(), None)
            if not running:
                time.sleep(0.5)
            for f in done:
                url = running.pop(f)
                try:
                    j = f.result()
                except Exception as e:
                    errors.append(f"{url.split('/')[2]}: {e}"[:140])
                    print(f"  {errors[-1]}", flush=True)
                    continue
                if j.get("elements"):
                    _mirror = OVERPASS.index(url)       # next time, start with the one that answered
                    return j["elements"]
                empty.add(url)
                print(f"  {url.split('/')[2]}: empty answer, asking another mirror", flush=True)
                if len(empty) >= 2:
                    return []
        raise RuntimeError(f"no map server answered within {deadline} s ({'; '.join(errors[-3:]) or 'no reply'})")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def pt(p):
    return [round(p["lon"], 5), round(p["lat"], 5)]


def shape(el, area):
    """(geom type, coords) in GeoJSON [lon, lat] order; Polygon = closed outer ring, flat list."""
    if el["type"] == "node":
        return "Point", pt(el)
    if "center" in el:
        return "Point", pt(el["center"])
    g = el["geometry"]
    closed = len(g) >= 4 and g[0] == g[-1]
    return ("Polygon" if area and closed else "LineString"), [pt(p) for p in g]


def exposure_kind(t):
    # most specific first: a school ground tagged landuse=residential is still a school
    if t.get("amenity") in ("school", "kindergarten"):
        return "school"
    if t.get("leisure") in ("playground", "park"):
        return t["leisure"]
    return "residential"


def bbox(c, r):
    """(south,west,north,east) around the centre. Overpass answers a bbox far faster than `around`,
    which matters when someone is waiting for a lookup; habitat.py clips to the circle afterwards."""
    dlat = r / 110_540
    dlon = r / (111_320 * math.cos(math.radians(c["lat"])))
    return f"{c['lat'] - dlat:.5f},{c['lon'] - dlon:.5f},{c['lat'] + dlat:.5f},{c['lon'] + dlon:.5f}"


def fetch_osm(key, c, attempts=8, qtimeout=180, use_bbox=False, backoff=20, hedged=False, deadline=120):
    """attempts/qtimeout are lowered for anywhere-mode lookups: a person is waiting, so fail fast.
    hedged=True (a new place, someone waiting): the water and the homes/schools queries run at the same
    time, each hedged across mirrors (overpass_hedged). Otherwise (scheduled refreshes): one after the
    other, patiently."""
    around = (lambda r: bbox(c, r)) if use_bbox else (lambda r: f"around:{r},{c['lat']},{c['lon']}")
    if hedged:   # one combined query, hedged across mirrors, then split by tags
        elements = overpass_hedged(COMBINED_Q.format(a=around(c["radius_m"] + EXPOSURE_MARGIN_M)), qtimeout, deadline)
        water_el = [el for el in elements if is_water(el.get("tags", {}))]
        exposure_el = [el for el in elements if not is_water(el.get("tags", {}))]
    else:        # scheduled refreshes: two patient queries, each tightly scoped
        water_el = overpass(WATER_Q.format(a=around(c["radius_m"])), attempts, qtimeout, backoff)
        exposure_el = overpass(EXPOSURE_Q.format(a=around(c["radius_m"] + EXPOSURE_MARGIN_M)),
                               attempts, qtimeout, backoff)
    water = []
    for el in water_el:
        t = el.get("tags", {})
        geom, coords = shape(el, area="waterway" not in t)  # waterway=* is always a line
        water.append({"id": f"{el['type']}/{el['id']}", "tags": {k: t[k] for k in WATER_TAGS if k in t},
                      "geom": geom, "coords": coords})
    exposure = []
    for el in exposure_el:
        geom, coords = shape(el, area=True)
        tags = el.get("tags", {})
        exposure.append({"id": f"{el['type']}/{el['id']}", "kind": exposure_kind(tags),
                         **({"name": tags["name"]} if tags.get("name") else {}),   # a school's name on its label
                         "geom": geom, "coords": coords})

    # Overpass fails in ways that look like an empty city. Never overwrite a good cache with that:
    # the cached data is what people see, and a mirror having a bad minute must not destroy it.
    out = DATA / "osm" / f"{key}.json"
    if not water or not exposure:
        have = json.loads(out.read_text())["water"] if out.exists() else []
        raise RuntimeError(f"{key}: Overpass returned {len(water)} water / {len(exposure)} exposure features; "
                           f"keeping the existing cache ({len(have)} water features). Try again later.")
    if not 50 <= len(water) <= 4000:
        print(f"  WARNING {key}: {len(water)} water features (expected 50..4000), not truncated")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"city": key, "fetched": date.today().isoformat(),
                               "center": [c["lat"], c["lon"]], "radius_m": c["radius_m"],
                               **({"name": c["name"], "country": c.get("country", ""),   # survive a restart
                                   "cells": c.get("cells", 9)}
                                  if c.get("anywhere") else {}),
                               "water": water, "exposure": exposure}, separators=(",", ":")))
    os.replace(tmp, out)


# ---------------------------------------------------------------- report + checks

def check_osm(key):
    """Load the cached JSON back and assert its shape."""
    d = json.loads((DATA / "osm" / f"{key}.json").read_text())
    assert d["city"] == key and len(d["center"]) == 2 and d["radius_m"] == CITIES[key]["radius_m"]
    for f in d["water"] + d["exposure"]:
        assert f["geom"] in ("Point", "LineString", "Polygon"), f
        pts = [f["coords"]] if f["geom"] == "Point" else f["coords"]
        assert all(len(p) == 2 and -180 <= p[0] <= 180 and -90 <= p[1] <= 90 for p in pts), f["id"]
        if f["geom"] == "Polygon":
            assert f["coords"][0] == f["coords"][-1] and len(f["coords"]) >= 4, f["id"]
    assert all(f["geom"] == "LineString" for f in d["water"] if "waterway" in f["tags"])
    assert {f["kind"] for f in d["exposure"]} <= {"residential", "park", "playground", "school"}
    return d


def report(key):
    raw = pd.read_csv(DATA / "weather" / f"{key}.csv", parse_dates=["date"])
    assert list(raw.columns) == ["date", "cell", "tmean", "tmax", "tmin", "precip", "kind"], list(raw.columns)
    cells = raw.groupby("cell")["date"].agg(["min", "max", "count"])
    assert cells.nunique().max() == 1, f"{key}: grid cells cover different days\n{cells}"  # one grid, one calendar
    w = raw[raw["cell"] == 0].set_index("date").drop(columns="cell")   # the centre: the city-wide summary
    assert (w.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all()
    summer = w.loc["2025-06-01":"2025-08-31", "tmean"].mean()
    obs = w[w["kind"] == "observed"]
    print(f"{key}: weather {len(cells)} cells x {len(w)} days {w.index[0].date()}..{w.index[-1].date()}, "
          f"last observed {obs.index[-1].date()}, {int((w['kind'] == 'forecast').sum())} forecast rows, "
          f"summer-2025 tmean {summer:.1f} C, 2025 precip {w.loc['2025', 'precip'].sum():.0f} mm")
    d = check_osm(key)
    wcount = pd.Series([t.get("waterway") or (f"water={t['water']}" if "water" in t else None)
                        or t.get("natural") or t.get("landuse") for t in (f["tags"] for f in d["water"])])
    kb = lambda p: (DATA / p / f"{key}.{'csv' if p == 'weather' else 'json'}").stat().st_size / 1024
    print(f"  osm water {len(d['water'])}: {wcount.value_counts().to_dict()}")
    print(f"  osm exposure {len(d['exposure'])}: {pd.Series([f['kind'] for f in d['exposure']]).value_counts().to_dict()}")
    print(f"  files: weather {kb('weather'):.0f} KB, osm {kb('osm'):.0f} KB")
    return summer


if __name__ == "__main__":
    keys = sys.argv[1:] or list(CITIES)
    failed = []
    for key in keys:
        print(f"fetching {key} ...")
        try:  # one city's bad luck with a public mirror should not stop the rest
            fetch_weather(key, CITIES[key])
            fetch_osm(key, CITIES[key])
        except Exception as e:
            print(f"  FAILED {key}: {e}")
            failed.append(key)
    summers = {key: report(key) for key in keys if key not in failed}
    if failed:
        print(f"\nfailed, cache left untouched: {', '.join(failed)} — rerun: python fetch.py {' '.join(failed)}")
    if {"oslo", "benevento"} <= set(summers):
        assert summers["oslo"] < summers["benevento"] - 3, summers  # sanity: Oslo is clearly cooler
        print("sanity ok: Oslo summer tmean < Benevento's")
    sys.exit(1 if failed else 0)
