# The science behind BiteCast

BiteCast does not use a trained model. Every number comes from published *Culex pipiens* physiology,
or it is labelled as an assumption. This page records what was checked, where it was checked, and
what could not be checked. Everything here was checked on 19 September 2026.

Status labels used below:

- **VERIFIED**: read in the primary source or its official abstract, which is cited.
- **SUPPORTED**: the value falls inside the range that the cited literature reports, but no source gives this exact value.
- **ASSUMPTION**: a modelling choice. It is stated openly and can be tuned.
- **DERIVED**: calculated here from verified inputs. It is not a published value.
- **UNVERIFIED**: no accessible source was found to confirm it.

---

## 1. Development: Loetti, Schweigmann & Burroni (2011)

Loetti V., Schweigmann N., Burroni N. (2011). Development rates, larval survivorship and wing length of
*Culex pipiens* (Diptera: Culicidae) at constant temperatures. *Journal of Natural History* 45(35–36):
2203–2213. doi:[10.1080/00222933.2011.590946](https://doi.org/10.1080/00222933.2011.590946)

Sources checked: the official abstract (through the Semantic Scholar and OpenAlex records of that DOI) and
the figure captions deposited on Zenodo ([Fig. 1](https://zenodo.org/records/5204281),
[Fig. 2](https://zenodo.org/records/5204283), [Fig. 3](https://zenodo.org/records/5204285)). The full text
is behind a paywall. The open copy in the CONICET repository (hdl.handle.net/11336/68656) could not be
reached.

| Claim in the battle plan | Female | Male | Status |
|---|---|---|---|
| Linear lower development threshold | 5.5 °C | 5.2 °C | **VERIFIED** (abstract) |
| Thermal constant | 199.5 DD | 186.5 DD | **VERIFIED** (abstract), but it covers larva I to adult, not egg to adult. See below |
| Brière lower / upper threshold | 9.8 / 34.2 °C | 8.4 / 34.4 °C | **VERIFIED** (abstract) |
| Seven constant temperatures, 7–33 °C | | | **VERIFIED** (abstract) |
| Brière coefficient *a* | | | **UNVERIFIED**: it is not in the abstract, and the full text was not accessible |

**Correction to the plan: the thermal constant covers larva I to adult, not egg to adult.** The abstract says
first-instar larvae were reared until adults emerged. The Fig. 2 caption describes the rates as running
"from larva I until adult emergence". So 199.5 DD does **not** include:

1. the **egg stage**. The ECDC species factsheet (see §2e) gives hatching after about 1 day at 30 °C,
   3 days at 20 °C and 10 days at 10 °C, with no hatching below 7 °C. Converted to degree-days above
   5.5 °C, that is about **25–45 DD** (DERIVED here, not a published constant).
2. the time from adult emergence to egg-laying (mating, blood meal, gonotrophic cycle).

The model's counter resets only on a flushing rain or outside the breeding season — never at emergence.
Once the first 199.5 DD are banked, adults emerge continuously at dd/K per day (overlapping generations),
which is what a real population does. The consequence is that after the first brood the model carries no
generation lag at all: neither the egg stage nor the delay from emergence to egg-laying. Emergence after
the first brood therefore starts sooner than in reality. The endpoint the model reports — *when biting
adults emerge* — is the one Loetti measured.

Other findings from the accessible text:

- Survival of the immature stages was highest at 25 °C (abstract).
- The Fig. 1 caption gives development times at **five** of the seven temperatures. Development was
  presumably not completed at the other two. Which two they were is UNVERIFIED.
- The abstract says development *rate* "decreased with increasing temperature until 30 °C". Read
  literally, that contradicts Fig. 2, where rate rises with temperature. It probably means development
  *time*. This is noted here and not relied on.
- Where the mosquito population came from (South America rather than Europe) is not stated in the
  accessible text, so it is UNVERIFIED. European populations may differ.

**Sanity check against an independent source.** The linear model gives 199.5/(T−5.5) days from larva to adult.
That is 21.0 days at 15 °C and 8.1 days at 30 °C. ECDC's factsheet says larvae become adults in
"6-7 days at 30 °C, 21-24 days at 15 °C". The model agrees closely at 15 °C and is about 1 day slow at 30 °C.

**A pooled alternative.** Shocket et al. (2020, eLife) fitted one Brière curve to *Cx. pipiens*
development-rate data from seven studies, Loetti 2011 among them. Their values: q = 3.76·10⁻⁵,
T_min = 0.1 °C, T_max = 38.5 °C, T_opt = 30.9 °C. They are wider than Loetti's thresholds, because they
pool several populations and cover egg-to-adult data.
doi:[10.7554/eLife.58511](https://doi.org/10.7554/eLife.58511), Appendix 1, table 3.

### How the model uses these numbers

`model/degree_days.py` is linear (T_b 5.5 °C, K 199.5 DD) up to the Brière optimum. Above the optimum the
rate falls away along the Brière shape from its value *at* the optimum, reaching zero at 34.2 °C, so the
model's fastest development is at T_opt = 28.54 °C, as the Brière fit says it should be. Only the *shape*
of the Brière curve is used, so *a* cancels out and its unverified value does not affect results.

If *a* is ever needed, one option is to calibrate it so that the Brière rate equals the linear rate at
20 °C. That gives a ≈ 9.46·10⁻⁵ (DERIVED, not from the paper). Calibrated this way, the Brière curve gives
30.9 days at 15 °C where the linear model gives 21.0 days. The two models disagree most in cool weather.

---

## 2. Model assumptions and the literature behind them

### a) Adult survival (model: e-folding time 7 d, so p = e^(−1/7) ≈ 0.867/day) and biting window (model: 21 d)

- **Jones et al. 2012** (field mark–release–recapture, Washington DC area): daily survival
  **0.904 ± 0.037**, which gives an average longevity of about 10.4 days. Rain lowered survival: about 1 cm
  in 24 h cut daily survival from 0.904 to 0.594. doi:[10.1603/ME11191](https://doi.org/10.1603/ME11191)
- **Matthews 2025** (meta-analysis of mark–recapture studies that released mosquitoes of known age):
  expected female lifetime for *Culex* was **7.22 days (95% CL 6.26–8.33, n = 18)** under a Weibull model,
  and 5.33 days (4.18–6.79) under an exponential model.
  doi:[10.1186/s13071-025-07024-2](https://doi.org/10.1186/s13071-025-07024-2)
- **Andreadis et al. 2014** (laboratory, *Cx. pipiens* f. *pipiens*, 15–30 °C): longevity falls as
  temperature rises. Mean longevity was 12 days or less at 30 °C, and some females lived up to 132 days
  at 15 °C. doi:[10.1007/s00436-014-4152-x](https://doi.org/10.1007/s00436-014-4152-x)
- **Supported range for p: about 0.83–0.90 per day.** This is DERIVED. The lower end is p = e^(−1/5.33),
  and the upper end is Jones's 0.904. The model's 0.867 lies inside it: **SUPPORTED**.
- **The biting window is an ASSUMPTION.** At p = 0.867, 14% of a cohort is still alive at day 14 and 5% at
  day 21. The 21-day kernel is therefore a cut-off of the tail, not a lifespan. No source was found for
  the "14–21 days" figure itself: UNVERIFIED as a literature value.

### b) Rain flushing larvae (model: 10–25 mm/day depending on habitat, 40 for lakes)

- **Koenraadt & Harrington 2008** (rain simulator, containers): *Cx. pipiens* larvae and pupae were flushed
  out more easily than *Ae. aegypti*. The proportion flushed rose with longer rain exposure and with warmer
  water. The abstract gives **no mm/day threshold**. The simulated intensities are not in the abstract, so
  they are UNVERIFIED. doi:[10.1093/jmedent/45.1.28](https://doi.org/10.1093/jmedent/45.1.28)
- **Geery & Holub 1989** (street catch basins, Illinois): normal rainfall **below 25 mm** flushed out only
  part of the larvae, cutting numbers by **22–34%**. *J Am Mosq Control Assoc* 5(4):537–540,
  [PubMed 2614404](https://pubmed.ncbi.nlm.nih.gov/2614404/).
- **Gardner et al. 2012** (catch basins, Chicago): a rain event of two or more hours above **3.48 cm (about
  35 mm)** reduced the number of catch basins producing larvae to near zero. Low rainfall went with high
  larval abundance. doi:[10.1603/ME11073](https://doi.org/10.1603/ME11073)
- **Rydzanicz et al. 2016** (catch basins, Wrocław, Poland): single-day falls of 30.3, 39.1 and 50.6 mm
  were followed by reduced larval development or flushing.
  doi:[10.1007/s00436-016-4912-x](https://doi.org/10.1007/s00436-016-4912-x)
- **Verdict:** for catch basins and stormwater systems, the literature puts near-complete flushing at
  **about 35 mm per event**, with only partial flushing below 25 mm. The model's 25 mm for basins and ponds
  sits at the low end: **SUPPORTED as a partial-flush threshold**. For ditches, drains and streams, **no
  quantitative threshold was found**, so the 10 mm value is an **ASSUMPTION**. The model's all-or-nothing
  reset is also an ASSUMPTION, because the studies above describe partial flushing below about 35 mm.

### c) Habitat: stagnant, organically enriched water, not flowing streams (model: habitat weights)

- **ECDC species factsheet**: the species can breed in clear water but also in water polluted by organic
  matter. Larvae are found in ponds with vegetation and "along river edges in still zones". Note that its
  sentence about larvae mainly in organic-rich, often underground water (catch basins, sewers) describes
  **form *molestus***, not the species as a whole.
  [ECDC factsheet](https://www.ecdc.europa.eu/en/infectious-disease-topics/related-public-health-topics/disease-vectors/facts/mosquito-factsheets/culex-pipiens)
  (last updated 15 June 2020).
- **Gardner et al. 2013** (60 catch basins, Chicago): aquatic ammonia and nitrates were positive predictors
  of larval abundance. doi:[10.1186/1756-3305-6-9](https://doi.org/10.1186/1756-3305-6-9)
- **Rydzanicz et al. 2016**: organically enriched catch basins, with higher Na⁺ and NO₃⁻, were more
  productive (doi above).
- **Crans (Rutgers Center for Vector Biology)**: the species is generally associated with water of high
  organic content, from mildly to grossly polluted.
  [vectorbio.rutgers.edu](https://vectorbio.rutgers.edu/outreach/species/pip2.htm) (undated web page).
- **Ma et al. 2016** (urban rivers, China, *Cx. pipiens pallens*): aquatic vegetation made breeding more
  likely and artificial aeration seemed to prevent it. Slow-moving water may be a marginal habitat.
  doi:[10.1016/j.actatropica.2016.08.010](https://doi.org/10.1016/j.actatropica.2016.08.010)
- **Verdict:** the *ranking* (basins, ponds, ditches and drains high; flowing streams and rivers low) is
  **SUPPORTED**. The individual weights (1.00 … 0.10) are **ASSUMPTIONS**.

### d) Dispersal (model: 300 m exposure buffer)

- **ECDC factsheet**: adults usually stay less than 500 m from their breeding site.
- **Jones et al. 2012**: dispersal was very low. There was marginal support for about 8.8 m/day in 2008 and
  no evidence of dispersal in 2009 (doi above).
- **Hamer et al. 2014** (stable-isotope marking of catch basins, Chicago): mean dispersal distance was
  **1.15 km**, maximum 2.48 km, and 90% of females stayed within 3 km.
  doi:[10.1371/journal.pntd.0002768](https://doi.org/10.1371/journal.pntd.0002768)
- **Verdict:** 300 m is **SUPPORTED as a conservative "most bites happen close by" radius** under the ECDC
  figure. It is well below the mean distance Hamer measured, so risk further out is under-counted.
  **ASSUMPTION.**

### e) Diapause and season (model: 1 March until a 12 h / 15 °C diapause switch)

- **ECDC factsheet**: only mated females overwinter, in frost-free shelters such as cellars and caves.
  Larvae are found from mid-spring until the first frosts. Overwintering females become active again when
  temperature and photoperiod rise.
- **Field et al. 2022** (semi-field studies plus surveillance, US populations): larvae developing at about
  **13.5 h daylight and below about 20 °C** can become diapausing adults. Diapause induction was strong
  (≥50% of females) after week 38, late September, at about 12 h daylight and 15 °C. It varies with
  latitude and altitude. doi:[10.1038/s42003-022-04276-x](https://doi.org/10.1038/s42003-022-04276-x)
- **Rydzanicz et al. 2016** (Wrocław): breeding activity was first detected in early May.
- **ECDC/EFSA 2026**: the earliest symptom onset among locally acquired human WNV cases in 2026 was
  12 May (doi in §2g).
- **Daylight below 13.5 h** (DERIVED from each city's latitude in `cities.py`, geometric day length without
  twilight): Benevento and Coimbra 18–19 Aug, Toulouse 22 Aug, Ghent 29 Aug, Oslo 5 Sep.
- **What the model does.** Development is counted from 1 March. The biting season then ends on the first
  day after midsummer with day length below **12 h** and a 7-day mean below **15 °C** — Field's ≥50%
  induction point, i.e. the day most newly emerging females would be overwintering rather than biting.
  The switch latches until the next spring, and adults already flying keep biting as their cohort decays.
- **Honest reading of the photoperiod term.** With day length measured sunrise to sunset, 12 h falls within
  a couple of days of the autumn equinox at *every* latitude, so that half of the test is effectively
  "after ~21 September" everywhere. The latitude difference between cities comes through the 15 °C test:
  modelled season ends land around 20 Sep in Oslo, late Sep in Ghent and Toulouse, and late Oct/early Nov
  in Coimbra and Benevento.
- **Verdict:** **ASSUMPTION**, and a hard switch where reality is a sliding share of diapausing females
  between Field's two points (13.5 h / 20 °C and 12 h / 15 °C). The critical photoperiod has only been
  verified for **US** populations; the European value is UNVERIFIED. Starting on 1 March is harmless
  because cold March weather banks almost no degree-days.

### f) Air temperature as a proxy for water temperature

The model samples air temperature on a 9-cell grid per place rather than at one point, because a city
spans real temperature variation (1–2 °C in Coimbra, driven largely by elevation). That variation comes
from the weather model's own terrain handling, not from measured stations inside the city, so it captures
terrain but not fine-grained urban heat-island effects.

- **Paaijmans et al. 2010** (Kenya, *Anopheles* pools): mean water temperature in small sunlit breeding
  pools was **4–6 °C above** the air temperature next to them. Development predicted from air temperature
  was therefore too slow. doi:[10.1186/1475-2875-9-196](https://doi.org/10.1186/1475-2875-9-196)
- **Rydzanicz et al. 2016**: water temperature in catch basins ranged from 9.8 to 23.5 °C over the season.
  Shaded and underground water is buffered compared with air.
- **Verdict:** **ASSUMPTION** with a bias that depends on habitat. Sunlit shallow pools are probably warmer
  than air, so the model develops them too slowly. Shaded or underground basins are damped and lag behind
  air temperature. No *Culex*-specific European air-to-water calibration was found.

### g) *Culex pipiens* and West Nile virus in Europe

- **ECDC factsheet**: in Europe, *Cx. pipiens* is considered a major WNV vector. It amplifies the virus
  between birds, acts as a bridge vector from birds to mammals, and serves as a reservoir. **VERIFIED.**
- **The plan's figure of 429 local cases in nine countries is VERIFIED.** ECDC's World Mosquito Day news
  item, published 20 August 2026, says 429 locally acquired WNV cases had been reported in nine European
  countries as of 13 August 2026.
  [ECDC news](https://www.ecdc.europa.eu/en/news-events/world-mosquito-day-2026-europe-must-upgrade-control-tools-disease-risks-rise)
- An earlier ECDC/EFSA monthly report put the total at 245 cases in seven countries as of 5 August 2026,
  with 12 deaths. doi:[10.2903/j.efsa.2026.10304](https://doi.org/10.2903/j.efsa.2026.10304)
- A later figure of "625 cases in 12 countries as of 21 August" appears only second-hand
  ([EU Perspectives](https://euperspectives.eu/2026/08/west-nile-virus-kills-across-europe-the-continent-still-has-no-vaccine/))
  and is UNVERIFIED at ECDC. ECDC's Week 34 threat report counts *areas* (98 areas in 12 countries as of
  21 Aug), not cases.

---

## 3. Brière-1 optimum (derived here)

The Brière-1 rate curve is r(T) = a·T·(T−T₀)·√(T_L−T) for T₀ < T < T_L. It is easiest to maximise ln r:

1. d/dT ln r = 1/T + 1/(T−T₀) − 1/(2(T_L−T)) = 0
2. Multiply by 2T(T−T₀)(T_L−T): 2(T−T₀)(T_L−T) + 2T(T_L−T) − T(T−T₀) = 0
3. Expand and collect terms: −5T² + (4T_L + 3T₀)T − 2T₀T_L = 0, which is 5T² − (4T_L+3T₀)T + 2T₀T_L = 0
4. Solve the quadratic: T = [(4T_L+3T₀) ± √((4T_L+3T₀)² − 40T₀T_L)] / 10.
   The discriminant simplifies to 16T_L² + 9T₀² − 16T₀T_L.
5. Take the + root. The − root is below T₀, which is outside the domain.

**T_opt = (4T_L + 3T₀ + √(16T_L² + 9T₀² − 16T₀T_L)) / 10**, which matches the formula in `model/degree_days.py`.

- Females (T₀ 9.8, T_L 34.2): **T_opt = 28.54 °C**. The − root is 4.70 °C, so it is discarded.
- Males (T₀ 8.4, T_L 34.4): T_opt = 28.51 °C.
- A numerical maximisation (scipy `minimize_scalar`) gives the same values to 4 decimal places.

---

## 4. OneAquaHealth: the Diptera indicator

Both documents are CC BY 4.0 on Zenodo. The page numbers refer to the local PDFs.

**Health Assessment Framework web page**
([oneaquahealth.eu](https://www.oneaquahealth.eu/health-assessment-framework-for-urban-aquatic-ecosystems/)):

> "…with less commonly monitored indicators, including birds, amphibians and adult Diptera, such as mosquitoes."

**Factsheets collection, factsheet X "Diptera Adults", p. 12**
(doi:[10.5281/zenodo.20345207](https://doi.org/10.5281/zenodo.20345207)):

> "Changes in their abundance or distribution can therefore signal shifts in the risk of vector-borne diseases within urban environments."

> "While some taxa (e.g., Culicidae) are already monitored for public-health purposes, other complex but informative groups strengthen ecological assessment."

> "Dipteran adults offer a sensitive early-warning indicators of urban stream health" [sic]

What the indicator measures (paraphrased, p. 12): at least two CO₂-baited traps (for example BG-Pro) per
stream site are run overnight. Specimens are identified under a microscope and reported as richness,
abundance and taxonomic composition. Pathogen screening is optional.

**Field Sampling Protocols** (doi:[10.5281/zenodo.20344421](https://doi.org/10.5281/zenodo.20344421)):

> p. 9, §4.2 Adult Diptera, Aim: "To collect flying Diptera communities, analyze the presence of disease vectors, and analyse their pathogens (causing e.g., Dengue, Zika, West Nile Virus, Usutu and Chikungunya)."

> p. 3, Introduction: "…disease vectors such as mosquitoes and other Diptera in the riparian zone."

**Framing notes for the pitch:**

- "Under-monitored" is supported, because the framework's own wording is "less commonly monitored".
- "Nobody is monitoring it" is **not** supported. The factsheet itself says Culicidae are already
  monitored for public health.
- "Their five tools have no coverage" was **not checked**: UNVERIFIED.
- BiteCast complements the trap protocol. It suggests *where and when* to set traps. It does not replace
  trapping.

---

## 5. Model constants

| Constant (file) | Value | Source | Status |
|---|---|---|---|
| `TB` (degree_days) | 5.5 °C | Loetti 2011, female, linear | VERIFIED |
| `K` (degree_days) | 199.5 DD | Loetti 2011, female, **larva I → adult** | VERIFIED (egg stage of about 25–45 DD not included, DERIVED; no oviposition lag either) |
| `T0`, `TL` (degree_days) | 9.8, 34.2 °C | Loetti 2011, female, Brière | VERIFIED |
| Brière *a* | not used (cancels) | not reported in the accessible text | UNVERIFIED |
| `T_OPT` (degree_days) | 28.54 °C | closed form, §3 | DERIVED |
| `ADULT_TAU` (degree_days) | 7 d, so p ≈ 0.867/day | Jones 2012 (0.904); Matthews 2025 (*Culex* EL 5.3–7.2 d) | SUPPORTED (literature range about 0.83–0.90) |
| `ADULT_DAYS` (degree_days) | 21 d | tail cut-off; 5% of a cohort alive at day 21 when p = 0.867 | ASSUMPTION |
| `SEASON_START_MONTH` (degree_days) | 3 (1 March) | ECDC (larvae from mid-spring) | ASSUMPTION |
| `CRIT_DAYLENGTH` (degree_days) | 12 h, sunrise to sunset | Field 2022, ≥50% induction point | SUPPORTED (US populations); in practice ≈ the equinox at every latitude |
| `DIAPAUSE_T` (degree_days) | 15 °C, 7-day mean | Field 2022, ≥50% induction point | SUPPORTED (US populations) |
| Flush threshold, basin/pond (habitat) | 25 mm/day | Geery & Holub 1989 (<25 mm partial); Gardner 2012 (about 35 mm near total) | SUPPORTED as a partial flush |
| Flush threshold, ditch/drain/stream/river (habitat) | 10 mm/day | no quantitative source found | ASSUMPTION |
| Flush threshold, canal (habitat) | 15 mm/day | no quantitative source found | ASSUMPTION |
| Flush threshold, lake (habitat) | 40 mm/day | no quantitative source found | ASSUMPTION |
| Habitat weights (habitat) | wastewater 1.00, stormwater basin 0.95, pond 0.95, ditch 0.90, drain 0.85, water basin 0.60, canal 0.60, lake 0.40, ornamental pool 0.30, stream 0.25, river 0.10 | ranking supported by ECDC, Gardner 2013, Rydzanicz 2016; ornamental pools low per Ma et al. 2016 (aeration suppresses breeding) | ASSUMPTION (the ranking itself is SUPPORTED) |
| Excluded as habitat (habitat) | fountains, swimming pools, fish/storage tanks, culverted reaches | maintained, chlorinated or recirculated water; culverts are unmapped | ASSUMPTION |
| `THIN_SHAPE` (habitat) | 4πA/P² < 0.2 → channel, not pond | none | ASSUMPTION (shape rule for untyped water polygons) |
| `EXPOSURE_M` (habitat) | 300 m | ECDC (<500 m usually); Hamer 2014 (mean 1.15 km) | ASSUMPTION (conservative) |
| `STAG_TAU` (stagnation) | 7 d | none found | ASSUMPTION |
| Air temperature used as water temperature | — | Paaijmans 2010 (sunlit pools 4–6 °C warmer than air) | ASSUMPTION (bias depends on habitat) |
| Weather grid | 9 cells per city, nearest-cell assignment | measured 1.1–2.2 °C spread across Coimbra (Open-Meteo, elevations 12–271 m) | DERIVED sampling choice; the within-city variation itself is real model output, not observed station data |
| `INTERMITTENT_BONUS`, `EXPOSURE_SCALE`, `EXPOSURE_FLOOR`, `SMALL_WATER_M2` | see code | none | ASSUMPTION |

## 6. Limitations in one paragraph

This is a modelled estimate, not a measurement. Development uses constant-temperature laboratory rates
applied to daily mean *air* temperature. The rates come from a single, probably non-European, population and
cover larva to adult only. Flushing is modelled as all-or-nothing, though the field studies show partial
flushing below about 35 mm. Diapause is a hard on/off switch at Field's 50% point, where reality is a sliding share, and its thresholds come from US populations. The model has not been checked
against trap counts from the five cities. The cross-city latitude gradient is a plausibility check, not a
validation.

## 7. Uncertainty and external validation (added 21 September 2026)

**Uncertainty.** Every label shows the 10th–90th percentile of the score over 40 samples of the
assumptions below, drawn uniformly across the stated ranges and seeded per feature and day so the range is
reproducible. The central score still uses the table in §5 unchanged.

| Assumption | Range sampled | Basis |
|---|---|---|
| Water minus air temperature | 0 to +4 °C | Paaijmans 2010 (sunlit pools +4–6 °C); one-sided because the known bias is warm |
| Thermal constant K | 199.5 × 0.9–1.1 | ASSUMPTION: the Loetti population's origin is UNVERIFIED and probably not European |
| Adult daily survival | 0.83–0.90 | SUPPORTED range, §2a |
| Flush threshold | class value × 0.6–1.6 | only 25 mm is SUPPORTED, §2b |
| Habitat weight | ±0.15 | ASSUMPTION |
| Stagnation time constant | 7 d × 0.5–1.5 | ASSUMPTION (no source) |

The index is normalised by the reference maximum in every sample, so faster development or longer-lived
adults raise it rather than cancelling out.

**Real records (GBIF).** The model's monthly adult index (centre cell, still water, 2024–2025) was
compared with the monthly distribution of *Culex pipiens* occurrence records in GBIF (taxon key 1652991,
2000–2025, georeferenced, presence only) for each city's country. Cached in
`data/validation/gbif_culex_pipiens.json`; reproduced by `python validate_gbif.py`.

| Country (city) | Records | Pearson r | Spearman | Peak month model / records |
|---|---|---|---|---|
| FR (Toulouse) | 1,423 | 0.80 | 0.83 | Aug / Sep |
| BE (Ghent) | 1,964 | 0.63 | 0.79 | Aug / Oct |
| IT (Benevento) | 329 | 0.30 | 0.34 | Aug / Sep |
| PT (Coimbra) | 51 | not judged (< 300 records) | | |
| NO (Oslo) | 58 | not judged (< 300 records) | | |

Caveats: records mix survey effort with mosquito abundance; winter records are hibernating females that
do not bite; the comparison is national against one city; it tests the season's *shape* only. Findings:
the shape agrees where data are sufficient; records peak ~1.3 months after the model (conditions lead
abundance); in Belgium the modelled season likely ends too early. Neither finding was tuned away.

**Water temperature.** Carried in the uncertainty range rather than corrected in the central score,
because no validated air-to-water relationship for European *Culex* habitats was found (§2f).

---

## 8. Species: the model anywhere on earth (added 22 September 2026, model v2.0.0)

v1 applied *Culex pipiens* biology and a European calendar to every place, so a lookup in Karachi read
Karachi's weather through the wrong insect (development "slowing" at 31 °C) and switched breeding off
every January and February at 25 °C. v2 chooses the species per place and gives each its own published
biology. **For the five research cities *Culex pipiens* runs line for line as v1**: a regression test over
12,864 feature-day scores and 50 full labels (score, range, factors, text, actions) found no difference,
and the season rule below gives identical days in all 45 city weather cells.

All sources in this section were read on 22 September 2026, in the table cited.

### a) Which species live at a place (`model/species.py`, `presence.py`)

Two independent kinds of evidence, and neither names a place:

1. **Records nearby.** GBIF occurrence records (2000 to date, `occurrenceStatus=PRESENT`, georeferenced)
   within **250 km** of the point, per species. Taxon keys checked with the GBIF species-match API:
   *Cx. pipiens* 1652991, *Cx. quinquefasciatus* 1652950, *Ae. aegypti* 1651891, *Ae. albopictus* 1651430.
   **VERIFIED** (keys). The radius is an ASSUMPTION. Counts are cached per place in `data/species/`.
2. **Can it survive the winter here?** From the place's own weather: coldest-month mean and annual mean
   over complete years.
   - *Ae. albopictus*: winter mean **> 0 °C** for eggs to overwinter and annual mean **> 11 °C** for adults.
     ECDC species factsheet (updated 20 December 2016). **VERIFIED.**
   - *Ae. aegypti*: no egg diapause, eggs die in frost (ECDC factsheet, updated 2 January 2023, **VERIFIED**);
     distribution roughly bounded by the **10 °C winter isotherm** (Christophers 1960, as cited in several
     reviews). **SUPPORTED**, with a known caveat: it is not an absolute limit
     ([PMC7140351](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7140351/)).

Rules. Fewer than **5** records within 250 km count as *not recorded* (a handful can be misidentifications
or one-off interceptions: ASSUMPTION). A species whose winter test fails is listed as *introduced* and not
modelled when it has 5–49 records (e.g. five *Ae. aegypti* records near Ghent). **Fifty or more records beat
the winter test**: *Ae. aegypti* has "high abundances of well-established populations" in Buenos Aires and
La Plata (Díaz-Nieto et al. 2013, *Geographical limits of the southeastern distribution of Aedes aegypti in
Argentina*, PLoS NTD, [PMC3561174](https://pmc.ncbi.nlm.nih.gov/articles/PMC3561174/), **VERIFIED**), where the coldest month in our weather
averages 9.5 °C, just under the 10 °C isotherm; with 108 records nearby it is modelled there and flagged as
beyond its usual winter limit. The 50 is an ASSUMPTION; that records outrank the rule is SUPPORTED. One
*Culex* is modelled on the mapped water: the one with more records nearby (at least 5); otherwise
*Cx. quinquefasciatus* below 36° latitude, *Cx. pipiens* above 39°, and in between the mild-winter rule. The 36°/39° bands are the North American pipiens/quinquefasciatus
split with a hybrid zone between (Rutgers Center for Vector Biology; microsatellite studies):
**SUPPORTED** for North America, an **ASSUMPTION** elsewhere. The in-between rule (coldest month ≥ 10 °C →
quinquefasciatus, which overwinters without diapause) is an **ASSUMPTION** borrowed from the *Ae. aegypti*
isotherm. A species that passes the winter test with **no** records nearby is modelled as *climate allows*
and the app says so: in under-sampled places (Karachi has 2 records of each within 250 km, though dengue
recurs there every year) this is how the real vectors get modelled at all; elsewhere it is a potential
(Coimbra: *Ae. aegypti*, not established in mainland Portugal).

Chosen at the time of writing: Benevento and Toulouse *Cx. pipiens* + *Ae. albopictus*; Coimbra those plus
*Ae. aegypti* (climate only); Ghent *Cx. pipiens* + *Ae. albopictus* (*Ae. aegypti* introduced); Oslo
*Cx. pipiens* only; Lahore *Cx. quinquefasciatus*, *Ae. aegypti*, *Ae. albopictus*, all recorded (772, 74
and 366 records); Karachi the same three from the climate alone (2, 2 and 0 records); Buenos Aires
*Cx. quinquefasciatus* (climate), *Ae. aegypti* (recorded, beyond the isotherm) and *Ae. albopictus* (climate).

### b) The biology, species by species

Each species runs on its published thermal responses, exactly as printed. Brière: q·T·(T−Tmin)·√(Tmax−T);
quadratic: q·(T−Tmin)·(Tmax−T); both zero outside Tmin..Tmax.

| Species | Development rate (1/day) | Immature survival | Lab adult lifespan (days) | Source | Status |
|---|---|---|---|---|---|
| *Cx. pipiens* | Loetti linear, Tb 5.5 °C, K 199.5 DD, Brière slowdown (§1) | not modelled (as v1) | constant, τ 7 d (§2a) | Loetti 2011 | as §5 |
| *Cx. quinquefasciatus* | Brière q 4.14·10⁻⁵ (95% CI 3.46–5.26·10⁻⁵), Tmin 0.1, Tmax 38.6 (Topt 31.0) | larval pLA, quadratic q 4.26·10⁻³, Tmin 8.9, Tmax 37.7 | linear, 136.3 − 3.80·T | Shocket et al. 2020, eLife, Appendix 1 tables 3 and 6 | **VERIFIED** |
| *Ae. aegypti* | Brière c 7.86·10⁻⁵ (5.75–9.93·10⁻⁵), T0 11.36, Tm 39.17 | egg-to-adult pEA, quadratic c 5.99·10⁻³, T0 13.56, Tm 38.29 | quadratic c 1.48·10⁻¹, T0 9.16, Tm 37.73 | Mordecai et al. 2017, PLoS NTD, S2 Text table B | **VERIFIED** |
| *Ae. albopictus* | Brière c 6.38·10⁻⁵ (4.67–8.23·10⁻⁵), T0 8.60, Tm 39.66 | pEA, quadratic c 3.61·10⁻³, T0 9.04, Tm 39.33 | quadratic c 1.43, T0 13.41, Tm 31.51 | Mordecai et al. 2017, S2 Text table A | **VERIFIED** |

Self-checks reproduce the printed optima (quinquefasciatus development peaks at 30.9 °C against 31.0 printed;
larval survival at 23.3 °C; aegypti pEA 0.915 at its midpoint).

**Field lifetimes.** Lab lifespans are far longer than field ones (the *Ae. albopictus* lab curve peaks near
117 days). So only the lab curve's *shape* is used, scaled to the field expected lifetime of females from
known-age mark–recapture studies: **7.22 d for *Culex* (95% CL 6.26–8.33, n = 18) and 7.92 d for *Aedes*
(5.57–11.2, n = 5)**, Matthews 2025 (**VERIFIED**, abstract). Scaling point 25 °C: ASSUMPTION (a warm-season
recapture day). Floor 0.5 d where the lab curve is zero and cap 2× the field mean in the cold: ASSUMPTIONS.

**How it runs.** The same cohort model as §1 (overlapping generations once the first cohort is through),
with daily adult survival following each day's temperature (`degree_days.cohort`). Each species' index is
normalised by the most it can sustain at its best constant temperature, so "development 100%" means "as
good as it gets for this species" (DERIVED). Indices are comparable within a species across places and
days, not as absolute numbers between species.

### c) Seasons anywhere

The *Cx. pipiens* winter pause now only happens where autumn actually induced diapause: a place whose weekly
mean never falls below 15 °C breeds all year (v1 paused every place from 1 January to 28 February). South
of the equator the season calendar is shifted by 182 days (spring from 1 September). DERIVED from the same
Field 2022 rule; for the research cities the result is identical to v1, day for day.
The other three species have no diapause in the model. For *Ae. albopictus* this is an **ASSUMPTION**: temperate
populations lay diapausing eggs below 13–14 h of daylight (ECDC, **VERIFIED**), but its development and adult
survival already fall to zero around 9–13 °C, so cold ends the season; where winters are mild the model may
keep it active slightly longer than reality.

### d) Container breeders (`model/containers.py`, habitat `load_places`)

*Aedes* lays eggs in buckets, drums, tanks, tyres and plant saucers (ECDC factsheets, **VERIFIED**), which
no map shows. So:

- **Where:** risk is drawn on the mapped homes, schools, parks and playgrounds, with exposure from the same
  formula as the water features. Every such place is assumed to hold containers (habitat 1.0): the honest
  gap, stated in every label. ASSUMPTION.
- **Water in containers** replaces stagnation: a bucket model, `level += (rain − EVAP_SHARE·ET0) / CAPACITY`,
  clipped to 0..1, plus a share `STORED` kept wet by people regardless of rain. ET0 is Hargreaves–Samani
  (FAO-56 eq. 52, radiation from eqs. 21–25), computed from each day's max/min temperature, so no new data
  source is needed: DERIVED from a standard method. CAPACITY 30 mm (sampled 15–60), EVAP_SHARE 0.5
  (0.25–1), STORED 0.3 (0.1–0.6): all **ASSUMPTIONS**, sampled in the uncertainty band. Rain does not
  flush containers (Koenraadt & Harrington 2008 found *Aedes* harder to flush than *Culex*).

This is what makes the dengue mosquito follow the monsoon where drain breeders are flushed by it: in
Karachi the *Ae. aegypti* index peaks in mid-September after the August–September rains, while
*Cx. quinquefasciatus* drops to its low for the year the same week.

### e) Uncertainty for the new species

As §7, but the development-rate constant is sampled across each paper's own 95% interval and the field
lifetime across Matthews' genus interval (both sourced), replacing v1's ±10% K and survival range;
container breeders add the three container assumptions. Species without a winter reset replay the 365 days
before the labelled day. *Cx. pipiens* keeps v1's sampler unchanged.

### f) Checked against local records, species by species

`python validate_gbif.py` now also compares, for every species modelled at a place, GBIF records **within
250 km**, month by month, with the index the map shows (development × stagnation, centre cell, still water
or containers, 2024–2025). Places: the five research cities and the reference place **Lahore** (weather
committed only for this check; not a research city). Judged where there are at least 300 records:

| Place | Species | Records | Pearson r | Peak model / records |
|---|---|---|---|---|
| Toulouse | *Ae. albopictus* | 6,174 | **0.94** | Jul / Aug |
| Benevento | *Ae. albopictus* | 1,492 | **0.87** | Sep / Aug |
| Ghent | *Cx. pipiens* | 3,172 | **0.73** | Aug / Sep |
| Lahore | *Cx. quinquefasciatus* | 771 | **0.65** | Nov / Nov |
| Coimbra | *Ae. albopictus* | 342 | 0.58 | Oct / Sep |
| Lahore | *Ae. albopictus* | 356 | 0.56 | Aug / Sep |

Mean r 0.72. Not judged (< 300 records): Toulouse *Cx. pipiens* 0.85 (193), Ghent *Ae. albopictus* 0.87
(200), Coimbra *Cx. pipiens* 0.78 (99), Oslo *Cx. pipiens* 0.79 (50), Lahore *Ae. aegypti* 0.30 (64).
Local records fit *Cx. pipiens* better than the national ones in §7 (Ghent 0.73 against 0.63). Caveats as
§7, and one more: **Lahore's records bunch in September–November**, when surveys are run for dengue, so its
quiet months are partly months nobody trapped. Karachi has too few records to check (0–2 per species), so
its numbers rest on the biology alone. The check suite fails if any judged pair drops below r = 0.2.

### g) What v2 still does not do

- Container density is unknown: every mapped neighbourhood is assumed to have containers. Water-supply
  interruptions (the main driver of storage in many cities) are not modelled beyond the fixed `STORED` share.
- *Anopheles* (malaria) is not modelled, including the urban invader *An. stephensi*.
- The lab curves come from constant-temperature experiments on a few populations per species.
- No abundance layer: like v1, the index tracks breeding conditions, and records peak a few weeks later.
