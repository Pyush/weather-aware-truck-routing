# Architecture

This document describes the implemented system. Start with the deployment view,
then follow one analysis request through the backend and back into the UI.

## Deployment and component boundaries

```mermaid
flowchart LR
    user[Driver's browser]
    tiles[OpenFreeMap<br/>style and tiles]
    geo[Open-Meteo geocoding<br/>Photon fallback]
    roads[Public OSRM<br/>route alternatives]
    forecast[Open-Meteo forecast<br/>or local mock provider]

    subgraph compose[Docker Compose stack]
        nginx[nginx<br/>frontend container :80<br/>published as localhost:5173]
        api[Gunicorn + Django REST API<br/>backend container :8000]
        cache[(Django cache<br/>locmem by default;<br/>Redis if configured)]
        redis[(Redis container<br/>optional cache backend)]
        nginx -- /api/v1/*, unbuffered --> api
        api -. cache reads/writes .-> cache
        cache -. only with CACHE_BACKEND=redis .-> redis
    end

    user -- same-origin HTML and hashed JS/CSS --> nginx
    user -- POST analysis / GET config --> nginx
    user -- map style and tiles directly --> tiles
    api -- resolve place names --> geo
    api -- fetch route geometries --> roads
    api -- fetch hourly weather --> forecast
```

- `docker-compose.yml` starts the three containers. Only nginx publishes a host
  port. The backend is reached over the internal Compose network; Redis is
  present but **not used by default** (`CACHE_BACKEND=locmem`). Set the cache
  backend to Redis when multiple Gunicorn workers should share cached results.
- `frontend/docker/nginx.conf` serves the built SPA and proxies `/api/` to Django.
  It disables response buffering so NDJSON arrives incrementally, and gives
  hashed assets long-lived caching while keeping `index.html` fresh.
- The browser fetches OpenFreeMap independently of Django. Map tiles do not
  pass through the API or Redis. In local development, Vite proxies `/api` to
  Django instead of nginx (`frontend/vite.config.ts`).
- Compose defaults to **mock weather**; `WEATHER_PROVIDER=open_meteo` switches to
  real forecasts. Geocoding and routing still use external, keyless providers.
  The mock is also used in offline tests. The production settings module is
  `backend/config/settings/prod.py`; a real deployment must supply its own
  secret, hosts, and TLS termination configuration.

## Backend layers

```mermaid
flowchart TB
    views[API boundary<br/>api/serializers.py, views.py, errors.py]
    service[TripAnalysisService.stream<br/>services/trip_analysis.py]
    registry[ServiceRegistry<br/>provider selection / dependency injection]
    adapters[Adapters<br/>geocoding.py, routing.py, weather.py,<br/>mock_weather.py, indexing.py]
    geo[Pure geometry and ETA domain<br/>domain/geo/]
    risk[Pure risk domain<br/>domain/risk/]
    views -->|validated TripQuery| service
    service --> registry --> adapters
    service --> geo
    service --> risk
    adapters -->|hourly series and coordinates| service
    geo -->|checkpoints and ETAs| service
    risk -->|levels, mileage, ranking| service
    service -->|event generator| views
```

1. `api/serializers.py` validates the request before a streaming HTTP response
   begins. `api/views.py` translates provider errors into public error codes.
2. `ServiceRegistry.from_settings()` injects the geocoder chain (Open-Meteo,
   then Photon), OSRM router, selected weather provider, and policy settings.
   `adapters/http.py` owns provider HTTP calls. The domain has no Django import
   or network I/O.
3. `domain/geo/` samples route geometry at the requested spacing and computes
   truck-adjusted checkpoint ETAs. `adapters/weather.py` plans the forecast
   window and deduplicates/coarsens weather locations to a configured budget.
   Forecast requests are batched sequentially rather than fanned out.
4. `adapters/indexing.py` evaluates each checkpoint for every forecast hour via
   `domain/risk/`. The base-hour assessment has detailed readings and reasons;
   `hourly_risk` contains the level ordinals for later departure choices.
   `domain/risk/miles.py` attributes distance by checkpoint midpoints, and
   `domain/risk/recommend.py` ranks routes lexicographically: fewer No Travel,
   Severe, and High miles, then average risk, drive time, and distance.
5. `TripAnalysisService.stream()` yields events. The JSON endpoint drains the
   same generator through `analyze()`, rather than implementing a second
   calculation path.

## A streaming analysis, in order

```mermaid
sequenceDiagram
    actor Driver
    participant UI as React + Zustand
    participant Proxy as nginx /api proxy
    participant API as Django API
    participant Service as TripAnalysisService
    participant Geo as Geocoder + OSRM
    participant Weather as Weather provider

    Driver->>UI: Submit origin, destination, departure, load, spacing
    UI->>Proxy: POST /api/v1/trips/analyze/stream/ (AbortController)
    Proxy->>API: Forward request without response buffering
    API->>API: Validate request and check analysis cache
    API->>Service: stream(TripQuery)
    Service->>Geo: Resolve both places and obtain route alternatives
    Geo-->>Service: Coordinates and route geometry
    Service-->>API: meta, route x N, weather_plan
    API-->>UI: NDJSON lines as they are yielded
    UI->>UI: Fold route events and mount MapView lazily
    Service->>Weather: Fetch deduplicated hourly series in batches
    Weather-->>Service: Forecast window per location
    Service->>Service: Classify hours, summarise miles, and rank routes
    Service-->>API: weather, checkpoints + summary per route, recommendation, done
    API-->>UI: Remaining NDJSON lines
    UI->>UI: Update cards, table, map sources, and recommendation
    UI-->>Driver: Interactive result
```

The successful event sequence is `meta`, one or more `route` events,
`weather_plan`, `weather`, then `checkpoints` and `summary` for each route,
`recommendation`, and `done`. Geometry can display before the slower forecast
finishes. Invalid input receives a normal JSON 400 **before** the stream opens;
failure after it opens is a terminal NDJSON `error` event (the status can no
longer change). The client rejects a stream that ends without `done` or `error`.

`POST /api/v1/trips/analyze/` returns the full result as JSON. Its successful
responses populate the analysis cache (default TTL from settings); the stream
endpoint can replay that cached payload but does not itself populate the cache.
`GET /api/v1/config/` exposes the server's thresholds, levels, colours, ranking
and assumptions. `GET /api/v1/health/` is a liveness endpoint that does not call
upstream providers. See `backend/tripplan/api/urls.py` for routes.

## Browser state and rendering

```mermaid
flowchart LR
    form[TripForm] -->|submit| client[lib/api.ts<br/>NDJSON reader]
    config[GET /api/v1/config/] -->|rules and colours| store[(Zustand useStore)]
    client -->|one event at a time| store
    store -->|analysis and selection| panel[RoutePanel<br/>comparison + checkpoint table]
    store -->|analysis and selection| map[Lazy MapView<br/>MapLibre sources/layers]
    store -->|offsetHours| selectors[lib/selectors.ts<br/>hourly lookup + route views]
    selectors --> panel
    selectors --> slider[ForecastSlider]
    store -->|offset subscription, no map rerender| map
    store --> url[lib/urlState.ts<br/>shareable query + offset]
```

- `frontend/src/App.tsx` loads configuration, restores shared-link inputs,
  submits and aborts streams, and mounts `MapView` only after route geometry
  exists. `frontend/src/store/useStore.ts` folds events into one analysis and
  stores selection, hover, layer toggles, and the departure offset. This is a
  direct stream reader, **not** a TanStack Query request.
- The departure slider reads the backend-precomputed
  `hourly_risk[eta_hour_index + offsetHours]`. Changing it makes **no** weather
  request and does not reclassify risk in JavaScript. Selectors recalculate
  route summaries from the shifted levels; MapLibre sources and the optional
  risk strip repaint through a store subscription without remounting the map.
- `frontend/src/components/MapView.tsx` draws route lines, endpoints,
  checkpoints, corridor heatmap, and an opt-in graduated risk strip. Style and
  tile loading is asynchronous; interactions and layer visibility attach after
  the MapLibre `load` event. Table hover and map hover share checkpoint IDs.
- URLs contain inputs and offset, not the large analysis payload. Opening a
  link restores the form and departure choice; a new analysis still requires
  submission. The backend-generated contract fixture in
  `frontend/src/types/contract.json` is checked for drift in CI.

## Important boundaries and limitations

The server is the sole authority for thresholds, risk classification, and
ranking. The browser receives only a base-hour detailed reading and per-hour
level ordinals: shifting the slider changes levels and ETAs, but it cannot
reconstruct detailed wind/rain/snow readings or reasons for another hour.
Weather is hourly even when checkpoints are sampled every 10 miles. Provider
failures during weather fetch degrade to a warning and unavailable risk data;
they must not be interpreted as a safe forecast. For measured performance and
remaining browser verification, see [BENCHMARKS.md](BENCHMARKS.md) and
[QA-CHECKLIST.md](QA-CHECKLIST.md).
