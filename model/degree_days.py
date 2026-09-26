"""Culex pipiens development from air temperature — the numbers everything else rests on.

Coefficients: Loetti V., Schweigmann N. & Burroni N. (2011) "Development rates, larval survivorship and
wing length of Culex pipiens (Diptera: Culicidae) at constant temperatures", Journal of Natural History
45(35-36), doi:10.1080/00222933.2011.590946. Female values throughout: females are the ones that bite.
"""
import numpy as np
import pandas as pd

TB = 5.5            # °C  lower development threshold, females, linear model (Loetti 2011)
K = 199.5           # DD  thermal constant, females: first-instar larva -> adult emergence (Loetti 2011).
#                     Excludes the egg stage (~1-3 days in summer), so emergence dates run a few days early.
T0, TL = 9.8, 34.2  # °C  Briére lower / upper development thresholds, females (Loetti 2011)
# Peak of the Briére-1 rate curve r(T) = a·T·(T−T0)·√(TL−T), from dr/dT = 0 (≈ 28.5 °C).
# Only the curve's shape is used below, so the fitted 'a' cancels out.
T_OPT = (4 * TL + 3 * T0 + np.sqrt(16 * TL**2 + 9 * T0**2 - 16 * T0 * TL)) / 10

ADULT_DAYS = 21     # adults counted as biting for 21 days after emergence (literature: 14-21 d). Assumption.
ADULT_TAU = 7.0     # days; daily adult survival e^(-1/7) ≈ 0.87. Assumption, see docs/SCIENCE.md.
SEASON_START_MONTH = 3  # overwintered females resume egg-laying in spring; cold March banks few DD anyway
# Autumn diapause: Field et al. 2022 (doi:10.1038/s42003-022-04276-x, US populations) found induction starts
# near 13.5 h day length and < 20 °C, and exceeds 50% of females by ~12 h and ~15 °C. We end the biting
# season at that majority point. limit: a hard switch; a sliding diapausing share would be smoother.
CRIT_DAYLENGTH = 12.0   # h
DIAPAUSE_T = 15.0       # °C, 7-day mean

KERNEL = np.exp(-np.arange(ADULT_DAYS) / ADULT_TAU)  # fraction of an adult cohort still alive, by age in days


def briere_shape(t):
    """Briére-1 development-rate curve without its fitted constant 'a' (zero outside T0..TL)."""
    t = np.asarray(t, float)
    inside = (t > T0) & (t < TL)
    return np.where(inside, t * (t - T0) * np.sqrt(np.clip(TL - t, 0, None)), 0.0)


def daily_dd(tmean):
    """Degree-days banked per day.

    Linear model (Loetti 2011) up to the Briére optimum; above it the rate falls along the Briére curve from
    its value at the optimum, reaching zero at 34.2 °C — development stalls again in extreme heat.
    Continuous at T_OPT, and T_OPT is the true maximum (scaling the still-rising linear term instead would
    push the peak to ~30 °C and contradict the Briére curve this is meant to follow).
    """
    t = np.asarray(tmean, float)
    dd = np.maximum(0.0, t - TB)
    return np.where(t > T_OPT, (T_OPT - TB) * briere_shape(t) / briere_shape(T_OPT), dd)


def daylength(lat, doy):
    """Hours from sunrise to sunset, sun's upper limb at -0.833° (refraction + solar radius)."""
    lat, decl = np.radians(lat), np.radians(23.44) * np.sin(2 * np.pi * (284 + np.asarray(doy)) / 365)
    cos_w = (np.sin(np.radians(-0.833)) - np.sin(lat) * np.sin(decl)) / (np.cos(lat) * np.cos(decl))
    return 24 / np.pi * np.arccos(np.clip(cos_w, -1, 1))


def breeding_season(dates, tmean, lat):
    """True on days when larvae develop into biting adults. Off from the day autumn diapause is induced
    (first day after midsummer with day length < 12 h and a 7-day mean < 15 °C) until 1 March: later
    cohorts become overwintering females. Adults already out keep biting.

    Day length passes 12 h within a few days of the equinox at every latitude, so in practice the
    photoperiod half of the switch is "after ~21 September" and the latitude difference between cities
    comes through the temperature half: Oslo cools past 15 °C weeks before Coimbra does.

    Anywhere on earth: the winter pause only happens where autumn actually induced diapause — a place
    whose weekly mean never falls below 15 °C breeds all year (v1 switched every place off in January and
    February). South of the equator the calendar runs half a year on: spring starts on 1 September. For
    the first winter of a record, with no autumn before it, a winter cold enough to induce diapause counts.
    """
    d = pd.to_datetime(pd.Series(np.asarray(dates)))      # a fresh 0..n index, aligned with tmean below
    s = d + pd.Timedelta(days=182) if lat < 0 else d     # the season calendar: southern dates shifted
    t7 = pd.Series(np.asarray(tmean, float)).rolling(7, min_periods=1).mean()
    short = daylength(lat, d.dt.dayofyear) < CRIT_DAYLENGTH
    trigger = (s.dt.dayofyear > 172) & short & (t7 < DIAPAUSE_T)
    year = s.dt.year
    induced = trigger.groupby(year).cummax()              # autumn: latches to the end of the season year
    winter = s.dt.month < SEASON_START_MONTH
    autumn = trigger.groupby(year).any()                  # did diapause set in, per season year
    first = year == year.iloc[0]
    cold_winter = bool((winter & first & short & (t7 < DIAPAUSE_T)).any())
    after_autumn = {y: bool(autumn[y - 1]) if y - 1 in autumn.index else cold_winter for y in autumn.index}
    dormant = winter & year.map(after_autumn).astype(bool)
    return (~induced & ~dormant).to_numpy()


def hargreaves_et0(tmax, tmin, tmean, lat, doy):
    """Reference evapotranspiration, mm/day (Hargreaves & Samani 1985, as given in FAO-56 eq. 52), from
    daily max/min/mean temperature and extraterrestrial radiation (FAO-56 eqs. 21-25). Needs no other data,
    so it works for any place the weather record covers."""
    phi = np.radians(lat)
    j = np.asarray(doy, float)
    dr = 1 + 0.033 * np.cos(2 * np.pi * j / 365)
    delta = 0.409 * np.sin(2 * np.pi * j / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1, 1))
    ra = 24 * 60 / np.pi * 0.0820 * dr * (ws * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(ws))
    spread = np.sqrt(np.clip(np.asarray(tmax, float) - np.asarray(tmin, float), 0, None))
    return np.clip(0.0023 * 0.408 * ra * (np.asarray(tmean, float) + 17.8) * spread, 0, None)


DD_MAX = float(daily_dd(np.linspace(0, 40, 4001)).max())  # fastest possible development, DD/day
ADULTS_MAX = DD_MAX / K * KERNEL.sum()                      # adult index sustained at that maximum


def development(dd, reset, k=K, tau=ADULT_TAU):
    """Follow the larval cohort in one habitat through time.

    dd:    daily degree-days (daily_dd output)
    reset: bool per day; True where the developing cohort is lost (flushing rain, or out of season)
    k, tau: thermal constant and adult lifetime; varied only by the uncertainty sampler. The index is always
            normalised by the reference ADULTS_MAX, so faster development or longer-lived adults really do
            raise it rather than cancelling out.

    Returns (banked, adults):
      banked — degree-days accumulated since the last reset; reaching K is when the first adults emerge.
      adults — biting-adult index, 0..1. Once the first generation since the reset has emerged, eggs are laid
               continuously and generations overlap, so adults keep emerging at the development rate
               (dd/K generations per day). Each day's emergence survives with KERNEL for ADULT_DAYS.
               Normalised by ADULTS_MAX, the level sustained at the thermal optimum.
    """
    n = len(dd)
    banked = np.empty(n)
    emerging = np.zeros(n)
    acc = 0.0
    for i in range(n):
        acc = 0.0 if reset[i] else acc + dd[i]
        banked[i] = acc
        if acc >= k:
            emerging[i] = dd[i] / k
    kernel = KERNEL if tau == ADULT_TAU else np.exp(-np.arange(ADULT_DAYS) / tau)
    adults = np.convolve(emerging, kernel)[:n] / ADULTS_MAX
    return banked, np.clip(adults, 0.0, 1.0)


def cohort(rate, production, survival, reset, norm):
    """development() for any species, with adult survival that changes with each day's temperature.

    rate:       development per day (1/days to adulthood); progress reaching 1 is when adults first emerge
    production: adults emerging per day once they do (rate x the share of immatures that survive)
    survival:   each day's adult survival probability
    reset:      bool per day; True where the developing cohort is lost (flush, or dormant season)
    norm:       the species' reference maximum (Species.adults_max), so 1.0 = as good as it gets

    Returns (progress, adults). As in development(), generations overlap after the first, and adults
    already flying outlive a reset. Survival is a daily recurrence rather than a fixed kernel, so a hot
    week shortens the lives of adults already out.
    """
    n = len(rate)
    progress, adults = np.empty(n), np.empty(n)
    acc = a = 0.0
    for i in range(n):
        acc = 0.0 if reset[i] else acc + rate[i]
        progress[i] = acc
        a = a * survival[i] + (production[i] if acc >= 1.0 else 0.0)
        adults[i] = a
    return progress, np.clip(adults / norm, 0.0, 1.0)


if __name__ == "__main__":
    # The checks an ecologist would do by hand.
    assert daily_dd(5.0) == 0 and daily_dd(20.0) == 14.5           # linear below the optimum
    assert daily_dd(30.0) < daily_dd(T_OPT)                          # Briére slowdown past the optimum
    grid = np.linspace(0, 40, 4001)
    assert abs(grid[int(np.argmax(daily_dd(grid)))] - T_OPT) < 0.02   # the curve peaks AT the optimum
    assert daily_dd(34.5) == 0                                       # above the upper threshold: no development
    assert 28.0 < T_OPT < 29.0, T_OPT
    # Constant 25 °C: 19.5 DD/day, so the first generation completes on day ceil(199.5/19.5) = 11.
    banked, adults = development(daily_dd(np.full(60, 25.0)), np.zeros(60, bool))
    first = int(np.argmax(adults > 0))
    assert first == 10, first                                       # 0-based day 10 = 11th day
    assert 0.5 < adults[-1] < 1.0                                    # steady state below the optimum's
    # A flush on day 5 restarts the clock: first adults five days later.
    reset = np.zeros(60, bool); reset[5] = True
    assert int(np.argmax(development(daily_dd(np.full(60, 25.0)), reset)[1] > 0)) == 16
    # Day length: longest at midsummer in the north; ~12 h everywhere within days of the equinox.
    assert daylength(59.9, 172) > daylength(40.2, 172) and abs(daylength(50, 266) - 12) < 0.15
    days = pd.date_range("2025-01-01", "2025-12-31").strftime("%Y-%m-%d")
    warm, cool = breeding_season(days, np.full(365, 25.0), 50), breeding_season(days, np.full(365, 12.0), 50)
    assert warm.all()                                             # never cold enough: breeds all year
    assert not cool[:59].any() and cool[59]                       # cold first winter: dormant to 1 March
    assert not cool[-1] and cool.sum() < warm.sum()               # cool -> induced once days shorten
    # two years: a warm-winter place that turns cold in its second autumn pauses only after that autumn
    two = pd.date_range("2024-01-01", "2025-12-31").strftime("%Y-%m-%d")
    t = np.where(np.arange(len(two)) < 366 + 250, 25.0, 10.0)     # cold from early Sep 2025
    s = breeding_season(two, t, 50)
    assert s[:366].all() and not s[-1]
    # south of the equator the season runs half a year on: summer is December-February
    south = breeding_season(days, 18 + 8 * np.cos(2 * np.pi * (np.arange(365) - 15) / 365), -40)  # Jan 26 °C, Jul 10 °C
    assert south[:30].all() and not south[200]                    # January breeds, July (midwinter) not
    # evaporation: summer > winter, tropics > high latitudes in winter, zero spread = zero ET0
    et_s, et_w = hargreaves_et0(30, 20, 25, 45, 180), hargreaves_et0(10, 2, 6, 45, 355)
    assert 4 < et_s < 7 and et_w < 1.5 and hargreaves_et0(30, 20, 25, 10, 355) > et_w
    assert hargreaves_et0(20, 20, 20, 45, 180) == 0
    # the general cohort model reduces to development() for constant survival and no immature losses
    dd = daily_dd(np.full(60, 25.0))
    _, a1 = development(dd, np.zeros(60, bool))
    _, a2 = cohort(dd / K, dd / K, np.full(60, np.exp(-1 / ADULT_TAU)), np.zeros(60, bool), ADULTS_MAX)
    assert np.allclose(a1[:ADULT_DAYS + 10], a2[:ADULT_DAYS + 10], atol=0.02)   # differ only by the 21-day tail cut
    print(f"ok  T_OPT={T_OPT:.2f} °C  DD_MAX={DD_MAX:.2f} DD/day  steady adults at 25 °C={adults[-1]:.2f}")
