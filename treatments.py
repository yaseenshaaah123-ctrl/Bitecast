"""Larvicide treatments, logged by whoever manages the water — not by the public.

A treatment (typically Bti) kills the larvae in the water that day. Adults already flying are untouched,
and new eggs laid afterwards develop normally, so the model treats it exactly like a flushing rain: the
larval cohort for that one feature is lost and its development clock restarts. Products with a long
residual effect (e.g. methoprene) are not modelled: every treatment is a single-day reset.

Writing is off unless the server has BITECAST_TREATMENT_TOKEN set, and every write must carry that token:
a treatment changes the forecast everyone sees, so it cannot be open like a bite report.
"""
import hmac
import os
from contextlib import closing
from datetime import datetime, timezone

import feedback   # shares the sqlite file, its write lock and its input checks

TOKEN_ENV = "BITECAST_TREATMENT_TOKEN"
SCHEMA = """CREATE TABLE IF NOT EXISTS treatments (
    id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    date TEXT NOT NULL,
    product TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (city, feature_id, date)
)"""


def _connect():
    conn = feedback._connect()
    feedback.ensure(conn, "treatments", SCHEMA)
    return conn


def enabled():
    return bool(os.environ.get(TOKEN_ENV))


def authorised(token):
    expected = os.environ.get(TOKEN_ENV)
    return bool(expected) and hmac.compare_digest((token or "").encode(), expected.encode())


def add(city, feature_id, date, product=None, note=None):
    feedback._check_city(city)
    if not isinstance(feature_id, str) or not feedback.FEATURE_RE.fullmatch(feature_id):
        raise ValueError(f"feature_id must look like way/123, got {feature_id!r}")
    day = feedback._parse_date(date)
    if (day - datetime.now(timezone.utc).date()).days > 1:
        raise ValueError(f"date {date} is in the future: log a treatment once it has been done")
    product = (product or "").strip()[:60] or None
    note = (note or "").strip()[:280] or None
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with feedback._WRITE, closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO treatments (city, feature_id, date, product, note, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (city, feature_id, date) "
                     "DO UPDATE SET product = excluded.product, note = excluded.note",
                     (city, feature_id, date, product, note, created_at))
    return {"city": city, "feature_id": feature_id, "date": date, "product": product, "note": note}


def entries(city):
    feedback._check_city(city)
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT city, feature_id, date, product, note FROM treatments WHERE city = ? "
                            "ORDER BY date", (city,)).fetchall()
    return [dict(r) for r in rows]


def dates_by_feature(city):
    """{feature_id: [date, ...]} — what the model needs."""
    out = {}
    try:
        for e in entries(city):
            out.setdefault(e["feature_id"], []).append(e["date"])
    except ValueError:
        pass   # an anywhere place not yet registered in this process: no treatments
    return out


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    scratch = feedback.scratch()                   # never the real database
    scratch.__enter__()
    os.environ[TOKEN_ENV] = "s3cret"
    assert enabled() and authorised("s3cret") and not authorised("wrong") and not authorised(None)
    add("coimbra", "way/1", "2025-08-10", "Bti")
    add("coimbra", "way/1", "2025-08-10", "Bti granules")          # same day: updated, not duplicated
    add("coimbra", "way/2", "2025-07-01")
    assert dates_by_feature("coimbra") == {"way/2": ["2025-07-01"], "way/1": ["2025-08-10"]}
    assert entries("coimbra")[1]["product"] == "Bti granules"
    for bad in [dict(city="paris"), dict(feature_id="street/1"), dict(date="2099-01-01"), dict(date="10/08/2025")]:
        try:
            add(**{"city": "coimbra", "feature_id": "way/1", "date": "2025-08-10", **bad})
            raise AssertionError(bad)
        except ValueError:
            pass
    del os.environ[TOKEN_ENV]
    assert not enabled() and not authorised("s3cret")
    scratch.__exit__(None, None, None)
    print("ok: treatments self-check passed on a temporary DB")
