"""Check the model's season against real Culex pipiens records.

GBIF (the Global Biodiversity Information Facility) holds dated, georeferenced records of Culex pipiens
from surveys, surveillance programmes and museum collections. Their month-by-month spread in each
country is an independent picture of when the mosquito is actually around. We compare it with the model's
month-by-month adult index for that country's research city.

What this can and cannot show, stated up front:
- It checks the *shape of the season*, not local numbers. Records are national, the model is one city.
- Records follow survey effort as well as mosquitoes: more trapping in late summer inflates those months.
- Winter records exist (females hibernating in cellars get recorded); the model tracks biting, which is
  zero then. That lowers the agreement, correctly.
- Countries with fewer than MIN_RECORDS records are reported but not judged.

    python validate_gbif.py            # use the cached counts (offline)
    python validate_gbif.py --fetch    # refresh the counts from GBIF first

A second, local check (added with the species model, v2): for every species modelled at a place, GBIF
records within 250 km of it, month by month, against the index the map shows (development x stagnation, in
still water or containers). It covers the research cities and the reference place Lahore, where the
southern house mosquito and both Aedes species have enough records to judge.
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from cities import CITIES, REFERENCE
from model import risk
from model import species as sp_

CACHE = Path(__file__).parent / "data" / "validation" / "gbif_culex_pipiens.json"
LOCAL_CACHE = Path(__file__).parent / "data" / "validation" / "gbif_local.json"
TAXON = 1652991                     # GBIF backbone key for Culex pipiens Linnaeus, 1758
YEARS = "2000,2025"
COUNTRY = {"ghent": "BE", "toulouse": "FR", "benevento": "IT", "coimbra": "PT", "oslo": "NO"}
MIN_RECORDS = 300                   # below this, one survey campaign can swing the monthly shape
MODEL_YEARS = (2024, 2025)          # complete seasons in the weather record


def fetch():
    import requests
    out = {"fetched": date.today().isoformat(), "taxon_key": TAXON, "years": YEARS,
           "query": "occurrence/search, hasCoordinate, occurrenceStatus=PRESENT, facet=month", "countries": {}}
    for cc in sorted(set(COUNTRY.values())):
        r = requests.get("https://api.gbif.org/v1/occurrence/search", timeout=60, params={
            "taxonKey": TAXON, "country": cc, "year": YEARS, "hasCoordinate": "true",
            "occurrenceStatus": "PRESENT", "facet": "month", "facetLimit": 12, "limit": 0})
        r.raise_for_status()
        counts = {int(c["name"]): c["count"] for c in r.json()["facets"][0]["counts"]}
        out["countries"][cc] = [counts.get(m, 0) for m in range(1, 13)]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def model_profile(city):
    """Mean adult index per calendar month over the complete seasons, centre cell, still water."""
    m = risk.weather_model(city)
    d = pd.to_datetime(pd.Series(m["dates"]))
    adults = pd.Series(m["cells"][0]["classes"]["pond"]["development"], index=d)
    adults = adults[d.dt.year.isin(MODEL_YEARS).to_numpy()]
    return adults.groupby(adults.index.month).mean().reindex(range(1, 13), fill_value=0).to_numpy()


def summary():
    """Per city: records by month, model by month (both as shares of their year), and how well they agree."""
    if not CACHE.exists():
        return None
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    out = {"source": "GBIF, Culex pipiens occurrence records " + YEARS.replace(",", "-"),
           "fetched": cache["fetched"], "min_records": MIN_RECORDS, "cities": {}}
    for city, cc in COUNTRY.items():
        obs = np.array(cache["countries"][cc], float)
        mod = model_profile(city)
        o, m = obs / obs.sum(), mod / mod.sum()
        out["cities"][city] = {
            "country": cc, "records": int(obs.sum()), "enough": bool(obs.sum() >= MIN_RECORDS),
            "records_share": [round(x, 4) for x in o], "model_share": [round(x, 4) for x in m],
            "r": round(float(np.corrcoef(o, m)[0, 1]), 2),
            "spearman": round(float(pd.Series(o).rank().corr(pd.Series(m).rank())), 2),
            "records_peak_month": int(np.argmax(o)) + 1, "model_peak_month": int(np.argmax(m)) + 1,
        }
    judged = [c for c in out["cities"].values() if c["enough"]]
    out["mean_r_where_enough"] = round(float(np.mean([c["r"] for c in judged])), 2) if judged else None
    out["mean_peak_lag_months"] = (round(float(np.mean([c["records_peak_month"] - c["model_peak_month"]
                                                        for c in judged])), 1) if judged else None)
    return out


def local_places():
    return {**CITIES, **REFERENCE}


def fetch_local():
    """Monthly GBIF records within NEARBY_KM of each place, for each species modelled there."""
    import requests
    out = {"fetched": date.today().isoformat(), "radius_km": sp_.NEARBY_KM, "years": YEARS,
           "query": "occurrence/search, geoDistance, hasCoordinate, occurrenceStatus=PRESENT, facet=month",
           "places": {}}
    for key, c in local_places().items():
        out["places"][key] = {}
        for sk in risk.weather_model(key)["modelled"]:
            r = requests.get("https://api.gbif.org/v1/occurrence/search", timeout=60, params={
                "taxonKey": sp_.SPECIES[sk].gbif, "geoDistance": f"{c['lat']},{c['lon']},{sp_.NEARBY_KM}km",
                "year": YEARS, "hasCoordinate": "true", "occurrenceStatus": "PRESENT",
                "facet": "month", "facetLimit": 12, "limit": 0})
            r.raise_for_status()
            facets = r.json()["facets"]
            counts = {int(x["name"]): x["count"] for x in facets[0]["counts"]} if facets else {}
            out["places"][key][sk] = [counts.get(m, 0) for m in range(1, 13)]
    LOCAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_CACHE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def local_profile(key, species):
    """What the map shows, per calendar month over the complete seasons: centre cell, still water or
    containers, development x stagnation."""
    m = risk.weather_model(key)
    d = pd.to_datetime(pd.Series(m["dates"]))
    v = m["cells"][0]["species"][species]["classes"]["pond" if sp_.SPECIES[species].breeds == "water" else "containers"]
    s = pd.Series(v["development"] * v["stagnation"], index=d)
    s = s[d.dt.year.isin(MODEL_YEARS).to_numpy()]
    return s.groupby(s.index.month).mean().reindex(range(1, 13), fill_value=0).to_numpy()


def local_summary():
    if not LOCAL_CACHE.exists():
        return None
    cache = json.loads(LOCAL_CACHE.read_text(encoding="utf-8"))
    rows = []
    for key, by_species in cache["places"].items():
        if key not in local_places():
            continue
        for sk, months in by_species.items():
            if sk not in risk.weather_model(key)["modelled"]:
                continue
            obs, mod = np.array(months, float), local_profile(key, sk)
            n = int(obs.sum())
            row = {"place": key, "name": local_places()[key]["name"], "species": sk,
                   "species_name": sp_.SPECIES[sk].name, "records": n, "enough": n >= MIN_RECORDS,
                   "reference": key in REFERENCE}
            if n and mod.sum():
                o, m_ = obs / obs.sum(), mod / mod.sum()
                row.update(r=round(float(np.corrcoef(o, m_)[0, 1]), 2),
                           records_share=[round(x, 4) for x in o], model_share=[round(x, 4) for x in m_],
                           records_peak_month=int(np.argmax(o)) + 1, model_peak_month=int(np.argmax(m_)) + 1)
            rows.append(row)
    judged = [r for r in rows if r["enough"] and "r" in r]
    return {"source": f"GBIF records within {cache['radius_km']} km, {cache['years'].replace(',', '-')}",
            "fetched": cache["fetched"], "min_records": MIN_RECORDS, "rows": rows,
            "mean_r_where_enough": round(float(np.mean([r["r"] for r in judged])), 2) if judged else None}


def plot_local(s, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [r for r in s["rows"] if r["enough"] and "r" in r]
    cols = 4
    fig, axes = plt.subplots((len(rows) + cols - 1) // cols, cols, figsize=(4 * cols, 3.1 * ((len(rows) + cols - 1) // cols)),
                             dpi=130, sharey=True, squeeze=False)
    for ax, r in zip(axes.flat, rows):
        ax.bar(range(12), r["records_share"], color="#c9ccc2", label=f"GBIF records, {r['records']}")
        ax.plot(range(12), r["model_share"], color="#a90021", lw=2, marker="o", ms=3, label="BiteCast index")
        ax.set_xticks(range(12), list("JFMAMJJASOND"))
        ax.set_title(f"{r['name']}: {r['species_name']}\nr = {r['r']:+.2f}", fontsize=9, style="italic")
        ax.legend(fontsize=6.5, loc="upper left", frameon=False)
        ax.grid(axis="y", alpha=0.3)
    for ax in list(axes.flat)[len(rows):]:
        ax.axis("off")
    fig.suptitle(f"Season shape by species: the map's index vs real records within {sp_.NEARBY_KM} km (GBIF)",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(path)


def plot(s, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cities = [c for c in COUNTRY if s["cities"][c]["enough"]]
    fig, axes = plt.subplots(1, len(cities), figsize=(4 * len(cities), 3.4), dpi=150, sharey=True)
    months = list("JFMAMJJASOND")
    for ax, city in zip(np.atleast_1d(axes), cities):
        c = s["cities"][city]
        ax.bar(range(12), c["records_share"], color="#c9ccc2", label=f"GBIF records ({c['country']}, n={c['records']})")
        ax.plot(range(12), c["model_share"], color="#a90021", lw=2, marker="o", ms=3, label="BiteCast adult index")
        ax.set_xticks(range(12), months)
        ax.set_title(f"{city.capitalize()}  r = {c['r']:+.2f}", fontsize=10)
        ax.legend(fontsize=7, loc="upper left", frameon=False)
        ax.grid(axis="y", alpha=0.3)
    np.atleast_1d(axes)[0].set_ylabel("share of the year")
    fig.suptitle("Season shape: modelled adults vs real Culex pipiens records (GBIF)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path)


if __name__ == "__main__":
    if "--fetch" in sys.argv or not CACHE.exists():
        fetch()
    s = summary()
    print(f"{s['source']} (fetched {s['fetched']})\n")
    print(f"{'city':10s} {'country':7s} {'records':>7s} {'r':>6s} {'spearman':>8s}  peak model/records")
    for city, c in s["cities"].items():
        flag = "" if c["enough"] else f"   (under {MIN_RECORDS} records: not judged)"
        print(f"{city:10s} {c['country']:7s} {c['records']:7d} {c['r']:+6.2f} {c['spearman']:+8.2f}  "
              f"{c['model_peak_month']:>2d} / {c['records_peak_month']:>2d}{flag}")
    print(f"\nmean r where there is enough data: {s['mean_r_where_enough']}; "
          f"records peak {s['mean_peak_lag_months']} month(s) after the model on average")
    out = Path(__file__).parent / "docs" / "gbif_validation.png"
    plot(s, out)
    print(f"chart -> {out}")

    if "--fetch" in sys.argv or not LOCAL_CACHE.exists():
        fetch_local()
    ls = local_summary()
    print(f"\nby species, local: {ls['source']} (fetched {ls['fetched']})\n")
    print(f"{'place':10s} {'species':24s} {'records':>7s} {'r':>6s}  peak model/records")
    for r in ls["rows"]:
        tail = (f"{r['r']:+6.2f}  {r['model_peak_month']:>2d} / {r['records_peak_month']:>2d}" if "r" in r else "     -")
        flag = "" if r["enough"] else f"   (under {MIN_RECORDS} records: not judged)"
        print(f"{r['place']:10s} {r['species_name']:24s} {r['records']:7d} {tail}{flag}")
    print(f"\nmean r where there is enough data: {ls['mean_r_where_enough']}")
    out = Path(__file__).parent / "docs" / "gbif_local_validation.png"
    plot_local(ls, out)
    print(f"chart -> {out}")
