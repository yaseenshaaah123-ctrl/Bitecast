"""The learned season: a gradient-boosted model trained on real mosquito records (learn.py trains it).

The trees are exported to model/learned.npz and walked here with numpy, so the server never imports
scikit-learn. Given a place's weather and the lab-based model's index, it predicts what share of the year's
mosquito activity falls in each calendar month.
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from model import species as sp_

PATH = Path(__file__).parent / "learned.npz"
SPECIES = list(sp_.SPECIES)
FEATURES = ["abs_lat", "tmean", "tmin", "tmax", "precip", "tmean_prev",
            "precip_prev", "tmean_year", "t_range", "mech_share", "mech_prev"] + [f"is_{s}" for s in SPECIES]


def features(w, lat, species, mech):
    """12 x len(FEATURES): one row per calendar month, from the observed days of a place's weather.
    `mech` is the lab-based index (development x stagnation) for the same days.
    No calendar month on purpose: given one, the trees learned when people go out trapping (summer
    surveys), not when mosquitoes are about, and did worse on places they had not seen."""
    d = pd.to_datetime(w["date"])
    seen = (w["kind"] == "observed").to_numpy() if "kind" in w.columns else np.ones(len(w), bool)
    by = lambda a: pd.Series(np.asarray(a, float)[seen]).groupby(d[seen].dt.month.to_numpy()).mean() \
        .reindex(range(1, 13)).interpolate(limit_direction="both").fillna(0).to_numpy()
    tm, tn, tx, pr, me = by(w["tmean"]), by(w["tmin"]), by(w["tmax"]), by(w["precip"]), by(mech)
    me = me / me.sum() if me.sum() else np.full(12, 1 / 12)
    prev = lambda a: np.roll(a, 1)
    cols = [np.full(12, abs(lat)), tm, tn, tx, pr, prev(tm), prev(pr),
            np.full(12, tm.mean()), np.full(12, tm.max() - tm.min()), me, prev(me)]
    cols += [np.full(12, float(species == s)) for s in SPECIES]
    return np.column_stack(cols)


class Trees:
    """A boosted sum of regression trees: baseline + the leaf each tree sends a row to."""

    def __init__(self, z):
        self.baseline = float(z["baseline"])
        self.start, self.feature, self.threshold = z["start"], z["feature"], z["threshold"]
        self.left, self.right, self.value, self.leaf = z["left"], z["right"], z["value"], z["leaf"]

    def predict(self, X):
        X = np.asarray(X, float)
        out = np.full(len(X), self.baseline)
        rows = np.arange(len(X))
        for s in self.start:
            node = np.full(len(X), s)
            while not self.leaf[node].all():
                go = ~self.leaf[node]
                n = node[go]
                left = X[rows[go], self.feature[n]] <= self.threshold[n]
                node[go] = s + np.where(left, self.left[n], self.right[n])
            out += self.value[node]
        return out


def save(est, path):
    """Flatten a fitted HistGradientBoostingRegressor into plain arrays."""
    parts, start, at = [], [], 0
    for (tree,) in est._predictors:
        parts.append(tree.nodes)
        start.append(at)
        at += len(tree.nodes)
    n = np.concatenate(parts)
    np.savez_compressed(path, baseline=np.ravel(est._baseline_prediction)[0], start=np.array(start),
                        feature=n["feature_idx"].astype(np.int32), threshold=n["num_threshold"].astype(float),
                        left=n["left"].astype(np.int32), right=n["right"].astype(np.int32),
                        value=n["value"].astype(float), leaf=n["is_leaf"].astype(bool))


@lru_cache(maxsize=1)
def load(path=PATH):
    with np.load(path) as z:
        return Trees({k: z[k] for k in z.files})


LAB_WEIGHT = 0.5   # the hybrid: an even average of the lab-based and learned seasons (learn.py tests both)


def _share(a):
    a = np.clip(np.asarray(a, float), 0, None)
    return a / a.sum() if a.sum() else np.full(12, 1 / 12)


def months(w, lat, species, mech):
    """Per calendar month, as shares of the year: {"lab", "learned", "hybrid"}, or None without a model."""
    if not PATH.exists():
        return None
    X = features(w, lat, species, mech)
    lab, ml = X[:, FEATURES.index("mech_share")], _share(load().predict(X))
    return {"lab": lab, "learned": ml, "hybrid": LAB_WEIGHT * lab + (1 - LAB_WEIGHT) * ml}
