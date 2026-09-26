"""Citizen feedback: "Was it bad here last night?" - one thumbs up/down per feature and date.

Stored in a hosted Postgres when DATABASE_URL is set (in the environment, or in a local .env file that is
never committed): Neon, Supabase or any other. Reports then survive redeploys, which a free host's disk
does not. Otherwise stdlib sqlite3 in data/feedback.sqlite3, created empty on first use and only ever
filled by real reports. The self-check never touches either: python feedback.py
"""
import os
import re
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cities import CITIES, place

def setting(name):
    """A setting from the environment, or from the local .env file (git-ignored: it holds passwords)."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in (Path(__file__).parent / ".env").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip("'\"") or None
    except OSError:
        pass
    return None


DB_PATH = Path(__file__).parent / "data" / "feedback.sqlite3"
DATABASE_URL = setting("DATABASE_URL")
PG_SCHEMA = None      # a throwaway schema while self-checking against a real Postgres (scratch())
# Assumption: the question is about "last night"; after two weeks bite memories are unreliable.
MAX_AGE_DAYS = 14
MAX_NOTE = 280  # assumption: one short sentence, tweet-sized
FEATURE_RE = re.compile(r"(node|way|relation|sat|pop)/[0-9]+")  # OSM element id, or a satellite feature (sat.py)

SCHEMA = """CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    date TEXT NOT NULL,
    bad INTEGER NOT NULL CHECK (bad IN (0, 1)),
    note TEXT,
    created_at TEXT NOT NULL,
    client TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS one_per_client ON feedback (city, feature_id, date, client)"""

# Limit: one process, one writer. sqlite under a thread pool drops writes with "database is locked";
# a lock is cheaper than a real database until this needs more than one worker.
_WRITE = threading.RLock()   # re-entrant: a write may create its table first (ensure)


class _Pg:
    """Just enough of a sqlite3 connection, over Postgres, for this file and treatments.py: "?" parameters,
    rows by column name, and `with conn:` committing (or rolling back on an error)."""

    def __init__(self):
        import psycopg
        from psycopg.rows import dict_row
        opts = {"options": f"-csearch_path={PG_SCHEMA}"} if PG_SCHEMA else {}
        self.conn = psycopg.connect(_direct() if PG_SCHEMA else DATABASE_URL, row_factory=dict_row,
                                    connect_timeout=15, **opts)

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("?", "%s"), params)

    def executescript(self, script):
        for stmt in script.split(";"):
            if stmt.strip():
                self.conn.execute(stmt.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY"))
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        self.conn.commit() if kind is None else self.conn.rollback()


_ready = set()        # (store, table) whose schema this process has already created


def _direct():
    """The unpooled address: a connection pooler (Neon's "-pooler" host) refuses the search_path option the
    self-checks use to work in a throwaway schema. The app itself always uses the pooled one."""
    return DATABASE_URL.replace("-pooler.", ".")


def _connect():
    if DATABASE_URL:
        conn = _Pg()
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
    ensure(conn, "feedback", SCHEMA)
    return conn


def ensure(conn, table, schema):
    """Create a table once per process (Postgres can trip over concurrent CREATE ... IF NOT EXISTS)."""
    key = (DATABASE_URL and f"pg:{PG_SCHEMA}" or str(DB_PATH), table)
    if key not in _ready:
        with _WRITE:
            conn.executescript(schema)
        _ready.add(key)


@contextmanager
def scratch():
    """Point this module at a throwaway store for a self-check or a test run, whatever is configured:
    a temporary sqlite file, or a temporary schema in the real Postgres, dropped afterwards."""
    global DB_PATH, PG_SCHEMA
    import tempfile
    saved = DB_PATH, PG_SCHEMA
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "check.sqlite3"
        if DATABASE_URL:
            import psycopg
            PG_SCHEMA = f"bitecast_check_{uuid.uuid4().hex[:10]}"
            with psycopg.connect(_direct(), autocommit=True) as c:
                c.execute(f'CREATE SCHEMA "{PG_SCHEMA}"')
        try:
            yield
        finally:
            if DATABASE_URL:
                with psycopg.connect(_direct(), autocommit=True) as c:
                    c.execute(f'DROP SCHEMA "{PG_SCHEMA}" CASCADE')
            DB_PATH, PG_SCHEMA = saved


def _row(r):
    return {**dict(r), "bad": bool(r["bad"])}


def _check_city(city):
    try:
        place(city)  # a research city, or an anywhere-mode place someone looked up
    except KeyError:
        raise ValueError(f"unknown city {city!r}; expected one of {sorted(CITIES)} or an anywhere-mode key")


def _parse_date(s):
    try:
        d = date.fromisoformat(s)
    except (TypeError, ValueError):
        d = None
    if d is None or d.isoformat() != s:  # fromisoformat also accepts 20260919, 2026-W38-6
        raise ValueError(f"date must be YYYY-MM-DD, got {s!r}")
    return d


def add(city: str, feature_id: str, date: str, bad: bool, note: str | None = None,
        client: str = "") -> dict:
    _check_city(city)
    if not isinstance(feature_id, str) or not FEATURE_RE.fullmatch(feature_id):
        raise ValueError(f"feature_id must look like way/123, got {feature_id!r}")
    age = (datetime.now(timezone.utc).date() - _parse_date(date)).days
    if age < -1:  # every city runs ahead of UTC, so a local date can be a day ahead just after midnight
        raise ValueError(f"date {date} is in the future")
    if age > MAX_AGE_DAYS:
        raise ValueError(f"date {date} is more than {MAX_AGE_DAYS} days ago")
    if not isinstance(bad, bool):
        raise ValueError(f"bad must be true or false, got {bad!r}")
    if note is not None:
        if not isinstance(note, str):
            raise ValueError("note must be text")
        note = note.strip() or None
        if note and len(note) > MAX_NOTE:
            raise ValueError(f"note is {len(note)} characters; max {MAX_NOTE}")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _WRITE, closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO feedback (city, feature_id, date, bad, note, created_at, client) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (city, feature_id, date, client) "
            "DO UPDATE SET bad = excluded.bad, note = excluded.note, created_at = excluded.created_at "
            "RETURNING id",
            (city, feature_id, date, int(bad), note, created_at, client))
        return {"id": cur.fetchone()["id"], "city": city, "feature_id": feature_id, "date": date, "bad": bad,
                "note": note, "created_at": created_at}


def entries(city: str, since: str | None = None, limit: int = 5000) -> list[dict]:
    _check_city(city)
    if since is not None:
        _parse_date(since)
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM feedback WHERE city = ?" + (" AND date >= ?" if since else "")
                            + " ORDER BY date DESC, id DESC LIMIT ?",
                            (city, *([since] if since else []), int(limit))).fetchall()
    return [_row(r) for r in reversed(rows)]


def summary(city: str) -> dict[str, dict]:
    _check_city(city)
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT feature_id, SUM(bad) AS bad, SUM(1 - bad) AS fine, MAX(date) AS last "
                            "FROM feedback WHERE city = ? GROUP BY feature_id", (city,)).fetchall()
    return {r["feature_id"]: {"bad": int(r["bad"]), "fine": int(r["fine"]), "last": r["last"]} for r in rows}


if __name__ == "__main__":
    real_db = DB_PATH
    with scratch():                            # never the real reports, wherever they are kept
        today = datetime.now(timezone.utc).date()
        d0, d1, oldest = today.isoformat(), (today - timedelta(days=1)).isoformat(), \
            (today - timedelta(days=MAX_AGE_DAYS)).isoformat()

        r = add("coimbra", "way/1", d1, True, "  lots of bites '); DROP TABLE feedback;--  ")
        assert r["note"] == "lots of bites '); DROP TABLE feedback;--" and r["bad"] is True
        add("coimbra", "way/1", d0, False, "   ")
        add("coimbra", "node/2", oldest, True)
        add("oslo", "relation/3", d0, False)

        es = entries("coimbra")
        assert [e["date"] for e in es] == [oldest, d1, d0] and es[2]["note"] is None
        assert [e["date"] for e in entries("coimbra", limit=2)] == [d1, d0]  # newest kept, oldest dropped
        assert len(entries("coimbra", since=d1)) == 2 and entries("benevento") == []
        assert summary("coimbra")["way/1"] == {"bad": 1, "fine": 1, "last": d0}
        assert summary("coimbra")["node/2"] == {"bad": 1, "fine": 0, "last": oldest}
        assert summary("oslo") == {"relation/3": {"bad": 0, "fine": 1, "last": d0}}

        tomorrow = (today + timedelta(days=1)).isoformat()
        add("coimbra", "way/9", tomorrow, False)  # a city ahead of UTC just after local midnight
        day_after = (today + timedelta(days=2)).isoformat()
        too_old = (today - timedelta(days=MAX_AGE_DAYS + 1)).isoformat()
        bad_calls = [
            dict(city="paris"), dict(feature_id="street/1"), dict(feature_id="way/12a"),
            dict(feature_id="way/1\n"), dict(feature_id="way/١٢"), dict(date=day_after), dict(date=too_old),
            dict(date=d0.replace("-", "")), dict(date="2026-02-30"), dict(bad=1), dict(bad="yes"),
            dict(note="x" * (MAX_NOTE + 1)),
        ]
        for kw in bad_calls:
            args = {"city": "coimbra", "feature_id": "way/1", "date": d0, "bad": True, **kw}
            try:
                add(**args)
                raise AssertionError(f"accepted {kw}")
            except ValueError as err:
                print("rejected", ascii(kw)[:40], "->", ascii(str(err)))
        for fn, arg in [(entries, "paris"), (summary, "paris")]:
            try:
                fn(arg)
                raise AssertionError(fn)
            except ValueError:
                pass
        assert len(entries("coimbra")) == 4  # nothing invalid got in
        # one report per client per feature per date: a second tap replaces the first
        add("coimbra", "way/1", d1, True, client="tok1")
        add("coimbra", "way/1", d1, False, client="tok1")
        assert summary("coimbra")["way/1"] == {"bad": 1, "fine": 2, "last": d0}
    assert DB_PATH == real_db and PG_SCHEMA is None
    print(f"ok: feedback self-check passed on a temporary {'Postgres schema' if DATABASE_URL else 'sqlite DB'}")
