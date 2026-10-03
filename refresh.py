"""Keep the cached data current while the server runs.

Weather has to be unbroken and recent: the model banks degree-days day by day, and the forecast half of
the slider is only worth showing if it was fetched today. Water geometry barely changes, so it is
refreshed rarely. Everything is written atomically by fetch.py, so a request never reads a half-written
file, and a failed refresh leaves the old data in place.

    python refresh.py        # run one pass by hand and report
"""
import asyncio
import json
import os
import time
from datetime import date, datetime, timezone

import pandas as pd

import fetch
import presence
from cities import ANYWHERE, CITIES, place
from model import habitat, risk

WEATHER_MAX_AGE_H = 6      # the forecast is reissued through the day; the archive gains a day at a time
OSM_MAX_AGE_DAYS = 30      # ditches do not move
CHECK_EVERY_S = 1800       # how often the loop wakes up
STALE_WARN_DAYS = 2        # after this, the app tells the viewer the data is old
USED_WITHIN_DAYS = 3       # a looked-up place is kept current while people are still opening it
BETWEEN_PLACES_S = 2       # spacing between places, so a long list never arrives as a burst
KICK_EVERY_S = 600         # how often opening one place may trigger its own out-of-turn refresh


def weather_age_h(key):
    p = habitat.DATA / "weather" / f"{key}.csv"
    return None if not p.exists() else (time.time() - p.stat().st_mtime) / 3600


def osm_age_days(key):
    p = habitat.DATA / "osm" / f"{key}.json"
    return None if not p.exists() else (time.time() - p.stat().st_mtime) / 86400


def last_observed(key):
    p = habitat.DATA / "weather" / f"{key}.csv"
    if not p.exists():
        return None
    w = pd.read_csv(p, usecols=["date", "kind"])
    obs = w[w["kind"] == "observed"]
    return obs["date"].iloc[-1] if len(obs) else None


def days_behind(key):
    """Days between today and the last finished day in the file; 1 is fully current (yesterday)."""
    observed = last_observed(key)
    return (date.today() - date.fromisoformat(observed)).days if observed else None


def weather_stale(key):
    """By age AND by content: a copy, unzip or git clone resets the file time, so a days-old file can
    look minutes old. The content cannot lie: if it ends before yesterday, it needs fetching."""
    behind = days_behind(key)
    return (weather_age_h(key) or 1e9) > WEATHER_MAX_AGE_H or behind is None or behind > 1


def status():
    """What the app shows about its own freshness."""
    out = {}
    for key in [*CITIES, *ANYWHERE]:
        observed = last_observed(key)
        behind = days_behind(key)
        out[key] = {"last_observed": observed, "days_behind": behind,
                    "weather_age_h": round(weather_age_h(key) or -1, 1),
                    "osm_age_days": round(osm_age_days(key) or -1, 1),
                    "stale": behind is None or behind > STALE_WARN_DAYS}
    return {"cities": out, "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stale": any(c["stale"] for c in out.values())}


def refresh_once(keys=None, force=False):
    """Refresh whatever is past its age limit: the research cities always, and any other place someone has
    opened in the last USED_WITHIN_DAYS. Refreshing every place ever looked up would grow without limit and
    trip the weather service's rate limit (it did: 15 places at startup, HTTP 429). A place that is opened
    again refreshes then. Returns what was done. Never raises."""
    import places as _places

    done, failed = [], []
    todo = keys or [*CITIES, *(k for k in ANYWHERE if _places.used_within(k, USED_WITHIN_DAYS))]
    for n, key in enumerate(todo):
        try:
            c = place(key)
        except KeyError:
            continue
        if n:
            time.sleep(BETWEEN_PLACES_S)     # a polite trickle, not a burst, whatever the list grows to
        try:
            if force or weather_stale(key):
                fetch.fetch_weather(key, c)
                done.append(f"{key}:weather")
                import placestore
                placestore.save(key, ("weather",))   # a place kept in Postgres keeps its fresh weather too
            if force or (osm_age_days(key) or 1e9) > OSM_MAX_AGE_DAYS:
                fetch.fetch_osm(key, c, **({"use_bbox": True} if c.get("anywhere") else {}))
                done.append(f"{key}:osm")
            age = presence.age_days(key)             # which mosquitoes live here: GBIF records accumulate
            if force or age is None or age > presence.MAX_AGE_DAYS:
                presence.fetch_counts(key, c["lat"], c["lon"])
                done.append(f"{key}:species")
        except Exception as e:                      # a public API having a bad hour is not fatal:
            failed.append(f"{key}: {e}")            # the previous cache stays, we try again next round
    for key in dict.fromkeys(d.split(":")[0] for d in done):
        risk.forget(key, rebuild=True)              # only this place; rebuilt now if it was in use
    return {"refreshed": done, "failed": failed}


_kicked, _queue, _qlock, _worker = {}, [], __import__("threading").Lock(), None


def kick(key):
    """Someone just opened this place and its weather is behind: fetch it now, in the background, rather
    than waiting for the next round. At most one kick per place per KICK_EVERY_S, and one refresh at a
    time through a single worker — a page that touches a dozen places must not fire a dozen downloads
    (that is how the weather service starts answering 429)."""
    import threading

    global _worker
    if _kicked.get(key, 0) > time.time() - KICK_EVERY_S or not weather_stale(key):
        return
    _kicked[key] = time.time()
    with _qlock:
        if key not in _queue:
            _queue.append(key)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, daemon=True, name="refresh:kicked")
            _worker.start()


def _drain():
    while True:
        with _qlock:
            if not _queue:
                return
            key = _queue.pop(0)
        result = refresh_once([key])
        if result["refreshed"] or result["failed"]:
            print(f"[refresh] {result}", flush=True)
        time.sleep(BETWEEN_PLACES_S)


async def loop():
    """Background task started with the API. One pass at startup, then every CHECK_EVERY_S."""
    while True:
        result = await asyncio.to_thread(refresh_once)   # fetch.py is blocking; keep the event loop free
        if result["refreshed"] or result["failed"]:
            print(f"[refresh] {result}", flush=True)
        await asyncio.sleep(CHECK_EVERY_S)


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv
    keys = None
    if "--all" in sys.argv:        # every cached place, not just the recently opened: the weekly data job
        import places              # registers the places cached on disk
        keys = [*CITIES, *ANYWHERE]
    print(json.dumps(refresh_once(keys, force=force), indent=2))
    print(json.dumps(status(), indent=2))
