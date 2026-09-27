"""Anywhere mode: build a BiteCast model for any point on earth, on demand.

The pipeline is already global — Open-Meteo covers the planet and OpenStreetMap maps water everywhere —
so a place only needs its data fetched once and cached. Places are keyed on a ~1 km grid so two people
in the same neighbourhood share one fetch.

The biology travels too: which mosquitoes are modelled is decided per place, from GBIF records near it and
from whether its winter lets each species establish (model/species.py). The GBIF counts are fetched with
the place; if GBIF is down, the choice falls back to the climate alone and the app says so.
"""
import json
import math
import threading
import time

import fetch
import placestore
import presence
from cities import ANYWHERE, ANYWHERE_RADIUS_M, CITIES, REFERENCE
from model import habitat, risk

GRID = 2                  # decimal places: ~1.1 km, so nearby lookups reuse one cached fetch
MAX_PLACES = 200          # cached places kept in memory; the files stay on disk. limit: no eviction yet
MAP_STATE = {}            # key -> {"stage": "loading" | "ready" | "error", ...}: a map still on its way
# A brand-new place costs the free public services real work (~150 weather calls, one heavy map query), so
# the whole server opens at most this many an hour and fetches this many maps at once, whoever asks.
NEW_PER_HOUR = 30
MAPS_AT_ONCE = 3
_new_times = []


class Busy(Exception):
    """Too many new places right now; the caller should try again in a few minutes."""


def _claim_new():
    now = time.time()
    _new_times[:] = [t for t in _new_times if t > now - 3600]
    if len(_new_times) >= NEW_PER_HOUR:
        raise Busy("Lots of new places are being opened right now. Try again in a few minutes, "
                   "or open a place that's already on the map.")
    if sum(s.get("stage") == "loading" for s in MAP_STATE.values()) >= MAPS_AT_ONCE:
        raise Busy("The map servers are busy drawing other new places. Try again in a minute.")
    _new_times.append(now)
TOUCHED = {}              # key -> when someone last opened it, so refreshes follow real use (refresh.py)


def touch(key):
    """Someone opened this place: keep its weather current for a while."""
    if key not in CITIES:
        TOUCHED[key] = time.time()


def used_within(key, days):
    return (time.time() - TOUCHED.get(key, 0)) < days * 86400

_locks = {}               # one lock per key: two people asking for the same new place fetch once
_guard = threading.Lock()


def key_for(lat, lon):
    return "at_{}_{}".format(f"{lat:.{GRID}f}", f"{lon:.{GRID}f}").replace("-", "m").replace(".", "p")


def covering(lat, lon):
    """A place already cached whose mapped area has this point well inside it (two-thirds of its radius),
    so "Lahore" by search and by GPS a couple of km apart open one place instead of fetching two."""
    best = None
    for key, p in [*ANYWHERE.items(), *CITIES.items()]:
        if key in ANYWHERE and not (habitat.DATA / "osm" / f"{key}.json").exists():
            continue                                  # still loading, or failed: not a place yet
        dy = (lat - p["lat"]) * 111_320
        dx = (lon - p["lon"]) * 111_320 * math.cos(math.radians(lat))
        d = math.hypot(dx, dy)
        if d < p["radius_m"] * 2 / 3 and (best is None or d < best[0]):
            best = (d, key)
    return best and best[1]


def ensure(lat, lon, name=None, country=None, wait_for_map=True):
    """Return the place key, fetching and caching weather + species records + the map the first time.

    Weather and the GBIF counts come first, together: a few seconds, and enough to open the place with its
    season, forecast and species. The map is the slow, flaky part (public Overpass servers), so with
    wait_for_map=False it is fetched in the background and the water appears when it lands; MAP_STATE says
    where it has got to. In the rare case the map has to shrink the area to fit the servers, the weather is
    fetched again for the area actually mapped, so the weather grid always samples the mapped area."""
    if not -60 <= lat <= 72 or not -180 <= lon <= 180:
        raise ValueError("latitude must be between -60 and 72, longitude between -180 and 180")
    near = covering(lat, lon)
    if near and near != key_for(lat, lon):
        touch(near)
        return near
    key = key_for(lat, lon)
    lat, lon = round(lat, GRID), round(lon, GRID)
    country = (country or "").upper()[:2] if (country or "").isalpha() else ""
    place = {"name": name or f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}",
             "country": country, "lat": lat, "lon": lon, "radius_m": ANYWHERE_RADIUS_M,
             "streams": "looked up in anywhere mode", "anywhere": True, "cells": 5}

    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    osm, weather = habitat.DATA / "osm" / f"{key}.json", habitat.DATA / "weather" / f"{key}.csv"
    with lock:
        known = key in ANYWHERE and osm.exists() and weather.exists()
        if known and name:
            ANYWHERE[key]["name"] = name            # a restored place only knew its coordinates
        if known and country:
            ANYWHERE[key]["country"] = country
        need_species = presence.cached(key) is None
        if not known and weather.exists():          # weather from an earlier try: keep its grid
            place["cells"] = 5 if fetch.weather_cells(key) == 5 else 9
        if not known and MAP_STATE.get(key, {}).get("stage") == "loading":
            return key                              # its map is already on its way; don't ask twice
        if not known:
            _claim_new()                            # raises Busy past the server-wide limits
            ANYWHERE[key] = place                   # fetch.py and the model look the place up here
            need_map = not osm.exists()             # a map from an earlier try is kept
            weather_ready = threading.Event()
            if need_map:
                MAP_STATE[key] = {"stage": "loading", "since": time.time()}
            if need_map and not wait_for_map:       # the slow map starts now, alongside the weather
                threading.Thread(target=_load_map, args=(key, place, weather_ready), daemon=True,
                                 name=f"map:{key}").start()
            from concurrent.futures import ThreadPoolExecutor
            try:
                with ThreadPoolExecutor(max_workers=2) as ex:   # weather and mosquito records: a few seconds
                    fw = ex.submit(fetch.fetch_weather, key, dict(place)) if not weather.exists() else None
                    fs = ex.submit(presence.fetch_counts, key, lat, lon) if need_species else None
                    try:
                        if fw:
                            fw.result()
                    except Exception:
                        ANYWHERE.pop(key, None)
                        weather.unlink(missing_ok=True)
                        raise
                    if fs:
                        try:
                            fs.result()
                        except Exception as e:
                            print(f"  {key}: GBIF species counts unavailable ({e}); choosing species from climate",
                                  flush=True)
                        need_species = False
            finally:
                weather_ready.set()
            if not need_map:                        # its map was already here: keep the new weather too
                threading.Thread(target=placestore.save, args=(key,), daemon=True).start()
            if need_map and wait_for_map:
                _load_map(key, place, weather_ready)   # raises if the map cannot be fetched
        if need_species:
            try:                                    # which mosquitoes live here: helpful, never fatal
                presence.fetch_counts(key, lat, lon)
            except Exception as e:
                print(f"  {key}: GBIF species counts unavailable ({e}); choosing species from climate", flush=True)
            else:
                if known:                           # its model may have been built from the climate alone
                    risk.weather_model.cache_clear()
                    risk.city_model.cache_clear()
        touch(key)                                  # opened now: worth keeping current for a few days
        if len(ANYWHERE) > MAX_PLACES:              # keep memory bounded; the disk cache survives
            for stale in list(ANYWHERE)[:-MAX_PLACES]:
                ANYWHERE.pop(stale, None)
                risk.weather_model.cache_clear()
                risk.city_model.cache_clear()
    return key


def map_state(key):
    """Where this place's map has got to: "ready", "loading", or "error" with the reason."""
    if (habitat.DATA / "osm" / f"{key}.json").exists():
        return {"stage": "ready"}
    state = MAP_STATE.get(key, {"stage": "ready" if key in CITIES else "missing"})
    if state.get("stage") == "loading" and (habitat.DATA / "sat" / f"{key}.json").exists():
        state = {**state, "satellite": True}    # the satellite water and settlements can be shown already
    return state


def _load_map(key, place, weather_ready=None):
    """The slow half of a lookup: the map, then the model caches so the water shows up. It runs alongside
    the weather fetch; weather_ready says that one has finished, so the two never write the file at once."""
    try:
        asked = place["radius_m"]
        # the satellite layers come at the same time as the map; they are extra, so never a reason to fail
        sat_job = threading.Thread(target=_fetch_sat, args=(key, dict(place)), daemon=True, name=f"sat:{key}")
        sat_job.start()
        _fetch_osm_adaptively(key, place)
        sat_job.join(60)
        if place["radius_m"] != asked:           # the map shrank: re-sample the weather over what was mapped
            if weather_ready:
                weather_ready.wait(300)
            fetch.fetch_weather(key, place, full=True)
        risk.weather_model.cache_clear()          # before saying "ready": the next request must see the water
        risk.city_model.cache_clear()
        MAP_STATE[key] = {"stage": "ready"}
        if weather_ready:
            weather_ready.wait(120)
        placestore.save(key)                      # so a restart of the server doesn't lose it (Postgres)
    except Exception as e:
        MAP_STATE[key] = {"stage": "error", "error": str(e)}
        print(f"  {key}: map unavailable ({e})", flush=True)
        raise
    finally:
        risk.weather_model.cache_clear()          # rebuild with the features (or without them, on failure)
        risk.city_model.cache_clear()


def _fetch_sat(key, place):
    try:
        import sat
        sat.fetch(key, place)
        risk.city_model.cache_clear()          # if it lands after the map, the next request picks it up
    except Exception as e:
        print(f"  {key}: satellite layers unavailable ({e})", flush=True)


def _fetch_osm_adaptively(key, place):
    """Dense cities can time out the public Overpass servers. Shrink the area rather than give up:
    2 km of mapped water around you beats an error page."""
    last = None
    # a busy server that hasn't answered the full area in a minute rarely does: ask for less, sooner
    for radius, deadline in ((place["radius_m"], 60), (2000, 60), (1000, 90)):
        place["radius_m"] = radius
        try:
            fetch.fetch_osm(key, place, qtimeout=55, use_bbox=True, hedged=True, deadline=deadline)
            return
        except Exception as e:
            last = e
            print(f"  {key}: no luck at {radius} m ({e})")
    raise RuntimeError("the public OpenStreetMap query service could not return this area's water in time "
                       f"(last tried 1 km). It may be busy — try again in a minute. [{last}]")


def restore():
    """Re-register places already cached on disk, so a restart (or a deploy) keeps them working.
    The searched-for name is kept in the OSM cache; older caches fall back to the coordinates."""
    for f in sorted((habitat.DATA / "osm").glob("at_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            lat, lon = d["center"]
        except Exception:
            continue
        ANYWHERE.setdefault(f.stem, {
            "name": d.get("name") or f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}",
            "country": d.get("country", ""), "lat": lat, "lon": lon, "radius_m": d.get("radius_m", ANYWHERE_RADIUS_M),
            "streams": "looked up in anywhere mode", "anywhere": True, "cells": d.get("cells", 9)})
    return sorted(ANYWHERE)


def listing():
    """Every place ready to open, anywhere on earth: each place someone has looked up (listed without
    building its model, so the list stays fast however long it grows) and the OneAquaHealth research
    cities, flagged as such."""
    out = [{"key": key, **p, "research": False} for key, p in ANYWHERE.items()
           if (habitat.DATA / "osm" / f"{key}.json").exists() and (habitat.DATA / "weather" / f"{key}.csv").exists()]
    return out + [summary(key) for key in CITIES]


def summary(key):
    """One place as the app lists it, including which mosquitoes are modelled there and why."""
    place = ANYWHERE.get(key) or CITIES.get(key) or REFERENCE[key]
    m = risk.city_model(key)
    return {"key": key, **place, "research": key in CITIES, "map": map_state(key),
            "today": m["today"], "first_date": m["dates"][0],
            "last_date": m["dates"][-1], "features": len(m["features"]), "neighbourhoods": len(m["homes"]),
            "species": m["species"], "species_evidence": m["evidence"], "climate": m["climate"],
            "model_version": risk.MODEL_VERSION}


placestore.restore_missing()   # places looked up before the last restart of a free host (Postgres)
restore()  # pick up anything cached by an earlier run
