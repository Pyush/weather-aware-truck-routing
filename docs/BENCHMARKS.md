# Benchmarks

Measured numbers rather than estimates, so the performance claims in `docs/TASKS.md`
and `docs/DECISIONS.md` rest on something reproducible. Each row says how it was
measured and when, because a number without its method is a rumour.

**Environment** — Chromium (browser pane), Linux container, mock weather provider
(`WEATHER_PROVIDER=mock`) unless noted. Backend on `:8000`, Vite dev server on
`:5173`, production build served by `vite preview` on `:4173`.

---

## Backend — live end-to-end (Chicago → Detroit, 25 mi spacing)

Recorded during Phase 3 against the **real** upstream APIs, so these include
network time and a 503 retry.

| Stage | Time |
|---|---|
| Geocode both endpoints (Open-Meteo) | **1.1 s** |
| 3 routes (2 OSRM alternatives + 1 via-waypoint variant) | **1.3 s** |
| 40 checkpoints → 25 unique locations after dedupe | instant |
| Weather, 25 locations, 2 batches | **19.1 s** (one 503 retried) |
| **Geometry usable (first events)** | **~2.5 s** |
| **Full analysis** | **~21 s** |

The 2.5 s / 21 s split is the entire justification for the NDJSON endpoint: the
map is usable long before the weather tail lands.

| Scenario | Time |
|---|---|
| Mock provider, full analysis | **< 0.5 s** |
| Cached repeat request | served from cache, `meta.cached: true` |

---

## Frontend — bundle (production build)

The map is lazily imported and mounted only once there is an analysis to draw, so
a first-time visitor never downloads the renderer.

| Chunk | Raw | Gzip | When |
|---|---:|---:|---|
| `index` (app + form + panels) | 277.8 kB | **85.7 kB** | first paint |
| `state` (zustand) | 8.5 kB | 3.3 kB | first paint |
| `rolldown-runtime` | 0.6 kB | 0.4 kB | first paint |
| `MapView` | 10.3 kB | 3.7 kB | on submit |
| `maplibre` | 1,035.8 kB | **280.9 kB** | on submit |
| `maplibre-gl-worker` | 507.8 kB | — | on submit |
| **First-paint transfer** | | **~90 kB** | |

**Before this pass** MapLibre was in the initial chunk: first paint transferred
~371 kB gzipped instead of ~90 kB. Measured via
`performance.getEntriesByType('resource')` on the production build.

---

## Frontend — render cost of the departure slider

The architectural claim is that dragging 0 → 48 h repaints the map **without**
re-rendering the React component that owns the WebGL context
(`frontend/src/renderCost.test.tsx`).

| Scenario | Map component renders across 49 offset changes |
|---|---:|
| Before (offset passed down from `App`) | **49** |
| After (imperative store subscription + `setData`) | **0** |

Both numbers come from the same test: re-introducing the prop threading makes it
fail with `map re-rendered 49 time(s)`, so the guard is live rather than
vacuous. The offset repaint itself still happens — `syncSources()` is subscribed
directly to the store and calls `setData`, which needs no render.

---

## Not yet measured

These are stated as open in `docs/TASKS.md` (FE-17, FE-33) rather than guessed:

- **1,000-mile route with 120 checkpoints at 60 fps.** Needs a fixture of that
  shape and frame-time capture; the current sample is a ~500 mi corridor.
- **Map pan/paint frame times** under load (Chrome tracing, not yet run).
- **Cold vs warm cache hit rate** over a realistic query mix.

Recording a number here means it was measured. An unmeasured claim belongs in the
task list, not in this file.

## Live third-party APIs

<!-- live-api:begin -->

### Live API latencies — recorded 2026-10-18T07:11:10+00:00

| Leg | Samples | Median | Slowest |
|---|---:|---:|---:|
| `geocode` | 2 | **0.148 s** | 0.148 s |
| `route` | 1 | **0.884 s** | 0.884 s |
| `weather` | 1 | **0.136 s** | 0.136 s |

Measured against the live APIs: 3 route options returned, 2 weather locations, 57-hour forecast window.

Reproduce with `make bench-integration`. These are *recorded, not asserted* — a hard threshold would turn a provider's slow day into a red build.
<!-- live-api:end -->
