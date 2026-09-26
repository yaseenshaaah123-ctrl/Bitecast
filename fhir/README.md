# FHIR output

`serialise.bundle(explanations)` turns `risk.explain()` dicts into one FHIR R4 `Bundle` of type
`collection`. See `example_bundle.json` (built from two clearly fake inputs by `python -m fhir.serialise`).
The API serves it at `/api/cities/{city}/fhir` as `application/fhir+json`.

## What is emitted

- **Location**, one per water feature: id `<city>-<osmtype>-<osmid>`, OpenStreetMap identifier
  (`https://www.openstreetmap.org` + `way/123`), `mode: instance`, WGS84 `position` from the feature's
  anchor point, habitat class and city in `description`, `physicalType` = `area`.
- **Observation**, one per feature and date: `valueQuantity` = the risk index 0-100 (UCUM annotation
  `{index}`), one `component` per model factor (development, stagnation, habitat, exposure; each 0-1,
  UCUM `1` = the dimensionless unity), `effectiveDateTime` = the day, `issued` = when the bundle was made,
  `method` cites Loetti et al. 2011, and a note carrying the risk band and the plain-English explanation,
  which ends "Modelled estimate, not a field measurement."
- **`status`** is `final` only for a day whose weather is finished and archived. Today is `preliminary`
  (its weather is still partly forecast and will change), and so is every forecast day.
- Every entry has a `urn:uuid:` fullUrl (deterministic uuid5 of the resource id), and
  `Observation.subject.reference` is the Location's fullUrl. A relative `Location/<id>` reference
  only resolves inside a Bundle when fullUrls are RESTful server URLs
  ([Bundle: resolving references](https://hl7.org/fhir/R4/bundle.html#references)); we have no server.
  One Location is emitted per feature however many dates it appears on, because `bdl-7` requires unique
  fullUrls within a bundle.

## Why the subject is a Location

A risk index for a ditch has no patient. R4 types `Observation.subject` as
`Reference(Patient | Group | Device | Location)` and defines it as:

> "The patient, or group of patients, location, or device this observation is about and into whose
> record the observation is placed."
> https://hl7.org/fhir/R4/observation-definitions.html#Observation.subject

(The sentence sometimes quoted about tests on "products, substances, and environments" is not in the R4
Observation page; the `subject` definition above is the one to rely on.)

`RiskAssessment` was considered. Its R4 subject is `Patient | Group` only, so it cannot take a Location
directly; a Group with a `characteristic.valueReference` to the Location would be the conformant route.
We kept Observation for every day and flag the future ones with `status: preliminary` — that is the only
machine-readable marker that a forecast day is a prediction rather than a measurement. A consumer that
needs predictions typed as such wants RiskAssessment; see "next version" in the main README.

## Honest gaps

- **No standard code.** There is no LOINC or SNOMED CT code for a modelled mosquito-emergence risk
  index, so `code` uses a local, namespaced system `https://bitecast.example/CodeSystem/vector-risk`,
  one code per species since model v2 (`culex-pipiens-emergence-risk`,
  `culex-quinquefasciatus-emergence-risk`, `aedes-aegypti-emergence-risk`,
  `aedes-albopictus-emergence-risk`) plus `factor-*` for the components. Standardised
  vector-surveillance codes are a gap. The CodeSystem resource itself is not published.
- **Container breeders.** For an *Aedes* forecast the Location is the neighbourhood (a residential area,
  school, park or playground), because the containers the species breeds in are not mapped; its
  `description` says so.
- **No category.** All R4 `observation-category` codes describe patient data (`survey` means an
  assessment instrument such as Apgar), so none is used rather than a misleading one.
- **No interpretation.** `ObservationInterpretation` codes are relative to a reference range, and the
  risk bands are our own thresholds. The band goes in a note instead.
- **No narrative.** No resource carries `text`, so a validator will raise the `dom-6` best-practice
  warning ("A resource should have narrative") on every resource.

## How to validate

Not validated by an external server here — nothing was sent anywhere. Either:

- Official HL7 validator (Java):
  `java -jar validator_cli.jar fhir/example_bundle.json -version 4.0.1`
  (download from https://github.com/hapifhir/org.hl7.fhir.core/releases).
- Or POST the bundle to a FHIR R4 server's `Bundle/$validate`, e.g. the hackathon sandbox.

Expect two kinds of message, both by design: `dom-6` narrative warnings, and informational notes that
`https://bitecast.example/CodeSystem/vector-risk` is an unknown code system.

`python -m fhir.serialise` runs a structural self-check (id patterns, `obs-7`, resolvable subjects,
UCUM on every quantity, one Location per feature across dates). The bundle also parses cleanly with the
`fhir.resources` R4B pydantic models, which check structure and types but not terminology bindings.
