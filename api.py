"""BiteCast HTTP API. Serves the model, FHIR output, citizen feedback, and the frontend in app/.

    uvicorn api:app --reload        # from the bitecast/ directory, then open http://127.0.0.1:8000
"""
import hashlib
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import asyncio
from contextlib import asynccontextmanager

import feedback
import places
import refresh
import treatments
import validate
import validate_gbif
from cities import ANYWHERE, CITIES
from fhir.serialise import bundle
from model import risk

@asynccontextmanager
async def lifespan(app):
    """Keep the cached weather current while the server runs, so an opened app is never days behind."""
    task = asyncio.create_task(refresh.loop())
    yield
    task.cancel()


app = FastAPI(title="BiteCast", lifespan=lifespan,
              description="Street-level mosquito risk nowcast for any town or city, for the species that live "
                          "there. Modelled estimate, not a field measurement.")
app.add_middleware(GZipMiddleware, minimum_size=1000)
# Reads are public; POST is same-origin only (the app is served from here), so a third-party page
# cannot make its visitors file reports.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


# ---------------------------------------------------------------- abuse limits and security headers

class Window:
    """At most n hits per key in any `seconds`. In memory: this is one process (see feedback.py).
    limit: per-process, so several workers would each allow the full amount; Redis if that ever happens."""

    def __init__(self, n, seconds):
        self.n, self.seconds, self.hits = n, seconds, defaultdict(deque)

    def allow(self, key):
        now = time.monotonic()
        if len(self.hits) > 20_000:                       # forget idle visitors, keep memory bounded
            for k in [k for k, q in self.hits.items() if not q or q[-1] < now - self.seconds]:
                del self.hits[k]
        q = self.hits[key]
        while q and q[0] <= now - self.seconds:
            q.popleft()
        if len(q) >= self.n:
            return False
        q.append(now)
        return True


# Per visitor: generous for a person, tight for a script. The expensive part (a brand-new place, which costs
# the free map and weather services real work) is also capped for the whole server in places.ensure.
LIMITS = [  # (method, path prefix, window, message)
    ("POST", "/api/feedback", Window(30, 3600), "That's a lot of reports from one place. Try again later."),
    ("GET", "/api/anywhere", Window(30, 600), "Too many new areas at once. Wait a few minutes and try again."),
    ("", "/api/", Window(300, 60), "Too many requests. Slow down a little."),
]
ALL_REPORTS = Window(2000, 3600)   # every visitor together: a flood can't bury the real reports

# The page only talks to itself, its map tiles and the place search; nothing may frame it.
CSP = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https://tiles.openfreemap.org; "
       "connect-src 'self' https://tiles.openfreemap.org https://photon.komoot.io; "
       "worker-src 'self' blob:; child-src blob:; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")


def visitor(request):
    """Who is asking, for the limits. Behind Render's proxies (Cloudflare in front) the socket is the proxy,
    so the forwarded address is used. It can be faked, which only dodges the per-visitor limits, never the
    server-wide ones."""
    h = request.headers
    ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
    return ip or (request.client.host if request.client else "?")


@app.middleware("http")
async def guard(request: Request, call_next):
    path, method = request.url.path, request.method
    if path.startswith("/api/"):
        who = visitor(request)
        for m, prefix, window, message in LIMITS:
            if (not m or m == method) and path.startswith(prefix) and not window.allow(who):
                return JSONResponse({"detail": message}, status_code=429, headers={"Retry-After": "60"})
        if method == "POST" and path == "/api/feedback" and not ALL_REPORTS.allow("all"):
            return JSONResponse({"detail": "Reports are paused for a few minutes. Try again later."},
                                status_code=429, headers={"Retry-After": "300"})
    response = await call_next(request)
    headers = response.headers
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    headers["X-Frame-Options"] = "DENY"
    headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=(), payment=()"
    if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
        headers["Strict-Transport-Security"] = "max-age=31536000"
    if path == "/" or path.endswith(".html"):
        # The page must always be revalidated: it carries the ?v= cache-busters for app.js and style.css,
        # so a browser holding a stale copy would keep running old code however often the assets change.
        headers["Cache-Control"] = "no-cache"
        headers["Content-Security-Policy"] = CSP
    return response


def _city(city):
    if city not in CITIES and city not in ANYWHERE:
        raise HTTPException(404, f"unknown city '{city}'; try one of {sorted(CITIES)}, or /api/anywhere")
    places.touch(city)   # keep the places people actually open current (refresh.py)
    refresh.kick(city)   # and if this one is behind, fetch it now rather than at the next round
    return city


def _call(fn, *args):
    """Model errors -> HTTP errors."""
    try:
        return fn(*args)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/cities")
def cities():
    """Every place ready to open, anywhere on earth: those already looked up, and the OneAquaHealth
    research cities ("research": true). Open any other point with /api/anywhere."""
    return places.listing()


@app.get("/api/anywhere")
def anywhere(lat: float = Query(..., ge=-60, le=72), lon: float = Query(..., ge=-180, le=180),
             name: Optional[str] = Query(None, max_length=120),
             country: Optional[str] = Query(None, pattern=r"^[A-Za-z]{2}$", description="ISO country code")):
    """Model any point on earth. First call for a new area fetches its map, weather and mosquito records
    (usually well under a minute; the public map servers decide); after that it is cached. Returns the
    same shape as /api/cities/{city}."""
    try:   # the place opens on its weather and species; its map follows (poll /api/cities/{key})
        key = places.ensure(lat, lon, name, country, wait_for_map=False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except places.Busy as e:
        raise HTTPException(429, str(e), headers={"Retry-After": "300"})
    except Exception as e:  # Overpass and Open-Meteo are public services having a bad minute
        raise HTTPException(503, f"couldn't fetch map or weather data for this area: {e}")
    return places.summary(key)


@app.get("/api/cities/{city}")
def city(city: str):
    """One place by key — a research city or an anywhere place already looked up (for shared links)."""
    return _call(places.summary, _city(city))


SPECIES_Q = Query(None, max_length=40, description="a species modelled at this place, e.g. aedes_aegypti; "
                                                  "default: the place's water breeder (see /api/cities)")


@app.get("/api/cities/{city}/season")
def season(city: str, species: Optional[str] = SPECIES_Q):
    """Daily development/stagnation per habitat class for the whole record (incl. 16-day forecast)."""
    return _call(risk.season, _city(city), species)


@app.get("/api/cities/{city}/features")
def features(city: str, species: Optional[str] = SPECIES_Q):
    """GeoJSON with static habitat and exposure weights: water features for a water breeder, the mapped
    neighbourhoods (homes, schools, parks, playgrounds) for a container breeder."""
    m = risk.city_model(_city(city))
    sk = _call(risk.resolve, m, species)
    return {"type": "FeatureCollection", "species": sk, "map": places.map_state(city), "features": [
        {"type": "Feature", "id": f["id"], "geometry": f["geometry"],
         "properties": {k: f.get(k) for k in ("id", "name", "cls", "kind", "cell", "habitat", "exposure",
                                              "nearby", "centroid", "label", "source")}}
        for f in risk._features(m, sk)]}


@app.get("/api/cities/{city}/hotspots")
def hotspots(city: str, date: Optional[str] = None, limit: int = Query(10, ge=1, le=100),
             species: Optional[str] = SPECIES_Q):
    ranked = _call(risk.scores, _city(city), date, species)[:limit]
    return [_call(risk.explain, city, f["id"], date, 0, species) for _, f in ranked]


@app.get("/api/cities/{city}/explain")
def explain(city: str, feature: str = Query(..., max_length=40), date: Optional[str] = Query(None, max_length=10),
            species: Optional[str] = SPECIES_Q):
    return _call(risk.explain, _city(city), feature, date, risk.SAMPLES, species)


@app.get("/api/cities/{city}/fhir")
def fhir(city: str, date: Optional[str] = None, feature: Optional[str] = None,
         limit: int = Query(20, ge=1, le=500), species: Optional[str] = SPECIES_Q):
    """FHIR R4 collection Bundle (Location + Observation per feature): one feature, or the top-risk ones."""
    if feature:
        explained = [_call(risk.explain, _city(city), feature, date, risk.SAMPLES, species)]
    else:
        ranked = _call(risk.scores, _city(city), date, species)[:limit]
        explained = [_call(risk.explain, city, f["id"], date, 0, species) for _, f in ranked]
    return JSONResponse(bundle(explained), media_type="application/fhir+json")


@app.get("/api/status")
def data_status():
    """How fresh the cached data is, per city, and whether anything is stale."""
    return refresh.status()


@app.get("/api/validation")
def validation():
    s = validate.summary()
    return {"cities": s, "failures": validate.check(s), "habitat": validate.HABITAT,
            "gbif": validate_gbif.summary(),         # real Culex pipiens records, national, committed cache
            "gbif_local": validate_gbif.local_summary()}   # every modelled species, records within 250 km


class Report(BaseModel):
    model_config = {"extra": "forbid"}
    city: str
    feature_id: str = Field(max_length=40)
    date: str = Field(max_length=10)
    bad: bool
    note: Optional[str] = Field(None, max_length=280)
    client: Optional[str] = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@app.post("/api/feedback", status_code=201)
def add_feedback(r: Report, request: Request):
    """'Was it bad here last night?' — one citizen report against a water feature and date.

    One report per client per feature per date: sending another replaces it (people change their mind,
    and a double tap should not count twice). The client id is a random token the browser keeps.
    """
    _city(r.city)
    m = risk.city_model(r.city)
    if r.feature_id not in m["by_id"] and r.feature_id not in m["by_home"]:   # a waterway or a neighbourhood
        raise HTTPException(404, f"unknown feature '{r.feature_id}' in {r.city}")
    client = r.client or "ip:" + hashlib.sha256((request.client.host or "").encode()).hexdigest()[:16]
    return _call(feedback.add, r.city, r.feature_id, r.date, r.bad, r.note, client)


@app.get("/api/feedback")
def list_feedback(city: str, since: Optional[str] = Query(None, max_length=10),
                  limit: int = Query(500, ge=1, le=5000)):
    """Reports for a city, each next to what the model predicted for that feature and date.
    Never the reporter's browser id: with it, anyone could overwrite someone else's report."""
    out = []
    for e in _call(feedback.entries, _city(city), since, limit):
        e.pop("client", None)
        try:
            e["predicted"] = risk.explain(city, e["feature_id"], e["date"], 0)["risk"]
        except (KeyError, ValueError):
            e["predicted"] = None
        out.append(e)
    return out


@app.get("/api/feedback/scoreboard")
def feedback_scoreboard(city: str):
    """Did the model call it? Each report against what was predicted for that spot and night.
    "Bites likely" means a predicted score of 50 or more."""
    grid = {"likely_bitten": 0, "likely_fine": 0, "unlikely_bitten": 0, "unlikely_fine": 0}
    for e in _call(feedback.entries, _city(city)):
        try:
            predicted = risk.explain(city, e["feature_id"], e["date"], 0)["risk"]
        except (KeyError, ValueError):
            continue
        grid[f"{'likely' if predicted >= risk.LIKELY else 'unlikely'}_{'bitten' if e['bad'] else 'fine'}"] += 1
    n = sum(grid.values())
    agree = grid["likely_bitten"] + grid["unlikely_fine"]
    return {**grid, "reports": n, "agree": agree, "agreement": round(agree / n, 2) if n else None,
            "threshold": risk.LIKELY}


class Treatment(BaseModel):
    model_config = {"extra": "forbid"}
    city: str
    feature_id: str = Field(max_length=40)
    date: str = Field(max_length=10)
    product: Optional[str] = Field(None, max_length=60)
    note: Optional[str] = Field(None, max_length=280)


@app.post("/api/treatments", status_code=201)
def add_treatment(t: Treatment, x_treatment_token: Optional[str] = Header(None)):
    """For mosquito-control teams: log a larvicide treatment. Changes the forecast for that feature,
    so it needs the server's treatment token (header X-Treatment-Token)."""
    if not treatments.enabled():
        raise HTTPException(403, "treatment logging is not enabled on this server")
    if not treatments.authorised(x_treatment_token):
        raise HTTPException(403, "a valid X-Treatment-Token header is required")
    _city(t.city)
    if t.feature_id not in risk.city_model(t.city)["by_id"]:
        raise HTTPException(404, f"unknown feature '{t.feature_id}' in {t.city}")
    saved = _call(treatments.add, t.city, t.feature_id, t.date, t.product, t.note)
    risk.city_model.cache_clear()   # the treated feature's series changes
    return saved


@app.get("/api/treatments")
def list_treatments(city: str):
    return _call(treatments.entries, _city(city))


@app.get("/api/feedback/summary")
def feedback_summary(city: str):
    return feedback.summary(_city(city))


APP_DIR = Path(__file__).parent / "app"
if APP_DIR.is_dir():
    app.mount("/", StaticFiles(directory=APP_DIR, html=True), name="app")
