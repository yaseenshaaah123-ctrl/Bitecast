"""Which mosquito, and its biology — one parameter set per species, every number sourced (docs/SCIENCE.md §8).

The model is the same for every species: a temperature-driven development rate turns local weather into
biting adults. What differs between species is the lab-measured curve behind each step, where they breed,
and whether they can survive the local winter. Nothing here names a place.

Sources (all read in the primary table, 22 Sep 2026):
  Loetti et al. 2011, J Nat Hist 45 — Culex pipiens (the v1 model, unchanged)
  Shocket et al. 2020, eLife 9:e58511, Appendix 1 tables 3 and 6 — Culex quinquefasciatus
  Mordecai et al. 2017, PLoS NTD 11:e0005568, S2 Text tables A and B — Aedes aegypti, Aedes albopictus
  Matthews 2025, Parasit Vectors, doi:10.1186/s13071-025-07024-2 — field adult life expectancy by genus
  ECDC species factsheets (Ae. aegypti updated 2 Jan 2023, Ae. albopictus 20 Dec 2016) — winter limits
  Christophers 1960 (via reviews) — Ae. aegypti roughly limited by the 10 °C winter isotherm
"""
from dataclasses import dataclass

import numpy as np

from model import degree_days as dd_


# ---------------------------------------------------------------- thermal response curves
# The published fits, exactly as printed. Brière: q·T·(T−Tmin)·√(Tmax−T). Quadratic: q·(T−Tmin)·(Tmax−T)
# (Mordecai prints q with a minus sign and the factors the other way round; the curve is the same).
# Both are zero outside Tmin..Tmax.

def briere(q, tmin, tmax):
    def f(t):
        t = np.asarray(t, float)
        inside = (t > tmin) & (t < tmax)
        return np.where(inside, q * t * (t - tmin) * np.sqrt(np.clip(tmax - t, 0, None)), 0.0)
    return f


def quadratic(q, tmin, tmax):
    def f(t):
        t = np.asarray(t, float)
        return np.where((t > tmin) & (t < tmax), q * (t - tmin) * (tmax - t), 0.0)
    return f


def linear(m, z):
    """Shocket 2020 fits lab lifespan as L(T) = −m·T + z."""
    def f(t):
        return np.clip(z - m * np.asarray(t, float), 0.0, None)
    return f


GRID = np.linspace(-5, 45, 5001)       # where curves are maximised (0.01 °C steps)
FIELD_REF_T = 25.0    # field life expectancy (Matthews 2025) is taken to hold at 25 °C, a warm-season
#                       mark-recapture day; the lab curve then scales it with temperature. ASSUMPTION.
MIN_LIFE = 0.5        # days: outside the lab curve's range adults die within about a day. ASSUMPTION.
LIFE_CAP = 2.0        # field lifespan never exceeds 2x the genus field mean (lab curves run long in the
#                       cold, when field adults are inactive rather than long-lived). ASSUMPTION.


@dataclass(frozen=True)
class Species:
    key: str
    name: str             # Latin binomial
    common: str           # plain words for the map
    genus: str
    gbif: int             # GBIF backbone taxon key (checked against api.gbif.org/v1/species/match)
    breeds: str           # "water": drains, ditches, ponds (the mapped water) | "containers": buckets, tanks, tyres
    diapause: bool        # overwinters as a dormant adult (Culex pipiens only)
    rate: object          # T -> development rate, 1/day, egg or larva to adult
    rate_ci: tuple        # multiplicative range on the rate constant, from the paper's 95% interval
    immature: object      # T -> probability an immature survives to adulthood (None: not modelled)
    lab_life: object      # T -> lab adult lifespan in days (None: constant field survival)
    field_life: tuple     # (mean, low, high) field life expectancy in days, Matthews 2025
    winter_min: float     # coldest-month mean °C needed to establish (None: any winter)
    annual_min: float     # annual mean °C needed (None: any)
    bites: str            # when it bites, for the advice text
    sources: str

    # ---- derived
    def survival(self, t, life_scale=1.0):
        """Daily adult survival probability at temperature t (field-scaled, see FIELD_REF_T)."""
        t = np.asarray(t, float)
        if self.lab_life is None:     # Culex pipiens v1: constant e^(-1/7)
            return np.full(t.shape, np.exp(-1 / (dd_.ADULT_TAU * life_scale)))
        ref = float(self.lab_life(FIELD_REF_T))
        life = self.field_life[0] * life_scale * self.lab_life(t) / ref
        life = np.clip(life, MIN_LIFE, LIFE_CAP * self.field_life[0] * life_scale)
        return np.exp(-1 / life)

    def production(self, t):
        """Adults produced per day per unit of breeding water: development rate x immature survival."""
        r = self.rate(t)
        return r * self.immature(t) if self.immature is not None else r

    @property
    def t_opt(self):
        """Temperature of fastest development."""
        return float(GRID[int(np.argmax(self.rate(GRID)))])

    @property
    def dev_limits(self):
        """(lowest, highest) temperature at which development proceeds at all."""
        ok = GRID[self.production(GRID) > 0]
        return float(ok.min()), float(ok.max())

    @property
    def adults_max(self):
        """The biting-adult index this species sustains at its best constant temperature: the scale that
        makes 'development = 100%' mean 'as good as it gets for this species'. For Culex pipiens this is
        exactly v1's ADULTS_MAX (21-day kernel), so its scores are unchanged."""
        if self.lab_life is None:
            return dd_.ADULTS_MAX
        p = self.survival(GRID)
        return float(np.max(self.production(GRID) / (1 - p)))


def _pipiens_rate(t):
    return dd_.daily_dd(t) / dd_.K          # Loetti 2011 linear + Brière slowdown, as in v1


SPECIES = {
    "culex_pipiens": Species(
        key="culex_pipiens", name="Culex pipiens", common="northern house mosquito", genus="Culex",
        gbif=1652991, breeds="water", diapause=True,
        rate=_pipiens_rate, rate_ci=(0.9, 1.1),   # v1's k_factor: Loetti's population origin is unverified
        immature=None, lab_life=None, field_life=(7.22, 6.26, 8.33),
        winter_min=None, annual_min=None, bites="at dusk and at night",
        sources="Loetti et al. 2011 (Tb 5.5 C, K 199.5 DD); Field et al. 2022 (diapause)"),
    "culex_quinquefasciatus": Species(
        key="culex_quinquefasciatus", name="Culex quinquefasciatus", common="southern house mosquito",
        genus="Culex", gbif=1652950, breeds="water", diapause=False,
        # Shocket 2020 App.1 table 3: MDR Brière q 4.14e-5 (3.46-5.26e-5), Tmin 0.1, Tmax 38.6
        rate=briere(4.14e-5, 0.1, 38.6), rate_ci=(3.46 / 4.14, 5.26 / 4.14),
        # table 3: larval survival pLA quadratic q 4.26e-3, Tmin 8.9, Tmax 37.7
        immature=quadratic(4.26e-3, 8.9, 37.7),
        # table 6: lifespan linear m 3.80, z 136.3 (Tmax 35.9)
        lab_life=linear(3.80, 136.3), field_life=(7.22, 6.26, 8.33),
        winter_min=None, annual_min=None, bites="at dusk and at night",
        sources="Shocket et al. 2020 eLife, Appendix 1 tables 3 and 6; Matthews 2025 (field lifetime)"),
    "aedes_aegypti": Species(
        key="aedes_aegypti", name="Aedes aegypti", common="yellow fever / dengue mosquito", genus="Aedes",
        gbif=1651891, breeds="containers", diapause=False,
        # Mordecai 2017 S2 Text table B: MDR Brière c 7.86e-5 (5.75-9.93e-5), T0 11.36, Tm 39.17
        rate=briere(7.86e-5, 11.36, 39.17), rate_ci=(5.75 / 7.86, 9.93 / 7.86),
        # table B: pEA quadratic c 5.99e-3, T0 13.56, Tm 38.29
        immature=quadratic(5.99e-3, 13.56, 38.29),
        # table B: lf quadratic c 1.48e-1, T0 9.16, Tm 37.73
        lab_life=quadratic(1.48e-1, 9.16, 37.73), field_life=(7.92, 5.57, 11.2),
        winter_min=10.0, annual_min=None, bites="in the daytime, most at dawn and dusk",
        sources="Mordecai et al. 2017 PLoS NTD, S2 Text table B; Matthews 2025; Christophers 1960 (10 C winter)"),
    "aedes_albopictus": Species(
        key="aedes_albopictus", name="Aedes albopictus", common="Asian tiger mosquito", genus="Aedes",
        gbif=1651430, breeds="containers", diapause=False,   # egg diapause not modelled: cold does the work
        # Mordecai 2017 S2 Text table A: MDR Brière c 6.38e-5 (4.67-8.23e-5), T0 8.60, Tm 39.66
        rate=briere(6.38e-5, 8.60, 39.66), rate_ci=(4.67 / 6.38, 8.23 / 6.38),
        # table A: pEA quadratic c 3.61e-3, T0 9.04, Tm 39.33
        immature=quadratic(3.61e-3, 9.04, 39.33),
        # table A: lf quadratic c 1.43, T0 13.41, Tm 31.51 (lab lifespans; peak ~117 d, hence field scaling)
        lab_life=quadratic(1.43, 13.41, 31.51), field_life=(7.92, 5.57, 11.2),
        winter_min=0.0, annual_min=11.0, bites="in the daytime, mostly outdoors",
        sources="Mordecai et al. 2017 PLoS NTD, S2 Text table A; Matthews 2025; ECDC factsheet (winter > 0 C, "
                "annual > 11 C)"),
}
WATER = [k for k, s in SPECIES.items() if s.breeds == "water"]
CONTAINERS = [k for k, s in SPECIES.items() if s.breeds == "containers"]
DEFAULT = "culex_pipiens"


# ---------------------------------------------------------------- which species live here
NEARBY_KM = 250           # GBIF search radius around the place
RECORDED_MIN = 5          # fewer records nearby than this could be misidentifications or one-off
#                           interceptions: treated as "not recorded". ASSUMPTION
SURVEYED_MIN = 100        # this many mosquito records nearby (the modelled species together) means the area is
                          # surveyed: a species with none among them is most likely absent, not missed. Assumption.
ESTABLISHED_MIN = 50      # this many records show an established population even where the winter test
#                           says no: Ae. aegypti has "well-established populations" in Buenos Aires
#                           (Díaz-Nieto et al. 2013, PLoS NTD, PMC3561174), whose coldest month averages ~9.5 °C,
#                           below the 10 °C isotherm. ASSUMPTION (the number); SUPPORTED (records beat the rule)
# Culex pipiens north of ~39°, Cx. quinquefasciatus south of ~36°, hybrids between (North America; Rutgers
# Center for Vector Biology). Used only when GBIF has no records of either near the place.
QUINQ_BELOW_LAT, PIPIENS_ABOVE_LAT = 36.0, 39.0
QUINQ_WINTER_MIN = 10.0   # in the hybrid band: quinquefasciatus where winters are mild. ASSUMPTION (it
#                           overwinters without diapause, like Ae. aegypti; borrowed from the 10 °C isotherm)


def climate(dates, tmean):
    """Coldest-month and annual mean temperature over the complete years in a daily record."""
    import pandas as pd
    s = pd.Series(np.asarray(tmean, float), index=pd.to_datetime(pd.Series(dates)))
    years = [y for y, n in s.groupby(s.index.year).size().items() if n >= 360]
    s = s[s.index.year.isin(years)] if years else s
    monthly = s.groupby([s.index.year, s.index.month]).mean()
    coldest = float(monthly.groupby(level=0).min().mean())
    return {"coldest_month": round(coldest, 1), "annual": round(float(s.mean()), 1),
            "years": years or sorted(set(s.index.year))}


def establishes(sp, clim):
    """Can this species survive the local winter? (Its summer is the model's job.)"""
    if sp.winter_min is not None and clim["coldest_month"] < sp.winter_min:
        return False
    if sp.annual_min is not None and clim["annual"] < sp.annual_min:
        return False
    return True


def choose(lat, clim, counts):
    """The species to model at a place, from GBIF records nearby (counts: {key: n} or None if unknown)
    and the local climate. Returns a list of dicts, the water breeder first (it drives the drain map).

    status: "recorded"     — at least RECORDED_MIN records within NEARBY_KM and the winter allows it, or
                             ESTABLISHED_MIN records whatever the winter test says ("edge": True)
            "climate"      — (almost) no records nearby, but the winter allows it (unrecorded, or under-sampled)
            "introduced"   — some records nearby, but winters are too cold for it to establish; not modelled
            "absent"       — none of its records among many of other mosquitoes nearby: the area is surveyed
                             and it isn't there (the dengue mosquito around Coimbra); not modelled
    """
    have = counts or {}
    n = lambda k: int(have.get(k) or 0)
    # one Culex for the mapped water: records decide; else the latitude/winter rule
    pip, quinq = n("culex_pipiens"), n("culex_quinquefasciatus")
    if max(pip, quinq) >= RECORDED_MIN:
        culex = "culex_quinquefasciatus" if quinq > pip else "culex_pipiens"
        why = "recorded"
    else:
        a = abs(lat)
        culex = ("culex_quinquefasciatus" if a < QUINQ_BELOW_LAT else "culex_pipiens" if a > PIPIENS_ABOVE_LAT
                 else "culex_quinquefasciatus" if clim["coldest_month"] >= QUINQ_WINTER_MIN else "culex_pipiens")
        why = "climate"
    out = [{"key": culex, "status": why, "records": n(culex), "modelled": True, "edge": False}]
    surveyed = sum(n(k) for k in SPECIES) >= SURVEYED_MIN
    for k in CONTAINERS:
        ok, r = establishes(SPECIES[k], clim), n(k)
        edge = not ok and r >= ESTABLISHED_MIN          # recorded in numbers beyond the textbook winter limit
        status = ("recorded" if (ok and r >= RECORDED_MIN) or edge else "absent" if ok and surveyed and r == 0
                  else "climate" if ok else "introduced" if r >= RECORDED_MIN else None)
        if status:
            out.append({"key": k, "status": status, "records": r, "modelled": status not in ("introduced", "absent"),
                        "edge": edge})
    for o in out:
        s = SPECIES[o["key"]]
        o.update(name=s.name, common=s.common, breeds=s.breeds, bites=s.bites)
    return out


if __name__ == "__main__":
    # the published curves, spot-checked against the tables' own optima
    q = SPECIES["culex_quinquefasciatus"]
    assert abs(q.t_opt - 31.0) < 0.2, q.t_opt                      # Shocket table 3: Topt 31.0
    assert abs(float(GRID[np.argmax(q.immature(GRID))]) - 23.3) < 0.1   # table 3: pLA Topt 23.3
    ae = SPECIES["aedes_aegypti"]
    assert 0.9 < float(ae.immature(25.9)) < 0.93                    # pEA peak ~0.915 at the midpoint
    assert 29 < float(ae.lab_life(23.4)) < 31                       # lab lifespan peak ~30 d
    assert ae.rate(11.0) == 0 and ae.rate(39.5) == 0 and ae.rate(30) > 0
    # field survival: the genus mean at 25 °C, shorter in the heat, never beyond the cap
    for s in SPECIES.values():
        life = -1 / np.log(s.survival(np.array([25.0])))[0]
        assert abs(life - (dd_.ADULT_TAU if s.lab_life is None else s.field_life[0])) < 1e-6, (s.key, life)
        assert s.survival(np.array([36.0]))[0] <= s.survival(np.array([25.0]))[0]
    # Culex pipiens is exactly v1: same rate, same normalisation
    p = SPECIES["culex_pipiens"]
    assert p.rate(20.0) == dd_.daily_dd(20.0) / dd_.K and p.adults_max == dd_.ADULTS_MAX
    # hotter-adapted species develop where Culex pipiens stalls
    assert p.rate(35.0) == 0 and q.rate(35.0) > 0 and ae.rate(35.0) > 0

    # which species where: invented climates and counts, no real place
    tropical = {"coldest_month": 19.0, "annual": 26.0}
    temperate = {"coldest_month": 3.0, "annual": 10.0}
    mild = {"coldest_month": 7.0, "annual": 14.0}
    keys = lambda xs: [(x["key"], x["status"]) for x in xs]
    assert keys(choose(24.9, tropical, {})) == [("culex_quinquefasciatus", "climate"),
                                                ("aedes_aegypti", "climate"), ("aedes_albopictus", "climate")]
    assert keys(choose(60, temperate, {"culex_pipiens": 98})) == [("culex_pipiens", "recorded")]
    assert keys(choose(43.6, mild, {"culex_pipiens": 263, "aedes_albopictus": 6571})) == \
        [("culex_pipiens", "recorded"), ("aedes_albopictus", "recorded")]
    # a handful of dengue-mosquito records where winters are too cold: listed, not modelled
    assert ("aedes_aegypti", "introduced") in keys(choose(50.9, {"coldest_month": 4.0, "annual": 11.5},
                                                         {"culex_pipiens": 4653, "aedes_aegypti": 5}))
    assert choose(-34.6, {"coldest_month": 11.0, "annual": 18.0}, None)[0]["key"] == "culex_quinquefasciatus"
    # plenty of records beat the textbook winter limit (the Buenos Aires case); a handful don't count
    ba = {x["key"]: x for x in choose(-34.6, {"coldest_month": 9.5, "annual": 18.0},
                                      {"aedes_aegypti": 108, "aedes_albopictus": 3})}
    assert ba["aedes_aegypti"]["status"] == "recorded" and ba["aedes_aegypti"]["edge"]
    assert ba["aedes_albopictus"]["status"] == "climate" and ba["culex_quinquefasciatus"]["status"] == "climate"
    assert keys(choose(24.9, tropical, {"culex_quinquefasciatus": 2, "aedes_aegypti": 2}))[:2] == \
        [("culex_quinquefasciatus", "climate"), ("aedes_aegypti", "climate")]
    # a surveyed area with none of a species' records: absent, not "climate" (Coimbra and the dengue mosquito)
    co = {x["key"]: x for x in choose(40.2, {"coldest_month": 10.5, "annual": 16.0},
                                      {"culex_pipiens": 194, "aedes_albopictus": 345})}
    assert co["aedes_aegypti"]["status"] == "absent" and not co["aedes_aegypti"]["modelled"]
    assert co["aedes_albopictus"]["modelled"]
    # ...but where almost nothing is recorded, silence says little and the climate still decides
    assert choose(6.5, tropical, {"culex_quinquefasciatus": 3})[1]["status"] == "climate"
    for s in SPECIES.values():
        lo, hi = s.dev_limits
        print(f"ok  {s.name:24s} develops {lo:5.1f}-{hi:4.1f} C, fastest {s.t_opt:4.1f} C, "
              f"field life at 25 C {-1 / np.log(s.survival(np.array([25.0]))[0]):4.1f} d")
