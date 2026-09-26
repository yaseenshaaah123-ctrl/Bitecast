"""The combined score: risk = 100 × development × stagnation × habitat × exposure, each factor 0..1.

Multiplicative on purpose: the weakest factor caps the score (no water, no mosquitoes; no people, no bites),
and it lets every score name the factor holding it back.
Weather is per city, so development and stagnation vary by habitat class and day; habitat and exposure vary
by feature. Temporal part is computed once per city and cached.

Which mosquito is modelled is decided per place (model/species.py, presence.py). Water breeders (Culex)
are scored on the mapped water, with stagnation = days since a flushing rain. Container breeders (Aedes)
are scored on the mapped neighbourhoods, with stagnation = the share of containers holding water. Culex
pipiens runs exactly the v1 model (Loetti 2011), so the research cities' scores are unchanged.
"""
import zlib
from datetime import date as Date
from functools import lru_cache

import numpy as np
import pandas as pd

import presence
import treatments
from cities import grid_points, place
from model import containers as ct
from model import degree_days as dd_
from model import habitat as hb
from model import species as sp_
from model import stagnation as st

# Bump when a coefficient or formula changes, so a forecast issued today stays comparable to later ones.
# 2.0.0: species chosen per place (Culex pipiens unchanged), year-round breeding where winters are mild,
# southern-hemisphere seasons, container breeders.
# 2.1.0: satellite water and people counts (sat.py), unmapped settlements for container breeders, and a
# species with no records in a well-surveyed area is not modelled on climate alone.
MODEL_VERSION = "2.1.0"

BANDS = [(25, "low"), (50, "moderate"), (75, "high"), (101, "very high")]
LIKELY = 50   # a score at or above this is "bites likely": the line the citizen scoreboard checks

# Restoration means restoring flow: a channel that runs clean and free is a poor Culex habitat.
# Still water (ponds, basins) is not "restored" into a stream, so it has no counterfactual here.
RESTORABLE = ("ditch", "drain", "canal", "stream")

# The assumptions we cannot pin down, each sampled across the range the literature allows (docs/SCIENCE.md).
# The risk range shown with every score is the 10th-90th percentile over these samples.
UNCERTAIN = {   # Culex pipiens, exactly as v1
    "water_warmer_c": (0.0, 4.0),   # water above air; sunlit pools +4-6 °C (Paaijmans 2010). One-sided on purpose
    "k_factor": (0.9, 1.1),         # thermal constant ±10%: the lab population was probably not European
    "adult_survival": (0.83, 0.90), # per day (Jones 2012; Matthews 2025)
    "flush_factor": (0.6, 1.6),     # x the class's flush threshold; only the 25 mm value has field support
    "habitat_shift": (-0.15, 0.15), # the ranking is sourced, the numbers are not
    "stag_factor": (0.5, 1.5),      # x STAG_TAU, which has no source at all
}
# Every other species: the development-rate constant across its paper's 95% interval (Species.rate_ci) and
# field lifetime across Matthews 2025's genus interval (Species.field_life) replace k_factor and
# adult_survival; container breeders add the container-water assumptions (model/containers.py).
UNCERTAIN_GENERAL = {
    "water_warmer_c": (0.0, 4.0),
    "flush_factor": (0.6, 1.6),
    "habitat_shift": (-0.15, 0.15),
    "stag_factor": (0.5, 1.5),
    "capacity_mm": (15.0, 60.0),
    "evap_share": (0.25, 1.0),
    "stored": (0.1, 0.6),
}
SAMPLES = 40
HISTORY_DAYS = 365    # the range for a species without a winter reset replays a year of weather before the day

CONTAINER_LABEL = "containers around homes"
FACTOR_NAMES = {   # plain words on screen; the modelling term in the tooltip
    "water": {"development": ["Mosquito growth", "Development: adults produced at this temperature"],
              "stagnation": ["Still water", "Stagnation: days since rain flushed the larvae out"],
              "habitat": ["Water type", "Habitat: how likely this water is to stand still"],
              "exposure": ["People nearby", "Exposure: homes, parks, playgrounds and schools within 300 m"]},
    "containers": {"development": ["Mosquito growth", "Development: adults produced at this temperature"],
                   "stagnation": ["Water in containers", "Rain fills buckets, tanks and tyres; heat dries them; "
                                  "some water is always stored"],
                   "habitat": ["Containers", "Assumed wherever people are: no map shows buckets, tanks or tyres"],
                   "exposure": ["People nearby", "Homes, parks, playgrounds and schools within 300 m"]},
}


def band(risk):
    return next(name for top, name in BANDS if risk < top)


# ---------------------------------------------------------------- the temporal half

def _temporal(sp, dates, w, lat, doy):
    """Per habitat class, per day: development (biting-adult index) and stagnation, for one weather cell."""
    tm, pr = w["tmean"].to_numpy(), w["precip"].to_numpy()
    n = len(tm)
    if sp.key == "culex_pipiens":                          # v1, line for line
        dd = dd_.daily_dd(tm)
        out = ~dd_.breeding_season(dates, w["tmean"], lat)
        classes = {}
        for cls, (label, _, flush_mm) in hb.CLASSES.items():
            dsf = st.days_since_flush(pr, flush_mm)
            reset = out | (dsf == 0)
            banked, adults = dd_.development(dd, reset)
            classes[cls] = {"label": label, "dsf": dsf, "banked": banked, "progress": banked / dd_.K,
                            "out": out, "reset": reset, "development": adults, "stagnation": st.stagnation(dsf)}
        return {"classes": classes, "drivers": None}
    rate, prod, surv = sp.rate(tm), sp.production(tm), sp.survival(tm)
    out = ~dd_.breeding_season(dates, w["tmean"], lat) if sp.diapause else np.zeros(n, bool)
    drivers = {"rate": rate, "production": prod, "survival": surv, "out": out}
    if sp.breeds == "containers":
        et0 = dd_.hargreaves_et0(w["tmax"].to_numpy(), w["tmin"].to_numpy(), tm, lat, doy)
        progress, adults = dd_.cohort(rate, prod, surv, out, sp.adults_max)
        classes = {"containers": {"label": CONTAINER_LABEL, "dsf": ct.days_since_top_up(pr), "banked": None,
                                  "progress": progress, "out": out, "reset": out, "development": adults,
                                  "stagnation": ct.water(pr, et0), "et0": et0}}
        return {"classes": classes, "drivers": drivers}
    classes = {}
    for cls, (label, _, flush_mm) in hb.CLASSES.items():
        dsf = st.days_since_flush(pr, flush_mm)
        reset = out | (dsf == 0)
        progress, adults = dd_.cohort(rate, prod, surv, reset, sp.adults_max)
        classes[cls] = {"label": label, "dsf": dsf, "banked": None, "progress": progress, "out": out,
                        "reset": reset, "development": adults, "stagnation": st.stagnation(dsf)}
    return {"classes": classes, "drivers": drivers}


@lru_cache(maxsize=16)   # a few recent places in memory; the free server has 512 MB
def weather_model(city):
    """The temporal half, per weather cell and per species modelled here.

    Cell 0 is the place's centre; the others sample its spread. Each feature is scored against its own
    nearest cell, so a hillside ditch is not given the valley's temperature. m["classes"] and
    m["cells"][i]["classes"] are the place's water breeder (for the research cities, Culex pipiens: v1).
    """
    c = place(city)  # raises KeyError for an unknown city
    raw = pd.read_csv(hb.DATA / "weather" / f"{city}.csv")
    if "cell" not in raw.columns:                      # a cache from before the grid existed
        raw = raw.assign(cell=0)
    centre = raw[raw["cell"] == 0].reset_index(drop=True)
    dates = centre["date"].tolist()
    doy = pd.to_datetime(pd.Series(dates)).dt.dayofyear.to_numpy()
    clim = sp_.climate(dates, centre["tmean"])
    chosen, evidence = presence.species_for(city, c["lat"], clim)
    modelled = [s["key"] for s in chosen if s["modelled"]]

    cells = []
    for i in sorted(raw["cell"].unique()):
        w = raw[raw["cell"] == i].reset_index(drop=True)
        per = {k: _temporal(sp_.SPECIES[k], dates, w, c["lat"], doy) for k in modelled}
        cells.append({"weather": w, "dd": dd_.daily_dd(w["tmean"].to_numpy()), "species": per,
                      "classes": per[modelled[0]]["classes"]})

    # "today" = the day after the last finished day, i.e. the day the weather was fetched: its own weather is
    # still partly forecast. Taken from the data, not the clock, so a cached place stays self-consistent.
    observed = centre.index[centre["kind"] == "observed"]
    today = dates[min(observed[-1] + 1, len(dates) - 1)] if len(observed) else dates[-1]
    return {
        "key": city, "lat": c["lat"],
        "weather": centre, "dates": dates, "index": {d: i for i, d in enumerate(dates)}, "doy": doy,
        "dd": cells[0]["dd"], "classes": cells[0]["classes"],   # centre cell, water breeder: the city-wide view
        "cells": cells, "points": grid_points(c)[:len(cells)], "today": today,
        "species": chosen, "modelled": modelled, "water_species": modelled[0], "evidence": evidence,
        "climate": clim,
    }


@lru_cache(maxsize=16)
def city_model(city):
    """Weather model + features: water for the water breeder, neighbourhoods for any container breeder.
    A feature treated with larvicide gets its own development series: its larvae die on the treatment day,
    exactly like a flush, and the clock restarts.

    A place whose map is still downloading has no features yet ("map_ready": False): its season, species
    and forecast work, and the water appears when the map lands."""
    m = weather_model(city)
    ws = sp_.SPECIES[m["water_species"]]
    try:
        features = hb.load_features(city)
    except FileNotFoundError:
        return {**m, "features": [], "by_id": {}, "homes": [], "by_home": {}, "map_ready": False}
    homes = hb.load_places(city) if any(sp_.SPECIES[k].breeds == "containers" for k in m["modelled"]) else []
    treated = treatments.dates_by_feature(city)
    for f in features:
        days = sorted(d for d in treated.get(f["id"], []) if d in m["index"])
        if not days:
            continue
        cell = _cell(m, f)
        v = cell["species"][ws.key]["classes"][f["cls"]]
        mask = np.zeros(len(m["dates"]), bool)
        mask[[m["index"][d] for d in days]] = True
        reset = v["reset"] | mask
        if ws.key == "culex_pipiens":
            banked, adults = dd_.development(cell["dd"], reset)
            progress = banked / dd_.K
        else:
            dr = cell["species"][ws.key]["drivers"]
            banked, (progress, adults) = None, dd_.cohort(dr["rate"], dr["production"], dr["survival"], reset,
                                                          ws.adults_max)
        f["treated"] = days
        f["override"] = {"reset": reset, "banked": banked, "progress": progress, "development": adults,
                         "treated_mask": mask}
    return {**m, "features": features, "by_id": {f["id"]: f for f in features},
            "homes": homes, "by_home": {h["id"]: h for h in homes}, "map_ready": True}


def resolve(m, species):
    """The species to use: the place's water breeder by default, else one the place actually models."""
    if species is None:
        return m["water_species"]
    if species not in m["modelled"]:
        raise ValueError(f"species {species!r} is not modelled here; modelled: {', '.join(m['modelled'])}")
    return species


def _features(m, species):
    return m["features"] if sp_.SPECIES[species].breeds == "water" else m["homes"]


def _lookup(m, feature_id, species):
    """(species, feature) for a feature id: water ids belong to the water breeder, neighbourhood ids to the
    first container breeder, unless a species is given."""
    if species is None:
        if feature_id in m["by_id"]:
            return m["water_species"], m["by_id"][feature_id]
        cont = [k for k in m["modelled"] if sp_.SPECIES[k].breeds == "containers"]
        if cont and feature_id in m["by_home"]:
            return cont[0], m["by_home"][feature_id]
        raise KeyError(feature_id)
    species = resolve(m, species)
    f = (m["by_id"] if sp_.SPECIES[species].breeds == "water" else m["by_home"]).get(feature_id)
    if f is None:
        raise KeyError(feature_id)
    return species, f


def _series(m, f, species):
    """The daily series for one feature: its class in its weather cell, plus its own treatments if any."""
    v = _cell(m, f)["species"][species]["classes"][f["cls"]]
    return {**v, **f["override"]} if f.get("override") and species == m["water_species"] else v


def _day(m, date):
    if date is None:
        return m["index"][m["today"]]
    if date not in m["index"]:
        try:  # fromisoformat also accepts 20260919 and 2026-09-19T00:00, so compare round-tripped
            real = Date.fromisoformat(date).isoformat() == date
        except (TypeError, ValueError):
            real = False
        if not real:
            raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
        raise ValueError(f"date {date} outside the modelled range {m['dates'][0]}..{m['dates'][-1]}")
    return m["index"][date]


def _cell(m, f):
    """The weather cell a feature belongs to (falls back to the centre for older caches)."""
    return m["cells"][f.get("cell", 0)] if f.get("cell", 0) < len(m["cells"]) else m["cells"][0]


def scores(city, date=None, species=None):
    """Risk for every feature on one day, highest first: [(risk, feature), ...]."""
    m = city_model(city)
    sk = resolve(m, species)
    i = _day(m, date)
    ranked = []
    for f in _features(m, sk):
        v = _series(m, f, sk)
        ranked.append((round(100 * v["development"][i] * v["stagnation"][i] * f["habitat"] * f["exposure"]), f))
    return sorted(ranked, key=lambda rf: -rf[0])


def season(city, species=None):
    """Everything the map slider needs in one payload. The client computes
    risk = round(100 × temporal["<cell>:<class>"][day] × feature.habitat × feature.exposure).
    Only the cell/class pairs the city actually has are sent."""
    m = city_model(city)
    sk = resolve(m, species)
    sp = sp_.SPECIES[sk]
    w = m["weather"]
    # 5 decimals: enough that the client's risk matches what explain() computes at full precision
    # (3 dp disagreed by a point on ~0.4% of feature-days).
    r5 = lambda a: [round(float(x), 5) for x in a]
    feats = _features(m, sk)
    used = sorted({(f.get("cell", 0), f["cls"]) for f in feats})
    temporal = {}
    for cell, cls in used:
        v = m["cells"][cell if cell < len(m["cells"]) else 0]["species"][sk]["classes"][cls]
        temporal[f"{cell}:{cls}"] = r5(v["development"] * v["stagnation"])
    for f in feats:                      # a treated feature has its own series: "f:<feature id>"
        if f.get("override") and sk == m["water_species"]:
            v = _series(m, f, sk)
            temporal[f"f:{f['id']}"] = r5(v["development"] * v["stagnation"])
    centre = m["cells"][0]["species"][sk]["classes"]["pond" if sp.breeds == "water" else "containers"]
    classes = ({c: {"label": lbl, "habitat": weight, "flush_mm": flush} for c, (lbl, weight, flush) in hb.CLASSES.items()}
               if sp.breeds == "water" else {"containers": {"label": CONTAINER_LABEL, "habitat": 1.0, "flush_mm": None}})
    return {
        "city": city, **place(city), "today": m["today"], "dates": m["dates"],
        "kind": w["kind"].tolist(), "tmean": w["tmean"].tolist(), "precip": w["precip"].tolist(),
        "points": m["points"], "species": sk, "species_name": sp.name, "species_common": sp.common,
        "breeds": sp.breeds, "species_list": m["species"], "species_evidence": m["evidence"],
        "map_ready": m["map_ready"], "classes": classes, "temporal": temporal,
        "strip": r5(centre["development"] * centre["stagnation"]),   # the season curve: centre, still water
    }


def _emergence(m, v, i):
    """When biting adults first appear after the last reset (flush or season start).

    Returns (day index, None) — in the past if adults are already emerging, else looking ahead through the
    data incl. the forecast — or (None, reason) if it won't happen in the record."""
    r = i
    while r > 0 and not v["reset"][r]:
        r -= 1
    for j in range(r, len(m["dates"])):
        if j > i and v["reset"][j]:
            return None, ("flush", j) if v["dsf"][j] == 0 else "season"
        if v["progress"][j] >= 1.0:
            return j, None
    return None, "horizon"


def risk_range(m, f, i, species=None, samples=SAMPLES):
    """10th-90th percentile risk for one feature on one day, over the uncertain assumptions.

    Deterministic: the sampler is seeded by the feature and day, so a label shows the same range every
    time. Culex pipiens runs only the current year up to day i (development depends only on the past, and
    its season resets every winter); species without a winter reset replay HISTORY_DAYS."""
    species = species or m["water_species"]
    sp = sp_.SPECIES[species]
    cell, cls = _cell(m, f), f["cls"]
    w = cell["weather"]
    rng = np.random.default_rng(zlib.crc32(f"{f['id']}|{m['dates'][i]}".encode()))  # stable across restarts
    if species == "culex_pipiens":                        # v1, line for line
        year0 = m["index"].get(m["dates"][i][:4] + "-01-01", 0)
        tmean = w["tmean"].to_numpy()[year0:i + 1]
        precip = w["precip"].to_numpy()[year0:i + 1]
        out = cell["species"][species]["classes"][cls]["out"][year0:i + 1]
        if f.get("override"):                 # treatments kill larvae whatever the assumptions
            out = out | f["override"]["treated_mask"][year0:i + 1]
        flush_mm = hb.CLASSES[cls][2]
        u = {k: rng.uniform(lo, hi, samples) for k, (lo, hi) in UNCERTAIN.items()}
        risks = []
        for n in range(samples):
            dd = dd_.daily_dd(tmean + u["water_warmer_c"][n])
            dsf = st.days_since_flush(precip, flush_mm * u["flush_factor"][n])
            _, adults = dd_.development(dd, out | (dsf == 0), k=dd_.K * u["k_factor"][n],
                                        tau=-1 / np.log(u["adult_survival"][n]))
            stagnation = 1 - np.exp(-dsf[-1] / (st.STAG_TAU * u["stag_factor"][n]))
            habitat = float(np.clip(f["habitat"] + u["habitat_shift"][n], 0.02, 1.0))
            risks.append(100 * adults[-1] * stagnation * habitat * f["exposure"])
        low, high = np.percentile(risks, [10, 90])
        return round(float(low)), round(float(high))

    s0 = max(0, i - HISTORY_DAYS)
    sl = slice(s0, i + 1)
    tmean, precip = w["tmean"].to_numpy()[sl], w["precip"].to_numpy()[sl]
    out = cell["species"][species]["classes"][cls]["out"][sl]
    if f.get("override") and species == m["water_species"]:
        out = out | f["override"]["treated_mask"][sl]
    u = {k: rng.uniform(lo, hi, samples) for k, (lo, hi) in UNCERTAIN_GENERAL.items()}
    u["rate_factor"] = rng.uniform(*sp.rate_ci, samples)
    u["life_scale"] = rng.uniform(sp.field_life[1], sp.field_life[2], samples) / sp.field_life[0]
    et0 = (dd_.hargreaves_et0(w["tmax"].to_numpy()[sl], w["tmin"].to_numpy()[sl], tmean, m["lat"], m["doy"][sl])
           if sp.breeds == "containers" else None)
    risks = []
    for n in range(samples):
        tw = tmean + u["water_warmer_c"][n]
        rate = sp.rate(tw) * u["rate_factor"][n]
        prod = sp.production(tw) * u["rate_factor"][n]
        surv = sp.survival(tmean, u["life_scale"][n])       # adults live in the air, not the water
        if sp.breeds == "water":
            dsf = st.days_since_flush(precip, hb.CLASSES[cls][2] * u["flush_factor"][n])
            _, adults = dd_.cohort(rate, prod, surv, out | (dsf == 0), sp.adults_max)
            stagnation = 1 - np.exp(-dsf[-1] / (st.STAG_TAU * u["stag_factor"][n]))
        else:
            _, adults = dd_.cohort(rate, prod, surv, out, sp.adults_max)
            stagnation = ct.water(precip, et0, u["capacity_mm"][n], u["evap_share"][n], u["stored"][n])[-1]
        habitat = float(np.clip(f["habitat"] + u["habitat_shift"][n], 0.02, 1.0))
        risks.append(100 * adults[-1] * stagnation * habitat * f["exposure"])
    low, high = np.percentile(risks, [10, 90])
    return round(float(low)), round(float(high))


def explain(city, feature_id, date=None, samples=SAMPLES, species=None):
    """One feature, one day. samples=0 skips the uncertainty range (bulk exports, rankings)."""
    m = city_model(city)
    sk, f = _lookup(m, feature_id, species)
    sp = sp_.SPECIES[sk]
    i = _day(m, date)
    cell = _cell(m, f)
    v = _series(m, f, sk)
    factors = {"development": float(v["development"][i]), "stagnation": float(v["stagnation"][i]),
               "habitat": f["habitat"], "exposure": f["exposure"]}
    risk = round(100 * np.prod(list(factors.values())))
    limiting = min(factors, key=factors.get)
    dsf = int(v["dsf"][i])
    j, why = _emergence(m, v, i)
    day = m["dates"][i]
    facts = {
        "tmean": float(cell["weather"]["tmean"][i]),
        "days_since_flush" if sp.breeds == "water" else "days_since_top_up": dsf,
        "progress": round(min(float(v["progress"][i]), 1.0), 3),
        "emerging_since": m["dates"][j] if j is not None and j <= i else None,   # adults already emerging
        "next_emergence": m["dates"][j] if j is not None and j > i else None,    # first adults still to come
        "days_to_emergence": j - i if j is not None and j > i else None,
        "nearby": f["nearby"],
    }
    if v["banked"] is not None:          # Culex pipiens: degree-days, as Loetti measured them
        facts["dd_banked"], facts["dd_needed"] = round(min(float(v["banked"][i]), dd_.K), 1), dd_.K
    t = m["index"][m["today"]]
    result = {
        "city": city, "feature_id": feature_id, "date": day,
        "kind": "observed" if i < t else "today" if i == t else "forecast",
        "name": f["name"], "cls": f["cls"], "cls_label": f.get("label") or v["label"], "centroid": f["centroid"],
        "source": f.get("source", "openstreetmap"), "note": f.get("note"),
        "place_kind": f.get("kind"),
        "species": sk, "species_name": sp.name, "species_common": sp.common, "breeds": sp.breeds,
        "risk": risk, "band": band(risk), "factors": {k: round(x, 3) for k, x in factors.items()},
        "limiting": limiting, "facts": facts, "factor_names": FACTOR_NAMES[sp.breeds],
        "method": ("Degree-day model, Loetti et al. 2011 (Tb 5.5 C, K 199.5 DD)" if sk == "culex_pipiens"
                   else f"Thermal-response model: {sp.sources}"),
    }
    s = i
    while s > 0 and v["out"][s - 1]:
        s -= 1
    result["text"] = _text(result, m["dates"][s] if v["out"][i] else None, why, m["dates"], sp)
    if samples:
        low, high = risk_range(m, f, i, sk, samples)
        result["range"] = {"low": min(low, risk), "high": max(high, risk), "samples": samples,
                           "note": "10th-90th percentile over the model's uncertain assumptions"}
    past = [d for d in f.get("treated", []) if d <= day] if sk == m["water_species"] else []
    result["facts"]["treated"] = past[-1] if past else None
    if past:
        result["text"] = result["text"].replace(
            " Modelled estimate, not a field measurement.",
            f" Treated with larvicide on {pd.Timestamp(past[-1]):%d %B}: the larvae in the water then were killed "
            "(adults already flying were not). Modelled estimate, not a field measurement.")
    result["restored"] = _restored(cell, f, i, factors, sk) if sp.breeds == "water" else None
    result["actions"] = _actions(result, bool(v["out"][i]), sp)
    result["verdict"] = _verdict(result, sp)
    result["model_version"] = MODEL_VERSION
    return result


def _restored(cell, f, i, factors, species):
    """What this reach would score as a clean, free-flowing stream: the human-health cost of its condition.
    Only for channels (a ditch or drain can be restored to flow); None for still water."""
    if f["cls"] not in RESTORABLE:
        return None
    s = cell["species"][species]["classes"]["stream"]   # same weather cell, same species, stream flushing
    weight = hb.CLASSES["stream"][1]                    # a permanent, flowing stream: no intermittent pooling
    if f["cls"] == "stream" and f["habitat"] <= weight:
        return None                                     # already a flowing stream
    risk = round(100 * s["development"][i] * s["stagnation"][i] * weight * factors["exposure"])
    return {"risk": risk, "band": band(risk), "as": "a clean, free-flowing stream"}


def _verdict(r, sp):
    """One line a person can act on, before any of the numbers."""
    if r["factors"]["development"] == 0:
        nxt = r["facts"]["next_emergence"]
        return (f"Quiet for now — the next batch of adults is due {pd.Timestamp(nxt):%d %B}." if nxt else
                "Quiet here: nothing is growing in this water right now." if sp.breeds == "water" else
                "Quiet here: this mosquito isn't breeding in this weather.")
    when = "after dark" if sp.genus == "Culex" else "in the daytime"
    return {"very high": f"You will probably get bitten here {when}.",
            "high": f"A likely spot for bites {when}.",
            "moderate": "Some biting mosquitoes likely " + ("around dusk." if sp.genus == "Culex" else "during the day."),
            "low": "Few mosquitoes expected " + ("from this water." if sp.breeds == "water" else "here.")}[r["band"]]


def _actions(r, out_of_season, sp):
    """One thing to do, for a resident and for whoever manages the water. Never medical advice."""
    f = r["facts"]
    if out_of_season and r["factors"]["development"] == 0:
        return {"resident": "Nothing to do: the biting season is over here.",
                "city": "No action until spring."}
    if sp.breeds == "containers":
        if r["risk"] >= LIKELY:
            return {"resident": "Once a week, empty, scrub or cover anything that holds water around your home: "
                                "buckets, drums, tanks, tyres, plant saucers, coolers. This mosquito bites "
                                "in the daytime, so use repellent then.",
                    "city": "Run a container clean-up here now, starting with schools, markets and building sites."}
        if r["risk"] >= 25:
            return {"resident": "Tip out standing water around your home once a week.",
                    "city": "Include this area in the next container inspection round."}
        return {"resident": "No special precautions needed.", "city": "No action needed."}
    if r["risk"] >= LIKELY:
        city = (f"Treat with larvicide within {f['days_to_emergence']} days, before the next brood emerges."
                if f["days_to_emergence"] else
                "Larvicide stops the next brood; adults already flying need other measures.")
        return {"resident": "Cover arms and legs at dusk near here, and empty standing water around your "
                            "home: buckets, plant saucers, blocked gutters.",
                "city": city}
    if r["risk"] >= 25:
        return {"resident": "Use repellent if you are out here around dusk.",
                "city": "Check this site on the next inspection round."}
    return {"resident": "No special precautions needed.", "city": "No action needed."}


NEARBY_LABEL = {"residential": "residential area", "park": "park", "playground": "playground", "school": "school"}


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _emerging(r, f, sp):
    """How long adults have been emerging. Where no winter or flush interrupts breeding the run can last
    over a year, so a bare "since 19 September" could be read as this week."""
    since = pd.Timestamp(f["emerging_since"])
    n = (pd.Timestamp(r["date"]) - since).days
    if n == 0:
        return "New adults start emerging today."
    if n <= 30:
        return f"New adults have been emerging for {_plural(n, 'day')}."
    if n <= 330:
        return f"New adults have been emerging since {since:%d %B}."
    return (f"New adults have been emerging without a break since {since:%d %B %Y}: "
            + ("neither a flushing rain nor a winter has interrupted breeding." if sp.breeds == "water" else
               "no winter here has been cold enough to stop them."))


def _text(r, season_off_since, why, dates, sp):
    """Plain English: never a bare number."""
    f, label = r["facts"], r["cls_label"]
    where = r["name"] or (f"This {NEARBY_LABEL.get(r['place_kind'], label)}" if sp.breeds == "containers"
                          else f"This {label}")
    parts = [f"{r['band'].capitalize()} risk ({r['risk']}/100)."]
    dev = r["factors"]["development"]
    lo, _ = sp.dev_limits

    if season_off_since and pd.Timestamp(r["date"]).month >= 7:
        parts.append(f"The season is winding down: since {pd.Timestamp(season_off_since):%d %B}, short days and "
                     f"cool weather mean newly emerging {sp.name} females overwinter instead of biting."
                     + (" Adults already on the wing are dying off." if dev > 0 else ""))
    elif season_off_since:
        parts.append(f"Outside the breeding season: overwintering {sp.name} females are dormant, "
                     "so no larvae are developing.")
    elif sp.breeds == "containers":
        top = f["days_since_top_up"]
        wet = r["factors"]["stagnation"]
        rain = ("Rain today topped up the containers here" if top == 0 else
                f"Rain {_plural(top, 'day')} ago topped up the containers here" if top < 30 else
                "No real rain has topped up the containers here for over a month")
        parts.append(f"{where}: {sp.name} lays its eggs in containers around people — buckets, drums, tanks, "
                     f"tyres, plant saucers — which no map shows. {rain}; with evaporation and the water "
                     f"people store, about {wet:.0%} of them are modelled as holding water.")
        if f["emerging_since"]:
            parts.append(_emerging(r, f, sp))
        elif f["next_emergence"]:
            parts.append(f"First adults expected in {_plural(f['days_to_emergence'], 'day')} ({f['next_emergence']}).")
    else:
        dsf = f["days_since_flush"]
        still = ("was flushed by heavy rain today" if dsf == 0 else
                 f"has been still for {_plural(dsf, 'day')} since rain last flushed it" if dsf < 30 else
                 "has not been flushed by heavy rain for over a month")
        if f["emerging_since"]:
            parts.append(f"{where} {still} — warm and still long enough for {sp.name} larvae to develop "
                         f"into adults. " + _emerging(r, f, sp))
        else:
            if "dd_banked" in f:
                parts.append(f"{where} {still}; larvae have banked {f['dd_banked']:.0f} of the {dd_.K:g} "
                             f"degree-days they need to become adults.")
            else:
                parts.append(f"{where} {still}; larvae are {f['progress']:.0%} of the way to becoming adults.")
            if f["next_emergence"]:
                when = f"in {_plural(f['days_to_emergence'], 'day')} ({f['next_emergence']})"
                # adults from an earlier brood outlive a flush that killed the larvae under them
                parts.append(f"First adults expected {when}." if dev == 0 else
                             f"Adults from an earlier brood are still biting here; the next brood emerges {when}.")
            elif isinstance(why, tuple):
                parts.append(f"Heavy rain on {dates[why[1]]} should flush the larvae out before they emerge.")
    if not season_off_since:
        t = f["tmean"]
        if sp.key == "culex_pipiens":
            if t > dd_.T_OPT:
                parts.append(f"At {t:.0f} °C it is hot enough that larval development is slowing "
                             f"(above ~{dd_.T_OPT:.1f} °C, Briére curve).")
        elif t < lo:
            parts.append(f"At {t:.0f} °C it is too cool for {sp.name} to develop (it needs about {lo:.0f} °C).")
        elif sp.immature is not None and float(sp.immature(t)) < 0.5:
            parts.append(f"At {t:.0f} °C the heat kills most {sp.name} larvae before they become adults.")
        elif t > sp.t_opt:
            parts.append(f"At {t:.0f} °C it is past this species' fastest development (~{sp.t_opt:.0f} °C).")

    if sp.breeds == "containers":
        kind = r["place_kind"]
        parts.append({"school": "Children are here in the daytime, when this mosquito bites.",
                      "playground": "Children play here in the daytime, when this mosquito bites.",
                      "park": "People pass through in the daytime, when this mosquito bites.",
                      }.get(kind, "People live here."))
    else:
        hab, base = r["factors"]["habitat"], hb.CLASSES[r["cls"]][1]
        if hab >= 0.6:
            parts.append(f"Habitat: {label}, the still, enriched water this species breeds in.")
        elif hab > base:
            parts.append(f"Habitat: intermittent {label}; it breaks into still pools when the flow stops.")
        elif r["cls"] == "lake":
            parts.append(f"Habitat: {label}; fish and waves limit breeding to sheltered edges.")
        else:
            parts.append(f"Habitat: {label}; flowing water makes poor breeding habitat.")
    if r.get("note"):                                       # what the satellite saw (sat.py)
        parts.append(r["note"])
    near = [f"about {n:,} people" if k == "people" else _plural(n, NEARBY_LABEL[k])
            for k, n in f["nearby"].items() if n]
    parts.append(f"Within {hb.EXPOSURE_M} m: {', '.join(near)}." if near
                 else f"Almost nobody lives within {hb.EXPOSURE_M} m.")
    worst = r["factors"][r["limiting"]]
    brake = (r["limiting"] if sp.breeds == "water"                   # v1 wording for water breeders
             else FACTOR_NAMES["containers"][r["limiting"]][0].lower())
    parts.append("Nothing much is holding the score back: all four factors are near their maximum."
                 if worst >= 0.85 else f"Biggest brake on the score: {brake} ({worst:.0%}).")
    if r["kind"] == "forecast":
        parts.append("Based on the weather forecast.")
    elif r["kind"] == "today":
        parts.append("Today's weather is still coming in, so this figure may shift.")
    parts.append("Modelled estimate, not a field measurement.")
    return " ".join(parts)
