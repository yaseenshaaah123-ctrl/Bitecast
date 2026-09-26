"""Water in containers: where Aedes breeds — buckets, drums, tanks, tyres, plant saucers, blocked gutters.

No map shows them, so their water is modelled from the weather alone: rain fills them, evaporation
empties them, and some water is kept by people whatever the rain does (tanks, drums, coolers). This is
what makes the dengue mosquito follow the monsoon, where drain breeders are flushed by it.
Evaporation is Hargreaves ET0 from the day's own temperatures (degree_days.hargreaves_et0).
All three constants are assumptions; each is sampled across its range for the uncertainty band.
"""
import numpy as np

CAPACITY_MM = 30.0    # rain that fills a typical open container from empty. ASSUMPTION (sampled 15-60)
EVAP_SHARE = 0.5      # a shaded, open container loses about half the reference evaporation. ASSUMPTION (0.25-1)
STORED = 0.3          # share of container habitat kept wet by people regardless of rain. ASSUMPTION (0.1-0.6)
TOP_UP_MM = 5.0       # a day with at least this much rain "tops up" the containers (for the text only)


def water(precip, et0, capacity=CAPACITY_MM, evap=EVAP_SHARE, stored=STORED):
    """Share of container habitat holding water each day, 0..1: stored water plus a rain-fed bucket."""
    level, out = 0.0, np.empty(len(precip))
    for i, (p, e) in enumerate(zip(np.asarray(precip, float), np.asarray(et0, float))):
        level = min(1.0, max(0.0, level + (p - evap * e) / capacity))
        out[i] = level
    return stored + (1 - stored) * out


def days_since_top_up(precip, mm=TOP_UP_MM, before=30):
    out, d = np.empty(len(precip), int), before
    for i, p in enumerate(precip):
        d = 0 if p >= mm else d + 1
        out[i] = d
    return out


if __name__ == "__main__":
    dry = water(np.zeros(30), np.full(30, 6.0))
    assert np.allclose(dry, STORED)                          # no rain: only stored water
    wet = water([60] + [0] * 29, np.full(30, 4.0))
    assert wet[0] == 1.0 and wet[5] < 1.0 and wet[-1] == STORED   # a downpour fills them, then they dry out
    assert list(days_since_top_up([0, 10, 0, 0, 6])) == [31, 0, 1, 2, 0]
    print("ok")
