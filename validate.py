"""Cross-city sanity check — the plan's go/no-go.

Oslo (60°N) must come out clearly lower and later than Benevento and Coimbra (40-41°N). This is not a
validation against mosquito counts (we have none); it checks the model reproduces the known latitudinal
gradient in Culex pipiens season length and intensity.

    python validate.py           # prints the table, writes docs/cross_city_validation.png, exits 1 on failure
"""
import sys

import numpy as np
import pandas as pd

from cities import CITIES
from model.risk import weather_model

HABITAT = "pond"          # reference habitat for the comparison (still water, flushes only in heavy rain)
YEARS = (2024, 2025)      # complete seasons in the record
LATER_BY_DAYS = 14        # "later": first biting adults at least two weeks after the southern cities
LOWER_RATIO = 0.6         # "lower": season total at most 60% of the southern cities'


def summary():
    out = {}
    for city, c in CITIES.items():
        m = weather_model(city)
        v = m["classes"][HABITAT]
        dates = pd.to_datetime(pd.Series(m["dates"]))
        dev, temporal = v["development"], v["development"] * v["stagnation"]
        years = {}
        for y in YEARS:
            sel = (dates.dt.year == y).to_numpy()
            if not sel.any():
                continue
            d = dev[sel]
            first = int(np.argmax(d > 0)) if (d > 0).any() else None
            in_season = sel & ~v["out"]
            weekly = pd.Series(temporal[sel], index=dates[sel]).resample("W").mean()
            years[y] = {
                "first_adults": dates[sel].iloc[first].date().isoformat() if first is not None else None,
                "first_adults_doy": first + 1 if first is not None else None,
                "peak_14d": round(float(pd.Series(temporal[sel]).rolling(14).mean().max()), 3),
                "season_total": round(float(temporal[sel].sum()), 1),
                "season_dd": round(float(m["dd"][in_season].sum())),
                "weeks": [d.date().isoformat() for d in weekly.index],
                "weekly": [round(float(x), 3) for x in weekly.to_numpy()],
            }
        out[city] = {"name": c["name"], "lat": c["lat"], "years": years}
    return out


def check(s):
    """Returns a list of failures (empty = pass)."""
    fails = []
    for y in YEARS:
        oslo = s["oslo"]["years"].get(y)
        for south in ("benevento", "coimbra"):
            other = s[south]["years"].get(y)
            if not oslo or not other:
                fails.append(f"{y}: missing data for oslo or {south}")
                continue
            if oslo["first_adults_doy"] is None or other["first_adults_doy"] is None or other["season_total"] <= 0:
                fails.append(f"{y}: the model produced no adults at all for oslo or {south}")
                continue
            if oslo["first_adults_doy"] < other["first_adults_doy"] + LATER_BY_DAYS:
                fails.append(f"{y}: Oslo first adults (day {oslo['first_adults_doy']}) not >= {LATER_BY_DAYS} "
                             f"days after {south} (day {other['first_adults_doy']})")
            if oslo["season_total"] > LOWER_RATIO * other["season_total"]:
                fails.append(f"{y}: Oslo season total {oslo['season_total']} not <= {LOWER_RATIO} x "
                             f"{south} {other['season_total']}")
    return fails


def plot(s, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    y = YEARS[-1]
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    for city in sorted(s, key=lambda k: s[k]["lat"]):
        d = s[city]["years"][y]
        ax.plot(pd.to_datetime(d["weeks"]), d["weekly"], lw=2,
                label=f"{s[city]['name']} ({s[city]['lat']:.0f}°N)  first adults {(d['first_adults'] or 'none')[5:]}")
    ax.set_title(f"BiteCast cross-city check, {y}: Culex pipiens activity in still water ({HABITAT})", fontsize=11)
    ax.set_ylabel("development × stagnation (weekly mean)")
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    ax.text(0.99, 0.02, "Modelled estimate from Open-Meteo weather; Loetti et al. 2011 thermal biology",
            transform=ax.transAxes, ha="right", fontsize=7, color="0.4")
    fig.tight_layout()
    fig.savefig(path)


if __name__ == "__main__":
    from pathlib import Path

    s = summary()
    print(f"{'city':12s} {'lat':>5s} {'year':>5s} {'first adults':>13s} {'peak14d':>8s} {'total':>7s} {'DD':>6s}")
    for city in sorted(s, key=lambda k: s[k]["lat"]):
        for y, d in s[city]["years"].items():
            print(f"{city:12s} {s[city]['lat']:5.1f} {y:5d} {str(d['first_adults']):>13s} "
                  f"{d['peak_14d']:8.3f} {d['season_total']:7.1f} {d['season_dd']:6d}")
    out = Path(__file__).parent / "docs" / "cross_city_validation.png"
    out.parent.mkdir(exist_ok=True)
    plot(s, out)
    print(f"chart -> {out}")
    fails = check(s)
    for f in fails:
        print("FAIL", f)
    print("PASS: Oslo is lower and later than Benevento and Coimbra" if not fails else "NO-GO: fix the model")
    sys.exit(1 if fails else 0)
