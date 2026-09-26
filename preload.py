"""Pre-load the places people are most likely to search, so they open instantly instead of waiting on the
public OpenStreetMap servers. Each is found with OSM's Nominatim (one request a second, per its usage
policy) and fetched exactly as a search from the app would be. Already-cached places are skipped.

    python preload.py                  # the list below
    python preload.py --top 60         # the next 60 biggest cities not cached yet (GeoNames, capitals first)
    python preload.py "Kyoto" "Accra"  # just these

Restart the server afterwards so it lists the new places.
"""
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import places
from fetch import HEADERS

WORLD = [
    # Europe
    "London", "Paris", "Berlin", "Madrid", "Barcelona", "Rome", "Lisbon", "Porto", "Amsterdam", "Brussels",
    "Athens", "Vienna", "Istanbul", "Bucharest", "Warsaw", "Stockholm",
    # Americas
    "New York", "Miami", "Houston", "Chicago", "Toronto", "Mexico City", "São Paulo", "Rio de Janeiro",
    "Bogotá", "Lima",
    # Asia
    "Mumbai", "Delhi", "Bengaluru", "Chennai", "Kolkata", "Hyderabad, Telangana", "Singapore", "Bangkok",
    "Manila", "Ho Chi Minh City", "Kuala Lumpur", "Tokyo", "Seoul", "Hong Kong", "Dubai",
    # Africa and Oceania
    "Lagos", "Accra", "Johannesburg", "Kinshasa", "Sydney", "Melbourne",
]


def geocode(q):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "addressdetails": 1, "accept-language": "en", "limit": 1,
         "featureType": "settlement"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as r:
        hits = json.load(r)
    time.sleep(1.1)                          # Nominatim: at most one request a second
    if not hits:
        raise LookupError(f"Nominatim found nothing for {q!r}")
    h = hits[0]
    return float(h["lat"]), float(h["lon"]), h.get("name") or q, h.get("address", {}).get("country_code", "")


GEONAMES = "https://download.geonames.org/export/dump/cities15000.zip"   # every town over 15,000 people, CC BY 4.0


def biggest(n):
    """The next n cities worth having ready: national capitals first, then by population, skipping any
    within 15 km of a place already cached (one per metro). From GeoNames, downloaded once (3 MB) into data/geonames/."""
    import io
    import zipfile
    from cities import ANYWHERE, CITIES
    zpath = Path(__file__).parent / "data" / "geonames" / "cities15000.zip"
    if not zpath.exists():
        zpath.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(urllib.request.Request(GEONAMES, headers=HEADERS), timeout=120) as r:
            zpath.write_bytes(r.read())
    rows = []
    with zipfile.ZipFile(zpath) as z, io.TextIOWrapper(z.open("cities15000.txt"), encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            lat, lon = float(c[4]), float(c[5])
            if -60 <= lat <= 72:                                 # where the model runs
                rows.append((c[7] == "PPLC", int(c[14] or 0), c[1], lat, lon, c[8]))
    rows.sort(key=lambda r: (not r[0], -r[1]))
    have = [(p["lat"], p["lon"]) for p in [*CITIES.values(), *ANYWHERE.values()]]
    near = lambda a, b: abs(a[0] - b[0]) < 0.14 and abs(a[1] - b[1]) < 0.14 / max(0.2, math.cos(math.radians(a[0])))
    out = []
    for capital, pop, name, lat, lon, cc in rows:
        if len(out) == n:
            break
        if not any(near((lat, lon), h) for h in have):
            out.append((name, lat, lon, cc))
            have.append((lat, lon))
    return out


if __name__ == "__main__":
    import fetch
    places.NEW_PER_HOUR = 10**6          # the server-wide limit protects the free services from visitors, not us
    top = "--top" in sys.argv
    if top:                              # ahead-of-time places get the best weather or wait for another day
        fetch.POWER_FALLBACK = False
        todo = biggest(int(sys.argv[sys.argv.index("--top") + 1]))
    else:
        todo = sys.argv[1:] or WORLD
    ok = failed = 0
    for q in todo:
        t = time.time()
        try:
            if top:
                name, lat, lon, cc = q
            else:
                lat, lon, name, cc = geocode(q)
            key = places.ensure(lat, lon, name, cc, wait_for_map=True)
            print(f"{name}: {key} in {time.time() - t:.0f} s", flush=True)
            ok += 1
        except Exception as e:                # one busy server must not stop the rest
            print(f"{q if not top else q[0]}: FAILED after {time.time() - t:.0f} s: {e}", flush=True)
            failed += 1
            if top and "open-meteo" in str(e):
                print("Open-Meteo's daily allowance is used up: run again tomorrow to continue.")
                break
    print(f"done: {ok} ready, {failed} failed (run again to retry the failures)")
