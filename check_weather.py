"""Offline self-check of fetch.fetch_weather's call-saving paths: a fake Open-Meteo (and NASA POWER) serve
a cached city's own record back, in a temporary data folder, so nothing real is touched or spent.

- incremental refresh: asks only the last month and reproduces the full record;
- a place left unrefreshed for 90 days is refilled without a gap;
- full=True asks from the start;
- a place looked up anywhere: 5 cells, history from 1 January last year, and when the archive refuses,
  NASA POWER stands in, shifted onto Open-Meteo's recent days."""
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import fetch
from cities import CITIES, grid_points

REAL = Path(__file__).parent / "data"
tmp = Path(tempfile.mkdtemp())
(tmp / "weather").mkdir()
key = "ghent"
shutil.copy(REAL / "weather" / f"{key}.csv", tmp / "weather" / f"{key}.csv")
fetch.DATA = tmp                                     # never the real cache
truth = pd.read_csv(tmp / "weather" / f"{key}.csv", parse_dates=["date"])
today = date.today()
asked, refuse_archive = [], False


def served(i, a, b):
    d = truth[(truth["cell"] == i % 9) & (truth["date"] >= pd.Timestamp(a)) & (truth["date"] <= pd.Timestamp(b))]
    return d


def fake(method, urls, params=None, **kw):
    url = urls[0]
    host = url.split("/")[2]
    if "power" in host:
        a, b = (date(int(params[k][:4]), int(params[k][4:6]), int(params[k][6:])) for k in ("start", "end"))
        asked.append((host, a, b, 1))
        d = served(0, a, b - timedelta(days=3))       # POWER lags ~3 days, and runs 2 C cold here
        par = {"T2M": d["tmean"] - 2, "T2M_MAX": d["tmax"] - 2, "T2M_MIN": d["tmin"] - 2, "PRECTOTCORR": d["precip"]}
        return {"properties": {"parameter": {k: dict(zip(d["date"].dt.strftime("%Y%m%d"), v)) for k, v in par.items()}}}
    n = len(params["latitude"].split(","))
    if "archive" in host:
        if refuse_archive:
            raise RuntimeError("3 attempts failed (fake 429)")
        a, b = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
        b = min(b, today - timedelta(days=5))         # the archive lags a few days, as the real one does
    else:
        a, b = today - timedelta(days=fetch.PAST_DAYS), today + timedelta(days=fetch.FORECAST_DAYS - 1)
    asked.append((host, a, b, n))
    return [{"daily": {"time": served(i, a, b)["date"].dt.strftime("%Y-%m-%d").tolist(),
                       **{api: served(i, a, b)[col].tolist() for api, col in fetch.DAILY.items()}}}
            for i in range(n)]


def archive_call():
    return next(x for x in asked if "archive" in x[0])


fetch.request_json = fake
fetch.fetch_weather(key, CITIES[key])
after = pd.read_csv(tmp / "weather" / f"{key}.csv", parse_dates=["date"])
assert archive_call()[1] >= today - timedelta(days=fetch.REFETCH_DAYS), f"not incremental: {archive_call()}"
m = truth.merge(after, on=["date", "cell"], suffixes=("_old", "_new"))
for col in ["tmean", "tmax", "tmin", "precip"]:
    assert (m[f"{col}_old"] - m[f"{col}_new"]).abs().max() < 0.051, col
assert after.groupby("cell")["date"].apply(lambda s: s.diff().dropna().dt.days.eq(1).all()).all(), "gap"

asked.clear()                                        # a stale file is refilled without a gap
stale = truth[truth["date"] < pd.Timestamp(today - timedelta(days=90))].assign(kind="observed")
stale.to_csv(tmp / "weather" / f"{key}.csv", index=False)
fetch.fetch_weather(key, CITIES[key])
assert archive_call()[1] == today - timedelta(days=90), archive_call()
assert len(pd.read_csv(tmp / "weather" / f"{key}.csv")) == len(after)

asked.clear()
fetch.fetch_weather(key, CITIES[key], full=True)
assert archive_call()[1] == fetch.START

# a place looked up anywhere: 5 cells, a shorter history, and NASA POWER when Open-Meteo refuses
anywhere = {**CITIES[key], "anywhere": True, "cells": 5}
assert len(grid_points(anywhere)) == 5 and grid_points(anywhere)[0] == grid_points(CITIES[key])[0]
asked.clear()
fetch.fetch_weather("at_test", anywhere)
assert archive_call()[1] == date(today.year - 1, 1, 1) and archive_call()[3] == 5, archive_call()
got = pd.read_csv(tmp / "weather" / "at_test.csv", parse_dates=["date"])
assert got["cell"].nunique() == 5 and got["date"].min() == pd.Timestamp(today.year - 1, 1, 1)

asked.clear()
refuse_archive = True
(tmp / "weather" / "at_test.csv").unlink()
fetch.fetch_weather("at_test", anywhere)
assert any("power" in x[0] for x in asked), asked
got = pd.read_csv(tmp / "weather" / "at_test.csv", parse_dates=["date"])
centre = got[got["cell"] == 0].merge(truth[truth["cell"] == 0], on="date", suffixes=("", "_om"))
old = centre[centre["date"] < pd.Timestamp(today - timedelta(days=30))]
assert abs((old["tmean"] - old["tmean_om"]).mean()) < 0.3, "the POWER shift did not remove its 2 C offset"
assert got.groupby("cell")["date"].apply(lambda s: s.diff().dropna().dt.days.eq(1).all()).all(), "gap"

shutil.rmtree(tmp)
print("ok")
