/* BiteCast frontend. No build step: MapLibre GL from cdnjs over OpenFreeMap vector tiles, everything else here.
   The map colours each feature by risk = 100 × temporal[series][day] × habitat × exposure, exactly as the API
   documents it (colour is a MapLibre feature-state, so dragging through the year only updates numbers);
   the detail panel always shows the server's own explanation. */
"use strict";

const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
// The theme lives in style.css; the map and the charts read the same colours from it.
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const C = { ink: css("--ink"), ink2: css("--ink-2"), ink3: css("--ink-3"), rule: css("--rule"), rule2: css("--rule-2"),
            bg: css("--bg"), calm: css("--r0"), water: css("--water"), waterLine: css("--water-line"), rain: css("--rain") };
// ±179.9, not ±180: MapLibre 5.6 crashes on a bound of exactly ±180. -75..85 keeps the world tall enough
// that, on a landscape screen, its width is what limits zooming out: no sideways sliding.
const WORLD = [[-179.9, -75], [179.9, 85]];
const BLANK_STYLE = { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": C.bg } }] };
// one red ramp, pale → deep: validated as an ordinal ramp against the light surface
const RAMP = [[0, C.calm], [1, "#e3918e"], [25, "#d76868"], [50, "#c83841"], [75, "#a90021"], [100, "#760618"]];
const CITY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];   // validation chart, south → north
const REGION = (() => { try { return new Intl.DisplayNames(["en"], { type: "region" }); } catch { return null; } })();
const countryName = (cc) => { try { return (cc && REGION?.of(cc.toUpperCase())) || cc || ""; } catch { return cc || ""; } };
const RECENT = "bitecast-recent";   // per-browser convenience only
const store = {
  get: (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const FACTOR_NAMES = {
  development: ["Mosquito growth", "Development: warmth banked by the larvae"],
  stagnation: ["Still water", "Stagnation: days since rain flushed the larvae out"],
  habitat: ["Water type", "Habitat: how likely this water is to stand still"],
  exposure: ["People nearby", "Exposure: homes, parks, playgrounds and schools within 300 m"],
};
const VERDICT = {
  "very high": "You will probably get bitten here after dark.",
  high: "A likely spot for bites after dark.",
  moderate: "Some biting mosquitoes likely around dusk.",
  low: "Few mosquitoes expected from this water.",
};
const REPORT_WINDOW_DAYS = 14;              // matches feedback.MAX_AGE_DAYS on the server
// Place search: Photon (komoot), built on OpenStreetMap for search-as-you-type, which OSM's own Nominatim forbids.
const PHOTON = "https://photon.komoot.io";
const PLACE_LAYERS = ["city", "district", "locality"];   // towns, cities and their districts, not shops or streets
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const PHONE = matchMedia("(max-width: 899px)");
const AUTO_ZOOM = 11;                       // a town fills the screen: load the area by itself
const AUTO_WAIT = 900;                      // ms after the map stops moving
const OPEN_SHARE = 0.8;                     // inside this share of a place's radius, it is the place you're looking at
const PLACE_LABEL = { residential: "residential area", school: "school", park: "park", playground: "playground" };
const SHORT_NAME = { culex_pipiens: "House mosquito", culex_quinquefasciatus: "House mosquito",
                     aedes_aegypti: "Dengue mosquito", aedes_albopictus: "Tiger mosquito" };
const STATUS_LABEL = {
  recorded: "recorded nearby",
  climate: "not recorded nearby; the climate allows it",
  introduced: "recorded, but winters here are too cold for it to settle",
};
// A random per-browser id so one person's second tap replaces their first instead of counting twice.
const CLIENT = (() => {
  try {
    const k = "bitecast-client";
    let v = localStorage.getItem(k);
    if (!v) localStorage.setItem(k, (v = Math.random().toString(36).slice(2) + Date.now().toString(36)));
    return v;
  } catch { return null; }
})();

const $ = (id) => document.getElementById(id);
const S = { cities: [], city: null, place: null, season: null, items: [], byFid: new Map(), reports: [],
            day: 0, year: 0, start: 0, end: 0, mode: "risk", selected: null, token: 0,
            daily: null, view: "world", sheet: "peek", pending: null, loading: false };

/* ---------- small helpers ---------- */
function h(tag, attrs, ...kids) {   // DOM builder; strings become text nodes (OSM names are untrusted)
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || `HTTP ${res.status}`);
  }
  return body;
}
const utc = (iso) => new Date(iso + "T00:00:00Z");
const fmtDate = (iso, o = { weekday: "short", day: "numeric", month: "short", year: "numeric" }) =>
  utc(iso).toLocaleDateString("en-GB", { ...o, timeZone: "UTC" });
const shortDate = (iso) => fmtDate(iso, { day: "numeric", month: "short" });
const dayDate = (iso) => fmtDate(iso, { weekday: "short", day: "numeric", month: "short" });
const coord = (lat, lon) => `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? "N" : "S"} ${Math.abs(lon).toFixed(4)}°${lon >= 0 ? "E" : "W"}`;
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const daysBetween = (a, b) => Math.round((utc(b) - utc(a)) / 86400000);
const band = (r) => (r < 25 ? "low" : r < 50 ? "moderate" : r < 75 ? "high" : "very high");
function distM(a, b) {       // metres between {lat, lon} points; plenty accurate at city scale
  const k = Math.PI / 180, x = (b.lon - a.lon) * k * Math.cos(((a.lat + b.lat) / 2) * k), y = (b.lat - a.lat) * k;
  return Math.hypot(x, y) * 6371000;
}
const ll = ([lat, lon]) => ({ lat, lon });
function color(r) {
  if (r <= 0) return C.rule;
  for (let i = 1; i < RAMP.length; i++) {
    const [r1, c1] = RAMP[i];
    if (r <= r1) {
      const [r0, c0] = RAMP[i - 1];
      const t = (r - r0) / (r1 - r0 || 1);
      const a = parseInt(c0.slice(1), 16), b = parseInt(c1.slice(1), 16);
      const mix = (s) => Math.round(((a >> s) & 255) * (1 - t) + ((b >> s) & 255) * t);
      return `rgb(${mix(16)},${mix(8)},${mix(0)})`;
    }
  }
  return RAMP[RAMP.length - 1][1];
}

/* ---------- the map ---------- */
const map = new maplibregl.Map({
  container: "map", style: BASEMAP, center: [15, 25], zoom: 0,
  renderWorldCopies: false, maxBounds: WORLD,   // one world that fills the map: zoomed out, it doesn't slide
  attributionControl: false, dragRotate: false, pitchWithRotate: false, touchPitch: false, fadeDuration: 120,
});
map.touchZoomRotate.disableRotation();
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
map.addControl(new maplibregl.AttributionControl({ compact: true,
  customAttribution: '<a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo</a> · <a href="https://www.gbif.org/" target="_blank" rel="noopener">GBIF</a>' }), "top-right");
// If the free basemap can't be reached, carry on over a plain background: our own layers still work.
let styleFallback = setTimeout(() => { if (!map.isStyleLoaded()) map.setStyle(BLANK_STYLE); }, 12000);
const mapReady = new Promise((ok) => map.once("style.load", () => { clearTimeout(styleFallback); ok(); }));
// Credits stay one tap away instead of covering the map; MapLibre unfolds them as data arrives, so fold
// them whenever the map settles, until the reader opens them.
const foldCredits = () => {
  const a = document.querySelector(".maplibregl-ctrl-attrib");
  a?.classList.remove("maplibregl-compact-show");
  a?.removeAttribute("open");
};
map.on("idle", foldCredits);
document.querySelector(".maplibregl-ctrl-attrib-button")?.addEventListener("click", () => map.off("idle", foldCredits), { once: true });
const farOut = () => document.body.classList.toggle("far", map.getZoom() < 9);
map.on("zoom", farOut);
farOut();
map.on("error", (e) => console.warn("map:", e?.error?.message || e));


const R = ["coalesce", ["feature-state", "r"], 0];
const RAMP_EXPR = ["interpolate", ["linear"], R, ...RAMP.flat()];
// shapes appear as you reach street level; below it, each spot is a dot sized by risk
const shapes = (v) => ["interpolate", ["linear"], ["zoom"], 10.4, 0, 11.4, v];
const IS_LINE = ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString"]]];
const IS_POLY = ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]];
const IS_POINT = ["in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]]];
const EMPTY = { type: "FeatureCollection", features: [] };

function styleBasemap() {
  // only water carries colour: the pale basemap's water drawn a still-water teal, parks a trace of green
  for (const [layer, prop, value] of [["water", "fill-color", C.water], ["waterway", "line-color", C.waterLine],
                                      ["park", "fill-color", "#e2eadf"]]) {
    try { if (map.getLayer(layer)) map.setPaintProperty(layer, prop, value); } catch { /* a restyled basemap */ }
  }
}

function addLayers() {
  styleBasemap();
  const labels = map.getStyle().layers.find((l) => l.type === "symbol")?.id;   // keep street names on top
  for (const id of ["feat", "dots", "reports", "places"]) map.addSource(id, { type: "geojson", data: EMPTY });
  const add = (layer, before = labels) => map.addLayer(layer, before);
  add({ id: "feat-fill", type: "fill", source: "feat", filter: IS_POLY,
        paint: { "fill-color": RAMP_EXPR, "fill-opacity": shapes(["interpolate", ["linear"], R, 0, 0.18, 100, 0.68]) } });
  add({ id: "feat-halo", type: "line", source: "feat", filter: IS_LINE, layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": RAMP_EXPR, "line-blur": 3,
                 "line-width": ["interpolate", ["linear"], ["zoom"], 12, ["+", 3, ["*", R, 0.12]], 17, ["+", 8, ["*", R, 0.34]]],
                 "line-opacity": shapes(["interpolate", ["linear"], R, 0, 0, 15, 0.05, 100, 0.36]) } });
  add({ id: "feat-sel", type: "line", source: "feat", filter: ["!", IS_POINT], layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": C.ink, "line-blur": 1,
                 "line-width": ["interpolate", ["linear"], ["zoom"], 11, 6, 17, 14],
                 "line-opacity": ["case", ["boolean", ["feature-state", "sel"], false], 0.32, 0] } });
  add({ id: "feat-line", type: "line", source: "feat", filter: IS_LINE, layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": RAMP_EXPR,
                 "line-width": ["interpolate", ["exponential", 1.4], ["zoom"], 11, ["+", 0.7, ["*", R, 0.02]],
                                15, ["+", 1.6, ["*", R, 0.06]], 19, ["+", 4, ["*", R, 0.15]]],
                 "line-opacity": shapes(["interpolate", ["linear"], R, 0, 0.5, 100, 1]) } });
  add({ id: "feat-edge", type: "line", source: "feat", filter: IS_POLY,
        paint: { "line-color": RAMP_EXPR, "line-width": 1.2, "line-opacity": shapes(0.85) } });
  add({ id: "feat-point", type: "circle", source: "feat", filter: IS_POINT,
        paint: { "circle-color": RAMP_EXPR, "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 3, 16, 7],
                 "circle-stroke-color": "#ffffff", "circle-stroke-width": 1,
                 "circle-opacity": shapes(0.9), "circle-stroke-opacity": shapes(0.9) } });
  // thin water is hard to tap: an invisible, wide line takes the clicks
  add({ id: "feat-hit", type: "line", source: "feat", filter: ["!", IS_POINT],
        paint: { "line-color": "#000000", "line-width": 18, "line-opacity": 0.001 } });
  const dotVis = ["interpolate", ["linear"], R, 0, 0.3, 100, 0.92];
  map.addLayer({ id: "dots", type: "circle", source: "dots",
    paint: { "circle-color": RAMP_EXPR, "circle-stroke-width": 0,
             "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, ["+", 1.5, ["*", ["sqrt", R], 0.35]],
                               11.6, ["+", 2.5, ["*", ["sqrt", R], 1.1]]],
             "circle-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0, 7, dotVis, 10.6, dotVis, 11.6, 0] } });
  map.addLayer({ id: "reports", type: "circle", source: "reports",
    paint: { "circle-radius": 6, "circle-stroke-width": 2,
             "circle-color": ["case", ["get", "bitten"], C.ink, "#ffffff"],
             "circle-stroke-color": ["case", ["get", "bitten"], "#ffffff", C.ink] } });
  map.addLayer({ id: "places", type: "circle", source: "places", maxzoom: 10,
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 4.5, 8, 7.5], "circle-stroke-width": 2,
             "circle-color": "#c83841", "circle-stroke-color": "#ffffff" } });

  const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, className: "tip" });
  const hover = (layer, text) => {
    map.on("mousemove", layer, (e) => {
      map.getCanvas().style.cursor = "pointer";
      const t = text(e.features[0]);
      if (t) tip.setLngLat(e.lngLat).setText(t).addTo(map);
    });
    map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; tip.remove(); });
  };
  for (const layer of ["feat-hit", "feat-fill", "feat-point", "dots"]) {
    map.on("click", layer, (e) => { e.preventDefault?.(); select(e.features[0].id, { fly: layer === "dots" }); });
    hover(layer, (f) => { const it = S.items[f.id]; return it && `${nameOf(it.p)} · ${it.r ?? 0}`; });
  }
  map.on("click", "places", (e) => { const k = e.features[0].properties.key; if (k) loadCity(k); });
  hover("places", (f) => f.properties.label);
  map.on("click", "reports", (e) => { const it = S.byFid.get(e.features[0].properties.fid); if (it) select(it.i); });
  hover("reports", (f) => `Bite reports: ${f.properties.bad} bitten, ${f.properties.fine} fine`);
}

function uiPadding() {
  if (PHONE.matches) return { top: 130, bottom: sheetPx() + 20, left: 24, right: 24 };
  const tl = $("timeline").hidden ? 0 : $("timeline").offsetHeight;
  return { top: 140, bottom: tl + 40, left: 48, right: 64 };     // the map starts beside the panel
}
function glideTo(opts) {   // fly the way a web map does; instant for readers who asked for less motion
  map.stop();
  if (REDUCED) map.jumpTo(opts.center ? opts : { center: opts.center, zoom: opts.zoom });
  map.flyTo({ ...opts, padding: uiPadding(), duration: REDUCED ? 0 : 1500, essential: false });
}
function fitPlace(p) {
  const dLat = (p.radius_m * 0.85) / 111320, dLon = dLat / Math.cos((p.lat * Math.PI) / 180);
  map.stop();
  map.fitBounds([[p.lon - dLon, p.lat - dLat], [p.lon + dLon, p.lat + dLat]],
                { padding: uiPadding(), duration: REDUCED ? 0 : 1400, maxZoom: 14.5 });
}

/* ---------- views: world, place, detail ---------- */
function showView(v) {
  S.view = v;
  for (const id of ["world", "place", "detail"]) $(`view-${id}`).hidden = id !== v;
  document.body.dataset.view = v;
  $("timeline").hidden = v === "world";
  $("panel-scroll").scrollTop = 0;
  if (PHONE.matches) setSheet(v === "detail" ? "half" : "peek");
}

/* ---------- a place ---------- */
const nameOf = (p) => p.name || cap((p.label || PLACE_LABEL[p.kind] || S.season?.classes?.[p.cls]?.label || "spot")
  .replace(/ \(satellite\)$/, ""));
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
function whereOf([lat, lon]) {
  const c = S.place;
  if (!c) return "";
  const m = distM(c, { lat, lon });
  if (m < 150) return "at the centre";
  const k = Math.PI / 180;
  const y = Math.sin((lon - c.lon) * k) * Math.cos(lat * k);
  const x = Math.cos(c.lat * k) * Math.sin(lat * k) - Math.sin(c.lat * k) * Math.cos(lat * k) * Math.cos((lon - c.lon) * k);
  const dir = COMPASS[Math.round(((Math.atan2(y, x) / k + 360) % 360) / 45) % 8];
  return `${m < 1000 ? `${Math.round(m / 50) * 50} m` : `${(m / 1000).toFixed(1)} km`} ${dir} of centre`;
}

async function loadCity(key, date, fid, species, { move = true } = {}) {
  const token = ++S.token;
  S.loading = true;
  const known = S.cities.find((c) => c.key === key);
  setStatus(`Loading ${known?.name || "this place"}…`, true);
  const q = species ? `?species=${encodeURIComponent(species)}` : "";
  let season, feats, reports;
  try {
    [season, feats, reports] = await Promise.all([
      api(`/api/cities/${key}/season${q}`), api(`/api/cities/${key}/features${q}`), api(`/api/feedback?city=${key}`)]);
  } catch (e) {
    S.loading = false;
    if (species && token === S.token) return loadCity(key, date, fid, null, { move });   // a species not modelled here
    if (token === S.token) setStatus("Couldn't load this place. Try it again in a moment.");
    return;
  }
  await mapReady;
  if (token !== S.token) return;                 // a newer click won

  const moved = key !== S.city || S.view === "world";
  S.city = key; S.season = season; S.reports = reports; S.species = season.species;
  S.place = { ...(known || {}), ...season, key };
  S.selected = null;
  store.set(RECENT, [key, ...store.get(RECENT, []).filter((k) => k !== key)].slice(0, 6));

  // features: MapLibre wants numeric ids for feature-state; the OSM id stays as fid
  S.items = feats.features.map((f, i) => { const fid = String(f.id); f.id = i; return { i, fid, p: f.properties, f, r: -1 }; });
  S.byFid = new Map(S.items.map((it) => [it.fid, it]));
  for (const src of ["feat", "dots"]) map.removeFeatureState({ source: src });
  map.getSource("feat").setData(feats);
  map.getSource("dots").setData({ type: "FeatureCollection", features: S.items.map((it) => ({
    type: "Feature", id: it.i, properties: {}, geometry: { type: "Point", coordinates: [it.p.centroid[1], it.p.centroid[0]] } })) });
  const homes = season.breeds === "containers";   // neighbourhoods can be km across: a light wash, not a block
  map.setPaintProperty("feat-fill", "fill-opacity",
    shapes(["interpolate", ["linear"], R, 0, homes ? 0.04 : 0.18, 100, homes ? 0.34 : 0.68]));

  S.index = new Map(season.dates.map((d, i) => [d, i]));
  S.todayIdx = S.index.get(season.today);
  prepare();
  drawPlaces();
  drawPlaceHeader();
  drawSpecies();
  drawReports();
  showFreshness();
  showView("place");
  if (move && moved && !(fid && S.byFid.has(fid))) fitPlace(S.place);

  const want = date && S.index.has(date) ? S.index.get(date) : S.todayIdx;
  buildYears();
  setYear(Number(season.dates[want].slice(0, 4)), want);
  S.loading = false;
  if (season.map_ready === false) waitForMap(key);   // the place is open; its water is still coming
  else setStatus(S.items.length ? "" : homes ? "No mapped homes, schools or parks here." : "No mapped water here.");
  if (fid && S.byFid.has(fid)) select(S.byFid.get(fid).i, { fly: true });
}

// Each feature's daily series and multiplier, then the whole place's "worst spots" value for every day —
// the number the season chart and the "tonight" line show, in the same units as the map.
function prepare() {
  const T = S.season.temporal;
  for (const it of S.items) {
    it.series = T[`f:${it.fid}`] || T[`${it.p.cell ?? 0}:${it.p.cls}`] || T[`0:${it.p.cls}`] || null;
    it.mult = 100 * it.p.habitat * (S.mode === "risk" ? it.p.exposure : 1);
    it.r = -1;
  }
  const n = S.season.dates.length, daily = new Float32Array(n), live = S.items.filter((it) => it.series);
  if (!live.length) {            // water still loading: breeding conditions in still water stand in
    for (let i = 0; i < n; i++) daily[i] = 100 * (S.season.strip[i] || 0);
  } else {
    const top = new Float32Array(5), k = Math.min(5, live.length);
    for (let i = 0; i < n; i++) {
      top.fill(0);
      for (const it of live) {
        const r = it.series[i] * it.mult;
        if (r <= top[4]) continue;
        let j = 4;
        while (j > 0 && top[j - 1] < r) { top[j] = top[j - 1]; j--; }
        top[j] = r;
      }
      let sum = 0; for (let j = 0; j < k; j++) sum += top[j];
      daily[i] = sum / k;
    }
  }
  S.daily = daily;
}

// A new place opens on its weather (season, forecast, species) in a few seconds; the water comes from the
// free public map servers, which are the slow part. Poll until it lands.
let mapPoll = 0;
function waitForMap(key) {
  clearTimeout(mapPoll);
  const token = S.token, name = S.place?.name || "this place";
  const t0 = Date.now(), what = S.season?.breeds === "containers" ? "homes and neighbourhoods" : "water";
  const early = S.items.length > 0;               // the satellite layers are on the map already
  const say = () => setStatus(early
    ? `Showing what satellites see around ${name}; adding the mapped streams and drains…`
    : Date.now() - t0 < 12000 ? `Mapping the ${what} around ${name}…`
    : `Mapping the ${what} around ${name}. The first visit to a new place can take up to a minute; after that it opens instantly.`, true);
  say();
  const clock = setInterval(() => (token === S.token && $("status").classList.contains("busy") ? say() : clearInterval(clock)), 1000);
  const tick = async () => {
    if (token !== S.token) return clearInterval(clock);
    let p;
    try { p = await api(`/api/cities/${encodeURIComponent(key)}`); } catch { p = null; }
    if (token !== S.token) return clearInterval(clock);
    if (p?.map?.stage === "ready") {
      clearInterval(clock);
      return loadCity(key, S.season.dates[S.day], S.selected != null ? S.items[S.selected]?.fid : null, S.species, { move: false });
    }
    if (p?.map?.stage === "error") {
      clearInterval(clock);
      console.warn("map unavailable:", p.map.error);
      return setStatus(early ? `Showing water seen by satellite around ${name}; the street map couldn't be fetched right now.`
                             : `Couldn't map the ${what} around ${name} right now. Try again in a minute.`);
    }
    if (p?.map?.satellite && !early) {            // satellite layers in: show them now, keep waiting for the map
      clearInterval(clock);
      return loadCity(key, S.season.dates[S.day], null, S.species, { move: false });
    }
    mapPoll = setTimeout(tick, 2500);
  };
  mapPoll = setTimeout(tick, 2500);
}

function drawPlaceHeader() {
  const p = S.place;
  $("p-country").textContent = p.country ? countryName(p.country) : "";
  $("p-name").textContent = p.name;
  $("fhir-place").href = `/api/cities/${p.key}/fhir?limit=20&species=${encodeURIComponent(S.species)}`;
}

function drawSpecies() {
  const modelled = (S.season.species_list || []).filter((s) => s.modelled);
  const ev = S.season.species_evidence || {};
  $("species").replaceChildren(...modelled.map((s) => h("button", {
      type: "button", "aria-pressed": String(s.key === S.species),
      title: `${s.name}: ${s.edge ? "recorded nearby in numbers, though winters are at the edge of its range" : STATUS_LABEL[s.status]}`,
      onclick: () => s.key !== S.species && loadCity(S.city, S.season.dates[S.day], null, s.key, { move: false }) },
    SHORT_NAME[s.key] || cap(s.common), h("small", {}, s.status === "climate" ? "climate fit" : "recorded"))));
  const here = modelled.find((s) => s.key === S.species);
  $("species-note").textContent = here
    ? (here.breeds === "containers" ? "Breeds in buckets and tanks around homes; bites by day."
                                    : "Breeds in drains, ditches and ponds; bites at dusk and night.")
      + (here.status === "climate" ? " No records within 250 km yet, but the climate suits it." : " Recorded within 250 km.")
      + (ev.gbif_fetched ? "" : " Chosen from the climate: mosquito records were unavailable.")
    : "";
}

function setStatus(msg, busy = false) {
  const el = $("status");
  el.textContent = msg;
  el.classList.toggle("busy", Boolean(msg && busy));
}

/* ---------- time ---------- */
function buildYears() {
  const years = [...new Set(S.season.dates.map((d) => d.slice(0, 4)))]
    .filter((y) => S.season.dates.filter((d) => d.startsWith(y)).length > 60);
  $("years").replaceChildren(...years.map((y) =>
    h("button", { type: "button", class: "chip", "data-year": y, "aria-pressed": "false", onclick: () => setYear(Number(y)) }, y)));
}
function setYear(y, dayIdx) {
  const d = S.season.dates;
  S.year = y;
  S.start = d.findIndex((x) => x.startsWith(String(y)));
  let e = S.start; while (e + 1 < d.length && d[e + 1].startsWith(String(y))) e++;
  S.end = e;
  for (const b of $("years").children) b.setAttribute("aria-pressed", String(b.dataset.year === String(y)));
  const slider = $("slider");
  slider.min = S.start; slider.max = S.end;
  let keep = dayIdx;
  if (keep == null) {           // same day of the year, in the year chosen
    const md = d[S.day]?.slice(5) || "06-01";
    keep = S.index.get(`${y}-${md}`) ?? (S.todayIdx >= S.start && S.todayIdx <= S.end ? S.todayIdx : S.end);
  }
  setDay(Math.min(Math.max(keep, S.start), S.end));
}

let raf = 0;
function setDay(i) {
  S.day = i;
  $("slider").value = i;
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(render);
  if (document.hidden) render();      // a background tab never draws a frame; keep the state right anyway
}

function render() {
  cancelAnimationFrame(raf);
  if (!S.season || S.view === "world") return;
  const i = S.day;
  for (const it of S.items) {
    const r = it.series ? Math.round(it.series[i] * it.mult) : 0;
    if (r === it.r) continue;           // only what changed: dragging through the year stays smooth
    it.r = r;
    map.setFeatureState({ source: "feat", id: it.i }, { r });
    map.setFeatureState({ source: "dots", id: it.i }, { r });
  }
  const iso = S.season.dates[i];
  $("slider").setAttribute("aria-valuetext", `${fmtDate(iso)}: ${band(S.daily[i])} at the worst spots`);
  drawTonight();
  renderHotspots();
  drawTimeline();
  writeHash();
  if (S.selected != null) refreshLabel();
}

function dayWord(i) {
  const iso = S.season.dates[i];
  return i === S.todayIdx ? "Tonight" : i > S.todayIdx ? `Forecast for ${dayDate(iso)}` : dayDate(iso);
}
function drawTonight() {
  const v = S.daily[S.day], waiting = !S.items.length && S.season.map_ready === false;
  $("tonight-sw").style.background = color(v);
  $("tonight-big").textContent = waiting ? "Drawing the water…" : v < 1 ? `${dayWord(S.day)}: quiet` : `${dayWord(S.day)}: ${band(v)} risk`;
  const sp = (S.season.species_list || []).find((s) => s.key === S.species);
  $("tonight-small").textContent = waiting ? "The season below is breeding conditions in still water until it lands."
    : v < 1 ? "No biting mosquitoes expected from the water here."
    : `at the worst spots${sp?.bites ? `; this mosquito bites ${sp.bites}` : ""}.`;
  if (PHONE.matches && S.sheet === "peek") setSheet("peek");   // the peek fits the line, whatever its length
}

function renderHotspots() {
  const iso = S.season.dates[S.day];
  $("hot-when").textContent = S.day === S.todayIdx ? "tonight" : `on ${shortDate(iso)}${S.day > S.todayIdx ? " (forecast)" : ""}`;
  // five different areas, not five pieces of one pond: skip spots within 200 m of one already listed
  const top = [];
  for (const it of S.items.filter((x) => x.r > 0).sort((a, b) => b.r - a.r)) {
    if (top.length === 5) break;
    if (!top.some((t) => distM(ll(t.p.centroid), ll(it.p.centroid)) < 200)) top.push(it);
  }
  drawRanks(top);
  $("hotspots").replaceChildren(...(top.length ? top.map((it, n) => h("li", {},
    h("button", { type: "button", class: "hot-row", onclick: () => select(it.i, { fly: true }) },
      h("span", { class: "rk" }, String(n + 1)),
      h("span", {},
        h("span", { class: "nm" }, nameOf(it.p)),
        h("span", { class: "mt" }, [it.p.name && (PLACE_LABEL[it.p.kind] || S.season.classes[it.p.cls]?.label),
                                    whereOf(it.p.centroid)].filter(Boolean).join(" · "))),
      h("span", { class: "sc", style: `color:${color(Math.max(it.r, 30))}` }, String(it.r)))))
    : [h("li", { class: "empty" }, !S.items.length
        ? (S.season.map_ready === false ? "The water is still loading." : "No mapped water here.")
        : "No mosquito activity expected on this day. Try another week in the season chart.")]));
}

let rankMarkers = [];
function drawRanks(top) {
  const key = top.map((it) => it.i).join(",");
  if (key === drawRanks.last) return;
  drawRanks.last = key;
  rankMarkers.forEach((m) => m.remove());
  rankMarkers = top.map((it, n) => {
    const el = h("div", { class: "rank", title: `${n + 1}. ${nameOf(it.p)}, ${whereOf(it.p.centroid)}` }, String(n + 1));
    el.addEventListener("click", (e) => { e.stopPropagation(); select(it.i); });
    return new maplibregl.Marker({ element: el }).setLngLat([it.p.centroid[1], it.p.centroid[0]]).addTo(map);
  });
}

/* ---------- the season chart: one bar per week, worst spots, map colours ---------- */
function weeksOfYear() {
  const y = S.year, jan1 = Date.UTC(y, 0, 1), days = Math.round((Date.UTC(y + 1, 0, 1) - jan1) / 864e5);
  const idx = new Int32Array(days).fill(-1);
  for (let doy = 0; doy < days; doy++) {
    const i = S.index.get(new Date(jan1 + doy * 864e5).toISOString().slice(0, 10));
    if (i != null) idx[doy] = i;
  }
  const weeks = [];
  for (let w = 0; w * 7 < days; w++) {
    let sum = 0, n = 0, rain = 0, future = false;
    for (let doy = w * 7; doy < Math.min(days, w * 7 + 7); doy++) {
      const i = idx[doy];
      if (i < 0) continue;
      sum += S.daily[i]; n++; rain += S.season.precip[i] || 0;
      if (i > S.todayIdx) future = true;
    }
    weeks.push({ w, v: n ? sum / n : null, rain, future, first: w * 7 });
  }
  return { days, idx, weeks };
}

function summarise(weeks) {
  const known = weeks.filter((x) => x.v != null && !x.future);
  const pool = known.length > 8 ? known : weeks.filter((x) => x.v != null);
  const max = Math.max(0, ...pool.map((x) => x.v));
  const when = (doy, end) => {
    const d = new Date(Date.UTC(S.year, 0, 1) + (doy + (end ? 6 : 0)) * 864e5), day = d.getUTCDate();
    return `${day <= 10 ? "early " : day > 20 ? "late " : ""}${d.toLocaleDateString("en-GB", { month: "long", timeZone: "UTC" })}`;
  };
  let peak;
  if (max < 25) peak = "A quiet year here: risk stays low.";
  else {
    const line = max >= 50 ? 50 : 25, hot = pool.filter((x) => x.v >= line);
    const a = when(hot[0].first), b = when(hot.at(-1).first, true);
    peak = `${line === 50 ? "Worst" : "Busiest"} from ${a} to ${b}${line === 25 ? ", moderate at most" : ""}.`;
  }
  if (S.todayIdx < S.start || S.todayIdx > S.end) return peak;
  const now = S.daily[S.todayIdx], before = S.daily[Math.max(0, S.todayIdx - 7)];
  const trend = now - before > 5 ? ", rising" : before - now > 5 ? ", falling" : "";
  return `${peak} Now: ${now < 1 ? "quiet" : band(now)}${trend}.`;
}

function drawTimeline() {
  const svg = $("tl-svg");
  const W = Math.max(260, Math.round($("tl-chart").clientWidth || 600));
  const H = PHONE.matches ? 112 : 120;
  const { days, idx, weeks } = weeksOfYear();
  const base = H - 30, top = 24, maxH = base - top, slot = W / weeks.length, bw = Math.max(2, slot * 0.66);
  const X = (doy) => (doy / days) * W;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("height", H);
  svg.replaceChildren();
  const defs = svgEl("defs", {});
  const pat = svgEl("pattern", { id: "tl-hatch", width: 5, height: 5, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
  pat.append(svgEl("rect", { width: 1.6, height: 5, fill: C.ink3, opacity: 0.35 }));
  defs.append(pat); svg.append(defs);

  // the forecast: from today to the last forecast day, shaded behind the bars
  const doyOf = (i) => idx.indexOf(i);
  const tDoy = doyOf(S.todayIdx), lastDoy = doyOf(S.end);
  if (tDoy >= 0 && lastDoy > tDoy) {
    svg.append(svgEl("rect", { x: X(tDoy + 0.5), y: top - 4, width: X(lastDoy + 1) - X(tDoy + 0.5), height: maxH + 4, fill: "url(#tl-hatch)" }));
  }
  const selDoy = doyOf(S.day), selWeek = Math.floor(selDoy / 7);
  for (const wk of weeks) {
    const x = wk.w * slot + (slot - bw) / 2;
    if (wk.v == null) {                          // not known yet
      svg.append(svgEl("rect", { x, y: base - 5, width: bw, height: 5, rx: 1.5, fill: "none", stroke: C.calm, "stroke-width": 0.8 }));
      continue;
    }
    const hgt = Math.max(2.5, (wk.v / 100) * maxH);
    const bar = svgEl("rect", { x, y: base - hgt, width: bw, height: hgt, rx: Math.min(2, bw / 3), fill: color(wk.v),
                                opacity: wk.future ? 0.55 : 1 });
    if (wk.w === selWeek) { bar.setAttribute("stroke", C.ink); bar.setAttribute("stroke-width", 1.5); }
    bar.append(svgEl("title", {}, `Week of ${shortDate(new Date(Date.UTC(S.year, 0, 1) + wk.first * 864e5).toISOString().slice(0, 10))}: `
      + `${wk.v < 1 ? "quiet" : band(wk.v)} (${Math.round(wk.v)})${wk.future ? ", forecast" : ""} · ${Math.round(wk.rain)} mm rain`));
    svg.append(bar);
    if (wk.rain >= 1) svg.append(svgEl("rect", { x: x + bw * 0.2, y: base + 3, width: bw * 0.6, height: Math.max(1, (Math.min(wk.rain, 60) / 60) * 11),
                                                  rx: 1, fill: C.rain, opacity: wk.future ? 0.55 : 0.9 }));
  }
  svg.append(svgEl("line", { x1: 0, x2: W, y1: base + 0.5, y2: base + 0.5, stroke: C.rule, "stroke-width": 1 }));
  for (let m = 0; m < 12; m++) {                   // month names along the bottom
    const doy = Math.round((Date.UTC(S.year, m, 1) - Date.UTC(S.year, 0, 1)) / 864e5);
    if (PHONE.matches && m % 2) continue;
    svg.append(svgEl("text", { x: X(doy) + 1, y: H - 2, "font-size": 11, fill: C.ink3, "font-family": "Archivo, sans-serif" }, MONTHS[m]));
  }
  if (tDoy >= 0 && S.day !== S.todayIdx) {         // today, when the reader has moved away from it
    const x = X(tDoy + 0.5);
    svg.append(svgEl("line", { x1: x, x2: x, y1: top - 4, y2: base, stroke: C.ink2, "stroke-width": 1, "stroke-dasharray": "3 2" }));
    svg.append(svgEl("text", { x: x + 3, y: top + 6, "font-size": 11, fill: C.ink2, "font-family": "Archivo, sans-serif" }, "today"));
  }
  if (selDoy >= 0) {                               // the day the map shows: a line and a label
    const x = X(selDoy + 0.5), v = S.daily[S.day];
    svg.append(svgEl("line", { class: "sel-line", x1: x, x2: x, y1: 20, y2: base, stroke: C.ink, "stroke-width": 1.5 }));
    const text = `${dayDate(S.season.dates[S.day])} · ${v < 1 ? "quiet" : band(v)}${S.day === S.todayIdx ? " · today" : S.day > S.todayIdx ? " · forecast" : ""}`;
    const tw = text.length * 6.3 + 18, bx = Math.min(Math.max(x - tw / 2, 0), W - tw);
    svg.append(svgEl("rect", { x: bx, y: 0, width: tw, height: 20, rx: 10, fill: C.ink }));
    svg.append(svgEl("text", { x: bx + tw / 2, y: 14, "text-anchor": "middle", "font-size": 11.5, "font-weight": 600,
                               fill: "#ffffff", "font-family": "Archivo, sans-serif" }, text));
  }

  const sp = (S.season.species_list || []).find((s) => s.key === S.species);
  $("tl-title").textContent = `Mosquito season in ${S.place.name}, ${S.year}`;
  $("tl-sub").textContent = `${SHORT_NAME[S.species] || cap(sp?.common || "mosquito")}`
    + ` · ${S.mode === "risk" ? "bite risk" : "breeding"} at the worst spots, week by week`;
  $("tl-sum").textContent = summarise(weeks);
}

// Drag or tap anywhere on the chart to move the map through the year
(() => {
  const chart = $("tl-chart");
  const dayAt = (clientX) => {
    const r = chart.getBoundingClientRect(), { days, idx } = weeksOfYear();
    let doy = Math.min(days - 1, Math.max(0, Math.floor(((clientX - r.left) / r.width) * days)));
    while (doy > 0 && idx[doy] < 0) doy--;          // past the last known day: stop at it
    while (doy < days - 1 && idx[doy] < 0) doy++;   // before the first: start at it
    return idx[doy];
  };
  chart.addEventListener("pointerdown", (e) => {
    if (!S.season) return;
    chart.setPointerCapture(e.pointerId);
    const i = dayAt(e.clientX); if (i >= 0) setDay(i);
  });
  chart.addEventListener("pointermove", (e) => {
    if (!chart.hasPointerCapture(e.pointerId)) return;
    const i = dayAt(e.clientX); if (i >= 0 && i !== S.day) setDay(i);
  });
  new ResizeObserver(() => S.season && S.view !== "world" && drawTimeline()).observe(chart);
})();

/* ---------- selecting a spot: the specimen label ---------- */
function select(i, { fly = false } = {}) {
  const it = S.items[i];
  if (!it) return;
  if (S.selected != null) map.setFeatureState({ source: "feat", id: S.selected }, { sel: false });
  S.selected = i;
  map.setFeatureState({ source: "feat", id: i }, { sel: true });
  $("back-to").textContent = S.place.name;
  $("detail-body").replaceChildren(h("p", { class: "note" }, "Loading…"));
  showView("detail");
  if (fly) glideTo({ center: [it.p.centroid[1], it.p.centroid[0]], zoom: Math.max(map.getZoom(), 15) });
  refreshLabel(true);
  writeHash();
}

function closeDetail() {
  if (S.selected != null) map.setFeatureState({ source: "feat", id: S.selected }, { sel: false });
  S.selected = null;
  if (S.season) showView("place");
  writeHash();
}

let labelTimer = 0, labelSeq = 0;
function refreshLabel(now) {
  clearTimeout(labelTimer);
  labelTimer = setTimeout(async () => {
    const seq = ++labelSeq, it = S.items[S.selected], iso = S.season.dates[S.day], city = S.city;
    if (!it) return;
    try {
      const e = await api(`/api/cities/${city}/explain?feature=${encodeURIComponent(it.fid)}&date=${iso}`
                          + `&species=${encodeURIComponent(S.species)}`);
      if (seq === labelSeq) $("detail-body").replaceChildren(...labelBody(e).filter(Boolean));
    } catch (err) {
      if (seq === labelSeq) $("detail-body").replaceChildren(h("p", { class: "note" }, `Couldn't load this spot: ${err.message}`));
    }
  }, now ? 0 : 160);
}

function verdict(e) {
  if (e.verdict) return e.verdict;
  if (e.factors.development === 0) {
    return e.facts?.next_emergence ? `Quiet for now: the next batch of adults is due ${shortDate(e.facts.next_emergence)}.`
                                   : "Quiet here: nothing is growing in this water right now.";
  }
  return VERDICT[e.band];
}

function bandStyle(r) {   // the band pill in the map's own colour for this score, text readable on it
  return `background:${color(Math.max(r, 1))};color:${r >= 50 ? "#fff" : C.ink}`;
}

function labelBody(e) {
  const p = S.place;
  const [lat, lon] = e.centroid;
  const text = e.text.replace(/^[^.]*\(\d+\/100\)\.\s*/, "").replace(/\s*Modelled estimate, not a field measurement\.$/, "");
  const here = S.reports.filter((r) => r.feature_id === e.feature_id);
  const bitten = here.filter((r) => r.bad).length;
  const age = daysBetween(e.date, new Date().toISOString().slice(0, 10));
  const canReport = e.kind !== "forecast" && age >= 0 && age <= REPORT_WINDOW_DAYS;
  const homes = e.breeds === "containers";
  const what = cap(homes && e.source !== "satellite" ? PLACE_LABEL[e.place_kind] || "neighbourhood" : e.cls_label);
  const names = e.factor_names || FACTOR_NAMES;
  const when = e.kind === "today" ? "today" : e.kind === "forecast" ? "forecast" : "past weather";
  const note = h("p", { class: "d-note" }, here.length ? `Reports here: ${bitten} bitten, ${here.length - bitten} fine.`
                                                       : "No reports here yet.",
    h("small", {}, "We keep this spot, the date, your answer and a random ID from your browser. No name, no account, no location."));
  const ask = canReport ? [
    h("h3", { class: "eyebrow" }, homes ? `Bitten near here on ${shortDate(e.date)}?` : `Bitten near here on the evening of ${shortDate(e.date)}?`),
    h("div", { class: "d-actions" },
      h("button", { type: "button", class: "d-btn", onclick: (ev) => report(e, true, ev, note) }, "Yes, I was bitten"),
      h("button", { type: "button", class: "d-btn", onclick: (ev) => report(e, false, ev, note) }, "No, it was fine")),
    note,
  ] : [
    h("h3", { class: "eyebrow" }, "Bite reports"),
    h("p", { class: "d-note" }, e.kind === "forecast" ? "You can report once the evening has happened."
                                                      : `Reports are open for the last ${REPORT_WINDOW_DAYS} days.`),
    h("div", { class: "d-actions" }, h("button", { type: "button", class: "d-btn", onclick: () => setYearAndDay(S.todayIdx) }, "Jump to today")),
    note,
  ];
  return [
    h("p", { class: "p-country" }, [p.name, p.country && countryName(p.country)].filter(Boolean).join(", ")),
    h("h2", { class: "d-name" }, e.name || `${what}, ${whereOf(e.centroid)}`),
    h("p", { class: "d-sub" }, [e.name ? `${what}, ${whereOf(e.centroid)}` : null, `${fmtDate(e.date)} · ${when}`]
      .filter(Boolean).join(" · ")),
    h("div", { class: "d-score" },
      h("span", { class: "sw", style: `background:${color(e.risk)}` }),
      h("span", { class: "n" }, String(e.risk), h("small", {}, " /100")),
      h("div", {},
        h("span", { class: "band", style: bandStyle(e.risk) }, `${cap(e.band)} risk`),
        e.range && h("span", { class: "range", title: e.range.note }, `likely ${e.range.low}–${e.range.high}`))),
    h("p", { class: "d-verdict" }, verdict(e)),
    h("h3", { class: "eyebrow" }, "Why this score"),
    h("div", { class: "factors" }, Object.entries(e.factors).map(([k, v]) =>
      h("div", { class: "factor" + (k === e.limiting ? " limiting" : ""), title: names[k]?.[1] || k },
        h("span", { class: "k" }, names[k]?.[0] || k, k === e.limiting && h("span", { class: "tag" }, "holding it back")),
        h("span", { class: "bar" }, h("i", { style: `width:${Math.round(v * 100)}%` })),
        h("span", { class: "v" }, `${Math.round(v * 100)}%`)))),
    h("p", { class: "d-text" }, text),
    e.restored && h("p", { class: "d-restore" },
      h("b", {}, `Restored to ${e.restored.as}: `), `${e.restored.risk}/100 instead of ${e.risk}. `,
      e.risk > e.restored.risk ? "That gap is what this water's condition costs the people living around it."
                               : "Restoring flow would change little here right now."),
    h("h3", { class: "eyebrow" }, "What to do"),
    h("div", { class: "d-do" },
      h("p", {}, h("b", {}, "If you live nearby: "), e.actions.resident),
      h("p", {}, h("b", {}, homes ? "If you manage this area: " : "If you manage this water: "), e.actions.city)),
    ...ask,
    h("p", { class: "d-foot" },
      e.species_name && h("span", {}, h("i", {}, e.species_name), ` (${e.species_common}) · `),
      `${coord(lat, lon)} · `,
      e.source === "satellite"
        ? (e.feature_id.startsWith("pop/")
          ? h("a", { href: "https://dataforgood.facebook.com/dfg/tools/high-resolution-population-density-maps", target: "_blank", rel: "noopener" },
              "Settlement seen by satellite (Meta population map), not yet on OpenStreetMap")
          : h("a", { href: "https://global-surface-water.appspot.com/", target: "_blank", rel: "noopener" },
              "Seen by satellite (JRC Global Surface Water), not yet on OpenStreetMap"))
        : h("a", { href: `https://www.openstreetmap.org/${e.feature_id}`, target: "_blank", rel: "noopener" }, `OpenStreetMap ${e.feature_id}`),
      h("br"), `Modelled estimate, not a field measurement · model v${e.model_version} · `,
      h("button", { type: "button", class: "linkish", onclick: () => showFhir(e) }, "FHIR record")),
  ];
}

function setYearAndDay(i) { setYear(Number(S.season.dates[i].slice(0, 4)), i); }

async function report(e, bad, ev, note) {
  const buttons = ev.target.parentElement.querySelectorAll("button");
  buttons.forEach((b) => (b.disabled = true));
  try {
    await api("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city: e.city, feature_id: e.feature_id, date: e.date, bad, client: CLIENT }) });
    S.reports = await api(`/api/feedback?city=${e.city}`);
    drawReports();
    note.textContent = `Saved: ${bad ? "bitten" : "fine"} on ${shortDate(e.date)}. Thank you, this is how the model gets checked.`;
  } catch (err) {
    note.textContent = `Not saved: ${err.message}`;
    buttons.forEach((b) => (b.disabled = false));
  }
}

function drawReports() {
  const byFeature = new Map();
  for (const r of S.reports) {
    const g = byFeature.get(r.feature_id) || { bad: 0, fine: 0 };
    r.bad ? g.bad++ : g.fine++;
    byFeature.set(r.feature_id, g);
  }
  const feats = [];
  for (const [fid, g] of byFeature) {
    const it = S.byFid.get(fid);
    if (it) feats.push({ type: "Feature", properties: { fid, bitten: g.bad > 0, bad: g.bad, fine: g.fine },
                         geometry: { type: "Point", coordinates: [it.p.centroid[1], it.p.centroid[0]] } });
  }
  map.getSource("reports")?.setData({ type: "FeatureCollection", features: feats });
  const n = S.reports.length, bitten = S.reports.filter((r) => r.bad).length;
  $("reports").textContent = n
    ? `${plural(n, "report")} here: ${bitten} bitten, ${n - bitten} fine. Dark dots on the map mark bites, white ones quiet evenings.`
    : "No reports here yet. Tap any spot on the map and say whether you were bitten there.";
  drawScoreboard();
}

async function drawScoreboard() {
  try {
    const sb = await api(`/api/feedback/scoreboard?city=${S.city}`);
    $("scoreboard").textContent = sb.reports
      ? `The model agreed with ${sb.agree} of ${plural(sb.reports, "report")} (${Math.round(sb.agreement * 100)}%): `
        + `${sb.likely_bitten} bites it expected, ${sb.unlikely_fine} quiet nights it expected, `
        + `${sb.unlikely_bitten} missed, ${sb.likely_fine} false alarms.`
      : "";
  } catch { $("scoreboard").textContent = ""; }
}

async function showFreshness() {
  try {
    const s = await api("/api/status");
    const mine = s.cities[S.city], el = $("freshness");
    if (!mine || mine.last_observed == null) return (el.textContent = "");
    el.className = "p-fresh" + (mine.stale ? " stale" : "");
    el.textContent = mine.stale
      ? `Weather last updated ${shortDate(mine.last_observed)}, ${plural(mine.days_behind, "day")} behind. Refreshing.`
      : `Weather up to ${shortDate(mine.last_observed)}, forecast to ${shortDate(S.season.dates.at(-1))}.`;
  } catch { /* the badge is a nicety */ }
}

/* ---------- FHIR ---------- */
async function showFhir(e) {
  const url = `/api/cities/${e.city}/fhir?feature=${encodeURIComponent(e.feature_id)}&date=${e.date}`
              + (e.species ? `&species=${encodeURIComponent(e.species)}` : "");
  $("fhir-link").href = url;
  $("fhir-json").textContent = "Loading…";
  $("fhir").showModal();
  try { $("fhir-json").textContent = JSON.stringify(await api(url), null, 2); }
  catch (err) { $("fhir-json").textContent = `Couldn't build the FHIR record: ${err.message}`; }
}

/* ---------- about + validation charts ---------- */
let validation = null;
async function openAbout() {
  $("about").showModal();
  if (validation) return;
  try { validation = await api("/api/validation"); drawValidation(); }
  catch (err) { $("valid-cap").textContent = `Couldn't load the comparison: ${err.message}`; }
}

function drawValidation() {
  const svg = $("valid-chart"), Y = "2025";
  const cities = Object.entries(validation.cities).sort((a, b) => a[1].lat - b[1].lat);
  const W = 700, H = 240, L0 = 34, R0 = 8, T0 = 8, B0 = 24;
  const weeks = cities[0][1].years[Y].weeks.map((w, i) => [w, i]).filter(([w]) => w >= `${Y}-03-01` && w <= `${Y}-11-30`);
  const x = (k) => L0 + (k / (weeks.length - 1)) * (W - L0 - R0), y = (v) => T0 + (1 - v) * (H - T0 - B0);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren();
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    svg.append(svgEl("line", { x1: L0, x2: W - R0, y1: y(v), y2: y(v), stroke: v ? C.rule2 : C.ink3, "stroke-width": 1 }));
    svg.append(svgEl("text", { x: L0 - 6, y: y(v) + 4, "text-anchor": "end", "font-size": 11, fill: C.ink2 }, `${v * 100}%`));
  }
  weeks.forEach(([w], k) => {
    const prev = weeks[k - 1]?.[0];
    if (!prev || prev.slice(5, 7) !== w.slice(5, 7)) svg.append(svgEl("text", { x: x(k), y: H - 6, "font-size": 11, fill: C.ink2 },
      utc(w).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" })));
  });
  cities.forEach(([, c], ci) => {
    const vals = weeks.map(([, i]) => c.years[Y].weekly[i]);
    svg.append(svgEl("path", { d: vals.map((v, k) => `${k ? "L" : "M"}${x(k).toFixed(1)} ${y(v).toFixed(1)}`).join(" "),
      fill: "none", stroke: CITY_COLORS[ci], "stroke-width": 2, "stroke-linejoin": "round" }));
  });
  const cross = svgEl("line", { y1: T0, y2: H - B0, stroke: C.ink, "stroke-width": 1, opacity: 0 });
  svg.append(cross);
  const setCap = (k) => {
    const [w, i] = weeks[k];
    $("valid-cap").textContent = `Week of ${shortDate(w)}: ` + cities.map(([, c]) => `${c.name} ${Math.round(c.years[Y].weekly[i] * 100)}%`).join(" · ");
  };
  const defaultCap = () => { $("valid-cap").textContent = "Hover or tap the chart to read a week. Oslo starts weeks later than the southern cities and its season totals under half of Coimbra's, as it should at 60°N."; cross.setAttribute("opacity", 0); };
  svg.onpointermove = (ev) => {
    const b = svg.getBoundingClientRect(), px = ((ev.clientX - b.left) / b.width) * W;
    const k = Math.max(0, Math.min(weeks.length - 1, Math.round(((px - L0) / (W - L0 - R0)) * (weeks.length - 1))));
    cross.setAttribute("x1", x(k)); cross.setAttribute("x2", x(k)); cross.setAttribute("opacity", 0.5);
    setCap(k);
  };
  svg.onpointerleave = defaultCap;
  defaultCap();
  drawGbif(validation.gbif);
  $("valid-table").replaceChildren(
    h("thead", {}, h("tr", {}, ["City", "Latitude", "First adults 2025", "Peak (14-day)", "Season total"].map((t) => h("th", {}, t)))),
    h("tbody", {}, cities.map(([, c], ci) => {
      const d = c.years[Y], key = h("span", { class: "key" });
      key.style.background = CITY_COLORS[ci];
      return h("tr", {}, h("td", {}, key, c.name), h("td", { class: "n" }, `${c.lat.toFixed(1)}°N`),
        h("td", {}, d.first_adults ? shortDate(d.first_adults) : "none"),
        h("td", { class: "n" }, `${Math.round(d.peak_14d * 100)}%`), h("td", { class: "n" }, d.season_total.toFixed(0)));
    })));
}

function drawGbif(g) {
  if (!g) return;
  const rows = Object.entries(g.cities).sort((a, b) => b[1].records - a[1].records);
  $("gbif-table").replaceChildren(
    h("thead", {}, h("tr", {}, ["City", "Records (country)", "Season match", "Peak: model / records"].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map(([city, c]) => h("tr", {},
      h("td", {}, cap(city)),
      h("td", { class: "n" }, `${c.records.toLocaleString()} (${c.country})`),
      h("td", { class: "n" }, c.enough ? `r = ${c.r.toFixed(2)}` : "too few to judge"),
      h("td", {}, `${MONTHS[c.model_peak_month - 1]} / ${MONTHS[c.records_peak_month - 1]}`)))));
  $("gbif-note").textContent =
    `Where there are enough records, the model's season matches reality with r = ${g.mean_r_where_enough} on average. `
    + `Real records peak about ${g.mean_peak_lag_months} month(s) later than the model: the model tracks breeding `
    + "conditions, while real populations take several generations to build up, and surveys trap more in late summer.";
  drawGbifLocal(validation.gbif_local);
}

function drawGbifLocal(g) {
  if (!g) return;
  const rows = g.rows.filter((r) => r.r != null && r.records >= 30).sort((a, b) => b.records - a.records);
  $("gbif-local-table").replaceChildren(
    h("thead", {}, h("tr", {}, ["Place", "Mosquito", "Records within 250 km", "Season match", "Peak: model / records"].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map((r) => h("tr", {},
      h("td", {}, r.name.replace(" (Zwalm basin)", "") + (r.reference ? " *" : "")),
      h("td", {}, h("i", {}, r.species_name)),
      h("td", { class: "n" }, r.records.toLocaleString()),
      h("td", { class: "n" }, r.enough ? `r = ${r.r.toFixed(2)}` : "too few to judge"),
      h("td", {}, `${MONTHS[r.model_peak_month - 1]} / ${MONTHS[r.records_peak_month - 1]}`)))));
  $("gbif-local-note").textContent =
    `Every species the map models, against real records near each place: r = ${g.mean_r_where_enough} on average `
    + `where there are at least ${g.min_records} records. * Lahore is a reference place kept only for this check.`;
}

function writeHash() {
  if (!S.season || S.view === "world") return history.replaceState(null, "", location.pathname);
  const p = new URLSearchParams({ city: S.city, date: S.season.dates[S.day] });
  if (S.species && S.species !== S.season.species_list?.[0]?.key) p.set("sp", S.species);
  if (S.selected != null) p.set("f", S.items[S.selected].fid);
  history.replaceState(null, "", "#" + p.toString());
}

/* ---------- the world: every place ready to open ---------- */
const ADMIN = /\s+(Division|District|Tehsil|Governorate|City District|Capital Territory|Prefecture|Metropolitan (Municipality|City|Corporation)|Municipality)$/i;
const placeName = (n) => n.replace(/^(City|Municipality) of\s+/i, "").replace(ADMIN, "").trim() || n;
const town = (c) => placeName(c.name.replace(/\s*\(.*\)$/, "")).toLowerCase();
const sameTown = (a, b) => town(a) === town(b);

function drawPlaces() {
  // one pin per town: two lookups of the same city a few km apart are both kept, only the latest is shown
  const recent = store.get(RECENT, []);
  const rank = (c) => { const i = recent.indexOf(c.key); return i < 0 ? 99 : i; };
  const mine = S.cities.filter((c) => !c.research).sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
    .filter((c, i, all) => !all.slice(0, i).some((o) => sameTown(o, c) && distM(o, c) < 10000));
  const shown = [...mine, ...S.cities.filter((c) => c.research)]
    .filter((c, i, all) => !all.slice(0, i).some((o) => sameTown(o, c) && distM(o, c) < 10000));
  map.getSource("places")?.setData({ type: "FeatureCollection", features: shown.map((c) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [c.lon, c.lat] },
    properties: { key: c.key, label: `${c.name}${c.country ? `, ${countryName(c.country)}` : ""}` } })) });
  const btn = (c) => h("button", { type: "button", class: "w-city", onclick: () => loadCity(c.key) },
    h("b", {}, c.name.replace(" (Zwalm basin)", "")), h("span", {}, countryName(c.country)));
  $("w-all").replaceChildren(...[...shown].sort((a, b) => a.name.localeCompare(b.name)).map(btn));
  const rec = recent.map((k) => shown.find((c) => c.key === k)).filter(Boolean).slice(0, 4);
  $("w-recent-h").hidden = !rec.length;
  $("w-recent").replaceChildren(...rec.map(btn));
}

function showWorld() {
  S.token++;
  if (S.selected != null) map.setFeatureState({ source: "feat", id: S.selected }, { sel: false });
  S.selected = null; S.season = null; S.city = null; S.place = null; S.items = []; S.byFid = new Map();
  for (const src of ["feat", "dots", "reports"]) map.getSource(src)?.setData(EMPTY);
  drawRanks([]);
  showView("world");
  drawPlaces();
  setStatus("");
  writeHash();
  map.stop();
  map.fitBounds(WORLD, { duration: REDUCED ? 0 : 1400 });
}

/* ---------- any point on earth ---------- */
async function lookAt(lat, lon, name, country, { move = true, auto = false } = {}) {
  S.pending = { lat: Number(lat), lon: Number(lon) };
  setStatus(auto ? `Loading mosquito data for ${name || "this area"}…` : `Opening ${name || "this place"}…`, true);
  if (move) glideTo({ center: [Number(lon), Number(lat)], zoom: 12.5 });   // the data arrives while the map travels
  // say what is happening while a new place is fetched, and never wait forever
  const label = name || "this place", ctrl = new AbortController();
  const steps = [setTimeout(() => setStatus(`Getting the weather and mosquito records for ${label}…`, true), 6000),
                 setTimeout(() => setStatus(`Still working on ${label}. A new place can take up to a minute the first time.`, true), 25000),
                 setTimeout(() => ctrl.abort(), 90000)];
  try {
    const place = await api(`/api/anywhere?lat=${lat}&lon=${lon}` + (name ? `&name=${encodeURIComponent(name)}` : "")
                            + (country ? `&country=${encodeURIComponent(country)}` : ""), { signal: ctrl.signal });
    steps.forEach(clearTimeout);
    S.cities = [place, ...S.cities.filter((c) => c.key !== place.key)];
    await loadCity(place.key, null, null, null, { move });
  } catch (e) {
    setStatus(e.name === "AbortError"
      ? `${label} is taking too long right now. Try again in a minute, or open a place from the list.`
      : `Couldn't open ${label}: ${e.message}`);
  } finally {
    steps.forEach(clearTimeout);
    S.pending = null;
  }
}

// Zoomed in close enough to see streets, anywhere on earth: load the area by itself.
let autoTimer = 0;
map.on("moveend", () => { clearTimeout(autoTimer); autoTimer = setTimeout(autoLoad, AUTO_WAIT); });
const ZOOM_HINT = "Zoom in a little more to load mosquito risk here.";
async function autoLoad() {
  const c = map.getCenter(), at = { lat: c.lat, lon: c.lng }, z = map.getZoom();
  const covered = S.cities.some((p) => distM(at, p) < p.radius_m * 1.5);
  const hint = z >= 7 && z < AUTO_ZOOM && !covered && !S.loading && !S.pending;
  if (hint) setStatus(ZOOM_HINT);
  else if ($("status").textContent === ZOOM_HINT) setStatus("");
  if (z < AUTO_ZOOM || S.loading || S.pending || document.hidden) return;
  if (S.place && distM(at, S.place) < S.place.radius_m * OPEN_SHARE) return;   // already looking at it
  const near = S.cities.filter((p) => distM(at, p) < p.radius_m * OPEN_SHARE).sort((a, b) => distM(at, a) - distM(at, b))[0];
  if (near) return loadCity(near.key, S.season?.dates[S.day], null, null, { move: false });
  S.pending = at;
  const hit = await whereIs(at.lat.toFixed(4), at.lon.toFixed(4));
  S.pending = null;
  if (map.getZoom() < AUTO_ZOOM || distM(at, { lat: map.getCenter().lat, lon: map.getCenter().lng }) > 1500) return;  // moved on
  lookAt(hit.lat, hit.lon, hit.name, hit.country, { move: false, auto: true });
}

function hitOf(f) {
  const p = f.properties, [lon, lat] = f.geometry.coordinates;
  return { lat, lon, country: (p.countrycode || "").toLowerCase(), name: placeName(p.name || p.city || ""),
           detail: [p.type === "district" && p.city, p.state, countryName(p.countrycode)].filter(Boolean).join(", ") };
}
async function places(q, signal) {
  for (const layers of [PLACE_LAYERS, []]) {         // towns and cities first, then anything (a postcode)
    const u = new URL(`${PHOTON}/api/`);
    u.searchParams.set("q", q); u.searchParams.set("limit", "6"); u.searchParams.set("lang", "en");
    for (const l of layers) u.searchParams.append("layer", l);
    const res = await fetch(u, { signal });
    if (!res.ok) throw new Error(`The place search isn't answering (HTTP ${res.status}). Try again in a moment.`);
    const hits = (await res.json()).features.map(hitOf).filter((x) => x.name);
    if (hits.length) return hits;
  }
  return [];
}
async function geocode(q) {
  const [hit] = await places(q);
  if (!hit) throw new Error(`No place called "${q}" found.`);
  return hit;
}
async function whereIs(lat, lon) {    // the town around a point, for a real name; never required
  try {
    const res = await fetch(`${PHOTON}/reverse?lat=${lat}&lon=${lon}&lang=en`);
    const p = res.ok ? (await res.json()).features?.[0]?.properties : null;
    const nm = p && (p.city || p.district || p.county || p.name);   // the town people know it by
    if (nm) return { lat, lon, country: (p.countrycode || "").toLowerCase(), name: placeName(nm) };
  } catch { /* offline or busy */ }
  return { lat, lon, name: "This area", country: "" };
}

// A town already here opens as it is: instant, and no second copy a few km from the first
async function openHit(hit) {
  const have = S.cities.find((c) => sameTown(c, hit) && distM(c, hit) < 10000);
  if (have) await loadCity(have.key);
  else await lookAt(hit.lat, hit.lon, hit.name, hit.country);
}

// Suggestions under the search box, as you type
const SG = { hits: [], active: -1, ctrl: null, timer: 0 };
function showSuggest(hits) {
  SG.hits = hits; SG.active = -1;
  const list = $("suggest");
  list.replaceChildren(...hits.map((x, i) => h("li", { role: "option", id: `sg-${i}`, "aria-selected": "false",
      onpointerdown: (e) => { e.preventDefault(); pick(i); } },
    h("b", {}, x.name), x.detail && h("span", {}, x.detail))));
  list.hidden = !hits.length;
  $("q").setAttribute("aria-expanded", String(!list.hidden));
}
function hideSuggest() { clearTimeout(SG.timer); SG.ctrl?.abort(); showSuggest([]); }
function moveSuggest(d) {
  if (!SG.hits.length) return;
  const n = SG.hits.length + 1;                 // the options, plus "none" (back to what was typed)
  SG.active = ((SG.active + 1 + d + n) % n) - 1;
  [...$("suggest").children].forEach((li, i) => li.setAttribute("aria-selected", String(i === SG.active)));
  $("q").setAttribute("aria-activedescendant", SG.active >= 0 ? `sg-${SG.active}` : "");
}
function pick(i) {
  const hit = SG.hits[i];
  if (!hit) return;
  $("q").value = hit.name;
  hideSuggest();
  $("q").blur();
  openHit(hit);
}
$("q").addEventListener("input", () => {
  clearTimeout(SG.timer);
  const q = $("q").value.trim();
  if (q.length < 2) return hideSuggest();
  SG.timer = setTimeout(async () => {
    SG.ctrl?.abort();
    SG.ctrl = new AbortController();
    try { if ($("q").value.trim() === q) showSuggest(await places(q, SG.ctrl.signal)); }
    catch { /* a newer keystroke, or offline: Enter still works */ }
  }, 250);
});
$("q").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); moveSuggest(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveSuggest(-1); }
  else if (e.key === "Escape") hideSuggest();
});
$("q").addEventListener("blur", () => setTimeout(hideSuggest, 120));

/* ---------- the phone sheet ---------- */
function sheetPx() {
  const H = innerHeight;
  if (S.sheet === "full") return H - 120;
  if (S.sheet === "half") return Math.round(H * 0.56);
  if (S.view === "world") return 190;
  const t = $("tonight");                    // peek: the place's name and tonight's line
  return S.view === "place" && t.offsetHeight ? t.offsetTop + t.offsetHeight + 14 : 180;
}
function setSheet(state) {
  S.sheet = state;
  if (!PHONE.matches) return;
  const p = $("panel");
  p.style.setProperty("--sheet", `${sheetPx()}px`);
  $("grab").setAttribute("aria-expanded", String(state !== "peek"));
  $("grab").setAttribute("aria-label", state === "full" ? "Show less" : "Show more");
}
(() => {
  const grab = $("grab"), p = $("panel");
  let y0 = 0, h0 = 0, moved = false;
  grab.addEventListener("pointerdown", (e) => {
    if (!PHONE.matches) return;
    y0 = e.clientY; h0 = p.getBoundingClientRect().height; moved = false;
    grab.setPointerCapture(e.pointerId);
    p.classList.add("dragging");
  });
  grab.addEventListener("pointermove", (e) => {
    if (!grab.hasPointerCapture(e.pointerId)) return;
    const dy = y0 - e.clientY;
    if (Math.abs(dy) > 6) moved = true;
    p.style.setProperty("--sheet", `${Math.min(Math.max(h0 + dy, 120), innerHeight - 110)}px`);
  });
  grab.addEventListener("pointerup", (e) => {
    if (!grab.hasPointerCapture(e.pointerId)) return;
    p.classList.remove("dragging");
    if (!moved) return setSheet(S.sheet === "peek" ? "half" : S.sheet === "half" ? "full" : "peek");
    const frac = p.getBoundingClientRect().height / innerHeight;
    setSheet(frac > 0.72 ? "full" : frac > 0.38 ? "half" : "peek");
  });
})();

// On a phone the season chart sits in the sheet under tonight's line; on a desktop it floats along the bottom
function placeTimeline() {
  const tl = $("timeline");
  if (PHONE.matches) $("tonight").after(tl);
  else document.body.insertBefore(tl, $("about"));
  setSheet(S.sheet);
  if (S.season && S.view !== "world") drawTimeline();
}
PHONE.addEventListener("change", placeTimeline);
addEventListener("resize", () => PHONE.matches && setSheet(S.sheet));

/* ---------- start ---------- */
async function init() {
  placeTimeline();
  await mapReady;
  addLayers();
  const p = new URLSearchParams(location.hash.slice(1));
  try { S.cities = await api("/api/cities"); }
  catch (e) { setStatus(`Couldn't reach the BiteCast server: ${e.message}. Reload to retry.`); return; }
  // A shared link opens its place; reloading the page starts again on the world, as it looks.
  const reloaded = performance.getEntriesByType?.("navigation")?.[0]?.type === "reload";
  if (reloaded) history.replaceState(null, "", location.pathname);
  let start = reloaded ? null : p.get("city");
  if (start && !S.cities.some((c) => c.key === start)) {    // a shared link to a place opened on another device
    try { S.cities.push(await api(`/api/cities/${encodeURIComponent(start)}`)); } catch { start = null; }
  }
  drawPlaces();
  if (start) {
    const f = p.get("f");
    await loadCity(start, p.get("date"), f, p.get("sp"));
  } else {
    showView("world");
    setStatus("");
  }
}

$("slider").addEventListener("input", (e) => { if (!S.season) return; setDay(Number(e.target.value)); });
$("to-today").addEventListener("click", () => S.season && setYearAndDay(S.todayIdx));
$("home").addEventListener("click", showWorld);
$("back").addEventListener("click", closeDetail);
document.querySelectorAll("[data-about]").forEach((b) => b.addEventListener("click", openAbout));
document.querySelectorAll("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));
document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
  S.mode = b.dataset.mode;
  b.parentElement.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (!S.season) return;
  prepare();
  render();
}));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && S.view === "detail" && !document.querySelector("dialog[open]")) closeDetail();
});
$("lookup").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (!q) return;
  if (SG.active >= 0) return pick(SG.active);
  hideSuggest();
  $("q").blur();
  setStatus(`Searching for "${q}"…`, true);
  try {
    await openHit(await geocode(q));
  } catch (err) {
    setStatus(err.message);
  }
});
$("here").addEventListener("click", () => {
  if (!navigator.geolocation) return setStatus("This browser won't share a location. Search for a place instead.");
  setStatus("Asking your browser where you are…", true);
  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const at = await whereIs(pos.coords.latitude.toFixed(4), pos.coords.longitude.toFixed(4));
      lookAt(at.lat, at.lon, at.name === "This area" ? "My location" : at.name, at.country);
    },
    (err) => setStatus(`Couldn't get your location (${err.message}). Search for a place instead.`),
    { timeout: 10000 });
});
init();
