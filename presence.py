"""Which mosquitoes live at a place: GBIF records nearby, plus whether the local winter lets each species
establish (model/species.py makes the choice; this file only fetches and caches the evidence).

Counts are cached per place in data/species/<key>.json — committed for the research cities, fetched once
for an anywhere place. Offline, or with GBIF down, the choice falls back to the climate alone, and says so.

    python presence.py              # refresh the research cities' counts
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import requests

from model import species as sp_

DIR = Path(__file__).parent / "data" / "species"
HEADERS = {"User-Agent": "BiteCast hackathon project (IEEE OneAquaHealth Hackathon 2026)"}
GBIF = "https://api.gbif.org/v1/occurrence/search"
FIRST_YEAR = 2000       # recent records only: ranges have shifted (Ae. albopictus reached Europe in 1979)
MAX_AGE_DAYS = 30       # re-check monthly; records accumulate slowly


def path(key):
    return DIR / f"{key}.json"


def fetch_counts(key, lat, lon, timeout=30):
    """GBIF occurrence counts of each modelled species within NEARBY_KM of the point. Raises on failure.
    The species are asked for at the same time: one count each, a few seconds in all."""
    from concurrent.futures import ThreadPoolExecutor

    def count(s):
        r = requests.get(GBIF, headers=HEADERS, timeout=timeout, params={
            "taxonKey": s.gbif, "geoDistance": f"{lat},{lon},{sp_.NEARBY_KM}km",
            "year": f"{FIRST_YEAR},{date.today().year}", "occurrenceStatus": "PRESENT",
            "hasCoordinate": "true", "limit": 0})
        r.raise_for_status()
        return int(r.json()["count"])

    with ThreadPoolExecutor(max_workers=len(sp_.SPECIES)) as ex:
        counts = dict(zip(sp_.SPECIES, ex.map(count, sp_.SPECIES.values())))
    out = {"key": key, "fetched": date.today().isoformat(), "lat": lat, "lon": lon,
           "radius_km": sp_.NEARBY_KM, "years": f"{FIRST_YEAR}-{date.today().year}",
           "source": "GBIF occurrence search, occurrenceStatus=PRESENT, hasCoordinate", "counts": counts}
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = path(key).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    os.replace(tmp, path(key))
    return out


def cached(key):
    """The cached counts for a place, or None."""
    try:
        return json.loads(path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def age_days(key):
    c = cached(key)
    return None if not c else (date.today() - date.fromisoformat(c["fetched"])).days


def species_for(key, lat, clim):
    """(species list, evidence) for a place. Never touches the network: that happens at lookup/refresh."""
    c = cached(key)
    chosen = sp_.choose(lat, clim, c["counts"] if c else None)
    return chosen, ({"gbif_fetched": c["fetched"], "radius_km": c["radius_km"]} if c else
                    {"gbif_fetched": None, "note": "no GBIF records fetched: chosen from the climate alone"})


if __name__ == "__main__":
    from cities import CITIES
    for key in sys.argv[1:] or list(CITIES):
        c = CITIES[key]
        print(key, fetch_counts(key, c["lat"], c["lon"])["counts"])
