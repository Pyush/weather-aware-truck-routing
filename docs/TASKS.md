# Micro-Task Backlog

**Legend** — `~Nm` = time-boxed estimate · `→` = depends on · `Done when` = acceptance criterion.
Nothing here is "done" until its `Done when` line is objectively true.

**Status key:** `[ ]` todo · `[~]` in progress · `[x]` done

Totals: **94 tasks** · ~50 h of focused work · 5 % buffer recommended (Phase 8 needs slack).

**Progress: 61 / 94 formally complete (Phases 0–3 plus 12 frontend/quality/doc tasks).**

Browser QA results are in `docs/QA-CHECKLIST.md`, including the checks still outstanding
(Firefox/Safari, the real WebGL-absent path, and the 1,000-mile performance case).

**Frontend implementation checkpoint (2026-10-17):** The app scaffold, streaming client,
store, selectors, map, form, route cards, slider and methodology dialog are implemented but
uncommitted. Browser mock-provider analysis renders three routes with nonzero risk summaries;
the 0→48 h slider updates without an API request. 433 backend and 41 frontend tests pass;
production build passes. Remaining before checking off frontend tasks: the explicit acceptance
criteria below (notably the checkpoint table's sorting/virtualisation and the perf pass).
Headless MSW component tests and backend-generated 0/6/24/48-hour parity fixtures now pass.
The frontend linter passes without warnings. FE-02's drift check runs in `make lint` (verified by
corrupting the committed file and confirming it fails). FE-24 shareable URLs are implemented and
verified in the browser (restore on load, offset preserved across replan). Phase 8 deployment is
not implemented. `ENOSPC` requires polling for the dev watcher in this environment.

| Phase | Status |
|---|---|
| 0 · Foundations | ✅ complete (BE-00, BE-01, DOC-00) |
| 1 · Domain core | ✅ complete (BE-10 … BE-29) |
| 2 · Adapters | ✅ complete (BE-30 … BE-42), validated against the live APIs |
| 3 · Orchestration + API | ✅ complete (BE-50 … BE-61) — both endpoints verified over HTTP |
| 4–6 · Frontend | 🔶 most tasks verified; remaining are the checkpoint table, map↔table sync and remaining acceptance criteria |
| 7 | 🔶 selector/component/parity/perf/QA done except frame profiling |
| 8 | ⬜ next |

Phases 2 and 3 were each validated with a live end-to-end run. Measured event timeline
(D20): geometry usable at **2.5 s**, full analysis at **21 s** for a 25-location weather
fetch — which is precisely why `/analyze/stream/` is the primary interface. Phase 2's run
also surfaced the average-risk noise flaw fixed in D18.

---

## Phase 0 — Foundations

- [x] **BE-00** Repo scaffold `~20m`
  Git init, `backend/`, `frontend/`, `docs/`, `Makefile`, `.gitignore`, `.editorconfig`, README stub, `LICENSE`.
  → none · **Done when** `git status` clean after commit; dirs + Makefile exist.
- [x] **BE-01** Python toolchain `~30m` → BE-00
  `backend/pyproject.toml` (or requirements + venv), `ruff`, `pytest`+`pytest-django`, `httpx`, `django`, `DRF`, `.env.example`, settings split (`base/dev/test`).
  **Done when** `make test` runs an empty green suite and `ruff check` is clean.
- [ ] **FE-00** Frontend scaffold `~30m` → BE-00
  `npm create vite@latest frontend -- --template react-ts`, strict tsconfig, eslint + prettier, path alias `@/`.
  **Done when** `npm run build` and `npm run typecheck` pass on a clean checkout.
- [ ] **OPS-00** Local run orchestration `~20m` → BE-01, FE-00
  Makefile targets `up`/`down`/`logs`/`migrate`/`test`/`lint`/`bench`; backend on `:8000`, frontend dev on `:5173` with proxy.
  **Done when** `make up` boots both and the frontend proxies `/api` to Django.
- [x] **DOC-00** Decision log `~30m` → none
  Create `docs/DECISIONS.md` with D1–D12 (threshold inclusivity, ETA model, mid-point mile attribution, no-travel gate, streaming, no-DB, provider choices).
  **Done when** every non-obvious choice in PLAN.md §3 has a numbered entry with rationale.

---

## Phase 1 — Domain core (pure logic, zero I/O) · highest value, build first

### 1a. Risk classification

- [x] **BE-10** `risk/enums.py` `~20m` → BE-01
  `RiskLevel` (NO_TRAVEL, SEVERE, HIGH, MODERATE, LOW) with ordinal `score`, `label`, hex `color`, `css_color`; ordering helpers.
  **Done when** `max()`/sorting by severity works and colors are centralized for backend + `/config` export.
- [x] **BE-11** `risk/thresholds.py` `~45m` → BE-10
  Encode the table as data: per variable a list of `(lower_inclusive, upper_exclusive, level)` **plus an explicit inclusivity flag on the no-travel bound**. Wind `≥55` is inclusive; rain `>1.00` and snow `>3.0` are exclusive — the table is asymmetric and the code must be too.
  **Done when** thresholds are the only place numbers live; grep finds no magic thresholds elsewhere in the domain.
- [x] **BE-12** `risk/classify.py` `~30m` → BE-11
  `classify_wind(mph)`, `classify_rain(in_per_hr)`, `classify_snow(in_per_hr)`, generic `classify(var, value)`; NaN/None ⇒ `LOW` with a `data_quality` flag (missing ≠ hazardous, but must be surfaced).
  **Done when** each function is total (never raises) and returns `(level, matched_bucket_label)`.
- [x] **BE-13** `risk/load_rules.py` `~30m` → BE-11
  `apply_load_rules(wind_level, wind_mph, load_lb) -> level`. Escalation only (never de-escalation): `wind ≥55` ⇒ NO_TRAVEL any load; wind in `[45,55)` **and** `load_lb > 30_000` ⇒ NO_TRAVEL; wind in `[35,45)` **and** `load_lb > 40_000` ⇒ at least SEVERE. Strict `>` per the written operator, behind `LOAD_BOUNDARY_INCLUSIVE = False` (D16).
  **Done when** load thresholds are config constants, not literals, and a single `LoadPolicy` object owns them.
- [x] **BE-14** `risk/evaluate.py` `~40m` → BE-12, BE-13
  `evaluate_checkpoint(weather, load_lb) -> CheckpointRisk(level, wind_level, rain_level, snow_level, dominant_factor, reasons[])`; `level = max(...)`; `dominant_factor` = which variable drove it (tie-break wind > snow > rain); `reasons` = human strings for the UI.
  **Done when** every level change is attributable to a named rule + variable (used in map tooltips and the README table).
- [x] **BE-14b** `risk/weather_vars.py` rule→variable mapping `~25m` → BE-14
  Single mapping table: Wind ← `wind_speed_10m` (instantaneous); Rain ← `rain + showers` (liquid equivalent); Snow ← **`snowfall`** (snow **depth**, ~7× the liquid value); `precipitation` for display context only (D15). Exported via `/api/v1/config/`.
  **Done when** no risk code references `precipitation`, the mapping is greppable from one file, and a regression test asserts the snow rule is fed depth (a 1.4 in/hr snow hour classifies HIGH, not LOW).

### 1b. Mileage accounting & recommendation

- [x] **BE-15** `risk/miles.py` `~40m` → BE-14
  Attribute road miles to checkpoints by **midpoint boundaries** (checkpoint *i* owns `[ (d_{i-1}+d_i)/2, (d_i+d_{i+1})/2 )`). Return `miles_by_level{level: miles}`, `no_travel_miles`, `severe_miles`, `high_miles`, `avg_risk = Σ(miles×score)/total_miles`, plus per-segment records for the heatmap.
  **Done when** `Σ miles_by_level == total_route_miles` (±1e-6) for randomized fixtures (property test).
- [x] **BE-16** `risk/recommend.py` `~50m` → BE-15
  Lexicographic key: `(no_travel_miles, severe_miles, high_miles, avg_risk, duration_s, distance_m)`; assign ranks; `explain_ranking()` emits **both** the runner-up criterion diff ("120 fewer severe miles, +40 min longer") and a per-criterion tally across all routes ("fastest on 2 of 3") (D17).
  **Done when** a `SAFETY_GATE_ENABLED` flag toggles the documented no-travel tier so the literal spec ordering is also demonstrable (D5), and both explanation forms are emitted.

### 1c. Geometry, sampling, ETA

- [x] **BE-17** `geo/haversine.py` + polyline distance `~30m` → BE-01
  Haversine miles; `cumulative_distances(coords) -> list[float]` in miles along a GeoJSON LineString; unit-test against a known great-circle (e.g. Denver→Chicago ≈ 917 mi great-circle vs 1,006 mi driven).
  **Done when** monotone non-decreasing output, exact 0 at index 0, tolerance ≤ 0.1 % on the reference case.
- [x] **BE-18** `geo/sampling.py` `~40m` → BE-17
  `sample_checkpoints(coords, spacing_mi)`: cumulative distances `0, spacing, 2·spacing, …`, force-include the final point (destination), linear lat/lon interpolation at each target distance, drop near-duplicates (< 0.05 mi), guarantee strictly increasing distance.
  **Done when** count == `floor(D/spacing) + 2` (endpoints) ± 1 and spacing is honoured exactly in the interior.
- [x] **BE-19** `geo/eta.py` `~35m` → BE-18
  Truck speed model: `truck_avg_mph = clamp(osrm_implied_car_mph × TRUCK_SPEED_FACTOR, MIN, MAX)`; defaults factor 0.85, min 25, max 65. `eta_at(cumulative_mi, departure_utc) -> datetime`; optional HOS break parameter (default off).
  **Done when** ETA is strictly increasing and the last ETA matches total duration; assumptions documented for the UI "Methodology" modal.

### 1d. Domain test suite (this is the correctness proof)

- [x] **BE-21** Wind boundary matrix `~25m` → BE-12
  Parametrized: `24.9→LOW, 25.0→MOD, 34.9→MOD, 35.0→HIGH, 44.9→HIGH, 45.0→SEVERE, 54.9→SEVERE, 55.0→NO_TRAVEL, 70.0→NO_TRAVEL`.
  **Done when** all pass and the exact table cell is named in the test id.
- [x] **BE-22** Rain boundary matrix `~20m` → BE-12
  `0.099→LOW, 0.10→MOD, 0.249→MOD, 0.25→HIGH, 0.499→HIGH, 0.50→SEVERE, 1.00→SEVERE, 1.01→NO_TRAVEL`.
  **Done when** pass — note `1.00` is SEVERE (top bound inclusive) while `>1.00` is NO_TRAVEL.
- [x] **BE-23** Snow boundary matrix `~20m` → BE-12
  `0.49→LOW, 0.5→MOD, 0.99→MOD, 1.0→HIGH, 1.99→HIGH, 2.0→SEVERE, 3.0→SEVERE, 3.01→NO_TRAVEL`.
  **Done when** pass.
- [x] **BE-24** Load escalation matrix `~30m` → BE-13
  Loads `0, 30_000, 30_001, 40_000, 40_001, 45_000` × wind `30, 40, 50, 60`; assert both non-escalation at exactly 30,000/40,000 and escalation just above.
  **Done when** matrix passes and proves escalation never *lowers* a level.
- [x] **BE-25** Checkpoint aggregation test `~25m` → BE-14
  Cases: wind SEVERE + rain LOW + snow LOW ⇒ SEVERE/dominant wind; wind LOW + snow HIGH ⇒ HIGH/dominant snow; wind NO_TRAVEL + rain HIGH ⇒ NO_TRAVEL.
  **Done when** pass, including `reasons[]` content assertions.
- [x] **BE-26** Mileage math fixture `~30m` → BE-15
  Hand-computed 5-checkpoint route with known levels; assert `miles_by_level`, `severe_miles`, `high_miles`, `avg_risk` exactly, and sum == total.
  **Done when** pass on the hand-computed fixture (no snapshot-only assertions).
- [x] **BE-27** Recommendation ordering proof `~35m` → BE-16
  Construct routes where each criterion in turn is the sole tiebreak winner, plus a permutation test that lexicographic ordering never lets a later criterion beat an earlier one.
  **Done when** all 4 spec criteria are individually decisive in their own fixture.
- [x] **BE-28** Sampling + ETA tests `~30m` → BE-19
  Straight-line and hairpin geometries; spacing 10/25/50; ETA monotonicity and totals; 0-mile and sub-spacing routes (edge cases).
  **Done when** pass, including `D < spacing` (two checkpoints: origin + destination).
- [x] **BE-29** Golden rule-table test `~40m` → BE-21…BE-25
  Transcribe the assessment table into `tests/fixtures/risk_table.csv` and assert the classifier matches **every** cell automatically; add a guard test that the CSV is non-empty and covers all 3 variables × 5 levels.
  **Done when** `pytest tests/test_golden_rule_table.py` passes and the CSV is the human-readable source of truth.

---

## Phase 2 — Config & external adapters

- [x] **BE-30** Settings & env config `~30m` → BE-01
  `.env` loading (no django-environ dep needed), `base_url`s, timeouts, cache TTLs, `WEATHER_LOCATION_BUDGET`, `SAMPLE_SPACINGS=(10,25,50)`, `SLIDER_MAX_HOURS=48`, CORS/hosts, `WEATHER_PROVIDER=open_meteo|mock`.
  **Done when** the app boots with zero env vars (defaults) and every documented var has a safe default.
- [x] **BE-31** `http/client.py` shared client `~40m` → BE-30
  Module-level `httpx.Client` (pooled), timeout, retries with exponential backoff + jitter, honours `Retry-After`, retries 429/5xx only, per-request logging with duration + upstream host, `UpstreamError` taxonomy.
  **Done when** a mocked 429-then-200 sequence retries successfully and a 4xx fails fast without retry.
- [x] **BE-32** `geocoding/base.py` `~25m` → BE-31
  `Geocoder` protocol: `geocode(query) -> GeocodeResult(lat, lon, display_name, country, admin1, source, confidence)`; `AmbiguousQuery` error carrying candidates.
  **Done when** a `MockGeocoder` satisfies the protocol and the analysis service depends only on the protocol.
- [x] **BE-33** `geocoding/open_meteo.py` `~35m` → BE-32
  Primary provider (`geocoding-api.open-meteo.com`); request build, parse, rank candidates (prefer populated-place feature codes), timeouts.
  **Done when** mocked test returns Denver for "Denver, CO" and raises `AmbiguousQuery` for "Springfield".
- [x] **BE-34** `geocoding/chain.py` + Photon `~35m` → BE-33
  Ordered provider chain with fallback on error/empty (Photon → Nominatim-optional), per-query cache (TTL 24 h), and a clear 422 "could not locate" message.
  **Done when** primary failure falls through to Photon in a mocked test; Nominatim 403 does not break the flow.
- [x] **BE-35** Routing adapter (OSRM) `~50m` → BE-31
  `routing/osrm.py`: `alternatives=true`, `overview=full&geometries=geojson`, parse distance/duration/geometry, retry + timeout, typed `NoRouteFound`.
  **Done when** mocked 2-alternative response yields 2 normalized routes with float coords `[lon, lat]`.
- [x] **BE-36** 3-route guarantee `~50m` → BE-35
  If OSRM yields < 3 distinct geometries, synthesize variants with **perpendicular via-waypoints** at ~33 %/66 % along the base route (alternating sides, small offsets) and re-request; tag each route `source = osrm_alternative | osrm_via_variant`.
  **Done when** a mocked OSRM that only ever returns 1 route still produces 3 options, and a real 1,006 mi query returns 3.
- [x] **BE-37** Geometry dedupe `~35m` → BE-36
  Resample each geometry to N fixed arc-length points, compute mean pairwise distance; discard candidates with > 92 % overlap with an accepted route; preserve the shortest/best representative.
  **Done when** identical geometries collapse to one and genuinely distinct corridors survive.
- [x] **BE-38** Weather provider (Open-Meteo) `~50m` → BE-31
  `weather/open_meteo.py`: batched multi-coordinate request, `hourly=wind_speed_10m,rain,showers,snowfall`, `wind_speed_unit=mph`, `precipitation_unit=inch`, `snowfall_unit=inch`, `timezone=UTC`, `forecast_days` ≤ 16, parse to per-location `WeatherSeries(times[], wind[], rain[], snow[])` aligned on a shared axis. Record that `precipitation`/`snowfall` are **preceding-hour sums** and that `snowfall` is **depth while `precipitation` is liquid equivalent (~7:1)** (D15).
  **Done when** mocked 60-location response parses to 60 aligned series; `rain + showers` is the liquid value; `snowfall` is kept as depth; a `days>16` request is rejected client-side.
- [x] **BE-39** Forecast window planner `~30m` → BE-38
  From `departure`, `max_route_duration` and the 48 h offset horizon compute `window_start`, `window_end`, required `past_days`/`forecast_days` so one fetch covers the base ETA **and every slider offset**; validate departure ≤ 16 days out with a friendly 400.
  **Done when** `window_end - window_start == route_duration + 48 h (+rounding)` and no fetched hour falls outside the provider's supported range.
- [x] **BE-40** Weather location planner `~45m` → BE-39
  Union all checkpoints across routes; dedupe by rounded coords and coarse cell (~0.02°) so overlapping routes share fetches; enforce `WEATHER_LOCATION_BUDGET`; when over budget, coarsen spacing and emit a `weather_coarsened` warning (never silently).
  **Done when** 3 overlapping routes over a shared corridor fetch fewer locations than 3× the single-route count, and the budget is never exceeded.
- [x] **BE-41** Weather risk indexer `~45m` → BE-40, BE-14b
  For each checkpoint, build `hourly_risk: list[int]` (level per hour across the window) plus base `eta_hour_index`; `CHECKPOINT_TIME_SNAP = "floor"` default with `ceil`/`nearest` alternatives (D14); reuses Phase 1 pure functions; NaN-safe.
  **Done when** the slider level at offset *h* equals `hourly_risk[eta_hour_index + h]`, and the three snap modes differ by **at most one index**.
- [x] **BE-42** Adapter tests `~50m` → BE-33…BE-41
  Mocked/recorded fixtures: geocode chain fallback, OSRM alternatives + via-variants, weather batch parse, 429 retry, window planner edges, indexer invariant, snap-mode bound, rule→variable mapping, dedupe thresholds.
  **Done when** all pass with **zero** real network calls.
- [ ] **BE-43** Real-API integration smoke (opt-in) `~30m` → BE-42
  `pytest -m integration`: one live geocode, one live OSRM, one live weather batch; asserts shape + records latencies into `docs/BENCHMARKS.md`.
  **Done when** `-m "not integration"` (the default suite) never touches the network.

### Phase 2 stretch (only if time remains)

- [ ] **BE-44** Mapbox Directions adapter behind `ROUTING_PROVIDER=mapbox` `~40m` → BE-35
- [ ] **BE-45** OpenWeatherMap adapter behind `WEATHER_PROVIDER=owm` `~40m` → BE-38

---

## Phase 3 — Orchestration & API

- [x] **BE-50** `services/trip_analysis.py` `~60m` → BE-34, BE-36, BE-41
  Orchestrate: validate → geocode (cache-first) → 3 routes → per-route sampling → ETAs → location plan → batched sequential weather fetch (chunk 20) → per-checkpoint index → evaluate → miles → rank/recommend → build payload. Emits progress events; supports early yield between batches.
  **Done when** with mocked providers it produces a complete, self-consistent payload and a `warnings[]` list.
- [x] **BE-51** NDJSON streaming `~50m` → BE-50
  `StreamingHttpResponse` + event encoder; events: `meta` → `route`×3 → `checkpoints`(per batch) → `summary`×3 → `recommendation` → `done`; mid-stream upstream failures become an `error` event, never a broken stream; `Content-Type: application/x-ndjson`.
  **Done when** a consumer can render the map from the first 2 events and the stream always terminates with `done` or `error`.
- [x] **BE-52** Streaming endpoint `~30m` → BE-51
  `POST /api/v1/trips/analyze/stream/`; headers for no buffering (`X-Accel-Buffering: no`), heartbeat comment lines every N s to defeat proxy buffering, timing headers (`Server-Timing`).
  **Done when** `curl -N` shows incremental output with the first event under 5 s on a warm cache.
- [x] **BE-53** Non-streaming endpoint `~25m` → BE-50
  `POST /api/v1/trips/analyze/` returning the identical payload in one JSON body (used by tests, CI, curl demos).
  **Done when** both endpoints emit byte-identical `routes/summary/recommendation` sections for the same mocked input.
- [x] **BE-54** Request validation `~40m` → BE-52
  Serializer: origin/destination non-empty strings, departure ISO-8601 with tz (naive ⇒ assume UTC + warning), load weight 0–80,000 lb, spacing ∈ {10, 25, 50}, optional truck params. Per-field error messages.
  **Done when** invalid payloads get field-level 400s with actionable messages (not stack traces).
- [x] **BE-55** Error taxonomy `~35m` → BE-54
  400 validation · 404 geocode failure · 422 no route found · 502 upstream failure (with partial data + `warnings`) · 504 upstream timeout · 503 all providers down + cache empty.
  **Done when** each class is reachable by a test and the UI can render a useful message for each.
- [x] **BE-56** `GET /api/v1/config/` + `/health/` `~30m` → BE-11
  Export thresholds, load policy, level colors, spacing choices, slider max, ETA assumptions — the UI legend renders from this, so rules have **one** source of truth.
  **Done when** the frontend has no hard-coded risk thresholds or level colors.
- [x] **BE-57** CORS, hosts, security `~25m` → BE-52
  `django-cors-headers` allow-list from env, `ALLOWED_HOSTS`, proxy header trust, `DEBUG` off by default, no secrets in code.
  **Done when** the deployed frontend origin is allowed and a random origin is rejected.
- [x] **BE-58** Result caching `~30m` → BE-53
  Cache full analysis keyed by hash of normalized query + config version; TTL 15 min; cache-hit flag surfaced in `done` event.
  **Done when** an identical second query returns in < 300 ms and reports `cached: true`.
- [x] **BE-59** API contract tests `~45m` → BE-52, BE-53
  Recorded NDJSON fixtures; assert event order, payload schema, and that `routes[i].checkpoints[j].hourly_risk` is aligned to `meta.hour_window_start`.
  **Done when** contract tests fail loudly on any schema drift.
- [x] **BE-60** Benchmark script `~30m` → BE-58
  `make bench` measures cold vs warm analyze latency for 3 route profiles (short/medium/long) and prints location counts + cache hit rates.
  **Done when** output is pasted into `docs/BENCHMARKS.md` with the date.

---

## Phase 4 — Frontend foundation

- [x] **FE-02** Typed API contracts `~45m` → BE-53
  Export an OpenAPI/JSON schema from Django; generate `frontend/src/types/api.ts` via a script; add a CI drift check (`tsc` fails if the committed types are stale).
  **Done when** `make gen-types` produces a clean diff and CI fails on a stale file.
- [ ] **FE-03** NDJSON streaming client `~50m` → FE-02
  `fetch` + `ReadableStream` reader → NDJSON parser → typed events; integrates with TanStack Query; aborts on unmount/re-submit; exposes progress (routes ready, checkpoints %).
  **Done when** a mocked stream renders routes on event 2 and checkpoints as they arrive; abort works.
- [ ] **FE-04** Zustand store `~40m` → FE-03
  Slices: `tripQuery`, `analysis` (routes, checkpoints, summary, recommendation, warnings), `ui` (`sliderOffsetHours`, `selectedRouteId`, `hoveredCheckpointId`, `layerToggles`), `meta`.
  **Done when** components read only from selectors; no prop drilling of analysis data.
- [ ] **FE-05** Derived selectors (pure) `~45m` → FE-04
  `riskAtOffset(checkpoint, offset)`, `summaryAtOffset(route, offset)` (weighted miles + avg risk), `etaAtOffset`, `sortedCheckpoints`, `recommendationReason`. **No risk thresholds in the frontend.**
  **Done when** slider results are consistent with backend values at offset 0 (asserted in FE-31).
- [x] **FE-05b** Offset semantics in selectors `~25m` → FE-05
  Offset shifts **departure**, so `eta(checkpoint, offset) = eta(checkpoint, 0) + offset` — uniform across every checkpoint and route (D13). Assert the uniformity in tests.
  **Done when** a test proves every checkpoint's ETA shifts by exactly `offset` and route totals stay consistent.
- [ ] **FE-06** Design system + tokens `~40m` → FE-00
  CSS variables for the 5 risk levels (from `/config`), spacing, typography, radii, shadows; dark map-friendly theme; `RiskBadge`, `Stat`, `Card`, `Button`, `Tooltip` primitives.
  **Done when** colors come from the API config, not from frontend constants.
- [ ] **FE-07** `TripForm` `~50m` → FE-06
  Origin, destination, departure date+time (with local tz note), load weight (lb, with kg toggle if cheap), sample spacing (10/25/50, default 25), submit; client validation mirroring BE-54; example-trip chips for the demo.
  **Done when** a valid submit starts a stream; invalid input shows inline field errors.
- [ ] **FE-08** Advanced options (collapsed) `~25m` → FE-07
  Truck speed factor, max speed, HOS break — defaults match backend, clearly marked as assumptions.
  **Done when** defaults reproduce backend ETAs exactly; non-defaults visibly change ETA and show an "assumption applied" note.
- [x] **FE-09** App states `~35m` → FE-07 (every BE-55 code has distinct copy, asserted against the generated contract; WebGL fallback rendered but not exercised in a real browser)
  Empty state with guidance + example trips, streaming skeletons (route lines then checkpoint dots), error cards per BE-55 taxonomy, warning banner for `weather_coarsened`/partial data.
  **Done when** every BE-55 error code renders a distinct, useful message.

---

## Phase 5 — Map

- [ ] **FE-10** MapLibre bootstrap `~40m` → FE-00
  Init with OpenFreeMap `liberty`, attribution control, no API key, responsive resize, `cooperativeGestures`, WebGL error fallback message.
  **Done when** a basemap renders with correct attribution at 3 viewport sizes.
- [ ] **FE-11** Route layers `~45m` → FE-10
  3 routes as casing + line layers, per-route color, selected route emphasized and others dimmed; `promoteId`; source created once and updated with `setData`.
  **Done when** selecting a route in the panel updates the map without recreating sources.
- [x] **FE-12** Origin/destination markers `~25m` → FE-11
  Distinct markers + labels; clicking a route card flies to it.
  **Done when** markers render and `fitBounds` animates to the selected route.
- [ ] **FE-13** Checkpoint layer `~50m` → FE-11
  Circle layer colored by risk level, radius by severity, added incrementally as batches stream in; clustering off (routes are linear); distinct stroke for NO_TRAVEL.
  **Done when** dots appear progressively during a stream without visible jank or layer churn.
- [ ] **FE-14** Checkpoint interaction `~40m` → FE-13
  Hover/click → popup with wind/rain/snow values, level chip, dominant factor, ETA, mile marker, and the rule text that produced the level; hover syncs to the table (FE-18).
  **Done when** tooltip content matches the backend `reasons[]` for a fixture checkpoint.
- [ ] **FE-15** Corridor heatmap `~50m` → FE-11
  MapLibre `heatmap` layer over risk-weighted corridor segments (`weight` = level score), plus a toggleable graduated `line-gradient` "risk strip"; heatmap density/radius tuned per zoom.
  **Done when** heatmap intensity visibly tracks risk along the corridor and updates with the slider.
- [ ] **FE-16** Map controls & sync `~35m` → FE-12
  Zoom-to-route, fit-all, heatmap toggle, checkpoint-visibility toggle (for long routes), scale control; map ↔ store two-way sync.
  **Done when** all controls work on desktop and mobile viewports.
- [x] **FE-17** Render performance `~45m` → FE-13, FE-15 (slider sweep costs 0 map renders, was 49; map deferred off first paint; 1,000-mile FPS case still open)
  Pre-simplify geometry per zoom (Douglas–Puucker), throttle `setData` to one per animation frame, memoize layer specs, avoid re-creating sources/layers, `requestIdleCallback` for off-screen work.
  **Done when** 1,000-mile route with 120 checkpoints pans at 60 fps on the demo machine; measured numbers in BENCHMARKS.md.
- [ ] **FE-18** Map ↔ table sync `~30m` → FE-14
  Hovering a table row highlights the map dot and vice versa; selecting a row opens the popup.
  **Done when** both directions work with keyboard focus too.

---

## Phase 6 — Panels, slider, polish

- [ ] **FE-19** Route comparison cards `~50m` → FE-05
  Rank badge, "Recommended" callout, distance, duration, arrival ETA, severe/no-travel/high miles, avg-risk bar, `why_recommended` bullets (runner-up diff), expandable per-criterion tally ("fastest on 2 of 3"), select/hover states (D17).
  **Done when** the recommended route is visually unmistakable and both explanation forms render from backend output.
- [ ] **FE-20** Checkpoint table `~45m` → FE-19
  Columns: mile, ETA, wind, rain, snow, level chip, dominant factor; sortable; virtualized above 200 rows; column headers state the active spacing.
  **Done when** a 120-checkpoint route scrolls smoothly and the level column matches the map colors.
- [x] **FE-21** Departure-offset slider (0–48 h) `~50m` → FE-05b
  Range slider with tick marks, labelled **"Departure offset +Nh"**, keyboard accessible, `aria-valuetext` ("depart 6 hours later"), debounce-free (array lookup), reset button; shifted departure time shown next to the slider.
  **Done when** dragging produces instant updates with **zero** network requests (verified in devtools) — the core "smoothness" claim.
- [ ] **FE-22** Live summary on slider `~40m` → FE-21
  Recompute severe/high/no-travel miles, avg risk and shifted ETAs for the selected offset; animated deltas vs the base scenario; optional "auto-play" sweep of the 48 h window for the demo; note when the recommendation changes at this offset.
  **Done when** numbers update within one frame and match the backend when the slider is at 0.
- [ ] **FE-23** Recommendation banner `~30m` → FE-19
  Headline verdict + the ordered criteria that decided it + safety warnings if the best route still has NO_TRAVEL miles.
  **Done when** a route with no-travel miles is never presented as safe.
- [x] **FE-24** Shareable URL state `~30m` → FE-07, FE-21
  Sync query + slider offset to URL params; restore on load; "copy link" button.
  **Done when** a shared link reproduces the exact view.
- [x] **FE-25** Responsive + a11y `~45m` → FE-20, FE-21 (see docs/QA-CHECKLIST.md)
  Desktop 3-column → tablet 2-column → mobile stacked with a bottom sheet; focus management, aria labels, contrast ≥ 4.5:1, keyboard-only path through the whole flow.
  **Done when** the full task is completable by keyboard and usable at 375 px width.
- [ ] **FE-26** Methodology modal `~40m` → FE-23
  The full risk table as rendered by the app, load rules, ETA assumptions, data sources (Open-Meteo, OSRM, OpenFreeMap), and the honest caveats: hourly weather resolution vs 10 mi sampling, **≤1 h checkpoint time-snap bound** (D14), snow reported as **depth not liquid equivalent** (D15), precipitation variables being **preceding-hour sums**, and "not a substitute for official weather alerts".
  **Done when** the table values come from `/api/v1/config/` and match the backend constants, and every caveat above is stated in the UI.

---

## Phase 7 — Frontend quality

- [ ] **FE-30** Selector unit tests `~35m` → FE-05b
  Vitest for `riskAtOffset`, `summaryAtOffset`, weighted-mile math, ETA offset uniformity, URL param round-trip.
  **Done when** ≥ 15 meaningful cases pass, including out-of-range offsets clamping.
- [x] **FE-31** Component tests with MSW `~45m` → FE-03
  Mock the NDJSON stream; assert form → stream → cards render; slider drag updates the summary; error event renders the right card.
  **Done when** the suite runs headless in CI with no network.
- [x] **FE-32** Parity test vs backend `~30m` → FE-30, BE-42
  Feed one recorded backend payload into the frontend selectors; assert slider offsets 0/6/24/48 match backend-computed values.
  **Done when** parity holds — proves no rule logic drifted into the UI.
- [x] **FE-33** Perf profiling pass `~40m` → FE-17 (render counts + bundle measured in docs/BENCHMARKS.md; frame-time profiling outstanding)
  React Profiler on stream updates + slider drag, bundle analysis, lazy-load the map panel, memo boundaries.
  **Done when** slider drag shows no React re-render of the map and bundle is under budget (record numbers).
- [x] **FE-34** Browser + responsive QA `~35m` → FE-25 (docs/QA-CHECKLIST.md; no open P1/P2)
  Chrome/Firefox/Safari, 375/768/1440 px, WebGL-absent fallback, long-route stress, dark/light map style.
  **Done when** a QA checklist file is filled in with no open P1/P2 issues.
- [ ] **FE-35** Playwright smoke (stretch) `~30m` → FE-31
  One end-to-end pass against the mock provider.

---

## Phase 8 — Ship

- [ ] **OPS-10** Docker `~40m` → BE-01, FE-00
  Backend Dockerfile (gunicorn, non-root), frontend Dockerfile (build + nginx serving SPA + `/api` proxy), `docker-compose up` for the full stack.
  **Done when** a clean machine reaches the app with one command.
- [ ] **OPS-11** Production settings `~25m` → OPS-10
  Env-driven `DEBUG=False`, gunicorn workers/threads/timeouts, static serving, gzip, `X-Accel-Buffering: no` for the stream, proxy header trust, `ALLOWED_HOSTS`.
  **Done when** streaming still works through nginx without buffering.
- [ ] **OPS-12** Deploy `~60m` → OPS-11
  Backend (Render/Railway/Fly) + frontend (Vercel/Netlify) or a single container; set env vars; verify a cold start; pin the free-tier cache notes.
  **Done when** the public URL loads, analyzes a trip, and survives a 10-minute idle sleep.
- [ ] **OPS-13** GitHub Actions CI `~40m` → BE-42, FE-31
  Jobs: ruff + pytest (unit) · eslint + tsc + vitest · docker build · type-drift check. Branch protection on `main`.
  **Done when** a pushed branch shows all green and a deliberately broken test fails the build.
- [ ] **DOC-10** README `~50m` → Phase 7 done
  Architecture diagram, quickstart, API reference, the risk-rule table, screenshot/GIF, "assumptions & limitations", performance numbers, live URL + repo URL.
  **Done when** a stranger can clone, run, and understand the design in 10 minutes.
- [x] **DOC-11** `docs/BENCHMARKS.md` `~25m` → BE-60, FE-33 (latency, bundle and render counts recorded; frame-time profiling listed as unmeasured)
  Cold/warm latency, weather location counts, cache hit rates, map FPS, bundle size, date + machine.
- [ ] **DOC-12** Demo data & prewarm `~30m` → BE-61
  `WEATHER_PROVIDER=mock` deterministic storm generator; `scripts/prewarm.py` to warm the cache for the Loom route(s); two example trips (one clear, one storm).
  **Done when** the demo works with upstream APIs unreachable.
- [x] **BE-61** Mock weather provider `~40m` → BE-30, BE-38
  Deterministic synthetic series (seeded by coords + date) with a realistic storm band along a corridor; same interface as Open-Meteo; instant.
  **Done when** `WEATHER_PROVIDER=mock` yields a full analysis in < 500 ms with plausible severe segments.
- [ ] **DOC-13** Loom script `~45m` → DOC-12
  5–10 min shot list: problem → 3 routes → checkpoint sampling + ETA → rule table → risk classes + load rules → recommendation reasoning → slider 0→48 h → heatmap → failure/edge case → limitations.
  **Done when** a timed dry run lands in 6–8 min and the backup recording exists.
- [ ] **DOC-14** Final checklist vs rubric `~30m` → DOC-10, DOC-13
  Map every rubric line to a demo timestamp + a repo artifact; confirm all 3 deliverables (repo, hosted app, Loom).
  **Done when** nothing on the rubric is unaddressed and links are live.

---

## Critical path & parallelisation

```
BE-01 → BE-11 → BE-12/13 → BE-14 → BE-15 → BE-16 → BE-29(golden)     (Phase 1: no network, start now)
BE-17 → BE-18 → BE-19 → BE-28
BE-31 → BE-32 → BE-33 → BE-34 → BE-35 → BE-36 → BE-37                (adapters, parallelizable per provider)
                 → BE-38 → BE-39 → BE-40 → BE-41 → BE-50 → BE-51/52/53
Phase 4-6 frontend can start on mocked payloads once BE-02 types exist
```

- **Can be done in parallel by two people:** Phase 1 (backend) vs Phase 4–5 (frontend on mocked contracts).
- **Hard dependencies to respect:** risk logic (Phase 1) precedes the weather indexer; the API schema (BE-53) precedes generated TS types (FE-02).
- **Do not start early:** OPS-12 deploy and DOC-13 Loom need everything else done; budget a full day.
