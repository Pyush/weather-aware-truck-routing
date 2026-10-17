# Weather-Aware Truck Routing — Implementation Plan

React + Django web app: a truck driver enters origin, destination, departure date/time and
load weight; the app returns **3 route options**, samples **weather every 10 / 25 / 50 miles at the
truck's ETA**, classifies **weather risk per checkpoint using the supplied risk/load rules**,
**recommends the best route**, and renders **routes, checkpoints, risk, ETAs on a map with a
weather heatmap and a 0–48 h forecast slider**.

This document is the architecture + roadmap. The executable backlog is
[`docs/TASKS.md`](docs/TASKS.md). Rule semantics and rationale are in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## 1. Stack (all keyless free tiers — verified reachable)

| Concern | Choice | Why |
|---|---|---|
| Backend | Python 3.14, Django 6.1, DRF 3.18, httpx 0.28 | Assessment requires Django |
| Backend tests | pytest 9 + pytest-django | Fast parametrized boundary matrices |
| Frontend | Vite 8, React 19, TypeScript (strict) | Assessment requires React |
| Map | MapLibre GL 6 + OpenFreeMap `liberty` style | Keyless basemap, GPU-accelerated layers/heatmap |
| Server state | TanStack Query 5 | Caching, retries, streaming-friendly |
| UI state | Zustand | Slider/selection state without prop drilling |
| Routing API | OSRM demo server | Keyless; returns alternatives + geometry |
| Geocoding | Open-Meteo geocoding, Photon fallback | Both keyless (see risk R1) |
| Weather API | Open-Meteo forecast | Keyless, batch multi-coordinate, hourly series |
| Cache | Django cache (LocMem → Redis optional) | No database needed; app is stateless |

**Deliberate simplification:** no relational database. The app stores no user data; all state
lives in the request/response and the cache. This removes an entire class of migration/auth work.
(Optional trip-history feature is explicitly deferred — see DECISIONS.md D9.)

## 2. Verified facts about the free APIs (measured, not assumed)

These measurements drove the architecture. Re-run via `make bench` (OPS-10 / BE-60).

| Observation | Measurement | Consequence |
|---|---|---|
| OSRM `alternatives=true` on a 1,006 mi trip | **2** routes returned | Need an explicit 3rd-route strategy (via-waypoint variants) |
| OSRM car profile, Denver→Chicago | 1,006 mi in 17.8 h ⇒ **56.4 mph** implied | Truck ETA needs its own speed model, not raw OSRM duration |
| Nominatim from this host | **HTTP 403 Access denied** (datacenter IP blocked) | Geocoder = Open-Meteo first, Photon fallback, Nominatim optional |
| Open-Meteo geocoding + Photon | 200 OK, good results | Confirms keyless geocoding path |
| Open-Meteo cost model | **≈ 4 s fixed + 0.53 s per location**, weakly dependent on hours requested | Weather latency is ~linear in location count — the #1 design constraint |
| Open-Meteo 6 parallel requests | **HTTP 429 Too Many Requests** | Sequential batches of ~20 locations + backoff; never fan out |
| Open-Meteo `forecast_days` | allowed 0–16 (17 ⇒ 400) | Validate departure ≤ 16 days out |
| Open-Meteo multi-coordinate | 60 coords ⇒ one response, shared 72 h time axis | One batched request covers all routes *and* the whole slider window |
| Open-Meteo `past_days` | works (2 days ⇒ first hour = today−2) | Needed to fetch weather for a *past/soon* departure window |
| Open-Meteo variable table | `precipitation` = *"Total precipitation (rain, showers, snow) sum of the preceding hour"*, but expressed as **liquid water equivalent**; `snowfall` is **snow depth** | snow_depth ÷ liquid_equiv = **6.94–7.08 : 1** (measured, 3 locations). Rain rule ← `rain + showers`; Snow rule ← `snowfall`. Feeding liquid `precipitation` into the *Snow* rule would understate snow by ~7× (D15) |
| Weather temporal resolution | **1 hour** | vs. 10 mi ≈ 13 min of driving at 45 mph ⇒ 10/25 mi modes give similar profiles; checkpoint time-snap bound is ≤ 1 h (D14) |

### The central performance consequence

A 1,000 mi trip at 25 mi spacing = ~40 checkpoints/route × 3 routes = ~120 locations
⇒ **~65 s of weather calls**, unavoidable on a cold cache. Therefore:

1. **Stream the response (NDJSON).** Geometry + summary reach the browser in ~2–4 s; checkpoint
   risk fills in progressively as batches resolve. Perceived latency ≈ OSRM round trip.
2. **Fetch one hourly *series* per location, not one timestamp.** Hours are nearly free in the cost
   model, so one fetch serves the base ETA *and* all 48 slider positions ⇒ the slider needs
   **zero network calls** and recomputes by array lookup.
3. **Dedupe locations across all 3 routes** (routes share a corridor) and cap the budget.
4. **Cache aggressively** (per-location hourly series, plus whole-result cache) so repeats are instant.
5. **Mock weather provider** (`WEATHER_PROVIDER=mock`) so CI, offline dev and the Loom demo never
   depend on an upstream 200/429.

## 3. Architecture

```
┌──────────── React (Vite/TS) ────────────┐        ┌──────── Django + DRF ────────┐
│ TripForm → useTripStream (NDJSON reader) │──POST─▶│ validate → geocode            │
│ Zustand: analysis + sliderOffset + sel.  │◀─stream│ → OSRM (3 routes)            │
│ MapLibre: routes / checkpoints / heatmap │        │ → sample @ 10/25/50 mi        │
│ RouteCards · CheckpointTable · Slider    │        │ → ETA (truck speed model)     │
└──────────────────────────────────────────┘        │ → dedupe weather locations    │
                                                     │ → Open-Meteo batched series   │
Map/UI never recompute risk: backend ships a     │ → evaluate risk (pure fns)    │
per-hour risk level array per checkpoint, so the       → miles + rank + recommend    │
slider is an O(1) lookup + a weighted sum.      │ → NDJSON events               
                                                     └────────────────────────────────┘
```

**Domain logic is pure Python, isolated from I/O** (`routing/risk/`, `routing/geo/`). Adapters
(`geocoding/`, `weather/`, `routing/`) speak small protocols so each is swappable and mockable.
This is what makes the 40 %-weighted correctness testable without network calls.

### Risk model (the graded core)

Per checkpoint, classify `wind`, `rain`, `snow` independently against the assessment table, apply
**load escalation** to the wind bucket, then `checkpoint_level = max(wind, rain, snow)`.

```
wind  ≥55 mph                → NO_TRAVEL            (any load)
wind  45–54 mph + load >30k → NO_TRAVEL
wind  35–44 mph + load >40k → at least SEVERE
checkpoint level = max(wind_level, rain_level, snow_level)
```

Route scoring: segment miles attributed to each checkpoint by **midpoint boundaries** (checkpoint
*i* owns the road between the midpoints to its neighbours) ⇒ `no_travel_miles`, `severe_miles`,
`high_miles`, `avg_risk = Σ(miles × score)/total_miles`. Ranking is lexicographic over the spec's
4 criteria, preceded by a documented no-travel safety gate (DECISIONS.md D5).

## 4. Roadmap (detail and acceptance criteria in docs/TASKS.md)

| Phase | Outcome | Tasks |
|---|---|---|
| **0. Foundations** | Repo, tooling, decision log | BE-00…BE-02, FE-00…FE-01, DOC-00 |
| **1. Domain core** | Pure risk/geo/ETA logic + exhaustive boundary tests — **do first**, it is 40 % of the grade and needs no network | BE-10…BE-29 |
| **2. Adapters** | Geocoding / OSRM routing / Open-Meteo weather, with retry, cache, dedupe | BE-30…BE-45 |
| **3. API layer** | Orchestration, NDJSON streaming, validation, error taxonomy, caching | BE-50…BE-61 |
| **4. FE foundation** | Typed contracts, store, streaming query, design tokens, trip form | FE-02…FE-09 |
| **5. Map** | Routes, checkpoints, corridor heatmap, interaction | FE-10…FE-18 |
| **6. Panels + slider** | Comparison cards, checkpoint table, 0–48 h slider, live summary | FE-19…FE-26 |
| **7. Quality** | Vitest/MSW, perf pass, a11y, responsive | FE-30…FE-34 |
| **8. Ship** | Docker, deploy, CI, README, Loom | OPS-10…OPS-14, DOC-10…DOC-14 |

### Suggested build order for maximum score per hour

1. **Phase 1** end-to-end (domain + tests) — zero external dependencies, unblocks everything.
2. **Phase 2 + 3** narrow slice: geocode → OSRM → sample → ETA, streaming skeleton with **mock**
   weather. Working end-to-end demo early.
3. **Phase 4 + 5** real map with 3 routes + checkpoints (mock data) — de-risks the 40 % UI score.
4. Swap in real weather (BE-39/40/42), then **Phase 6** slider/heatmap (the showpiece).
5. **Phase 7–8** hardening, deploy, Loom. Leave ≥1 day buffer for deploy + recording.

## 5. Risks & mitigations

| ID | Risk | Mitigation |
|---|---|---|
| R1 | Nominatim 403 from datacenter IPs | Open-Meteo + Photon providers; Nominatim optional (BE-34) |
| R2 | Open-Meteo 429 / cold-cache latency 30–65 s | Sequential batching, backoff, dedupe, caching, NDJSON streaming, mock provider (BE-41, BE-51, BE-61) |
| R3 | OSRM returns < 3 distinct routes | Via-waypoint variant generator + geometry dedupe, honest "only N distinct routes" messaging (BE-36, BE-37) |
| R4 | Rule-boundary ambiguity (25 vs 25.0, >30,000 vs 30,000) | Explicit inclusivity flags transcribed from the table + golden-matrix tests + DECISIONS.md (BE-11, BE-29) |
| R5 | Frontend/Backend risk logic drift | Backend emits per-hour risk arrays; UI does lookup + weighted sum only. Schema parity test (BE-51, FE-02, FE-30) |
| R6 | Free deploy hosts cold-start / sleep | Pre-warming script for the demo route + cache docs (DOC-12) |
| R7 | Free-host rate limits in the Loom | Pre-cached demo trip + recorded fallback (DOC-13) |

## 6. Definition of done (mapped to the rubric)

| Rubric | Evidence produced |
|---|---|
| 40 % routes, weather, risk & recommendation | Phase 1 pure logic + golden boundary matrix; 3 real routes; ETA-sampled weather; lexicographic ranking with reasons |
| 40 % UI, map & heatmap smoothness & performance | MapLibre heatmap/corridor layers, instant 0–48 h slider (no refetch), NDJSON progressive render, perf measurements in `docs/BENCHMARKS.md` |
| 15 % code quality | Layered architecture, protocols + mocks, ruff/eslint/tsc strict, CI |
| 5 % docs & walkthrough | README, DECISIONS.md, BENCHMARKS.md, scripted 5–10 min Loom |
