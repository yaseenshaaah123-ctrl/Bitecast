"""Where the water is (habitat suitability) and who lives next to it (exposure), from cached OSM data.

Culex pipiens breeds in stagnant, organically enriched water (ditches, drains, ponds, stormwater basins),
not in clean flowing streams — this is where stream condition enters the model.
"""
import json
import math
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from cities import grid_points, place

DATA = Path(__file__).resolve().parent.parent / "data"

# class: (label, habitat weight 0..1, flush threshold mm/day)
# weight = how likely the feature holds the still water Culex pipiens breeds in; ranked from the species'
# habitat preference, values are assumptions. flush = daily rain that washes larvae out: small channels
# flush easily; ponds and basins have volume and barely flush. Starting point ~10-15 mm/day, tunable.
CLASSES = {
    "wastewater": ("wastewater basin",    1.00, 25),
    "stormwater": ("stormwater basin",    0.95, 25),
    "pond":       ("pond",                0.95, 25),
    "ditch":      ("drainage ditch",      0.90, 10),
    "drain":      ("urban drain",         0.85, 10),
    "basin":      ("water basin",         0.60, 25),   # park basins and bare landuse=basin, often dry
    "canal":      ("canal",               0.60, 15),
    "lake":       ("lake or reservoir",   0.40, 40),
    "ornamental": ("ornamental pool",     0.30, 25),   # plaza pools: maintained, usually recirculated
    "stream":     ("stream",              0.25, 10),
    "river":      ("river",               0.10, 10),
}
FLOWING = ("river", "stream", "canal", "ditch", "drain")
INTERMITTENT_BONUS = 0.25   # a stream that stops flowing breaks into still pools. Assumption.
# Only for flowing water: an intermittent *pond* is one that dries out, which is the opposite of habitat.
SMALL_WATER_M2 = 20_000     # untyped natural=water below 2 ha counts as a pond, above as a lake. Assumption.
THIN_SHAPE = 0.2            # Polsby-Popper 4πA/P²: below this a polygon is a channel, not a pond

EXPOSURE_M = 300            # Culex pipiens mostly stays within a few hundred metres of where it hatched
EXPOSURE_WEIGHT = {"residential": 1, "park": 1, "playground": 2, "school": 2}  # children weigh double
EXPOSURE_SCALE = 3.0        # weighted count at which exposure reaches 63% of its maximum. Assumption.
EXPOSURE_FLOOR = 0.2        # nobody nearby still leaves 20% of the score: people pass through. Assumption.

# Satellite layers (sat.py), when cached for a place: water nobody mapped, and people counted, not guessed.
PEOPLE_PER_UNIT = 400       # people within 300 m that count like one mapped residential area. Assumption:
                            # a 300 m circle of town at 5,000/km2 holds ~1,400 people, i.e. ~3.5 areas.
SEASONAL_BELOW = 0.5        # a patch wet all year on less than half its pixels is a seasonal pool
SAT_MIN_PIXELS = 4          # ~0.36 ha: smaller patches are too often a wet roof, a shadow or a dock (Dakar)
DUPLICATE_M = 30            # a satellite patch this close to mapped water is the same water: OSM's copy wins
POP_BLOCK = 3               # unmapped settlements are drawn as squares of 3x3 population cells (~270 m)
POP_MIN = 30                # people in a square before it counts as somewhere people live. Assumption.


def classify(tags, area_m2=0.0, shape=1.0):
    """Habitat class for a water feature, or None to skip it.

    Skipped: culverted reaches (invisible), and water people keep clean and moving — fountains, swimming
    pools, fish and storage tanks — which is not where Culex pipiens breeds.
    """
    if tags.get("tunnel", "no") != "no" or tags.get("covered") == "yes" or tags.get("location") == "underground":
        return None
    ww, water = tags.get("waterway"), tags.get("water")
    if ww in FLOWING:
        return ww
    if tags.get("natural", "water") != "water":
        return None  # e.g. landuse=basin + natural=sand: a dry basin
    if (tags.get("amenity") == "fountain" or water == "fountain" or tags.get("leisure") == "swimming_pool"
            or tags.get("man_made") in ("fish_tank", "water_tank", "storage_tank", "reservoir_covered")):
        return None
    if water in ("stream", "rapids"):
        return "stream"   # a polygon drawn around a flowing watercourse
    if water == "river":
        return "river"
    if water in ("canal", "lock"):
        return "canal"
    if water in ("ditch", "drain"):
        return water
    if water == "wastewater" or tags.get("reservoir_type") == "sewage":
        return "wastewater"
    if tags.get("landuse") == "basin" or water == "basin":
        return "stormwater" if tags.get("basin") in ("detention", "retention", "infiltration") else "basin"
    if tags.get("landuse") == "reservoir" or water in ("lake", "reservoir"):
        return "lake"
    if water == "reflecting_pool":
        return "ornamental"
    if water in ("pond", "oxbow", "lagoon", "stream_pool", "moat"):
        return "pond"
    if shape < THIN_SHAPE:
        return "stream"   # long and thin with no type: a channel, and its centreline is already a feature
    return "pond" if area_m2 < SMALL_WATER_M2 else "lake"


def _projector(lat0, lon0):
    """Local equirectangular metres around the city centre; error is negligible over a few km."""
    kx, ky = 111_320 * math.cos(math.radians(lat0)), 110_540
    return lambda coords: [((lon - lon0) * kx, (lat - lat0) * ky) for lon, lat in coords]


def _fix(g):
    """Repair a self-intersecting ring rather than dropping the feature; None if nothing usable is left."""
    if g is None:
        return None
    if not g.is_valid:
        g = shapely.make_valid(g)
    return None if g.is_empty else g


def _geom(kind, coords, proj):
    if kind == "Point":
        return Point(proj([coords])[0])
    pts = proj(coords)
    if kind == "Polygon" and len(pts) >= 4:
        return Polygon(pts)
    return LineString(pts) if len(pts) >= 2 else None


def _sat(city):
    """The cached satellite layers for a place (sat.py), or None."""
    try:
        return json.loads((DATA / "sat" / f"{city}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _people(sat, c, proj):
    """(tree of populated cells, people per cell) from the satellite population grid, or None."""
    g = sat and sat.get("people")
    if not g:
        return None
    pts, n = [], []
    for i, v in enumerate(g["people"]):
        if v > 0:
            r, col = divmod(i, g["cols"])
            pts.append(Point(proj([(g["lon0"] + (col + 0.5) * g["dlon"], g["lat0"] - (r + 0.5) * g["dlat"])])[0]))
            n.append(v)
    return (shapely.STRtree(pts), np.array(n)) if pts else None


def _exposure(g, nearby, people):
    """Exposure from the mapped places around a feature and, where the satellite layer is cached, the people
    counted around it: whichever says more (a town OSM barely maps still has its people)."""
    score = sum(EXPOSURE_WEIGHT[k] * n for k, n in nearby.items())
    if people is not None:
        tree, n = people
        count = int(n[tree.query(g, predicate="dwithin", distance=EXPOSURE_M)].sum())
        score = max(score, count / PEOPLE_PER_UNIT)
        nearby["people"] = count
    return EXPOSURE_FLOOR + (1 - EXPOSURE_FLOOR) * (1 - math.exp(-score / EXPOSURE_SCALE))


def load_features(city):
    """Water features for a city with habitat weight and exposure. Returns a list of dicts."""
    raw = json.loads((DATA / "osm" / f"{city}.json").read_text(encoding="utf-8"))
    c = place(city)
    proj = _projector(c["lat"], c["lon"])
    sat = _sat(city)
    people_sat = _people(sat, c, proj)
    kx, ky = 111_320 * math.cos(math.radians(c["lat"])), 110_540

    # each feature scores against its nearest weather cell (cell 0 is the centre)
    cells_xy = [((lon - c["lon"]) * kx, (lat - c["lat"]) * ky) for lat, lon in grid_points(c)]

    people, kinds = [], []
    for e in raw["exposure"]:
        g = _fix(_geom(e["geom"], e["coords"], proj))
        if g is not None:
            people.append(g)
            kinds.append(e["kind"])
    tree = shapely.STRtree(people)
    # Overpass returns whole ways, so a 42 km river arrives with the few hundred metres that are in town.
    # Clip to the study area: the marker, the FHIR position and the exposure count all belong in the city.
    area = Point(0, 0).buffer(c["radius_m"])

    out, mapped = [], []
    for w in raw["water"]:
        g = _fix(_geom(w["geom"], w["coords"], proj))
        if g is None:
            continue
        mapped.append(g)                # every mapped water, kept or not: a fountain seen from space stays out
        tags = w["tags"]
        perim = g.length if g.geom_type == "Polygon" else 0
        cls = classify(tags, g.area, 4 * math.pi * g.area / perim**2 if perim else 1.0)
        if cls is None:
            continue
        g = g.intersection(area) or g   # falls back to the whole way if it somehow misses the circle
        label, weight, _ = CLASSES[cls]
        if cls in FLOWING and (tags.get("intermittent") == "yes" or tags.get("seasonal") == "yes"):
            weight = min(1.0, weight + INTERMITTENT_BONUS)

        nearby = dict.fromkeys(EXPOSURE_WEIGHT, 0)
        for j in tree.query(g, predicate="dwithin", distance=EXPOSURE_M):
            nearby[kinds[j]] += 1
        exposure = _exposure(g, nearby, people_sat)

        anchor = g.interpolate(0.5, normalized=True) if g.geom_type == "LineString" else g.representative_point()
        cell = min(range(len(cells_xy)), key=lambda j: (cells_xy[j][0] - anchor.x) ** 2 + (cells_xy[j][1] - anchor.y) ** 2)
        out.append({
            "id": w["id"],
            "name": tags.get("name"),
            "cls": cls,
            "habitat": round(weight, 3),
            "exposure": round(exposure, 3),
            "nearby": nearby,
            "cell": cell,
            # anchor point back to lat/lon, for markers and FHIR Location.position
            "centroid": [round(c["lat"] + anchor.y / ky, 5), round(c["lon"] + anchor.x / kx, 5)],
            "geometry": {"type": w["geom"], "coordinates": [w["coords"]] if w["geom"] == "Polygon" else w["coords"]},
        })

    # Water seen from space that nobody has mapped: ponds and seasonal pools, typed by size, shape and how
    # many months a year they hold water. Anything touching mapped water is that water, already scored.
    same = shapely.STRtree([m.buffer(DUPLICATE_M) for m in mapped]) if mapped else None
    for p in (sat or {}).get("water", []):
        g = _fix(Polygon(proj(p["coords"]))) if len(p["coords"]) >= 4 else None
        if p["pixels"] < SAT_MIN_PIXELS or g is None or not g.intersects(area) or                 (same is not None and len(same.query(g, predicate="intersects"))):
            continue
        g = g.intersection(area)
        if g.is_empty:
            continue
        if p["permanent"] < SEASONAL_BELOW:
            cls, label = "pond", "seasonal pool (satellite)"
        else:
            cls = classify({"natural": "water"}, g.area, 4 * math.pi * g.area / g.length**2 if g.length else 1.0)
            label = f"{CLASSES[cls][0]} (satellite)"
        when = "all year" if p["months"] >= 11.5 else f"about {p['months']:.0f} months a year"
        note = f"Not on OpenStreetMap: satellites (JRC Global Surface Water) see water here {when}."
        nearby = dict.fromkeys(EXPOSURE_WEIGHT, 0)
        for j in tree.query(g, predicate="dwithin", distance=EXPOSURE_M):
            nearby[kinds[j]] += 1
        exposure = _exposure(g, nearby, people_sat)
        anchor = g.representative_point()
        cell = min(range(len(cells_xy)), key=lambda j: (cells_xy[j][0] - anchor.x) ** 2 + (cells_xy[j][1] - anchor.y) ** 2)
        out.append({
            "id": p["id"], "name": None, "cls": cls, "label": label, "source": "satellite", "note": note,
            "habitat": round(CLASSES[cls][1], 3), "exposure": round(exposure, 3), "nearby": nearby, "cell": cell,
            "centroid": [round(c["lat"] + anchor.y / ky, 5), round(c["lon"] + anchor.x / kx, 5)],
            "geometry": {"type": "Polygon", "coordinates": [p["coords"]]},
        })
    return out


# Container breeders (Aedes) lay eggs in buckets, tanks, tyres and plant saucers around people, which no map
# shows. So their risk is drawn on the places people are — the mapped homes, schools, parks and playgrounds
# — and every such place is assumed to have containers (habitat 1.0: the honest gap, see SCIENCE.md §8).
PLACE_LABEL = {"residential": "residential area", "school": "school", "park": "park", "playground": "playground"}


def load_places(city):
    """Neighbourhood features for container-breeding species: same shape as load_features() rows, class
    "containers", exposure from the same formula as water features (this place plus its neighbours)."""
    raw = json.loads((DATA / "osm" / f"{city}.json").read_text(encoding="utf-8"))
    c = place(city)
    proj = _projector(c["lat"], c["lon"])
    kx, ky = 111_320 * math.cos(math.radians(c["lat"])), 110_540
    cells_xy = [((lon - c["lon"]) * kx, (lat - c["lat"]) * ky) for lat, lon in grid_points(c)]
    area = Point(0, 0).buffer(c["radius_m"])
    sat = _sat(city)
    people_sat = _people(sat, c, proj)
    geoms, rows = [], []
    for e in raw["exposure"]:
        g = _fix(_geom(e["geom"], e["coords"], proj))
        if g is None:
            continue
        geoms.append(g)
        rows.append(e)
    tree = shapely.STRtree(geoms)
    out = []
    for g, e in zip(geoms, rows):
        inside = g.intersection(area) if g.geom_type != "Point" else (g if area.contains(g) else None)
        if inside is None or inside.is_empty:
            continue
        nearby = dict.fromkeys(EXPOSURE_WEIGHT, 0)
        for j in tree.query(inside, predicate="dwithin", distance=EXPOSURE_M):
            nearby[rows[j]["kind"]] += 1          # includes the place itself
        exposure = _exposure(inside, nearby, people_sat)
        anchor = inside if inside.geom_type == "Point" else inside.representative_point()
        cell = min(range(len(cells_xy)), key=lambda j: (cells_xy[j][0] - anchor.x) ** 2 + (cells_xy[j][1] - anchor.y) ** 2)
        out.append({
            "id": e["id"], "name": e.get("name"), "cls": "containers", "kind": e["kind"],
            "habitat": 1.0, "exposure": round(exposure, 3), "nearby": nearby, "cell": cell,
            "centroid": [round(c["lat"] + anchor.y / ky, 5), round(c["lon"] + anchor.x / kx, 5)],
            "geometry": {"type": e["geom"], "coordinates": [e["coords"]] if e["geom"] == "Polygon" else e["coords"]},
        })

    # Where people live but OpenStreetMap shows no homes, school or park (a town nobody has mapped yet), the
    # satellite population layer draws the settlement as ~270 m squares: containers are wherever people are.
    g = (sat or {}).get("people")
    if g:
        mapped = shapely.STRtree([x.buffer(50) for x in geoms]) if geoms else None
        B, cols = POP_BLOCK, g["cols"]
        for r0 in range(0, g["rows"] - B + 1, B):
            for c0 in range(0, cols - B + 1, B):
                n = sum(g["people"][(r0 + i) * cols + c0 + j] for i in range(B) for j in range(B))
                if n < POP_MIN:
                    continue
                top, left = g["lat0"] - r0 * g["dlat"], g["lon0"] + c0 * g["dlon"]
                bottom, right = top - B * g["dlat"], left + B * g["dlon"]
                ring = [[round(x, 6), round(y, 6)] for x, y in
                        ((left, top), (right, top), (right, bottom), (left, bottom), (left, top))]
                sq = Polygon(proj(ring))
                if not area.contains(sq.centroid) or (mapped is not None and len(mapped.query(sq, predicate="intersects"))):
                    continue
                inside = sq.intersection(area)
                nearby = dict.fromkeys(EXPOSURE_WEIGHT, 0)
                for j in tree.query(inside, predicate="dwithin", distance=EXPOSURE_M):
                    nearby[rows[j]["kind"]] += 1
                exposure = _exposure(inside, nearby, people_sat)
                anchor = inside.representative_point()
                cell = min(range(len(cells_xy)), key=lambda j: (cells_xy[j][0] - anchor.x) ** 2 + (cells_xy[j][1] - anchor.y) ** 2)
                out.append({
                    "id": f"pop/{r0 * cols + c0}", "name": None, "cls": "containers", "kind": "residential",
                    "label": "populated area (satellite)", "source": "satellite",
                    "note": f"Not on OpenStreetMap: about {n:,} people live in this ~270 m square (Meta population map).",
                    "habitat": 1.0, "exposure": round(exposure, 3), "nearby": nearby, "cell": cell,
                    "centroid": [round(c["lat"] + anchor.y / ky, 5), round(c["lon"] + anchor.x / kx, 5)],
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                })
    return out


if __name__ == "__main__":
    assert classify({"waterway": "ditch"}) == "ditch"
    assert classify({"waterway": "stream", "tunnel": "culvert"}) is None
    assert classify({"natural": "water"}, 500) == "pond" and classify({"natural": "water"}, 1e6) == "lake"
    assert classify({"natural": "water", "water": "fountain"}) is None
    import sys
    from cities import CITIES
    # the research cities only: anywhere-mode lookups are exercised through the API, and globbing data/osm
    # would pick up stray copies such as "toulouse(1).json"
    for city in sys.argv[1:] or list(CITIES):
        feats = load_features(city)
        counts = {}
        for f in feats:
            counts[f["cls"]] = counts.get(f["cls"], 0) + 1
        ex = np.array([f["exposure"] for f in feats])
        sat_n = sum(f.get("source") == "satellite" for f in feats)
        assert all(f["cls"] in CLASSES for f in feats) and len({f["id"] for f in feats}) == len(feats)
        print(f"{city:10s} {len(feats):5d} features ({sat_n} from satellite)  {counts}  "
              f"exposure mean={ex.mean():.2f} max={ex.max():.2f}")
    print("ok")
