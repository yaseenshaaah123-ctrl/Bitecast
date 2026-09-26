"""Run every check in the project. Exits non-zero if anything fails — this is what CI runs.

    python check.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
SELF_CHECKS = [
    ["-m", "model.degree_days"], ["-m", "model.stagnation"], ["-m", "model.habitat"],
    ["-m", "model.species"], ["-m", "model.containers"],
    ["-m", "fhir.serialise"], ["feedback.py"], ["treatments.py"], ["validate.py"], ["check_weather.py"],
]


def run_self_checks():
    fails = []
    for args in SELF_CHECKS:
        r = subprocess.run([sys.executable, *args], cwd=HERE, env=ENV, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        name = " ".join(args)
        if r.returncode:
            fails.append(f"{name}: exit {r.returncode}\n{(r.stdout + r.stderr)[-600:]}")
        print(f"  {'ok  ' if not r.returncode else 'FAIL'} {name}")
    return fails


def run_api_smoke():
    """The API across every place, on a throwaway reports store (a temp sqlite file, or a temp Postgres
    schema), with a guard that the real reports were not touched."""
    import sqlite3
    import feedback

    def real_rows():
        if not feedback.DB_PATH.exists():
            return 0
        with sqlite3.connect(feedback.DB_PATH) as conn:
            return conn.execute("SELECT count(*) FROM feedback").fetchone()[0]
    before = real_rows()
    with feedback.scratch():
        fails = _api_smoke()
    if real_rows() != before:
        fails.append(f"the real reports database changed during the check ({before} -> {real_rows()} rows)")
    return fails


def _api_smoke():
    from fastapi.testclient import TestClient
    import api
    import refresh
    refresh.kick = lambda key: None   # a weather refresh landing mid-check would change the answers under it
    from api import app
    from model import risk

    for *_, window, _msg in api.LIMITS:   # the smoke test is one very fast visitor: lift the per-visitor limits
        window.n = 10**9
    c, fails = TestClient(app), []

    def get(path, want=200):
        r = c.get(path)
        if r.status_code != want:
            fails.append(f"GET {path}: {r.status_code} (wanted {want}) {r.text[:200]}")
        return r

    cities = get("/api/cities").json()   # every place ready to open: research cities plus any looked up here
    if sum(1 for p in cities if p.get("research")) != 5:
        fails.append("the five research cities are not all listed")
    for listed in cities:
        city = listed["key"]
        place = get(f"/api/cities/{city}").json()
        modelled = [s["key"] for s in place.get("species", []) if s["modelled"]]
        if not modelled:
            fails.append(f"{city}: no species modelled")
        for sk in modelled:                    # every species the place models, through the whole API
            q = f"species={sk}"
            season = get(f"/api/cities/{city}/season?{q}").json()
            feats = get(f"/api/cities/{city}/features?{q}").json()["features"]
            top = get(f"/api/cities/{city}/hotspots?limit=3&{q}").json()
            if not feats or not top:
                fails.append(f"{city} {sk}: no features or hotspots")
                continue
            fid = top[0]["feature_id"]
            e = get(f"/api/cities/{city}/explain?feature={fid}&{q}").json()
            for key in ("risk", "band", "factors", "text", "range", "actions", "verdict", "species", "model_version"):
                if key not in e:
                    fails.append(f"{city} {sk} explain lacks {key}")
            if e.get("species") != sk:
                fails.append(f"{city}: asked for {sk}, label is for {e.get('species')}")
            rg = e.get("range") or {}
            if not rg.get("low", 1) <= e["risk"] <= rg.get("high", -1):
                fails.append(f"{city} {sk}: range {rg} does not contain the score {e['risk']}")
            if get(f"/api/cities/{city}/explain?feature={fid}&{q}").json().get("range") != rg:
                fails.append(f"{city} {sk}: uncertainty range is not deterministic")
            fhir = get(f"/api/cities/{city}/fhir?feature={fid}&{q}").json()
            codes = [x["resource"]["code"]["coding"][0]["code"] for x in fhir.get("entry", [])
                     if x["resource"]["resourceType"] == "Observation"]
            if codes != [f"{e['species_name'].lower().replace(' ', '-')}-emergence-risk"]:
                fails.append(f"{city} {sk}: FHIR code {codes}")
            # the map's client-side score must match the server's for the same feature and day
            p = next(f["properties"] for f in feats if f["id"] == fid)
            i = season["dates"].index(e["date"])
            series = season["temporal"].get(f"{p.get('cell', 0)}:{p['cls']}")
            client = round(100 * series[i] * p["habitat"] * p["exposure"]) if series else None
            if client is None or abs(client - e["risk"]) > 1:
                fails.append(f"{city} {sk}: map shows {client}, label shows {e['risk']} for {fid}")
    get("/api/cities/oslo/season?species=aedes_aegypti", 400)   # not modelled there: a clear refusal
    get("/api/cities/coimbra")
    for path in ("/api/status", "/api/cities/nowhere/season"):
        get(path, 404 if "nowhere" in path else 200)
    # the season must still agree with real Culex pipiens records wherever there are enough of them
    gbif = get("/api/validation").json().get("gbif")
    if not gbif:
        fails.append("GBIF comparison missing (run: python validate_gbif.py --fetch)")
    else:
        for city, g in gbif["cities"].items():
            if g["enough"] and g["r"] < 0.2:
                fails.append(f"{city}: season no longer matches real records (r={g['r']})")
    local = get("/api/validation").json().get("gbif_local")
    if not local:
        fails.append("local GBIF comparison missing (run: python validate_gbif.py)")
    else:   # every species, against the records near each place, including the reference place Lahore
        for g in local["rows"]:
            if g["enough"] and g.get("r", -1) < 0.2:
                fails.append(f"{g['place']} {g['species']}: season no longer matches local records (r={g.get('r')})")
    get("/api/cities/coimbra/hotspots?date=2026-02-30", 400)

    today = get("/api/cities/coimbra").json()["today"]
    fid = get("/api/cities/coimbra/hotspots?limit=1").json()[0]["feature_id"]
    r = c.post("/api/feedback", json={"city": "coimbra", "feature_id": fid, "date": today, "bad": True, "client": "ci"})
    if r.status_code != 201:
        fails.append(f"POST feedback: {r.status_code} {r.text[:200]}")
    # security: nobody else's browser id is ever served, the page is locked down, and the limits bite
    if any("client" in e for e in get("/api/feedback?city=coimbra").json()):
        fails.append("GET /api/feedback leaks the reporters' browser ids")
    page = c.get("/")
    for h in ("content-security-policy", "x-content-type-options", "x-frame-options"):
        if h not in page.headers:
            fails.append(f"GET /: missing the {h} header")
    w = api.Window(3, 60)
    if [w.allow("x") for _ in range(4)] != [True, True, True, False] or not w.allow("y"):
        fails.append("rate limit window does not stop the 4th hit, or blocks a different visitor")
    print(f"  {'ok  ' if not fails else 'FAIL'} api smoke across {len(cities)} places")
    return fails


if __name__ == "__main__":
    os.chdir(HERE)
    sys.path.insert(0, str(HERE))
    print("self-checks")
    fails = run_self_checks()
    print("api")
    fails += run_api_smoke()
    for f in fails:
        print("\nFAIL:", f)
    print("\nALL CHECKS PASS" if not fails else f"\n{len(fails)} FAILURE(S)")
    sys.exit(1 if fails else 0)
