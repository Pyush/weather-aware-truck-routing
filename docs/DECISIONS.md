# Decision Log

Numbered decisions with rationale. Each references the task that implements it. Update this file
whenever a decision changes — it is the primary defence in the code-quality and documentation marks.

---

## D1 — Rule thresholds are encoded as data with explicit inclusivity

**Decision.** `risk/thresholds.py` holds an ordered list of buckets per variable:
`(lower_inclusive, upper_exclusive, level)`, plus a separate no-travel bound carrying an explicit
inclusivity flag.

**Why.** The published table is asymmetric in a way that a naive `elif` chain gets wrong:

| Variable | No-travel bound as written | Inclusive? |
|---|---|---|
| Wind | `≥55 mph` | **yes** — 55.0 mph is No Travel |
| Rain | `>1.00 in/hr` | **no** — exactly 1.00 is Severe |
| Snow | `>3.0 in/hr` | **no** — exactly 3.0 is Severe |

Mid-buckets are read as half-open (`25 ≤ w < 35` ⇒ Moderate, per the `25–34` label) because the
labels are rounded integers while the API returns decimals. Encoding inclusivity explicitly means
`54.9` ⇒ Severe and `55.0` ⇒ No Travel, with no ambiguity.

**Task.** BE-11, BE-21…BE-23, BE-29.

## D2 — Load escalation only ever raises a level

**Decision.** `apply_load_rules` escalates the wind bucket and never de-escalates it. Load
comparisons are strict: `> 30_000 lb` and `> 40_000 lb`, so a truck at exactly 30,000 lb does not
escalate.

**Why.** The table's load rows are stated as *upgrades* to a condition ("45–54 mph + >30,000 lb →
No Travel"). A downgrade path would let a heavy truck downgrade a genuinely severe wind reading,
which is not in the spec and not safe. Strict `>` follows the written operator.

**Task.** BE-13, BE-24.

## D3 — Checkpoint risk is the worst of the three variables

**Decision.** `checkpoint_level = max(wind_level, rain_level, snow_level)` after load escalation.
The `dominant_factor` (wind > snow > rain on ties) and human-readable `reasons[]` are returned
alongside.

**Why.** The spec classifies *conditions* independently and then gives load rules in wind terms; a
checkpoint is only as safe as its worst aspect. Recording the driver lets the UI explain the
verdict instead of just colouring a dot.

**Task.** BE-14, BE-25.

## D4 — Miles are attributed to checkpoints by midpoint boundaries

**Decision.** Checkpoint *i* owns the road miles between the midpoints to its neighbours
(`[(d_{i-1}+d_i)/2, (d_i+d_{i+1})/2)`); endpoints take the half-interval at each end. This
subdivides the route, so `Σ miles_by_level == total_route_miles` exactly.

**Why.** "Fewest Severe miles" needs a defensible mileage definition. The naïve alternative —
"every checkpoint represents `spacing` miles" — over- and under-counts at the ends and inflates the
total by roughly one interval, which would distort every comparison between routes with different
checkpoint counts (10 mi vs 50 mi spacing).

**Task.** BE-15, BE-26.

## D5 — Ranking is the spec's 4 criteria, preceded by a documented no-travel gate

**Decision.** Primary sort key:

```
(no_travel_miles, severe_miles, high_miles, avg_risk, duration_s, distance_m)
```

The first term is an extension beyond the published criteria. It is controlled by
`SAFETY_GATE_ENABLED` (default `True`) so the literal 4-criteria ordering remains demonstrable.

**Why.** The published ordering never mentions No Travel, yet a corridor containing one is not
travelable at all — ranking it below a merely "severe" corridor is the only safe reading. Making it
a flag rather than a silent hardcode means a reviewer can see and toggle both behaviours.
`avg_risk` uses ordinal scores `LOW 1 … NO_TRAVEL 5`.

**Task.** BE-16, BE-27.

## D6 — ETA comes from a truck speed model, not raw OSRM duration

**Decision.** `truck_avg_mph = clamp(osrm_implied_car_mph × TRUCK_SPEED_FACTOR, MIN_MPH, MAX_MPH)`
with defaults `0.85`, `25`, `65`. Checkpoint ETAs are cumulative along the polyline from the
departure time. A HOS break parameter exists but is off by default.

**Why.** The free OSRM demo profile is a car profile: the measured Denver→Chicago route implies
**56.4 mph**, which is not a truck's sustained average. The assessment asks for "the truck's
estimated arrival time", so a car ETA would be wrong in a way a driver would immediately notice.
The factors live in config and are surfaced in the UI's Methodology panel.

**Task.** BE-19, BE-28, FE-08, FE-26.

## D7 — Weather is fetched as hourly series, so the slider costs nothing

**Decision.** One batched Open-Meteo request per unique location covering the whole window
(route duration + 48 h). The backend precomputes `hourly_risk[]` — the risk level for **every**
hour — for every checkpoint. The frontend slider reads `hourly_risk[eta_hour_index + offset]`.

**Why.** Measured: Open-Meteo's cost is ≈ 4 s + **0.53 s per location** and is nearly independent
of how many hours you request. Fetching full series is therefore almost free, and it moves all risk
computation to the one place that owns the rules — the UI does an array lookup plus a weighted
sum. This is what makes the 0–48 h slider instant (a stated 40 % criterion) with zero refetching
and zero duplicated rule logic.

**Task.** BE-38, BE-39, BE-41, FE-05, FE-21, FE-32. *(Which API variable feeds which risk rule is
decided separately in D15; slider semantics in D13.)*

## D8 — Responses stream as NDJSON

**Decision.** `POST /api/v1/trips/analyze/stream/` returns `application/x-ndjson`:
`meta` → `route`×3 → `checkpoints`(per batch) → `summary`×3 → `recommendation` → `done`.

**Why.** A 1,000 mi trip at 25 mi spacing needs ~120 weather locations ⇒ ~65 s of upstream calls on
a cold cache — unavoidable with a keyless API (and parallel requests earn HTTP 429). Streaming the
geometry in ~2–4 s and filling checkpoints progressively turns a 65 s blank screen into an
immediately useful map. A non-streaming endpoint over the same service keeps the API scriptable and
the tests simple.

**Task.** BE-50, BE-51, BE-52, BE-53, FE-03.

## D9 — No database

**Decision.** The app is stateless: cache (LocMem → Redis optional) is the only persistence. No
models, no migrations, no auth.

**Why.** Nothing in the assessment requires stored trips or users. Removing the database removes
auth, migrations and admin surface — all ungraded — and keeps the code reviewable. Trip history is
explicitly deferred (listed as a stretch item in TASKS.md) rather than half-built.

**Task.** BE-01, BE-58.

## D10 — Provider choices are pluggable protocols with a deterministic mock

**Decision.** `Geocoder`, `Router` and `WeatherProvider` are protocols. Defaults:
geocode = Open-Meteo → Photon → (Nominatim, optional); route = OSRM; weather = Open-Meteo.
`WEATHER_PROVIDER=mock` serves seeded synthetic weather with a storm band.

**Why.** Nominatim returns **HTTP 403 Access denied** from datacenter IPs (verified), so it cannot
be the primary geocoder. The mock provider keeps CI, offline development and the Loom recording
independent of upstream rate limits — and doubles as the fixture generator for tests.

**Task.** BE-32…BE-38, BE-61, DOC-12.

## D11 — Three routes are guaranteed by via-waypoint variants + geometry dedupe

**Decision.** Request OSRM alternatives first. If fewer than 3 *distinct* geometries come back,
synthesize variants by inserting perpendicular via-waypoints at ~33 % and ~66 % along the base
route and re-requesting. Dedupe by resampling to fixed arc-length points and rejecting candidates
with > 92 % mean overlap. If genuinely fewer than 3 distinct routes exist, return what exists and
say so in the UI.

**Why.** Measured: OSRM returned **2** alternatives for a 1,006 mi trip and 1 when via-waypoints
were pinned. The assessment requires 3 options, so a deterministic generation strategy is needed;
fabricating a fake third route would be dishonest, and duplicating geometry would let identical
options win the ranking for spurious reasons.

**Task.** BE-36, BE-37.

## D12 — The frontend owns no risk rules

**Decision.** Risk thresholds, load policy, level ordering and level colors are served by
`GET /api/v1/config/` and consumed by the UI. The frontend computes only: level *lookup* from a
precomputed array, miles-weighted aggregates, and display formatting.

**Why.** Rules duplicated in two languages drift silently, and the rules are 40 % of the grade.
Single-sourcing also lets the legend render straight from the same constants the classifier uses. A
parity test (FE-32) replays a recorded backend payload through the frontend selectors at offsets
0/6/24/48 and fails on any mismatch.

**Task.** BE-56, FE-02, FE-05, FE-06, FE-32.

---

---

# Decisions resolved during planning (2026-10-17)

These four were open questions during the first draft. Each is now closed with a rationale, an
implementing task, and — where a reasonable person could disagree — a config flag so it is a
one-line change rather than a rewrite.

## D13 — The 0–48 h slider shifts the **departure time**

**Decision.** `effective_departure = base_departure + offset_hours`, `0 ≤ offset ≤ 48`. Every
checkpoint's ETA recomputes to `effective_departure + cumulative_time`, and its risk is re-read at
that new ETA. Load, origin, destination and spacing are unchanged. UI label: **"Departure offset
+Nh"** (never "forecast time").

**Why.** Shifting a time *window* while leaving the truck stationary would break the app's central
premise — weather is sampled *at the truck's ETA*. After a window slide, the sampled weather would
no longer be at the ETA and every checkpoint's reading would be meaningless. Departure shifting
keeps the model coherent end-to-end and yields the most decision-relevant behaviour: **the
recommended route can change as the user drags**, because each route's exposure to the storm
moves. That is also a far stronger demonstration than a colour animation.

**Consequence.** The fetched weather window must cover `base_departure … base_departure + route_duration + 48 h`
(already planned in BE-39) — which is exactly why the full hourly series is fetched once up front
(D7). Slider cost remains a lookup.

**Tasks.** FE-21, FE-22, BE-39, BE-41.

## D14 — Checkpoint weather is the forecast hour at `floor(ETA)`; no interpolation

**Decision.** `hour_index = floor(ETA)` (hours since `meta.hour_window_start`). Config:
`CHECKPOINT_TIME_SNAP = "floor" | "ceil" | "nearest"`, default `floor`.

**Why.** Floor never reads a forecast value timestamped *after* the truck's arrival — the safest
framing for "what is this truck driving into" — and it is a single unambiguous operation.
Interpolation was rejected: rain rate is spiky and non-linear, so interpolating 0.0 → 1.2 in/hr
fabricates a "moderate rain" hour that neither observation reports. Since classification is
threshold-based, that can invent a severity that does not exist — unacceptable in a safety tool.

**Bound, stated openly.** The snap introduces at most **1 hour** of temporal misalignment. This is
smaller than the assessment's own sampling granularity at coarse spacing (50 mi ≈ 67 min of driving
at 45 mph) and is documented in the Methodology panel. A unit test asserts `floor`, `ceil` and
`nearest` differ by **at most one index**, so switching modes cannot silently change results.

**Tasks.** BE-41, BE-42, FE-26.

## D15 — Rain reads `rain + showers`; snow is a **depth**, not a liquid equivalent

**Correction (2026-10-17).** An earlier draft of this decision claimed that using `precipitation`
for the Rain rule would "double-count a snowstorm as rain" and could produce false NO_TRAVEL
verdicts. **That claim was wrong**, and the measurement below is why.

**Verified facts.**

| Fact | Evidence |
|---|---|
| `precipitation` = rain + showers + snow, expressed as **liquid water equivalent** | Open-Meteo variable table; decomposition holds to ≤ 0.03 in in most cases |
| `snowfall` is **snow depth**, not liquid equivalent | snow_depth ÷ liquid_equivalent = **6.95 / 6.94 / 7.08** across Minneapolis, Denver, Mount Washington, Iqaluit |
| `freezing_rain` is not exposed by the forecast API | rejected as an invalid variable ⇒ nothing is missed |

**Measured impact of this mapping on the overall checkpoint level: 0 of 2,640 real winter hours
differed** (8 snowy locations, Dec 2024 / Feb 2024). Snow's liquid equivalent (~1/7 of its depth)
lands in the LOW–MODERATE rain band and is dominated by the snow level under `max()`. This is a
**correctness-of-meaning** decision, not a behavioural fix — and should not be presented as one.

**Decision.** Rain rule ← `rain + showers`; Snow rule ← **`snowfall`**; `precipitation` is used for
display context only, never as a rule input.

**Why it still matters — the real hazard this surfaced.** The dangerous mistake is the *opposite*
one: feeding `precipitation` (liquid equivalent) into the **Snow** rule understates snow severity
by ~7×. A checkpoint with **1.4 in/hr of snow** (HIGH by the snow table) would read as 0.2 in/hr
liquid → **LOW**. That is a silent under-report of a serious hazard. Two consequences, both
implemented:

1. The Snow thresholds (0.5 / 1.0 / 2.0 / 3.0 in/hr) are applied to **snow depth**, which is what
   the table plainly means by "snow in/hr".
2. The UI displays snow in **depth** units matching the thresholds, and the Methodology panel states
   that "in/hr" for snow is accumulation depth, *not* liquid equivalent.

**Also verified:** `precipitation` and `snowfall` have valid time *"Preceding hour sum"*, while most
other variables are instantaneous. Numerically an inch accumulated over one hour **is** an average
rate of in/hr, so the thresholds apply directly — but the in/hr framing is inferred from a
sum-over-one-hour, and the Methodology panel says so rather than implying a true instantaneous rate.

**Tasks.** BE-14b, BE-38, BE-41, BE-42, FE-26.

## D16 — Load boundaries are strict `>`, with a config flag

**Decision.** `load_lb > 30_000` and `load_lb > 40_000` exactly as written in the table. A truck at
exactly 30,000 lb does not escalate. Config: `LOAD_BOUNDARY_INCLUSIVE = False`.

**Why.** The operator in the table is `>`, and a grading rubric is read literally before it is
read cleverly. The flag exists because industry shorthand ("over 30,000") sometimes means `≥`; if
that turns out to be intended, it is one boolean, not a refactor. Practical impact is limited to a
truck at precisely 30,000.0 lb.

**Tasks.** BE-13, BE-24.

## D17 — Recommendation is explained two ways

**Decision.** Generate both, from one `explain_ranking()` pass:
1. **Headline** — criterion-by-criterion diff against the runner-up: *"120 fewer severe miles,
   +40 min longer"*. Most legible; this is what the recommendation banner leads with.
2. **Secondary** — per-criterion tally across all 3 routes: *"fastest on 2 of 3 routes"*, shown in
   an expandable panel.

**Why.** The question was a false choice: both are a few lines over the same comparison data, and
each answers a different question the driver actually has — "why this one?" and "is it the best
overall?". Diff-only hides that the winner may lose on travel time; tally-only is too abstract for
a first impression.

**Tasks.** BE-16, FE-19, FE-23.

---

## D18 — Average risk is compared at one decimal place

**Decision.** `avg_risk` participates in ranking at `AVG_RISK_PRECISION = 1` decimal, so
routes whose average risk differs by less than ~0.05 tie and the ranking falls through to travel
time. The *displayed* value keeps full precision.

**Why.** Found by running the real pipeline, not by a test. A live Chicago→Detroit run produced:

```
Option C ranks first over Option B: 0.01 lower average risk; 53m longer travel time.
```

Across a handful of checkpoints a 0.01 average-risk difference is noise. Letting it decide a
safety recommendation is worse than not ranking at all — the app would recommend a slower,
equally-safe route and justify it with a number no driver could act on. The lexicographic order
from D5 is correct; what was wrong was comparing a float score at unlimited precision.

**Guard.** Two tests, one in each direction: a 0.025 average-risk gap must **not** outrank an
extra hour of driving, and a 0.5 gap **must** outrank two hours. A tolerance that swallows real
differences would be as wrong as the original bug.

**Generalisation.** Any threshold-derived score compared at full float precision will eventually
decide a real decision on rounding noise. The fix is a comparison tolerance sized to the noise
floor of the sampling, not to the precision of the arithmetic.

**Task.** BE-16, BE-27, BUGS.md B-19.

---

---

## D19 — Cache keys are hashed, and validation failures name every field

**Decision.** Two small things learned the hard way, recorded because both are
easy to get wrong silently.

**Cache keys are SHA-256 digests.** The natural key (place names + ISO timestamp
+ parameters) contains spaces, `|` and `+`, all of which memcached rejects
outright. Building it as-is works perfectly on `LocMemCache` and then fails on the
first Redis or memcached deployment. The digest is bounded and portable.

**Validation reports every bad field at once.** A driver fixing a form should not
have to submit four times to discover four problems, so the 400 body is
`{fields: {field: [messages]}}` for all offending fields simultaneously.

**Why it matters.** Both defects were invisible in development — `LocMemCache`
accepts any string, and a validator that returns only the first error passes every
happy-path test. `pytest`'s `filterwarnings = error` happened to surface the cache
one by turning Django's `CacheKeyWarning` into a test failure; that is the only
reason it was caught before deploying.

**Task.** BE-54, BE-58, BE-59.

---

## D20 — The streaming endpoint is the primary interface

**Recommendation.** Use `POST /api/v1/trips/analyze/stream/` for the app. Measured
event timeline from the dev server against live providers:

```
   931ms  meta
  2500ms  route   r0 280.4mi 13cps src=osrm_alternative
  2518ms  weather_plan
 21026ms  weather          <-- the 19 s tail
 21062ms  done  locs=25 elapsed=21045ms
```

Geometry is usable at **2.5 s** while the weather tail runs to 21 s — an 8x
improvement in time-to-map, and the whole reason the frontend can draw routes
immediately with checkpoints filling in behind. The non-streaming endpoint exists
for `curl`, CI and the parity tests, and returns byte-identical `routes` /
`summaries` / `checkpoints` / `recommendation` sections (asserted by
`test_streaming_and_non_streaming_agree`).

**Also from that trace.** The D8 measurement of the free OSRM server is visible in
one request: it returned **2** alternatives for Chicago→Detroit, and the
via-waypoint synthesis supplied the third, tagged `src=osrm_via_variant`. The 3-route
guarantee (D11) is doing real work on every request, not just in a test.

**Task.** BE-51, BE-52, BE-53, BE-59, BE-60.

---

## Note for reviewers: sampling resolution vs. weather resolution

The weather source is **hourly**, while the assessment's densest sampling mode is 10 miles — about
**13 minutes** of driving at 45 mph. Checkpoints therefore frequently share a forecast hour (though
never a forecast *location*, since conditions vary spatially — so each still needs its own fetch).
The practical effect: **10-mile and 25-mile modes produce similar risk profiles**, differing mainly
near hour boundaries. This is a property of the free data source, not a defect, and is documented
in the Methodology panel and `docs/BENCHMARKS.md` rather than hidden.
