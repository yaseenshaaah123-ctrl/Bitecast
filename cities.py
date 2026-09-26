"""The five OneAquaHealth research cities (https://www.oneaquahealth.eu/research-cities/), plus the
"anywhere" places people look up at runtime.

Centre = weather point and OSM search centre. Radius chosen to cover the streams each
city's OneAquaHealth team samples.
"""

CITIES = {
    "benevento": {"name": "Benevento", "country": "IT", "lat": 41.1300, "lon": 14.7800, "radius_m": 6000,
                  "streams": "Calore and Sabato rivers"},
    "coimbra": {"name": "Coimbra", "country": "PT", "lat": 40.2100, "lon": -8.4200, "radius_m": 5000,
                "streams": "Mondego and its urban tributaries (e.g. Ribeira de Eiras)"},
    "ghent": {"name": "Ghent (Zwalm basin)", "country": "BE", "lat": 50.8700, "lon": 3.7800, "radius_m": 5000,
              "streams": "Zwalm river basin near Zottegem, the Ghent University study area"},
    "oslo": {"name": "Oslo", "country": "NO", "lat": 59.9050, "lon": 10.7500, "radius_m": 7000,
             "streams": "Ljanselva, Hovinbekken and Hoffselva"},
    "toulouse": {"name": "Toulouse", "country": "FR", "lat": 43.6000, "lon": 1.4400, "radius_m": 5000,
                 "streams": "Garonne, Canal du Midi and small southern tributaries"},
}

# Places looked up through /api/anywhere. Same shape as CITIES, filled at runtime by places.ensure()
# and cached on disk under data/ with an "at_" key, so they are never committed with the research cities.
ANYWHERE = {}
ANYWHERE_RADIUS_M = 3000  # smaller than a research city: a neighbourhood, and a query the public servers can answer

# Reference places: weather committed only so the species model can be checked against real records
# outside Europe (validate_gbif.py). Not research cities, not in the app's city list.
REFERENCE = {
    "lahore": {"name": "Lahore", "country": "PK", "lat": 31.55, "lon": 74.34, "radius_m": 3000,
               "streams": "reference place: 771 Culex quinquefasciatus records within 250 km (GBIF)"},
}


def place(key):
    """A research city, an anywhere-mode place or a reference place. Raises KeyError if none."""
    p = CITIES.get(key) or ANYWHERE.get(key) or REFERENCE.get(key)
    if p is None:
        raise KeyError(key)
    return p


# Weather is sampled on a small grid per place, not at one point: a hilly or spread-out city varies by
# 1-2 °C across itself, which is a day or more of mosquito development. Cell 0 is always the centre.
GRID_OFFSETS = [(0, 0), (-0.7, -0.7), (-0.7, 0), (-0.7, 0.7), (0, -0.7),
                (0, 0.7), (0.7, -0.7), (0.7, 0), (0.7, 0.7)]
# A place looked up anywhere gets the centre and four compass points: Open-Meteo counts each point as a
# separate call, and five still catch a hillside or a coast. Places cached before this keep their nine.
GRID_5 = [(0, 0), (-0.7, 0), (0, -0.7), (0, 0.7), (0.7, 0)]


def grid_points(c):
    """[(lat, lon), ...] sampling the place's area; the first is its centre."""
    import math
    dlat = c["radius_m"] / 110_540
    dlon = c["radius_m"] / (111_320 * math.cos(math.radians(c["lat"])))
    offsets = GRID_5 if c.get("cells") == 5 else GRID_OFFSETS
    return [(round(c["lat"] + fy * dlat, 4), round(c["lon"] + fx * dlon, 4)) for fy, fx in offsets]
