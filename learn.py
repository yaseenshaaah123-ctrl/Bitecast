"""The learned half: a gradient-boosted model of *when* mosquitoes are active, trained on real records.

The mechanistic model (model/risk.py) says how fast larvae grow at a temperature and when rain flushes them;
it was built from lab studies, not fitted to any place. This model learns the season from the field instead:
GBIF holds dated mosquito records around every place BiteCast has opened. For each place and species, the
share of the year's records falling in each month is the target; the features are that month's weather, the
month before, latitude, the species, and the mechanistic model's own index for the month. So it learns what
the lab-based model misses (overwintering, dry-season breeding in tanks, the lag between larvae and bites)
while starting from what the lab already knows.

Tested honestly: places are held out in blocks of 10° x 10°, so a city is never scored by a model that trained
on its neighbours' records (the records are pooled within 250 km, and neighbours share them).

    python learn.py            # train on the cached records, cross-validate, save the model
    python learn.py --fetch    # refresh the monthly records from GBIF first (a few minutes)

Writes model/learned.npz (the trees, read by model/learned.py with numpy alone: the server needs no
scikit-learn), data/validation/learned.json (the scores) and docs/learned_validation.png.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from model import learned
from model import risk
from model import species as sp_

ROOT = Path(__file__).parent
CACHE = ROOT / "data" / "validation" / "gbif_monthly.json"
REPORT = ROOT / "data" / "validation" / "learned.json"
YEARS = "2000,2025"
MIN_FETCH = 50      # records within 250 km (from data/species): below this, not worth asking for months
MIN_TRAIN = 100     # records in a place-species series: below this, one survey swings the monthly shape
MIN_JUDGE = 300     # as validate_gbif.MIN_RECORDS
BLOCK_DEG = 10      # cross-validation holds out whole 10° x 10° blocks


def places():
    """Every place with both cached weather and cached record counts: {key: species json}."""
    out = {}
    for f in sorted((ROOT / "data" / "species").glob("*.json")):
        if (ROOT / "data" / "weather" / f"{f.stem}.csv").exists():
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return out


def fetch():
    import requests
    todo = [(k, s, p["lat"], p["lon"]) for k, p in places().items() for s, n in p["counts"].items()
            if n >= MIN_FETCH and s in sp_.SPECIES]

    def months(job):
        k, s, lat, lon = job
        r = requests.get("https://api.gbif.org/v1/occurrence/search", timeout=60, params={
            "taxonKey": sp_.SPECIES[s].gbif, "geoDistance": f"{lat},{lon},{sp_.NEARBY_KM}km", "year": YEARS,
            "hasCoordinate": "true", "occurrenceStatus": "PRESENT", "facet": "month", "facetLimit": 12, "limit": 0},
            headers={"User-Agent": "BiteCast/2.2 (+https://github.com/yaseenshaaah123-ctrl/Bitecast)"})
        r.raise_for_status()
        facets = r.json()["facets"]
        counts = {int(x["name"]): x["count"] for x in facets[0]["counts"]} if facets else {}
        return k, s, [counts.get(m, 0) for m in range(1, 13)]

    out = {"fetched": date.today().isoformat(), "radius_km": sp_.NEARBY_KM, "years": YEARS, "places": {}}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for k, s, m in ex.map(months, todo):
            out["places"].setdefault(k, {})[s] = m
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"fetched {len(todo)} series for {len(out['places'])} places")
    return out


def dataset():
    """One row per (place, species, month) with enough records."""
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    meta = places()
    X, y, groups, series = [], [], [], []
    for k, by_species in cache["places"].items():
        if k not in meta:
            continue
        lat, lon = meta[k]["lat"], meta[k]["lon"]
        w = pd.read_csv(ROOT / "data" / "weather" / f"{k}.csv")
        w = w[w["cell"] == 0].reset_index(drop=True) if "cell" in w.columns else w
        for s, months in by_species.items():
            obs = np.array(months, float)
            if obs.sum() < MIN_TRAIN:
                continue
            doy = pd.to_datetime(w["date"]).dt.dayofyear.to_numpy()
            v = risk._temporal(sp_.SPECIES[s], w["date"].tolist(), w, lat, doy)["classes"]
            v = v["pond" if sp_.SPECIES[s].breeds == "water" else "containers"]
            feats = learned.features(w, lat, s, v["development"] * v["stagnation"])
            X.append(feats)
            y.append(obs / obs.sum())
            block = (int(np.floor(lat / BLOCK_DEG)), int(np.floor(lon / BLOCK_DEG)))
            groups += [hash(block)] * 12
            series.append({"place": k, "species": s, "records": int(obs.sum()), "lat": lat, "lon": lon,
                           "mech": feats[:, learned.FEATURES.index("mech_share")]})
    return np.vstack(X), np.concatenate(y), np.array(groups), series


def fit(X, y, w):
    from sklearn.ensemble import HistGradientBoostingRegressor
    # small and heavily regularised: ~1000 rows, and bigger trees did worse on held-out places
    return HistGradientBoostingRegressor(max_iter=100, learning_rate=0.03, max_depth=3, min_samples_leaf=60,
                                         l2_regularization=5.0, random_state=0).fit(X, y, sample_weight=w)


def _share(p):
    p = np.clip(p, 0, None)
    return p / p.sum() if p.sum() else np.full(12, 1 / 12)


def _r(a, b):
    return float(np.corrcoef(a, b)[0, 1]) if np.std(a) and np.std(b) else 0.0


def cross_validate(X, y, groups, series):
    from sklearn.model_selection import GroupKFold
    w = np.repeat([np.log10(s["records"]) for s in series], 12)
    pred = np.zeros_like(y)
    for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
        pred[te] = fit(X[tr], y[tr], w[tr]).predict(X[te])
    rows = []
    for i, s in enumerate(series):
        sl = slice(12 * i, 12 * i + 12)
        obs, ml, mech = y[sl], _share(pred[sl]), _share(s["mech"])
        hy = learned.LAB_WEIGHT * mech + (1 - learned.LAB_WEIGHT) * ml
        rows.append({**{k: s[k] for k in ("place", "species", "records")},
                     "r_mechanistic": round(_r(obs, mech), 2), "r_learned": round(_r(obs, ml), 2),
                     "r_hybrid": round(_r(obs, hy), 2), "hybrid_share": [round(x, 4) for x in hy],
                     "records_share": [round(x, 4) for x in obs], "learned_share": [round(x, 4) for x in ml],
                     "mechanistic_share": [round(x, 4) for x in mech]})
    return rows


def summarise(rows):
    judged = [r for r in rows if r["records"] >= MIN_JUDGE]
    mean = lambda key, rs: round(float(np.mean([r[key] for r in rs])), 2)
    return {"series": len(rows), "judged": len(judged), "min_judge": MIN_JUDGE,
            "mean_r_mechanistic": mean("r_mechanistic", judged), "mean_r_learned": mean("r_learned", judged),
            "mean_r_hybrid": mean("r_hybrid", judged),
            "learned_better": sum(r["r_learned"] > r["r_mechanistic"] for r in judged),
            "by_species": {s: {"n": len(rs), "mechanistic": mean("r_mechanistic", rs), "learned": mean("r_learned", rs),
                               "hybrid": mean("r_hybrid", rs)}
                           for s in sp_.SPECIES for rs in [[r for r in judged if r["species"] == s]] if rs}}


def plot(rows, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    judged = [r for r in rows if r["records"] >= MIN_JUDGE]
    pick = sorted(judged, key=lambda r: -r["records"])[:12]
    fig, axes = plt.subplots(3, 4, figsize=(16, 9.3), dpi=120, sharey=False)
    for ax, r in zip(axes.flat, pick):
        ax.bar(range(12), r["records_share"], color="#c9ccc2", label=f"GBIF records, {r['records']}")
        ax.plot(range(12), r["mechanistic_share"], color="#a90021", lw=1.6, marker="o", ms=2.5,
                label=f"lab-based model, r = {r['r_mechanistic']:+.2f}")
        ax.plot(range(12), r["learned_share"], color="#1d6fb8", lw=1.4, ls="--",
                label=f"learned model, r = {r['r_learned']:+.2f}")
        ax.plot(range(12), r["hybrid_share"], color="#1d1936", lw=2.2,
                label=f"hybrid (average), r = {r['r_hybrid']:+.2f}")
        ax.set_xticks(range(12), list("JFMAMJJASOND"))
        ax.set_title(f"{r['place'].replace('_', ' ').title()}: {sp_.SPECIES[r['species']].name}", fontsize=9)
        ax.legend(fontsize=6.5, loc="upper left", frameon=False)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("Season shape at places the learned model never trained on (blocked cross-validation)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path)


if __name__ == "__main__":
    if "--fetch" in sys.argv or not CACHE.exists():
        fetch()
    X, y, groups, series = dataset()
    rows = cross_validate(X, y, groups, series)
    s = summarise(rows)
    print(f"{s['series']} place-species series, {len(y)} rows; judged (>= {MIN_JUDGE} records): {s['judged']}")
    print(f"mean r, held-out places: lab-based {s['mean_r_mechanistic']}  learned {s['mean_r_learned']}  "
          f"hybrid {s['mean_r_hybrid']}  (learned better on {s['learned_better']} of {s['judged']})")
    for k, v in s["by_species"].items():
        print(f"  {k:24s} n={v['n']:3d}  lab-based {v['mechanistic']:+.2f}  learned {v['learned']:+.2f}  hybrid {v['hybrid']:+.2f}")

    w = np.repeat([np.log10(x["records"]) for x in series], 12)
    est = fit(X, y, w)
    learned.save(est, learned.PATH)
    assert np.allclose(learned.load(learned.PATH).predict(X), est.predict(X), atol=1e-9), "tree export disagrees"
    REPORT.write_text(json.dumps({"trained": date.today().isoformat(), "records": int(sum(x["records"] for x in series)),
                                  "places": len({x["place"] for x in series}), "features": learned.FEATURES,
                                  **s, "rows": rows}, indent=1), encoding="utf-8")
    plot(rows, ROOT / "docs" / "learned_validation.png")
    print(f"saved {learned.PATH.name}, {REPORT.name}, learned_validation.png")
