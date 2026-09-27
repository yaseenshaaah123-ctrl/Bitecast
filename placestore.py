"""Places looked up on the live server, kept in Postgres so they survive a restart.

A free host's disk is wiped at every restart and deploy, so a town someone searched would be fetched again,
slowly, by the next person. When a place finishes loading, its cached files (map, weather, satellite layers,
species records) are stored gzip-compressed in the `place_files` table; at startup the server writes back
any it does not have. On by default only on Render (the RENDER variable), so a laptop run never copies the
live server's places into the repository by accident.

    python placestore.py     # self-check, in a throwaway schema
"""
import gzip
import os
from pathlib import Path

import feedback

DATA = Path(__file__).parent / "data"
FILES = {"osm": "osm/{key}.json", "weather": "weather/{key}.csv", "sat": "sat/{key}.json",
         "species": "species/{key}.json"}
SCHEMA = """CREATE TABLE IF NOT EXISTS place_files (
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    content BYTEA NOT NULL,
    updated TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key, name)
)"""


def enabled():
    return bool(feedback.DATABASE_URL) and bool(os.environ.get("RENDER") or os.environ.get("BITECAST_PLACESTORE"))


def _connect():
    import psycopg
    opts = {"options": f"-csearch_path={feedback.PG_SCHEMA}"} if feedback.PG_SCHEMA else {}
    conn = psycopg.connect(feedback._direct() if feedback.PG_SCHEMA else feedback.DATABASE_URL,
                           connect_timeout=15, **opts)
    conn.execute(SCHEMA)
    return conn


def save(key, names=tuple(FILES)):
    """Store a place's cached files (those that exist). Never raises: this is a convenience, not the data."""
    if not enabled() or not key.startswith("at_"):
        return 0
    rows = []
    for name in names:
        path = DATA / FILES[name].format(key=key)
        if path.exists():
            rows.append((key, name, gzip.compress(path.read_bytes())))
    if not rows:
        return 0
    try:
        with _connect() as conn:
            conn.cursor().executemany(
                "INSERT INTO place_files (key, name, content) VALUES (%s, %s, %s) ON CONFLICT (key, name) "
                "DO UPDATE SET content = excluded.content, updated = now()", rows)
        return len(rows)
    except Exception as e:
        print(f"  {key}: could not keep it in the database ({e})", flush=True)
        return 0


def restore_missing():
    """Write back every stored place file this disk lacks. Returns how many places came back."""
    if not enabled():
        return 0
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT key, name, content FROM place_files").fetchall()
    except Exception as e:
        print(f"  places from the database unavailable ({e})", flush=True)
        return 0
    back = set()
    for key, name, content in rows:
        path = DATA / FILES[name].format(key=key)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(gzip.decompress(content))
            os.replace(tmp, path)
            back.add(key)
    if back:
        print(f"  {len(back)} place(s) restored from the database", flush=True)
    return len(back)


if __name__ == "__main__":
    import shutil
    import tempfile
    if not feedback.DATABASE_URL:
        print("skipped: no DATABASE_URL")
    else:
        os.environ["BITECAST_PLACESTORE"] = "1"
        real = DATA
        with feedback.scratch():                          # a throwaway schema, dropped afterwards
            DATA = Path(tempfile.mkdtemp())
            (DATA / "weather").mkdir()
            (DATA / "weather" / "at_1p00_2p00.csv").write_text("date,cell\n2026-01-01,0\n")
            assert save("at_1p00_2p00") == 1 and save("coimbra") == 0     # research cities ship with the code
            (DATA / "weather" / "at_1p00_2p00.csv").unlink()
            assert restore_missing() == 1
            assert (DATA / "weather" / "at_1p00_2p00.csv").read_text().startswith("date,cell")
            assert restore_missing() == 0                                 # nothing missing any more
            shutil.rmtree(DATA)
        DATA = real
        del os.environ["BITECAST_PLACESTORE"]
        print("ok: placestore self-check passed on a temporary Postgres schema")
