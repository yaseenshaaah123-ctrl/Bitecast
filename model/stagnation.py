"""Stagnation index: days since the last flushing rain.

Rain above a habitat's flush threshold washes larvae out of breeding sites (Koenraadt & Harrington 2008,
see docs/SCIENCE.md); below it the water sits still and enriches. So risk rises with days since the last
flush, not with rainfall itself. Flush thresholds per habitat live in habitat.CLASSES.
"""
import numpy as np

STAG_TAU = 7.0      # days for flushed water to become good Culex habitat again (63% at 7 d). Assumption.
NO_FLUSH_YET = 30   # days-since-flush assumed before the first day of the weather record


def days_since_flush(precip, flush_mm):
    out = np.empty(len(precip), int)
    d = NO_FLUSH_YET
    for i, p in enumerate(precip):
        d = 0 if p >= flush_mm else d + 1
        out[i] = d
    return out


def stagnation(days):
    """0 on the day of a flush, rising towards 1 as the water stays still."""
    return 1.0 - np.exp(-np.asarray(days, float) / STAG_TAU)


if __name__ == "__main__":
    assert list(days_since_flush([0, 20, 0, 5, 12], 10)) == [31, 0, 1, 2, 0]
    assert stagnation(0) == 0 and 0.6 < stagnation(7) < 0.65
    print("ok")
