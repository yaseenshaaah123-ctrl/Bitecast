"""Serialise risk.explain() dicts as a FHIR R4 Bundle (type "collection").

One Location per water feature and one Observation per (feature, date). Element names,
cardinalities and invariants checked against https://hl7.org/fhir/R4/ (Observation,
Location, Bundle pages). Not validated by an external server - see fhir/README.md.

Self-check (run from the bitecast dir so `cities` imports):  python -m fhir.serialise
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cities import place

# Local code system. There is no LOINC/SNOMED code for a modelled mosquito-emergence risk
# index: standardised vector-surveillance codes are a gap in current terminologies.
# One code per species (v2): "<genus>-<species>-emergence-risk", e.g. aedes-aegypti-emergence-risk.
CS = "https://bitecast.example/CodeSystem/vector-risk"
UCUM = "http://unitsofmeasure.org"
OSM = "https://www.openstreetmap.org"
FACTORS = ["development", "stagnation", "habitat", "exposure"]
# Female parameters from Loetti, Schweigmann & Burroni 2011, J. Nat. Hist. 45(35-36).
METHOD = "Degree-day model, Loetti et al. 2011 (Tb 5.5 C, K 199.5 DD)"
# "final" only for a day whose weather is finished and archived; today is still in progress and
# forecast days are, of course, preliminary. R4: preliminary = "initial/interim" data.
STATUS = {"observed": "final"}
DEFAULT_STATUS = "preliminary"

ID_RE = re.compile(r"[A-Za-z0-9\-.]{1,64}")  # FHIR R4 id datatype
FEATURE_RE = re.compile(r"(node|way|relation|sat|pop)/[0-9]+")
GSW = "https://global-surface-water.appspot.com"     # a patch of open water seen by satellite (sat.py)
HRSL = "https://dataforgood.facebook.com/dfg/tools/high-resolution-population-density-maps"   # a settlement, ditto
INSTANT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")


def _id(*parts):
    rid = re.sub(r"[^A-Za-z0-9\-.]", "-", "-".join(parts))
    if not ID_RE.fullmatch(rid):  # only fails past 64 chars; truncating could collide
        raise ValueError(f"cannot make a FHIR id from {parts!r}")
    return rid


def _full_url(resource_type, rid):
    # Deterministic, so the same feature/date always gets the same fullUrl.
    return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://bitecast.example/{resource_type}/{rid}"))


def _qty(value, unit, code="1"):
    # UCUM: "1" = the dimensionless unity (the 0..1 factors). The 0-100 index is 100x its own factors'
    # product, so it carries the UCUM annotation "{index}" instead of claiming to be unity.
    return {"value": value, "unit": unit, "system": UCUM, "code": code}


def _code(code, display):
    return {"coding": [{"system": CS, "code": code, "display": display}], "text": display}


PLACE_LABEL = {"residential": "residential area", "school": "school", "park": "park", "playground": "playground"}


def _location(e, rid):
    city = place(e["city"])            # a research city or an anywhere-mode place
    where = f"{city['name']}, {city['country']}" if city.get("country") else city["name"]
    lat, lon = e["centroid"]
    species = e.get("species_name", "Culex pipiens")
    if e.get("breeds") == "containers":
        kind = PLACE_LABEL.get(e.get("place_kind"), "neighbourhood")
        name = (e.get("name") or "").strip() or f"Unnamed {kind}"
        what = (f"{kind} in {where}; OpenStreetMap {e['feature_id']}, area where {species} breeds in "
                "containers (containers themselves are not mapped).")
    else:
        name = (e.get("name") or "").strip() or f"Unnamed {e['cls_label']}"
        what = (f"{e['cls_label']} (habitat class '{e['cls']}') in {where}; "
                + (f"JRC Global Surface Water patch {e['feature_id']} (not on OpenStreetMap)"
                   if e["feature_id"].startswith("sat/") else f"OpenStreetMap {e['feature_id']}")
                + f", candidate {species} larval habitat.")
    return {
        "resourceType": "Location",
        "id": rid,
        "identifier": [{"system": GSW if e["feature_id"].startswith("sat/") else HRSL,
                        "value": f"{e['city']}/{e['feature_id']}"} if e["feature_id"][:4] in ("sat/", "pop/")
                       else {"system": OSM, "value": e["feature_id"]}],
        "status": "active",
        "name": name,
        "description": what,
        "mode": "instance",
        # R4 location-physical-type defines "area" as "a defined physical boundary of something, such as
        # a flood risk zone, region, postcode" — which is what a mapped water body is. Binding is Example,
        # so the free text carries the detail.
        "physicalType": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/location-physical-type",
                                     "code": "area"}], "text": e["cls_label"]},
        # WGS84 per spec; 6 decimals ~ 0.1 m. FHIR decimals are JSON numbers, not strings.
        "position": {"longitude": round(lon, 6), "latitude": round(lat, 6)},
    }


def _observation(e, rid, loc_url, loc_name, issued):
    species = e.get("species_name", "Culex pipiens")
    return {
        "resourceType": "Observation",
        "id": rid,
        "status": STATUS.get(e["kind"], DEFAULT_STATUS),
        # category omitted (0..* in base R4). Every observation-category code (social-history,
        # vital-signs, imaging, laboratory, procedure, survey, exam, therapy, activity) is about
        # a patient; "survey" means an assessment instrument such as Apgar. None fits a
        # modelled environmental index, and a wrong category is worse than none.
        "code": _code(f"{species.lower().replace(' ', '-')}-emergence-risk", f"{species} emergence risk index"),
        # R4 Observation.subject is Reference(Patient | Group | Device | Location): "The patient,
        # or group of patients, location, or device this observation is about"
        # (https://hl7.org/fhir/R4/observation-definitions.html#Observation.subject).
        # RiskAssessment was not used: its R4 subject is Patient | Group only.
        "subject": {"reference": loc_url, "display": loc_name},
        "effectiveDateTime": e["date"],  # a date-only value is a valid FHIR dateTime
        "issued": issued,
        "valueQuantity": _qty(e["risk"], "index", "{index}"),
        # interpretation omitted: v3 ObservationInterpretation codes (H, HH, A, ...) are relative to
        # a reference range; our bands are our own thresholds, so the band goes in a note instead.
        "note": [{"text": f"Risk band: {e['band']}. {e['text']}".strip()}],  # e['text'] ends with the disclaimer
        "method": {"text": f"{e.get('method') or METHOD}; BiteCast model v{e.get('model_version', 'unknown')}"},
        # obs-7: component codes differ from Observation.code (factor-* vs <species>-emergence-risk).
        "component": [{"code": _code(f"factor-{f}", f"Risk factor: {f} (0-1, risk = 100 x product)"),
                       "valueQuantity": _qty(e["factors"][f], "1")} for f in FACTORS]
                     + ([{"code": _code(f"risk-{end}", f"Risk index, {pct} percentile over model assumptions"),
                          "valueQuantity": _qty(e["range"][end], "index", "{index}")}
                         for end, pct in (("low", "10th"), ("high", "90th"))] if e.get("range") else []),
    }


def bundle(explanations: list[dict], issued: str | None = None) -> dict:
    issued = issued or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not INSTANT_RE.fullmatch(issued):
        raise ValueError(f"issued must be a FHIR instant with timezone, got {issued!r}")
    # References: in a Bundle a relative "Location/<id>" only resolves when the entry fullUrl is
    # RESTful (http://server/Location/<id>); with urn:uuid fullUrls it "has no defined meaning"
    # (https://hl7.org/fhir/R4/bundle.html#references). We have no server base, so every
    # fullUrl is a urn:uuid and subject.reference is the Location's fullUrl verbatim.
    entries = {}  # keyed by fullUrl: one Location per feature even across dates (bdl-5)
    for e in explanations:
        if not FEATURE_RE.fullmatch(e["feature_id"]):
            raise ValueError(f"bad feature_id {e['feature_id']!r}")
        osm_type, osm_id = e["feature_id"].split("/")
        loc_id = _id(e["city"], osm_type, osm_id)
        obs_id = _id(e["city"], osm_type, osm_id, e["date"])
        loc_url, obs_url = _full_url("Location", loc_id), _full_url("Observation", obs_id)
        loc = entries.setdefault(loc_url, {"fullUrl": loc_url, "resource": _location(e, loc_id)})
        entries.setdefault(obs_url, {"fullUrl": obs_url,
                                     "resource": _observation(e, obs_id, loc_url, loc["resource"]["name"], issued)})
    return {"resourceType": "Bundle", "type": "collection", "timestamp": issued, "entry": list(entries.values())}


if __name__ == "__main__":
    # FAKE explanation dicts, only for this structural check. Not model output, not real data.
    # model_version is the real one, so the example's method line reads like a real export.
    from model.risk import MODEL_VERSION
    fake = [{**e, "model_version": MODEL_VERSION} for e in [
        {"city": "coimbra", "feature_id": "way/123", "date": "2026-09-19", "kind": "observed",
         "name": "FAKE test ditch", "cls": "ditch", "cls_label": "drainage ditch", "centroid": [40.21, -8.42],
         "risk": 62, "band": "high",
         "factors": {"development": 0.81, "stagnation": 0.90, "habitat": 0.90, "exposure": 0.95},
         "limiting": "development", "facts": {}, "text": "FAKE explanation text for the self-check."},
        {"city": "oslo", "feature_id": "relation/456", "date": "2026-09-22", "kind": "forecast",
         "name": None, "cls": "pond", "cls_label": "pond", "centroid": [59.905, 10.75],
         "risk": 3, "band": "low",
         "factors": {"development": 0.10, "stagnation": 0.50, "habitat": 0.80, "exposure": 0.75},
         "limiting": "development", "facts": {}, "text": "FAKE explanation text for the self-check."},
    ]]
    b = bundle(fake, issued="2026-09-19T08:00:00Z")

    def walk(x):  # FHIR JSON forbids null, "" and empty arrays/objects
        assert x not in (None, "", [], {}), "empty value in bundle"
        for v in (x.values() if isinstance(x, dict) else x if isinstance(x, list) else []):
            walk(v)

    walk(b)
    assert all(f"model v{MODEL_VERSION}" in o["method"]["text"] for o in
               (en["resource"] for en in b["entry"]) if o["resourceType"] == "Observation")
    assert b["type"] == "collection" and INSTANT_RE.fullmatch(b["timestamp"])
    urls = [en["fullUrl"] for en in b["entry"]]
    assert len(urls) == len(set(urls)) == 4 and all(u.startswith("urn:uuid:") for u in urls)
    by_url = {en["fullUrl"]: en["resource"] for en in b["entry"]}
    obs = [r for r in by_url.values() if r["resourceType"] == "Observation"]
    for r in by_url.values():
        assert ID_RE.fullmatch(r["id"]), r["id"]
    for o in obs:
        assert by_url[o["subject"]["reference"]]["resourceType"] == "Location"
        main = o["code"]["coding"][0]
        assert all(c["code"]["coding"][0] != main for c in o["component"])  # obs-7
        for q in [o["valueQuantity"]] + [c["valueQuantity"] for c in o["component"]]:
            assert q["system"] == UCUM
        assert o["valueQuantity"]["code"] == "{index}"
        assert all(c["valueQuantity"]["code"] == "1" for c in o["component"])
        assert "category" not in o and "interpretation" not in o
        assert o["valueQuantity"]["code"] == "{index}" and all(c["valueQuantity"]["code"] == "1" for c in o["component"])
    assert [o["status"] for o in obs] == ["final", "preliminary"]
    assert obs[0]["id"] == "coimbra-way-123-2026-09-19"
    loc = by_url[obs[0]["subject"]["reference"]]
    assert loc["id"] == "coimbra-way-123" and loc["position"] == {"longitude": -8.42, "latitude": 40.21}
    assert by_url[obs[1]["subject"]["reference"]]["name"] == "Unnamed pond"
    # Same feature on two dates -> one Location, two Observations.
    again = bundle(fake + [dict(fake[0], date="2026-09-20", kind="forecast")])
    assert [en["resource"]["resourceType"] for en in again["entry"]].count("Location") == 2
    assert len(again["entry"]) == 5
    for bad in ["street/1", "way/12a", "way/1\n"]:
        try:
            bundle([dict(fake[0], feature_id=bad)])
            raise AssertionError(bad)
        except ValueError:
            pass
    # one code per species; a container breeder's Location is the neighbourhood, not a waterway
    assert obs[0]["code"]["coding"][0]["code"] == "culex-pipiens-emergence-risk"
    aedes = dict(fake[0], feature_id="way/999", breeds="containers", species_name="Aedes albopictus",
                 place_kind="school", name=None, cls="containers", cls_label="containers around homes")
    b2 = bundle([aedes])
    walk(b2)
    res = {en["resource"]["resourceType"]: en["resource"] for en in b2["entry"]}
    assert res["Observation"]["code"]["coding"][0]["code"] == "aedes-albopictus-emergence-risk"
    assert res["Location"]["name"] == "Unnamed school" and "containers" in res["Location"]["description"]

    out = Path(__file__).with_name("example_bundle.json")
    out.write_text(json.dumps(b, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"ok: {len(b['entry'])} entries ({len(obs)} Observations), wrote {out}")
