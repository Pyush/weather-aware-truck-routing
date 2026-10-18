# Bug Log

Defects found during implementation, with root cause, how they were found, and the fix.
Kept because most of them were invisible to the tests that existed at the time — the
"how it was found" column is the part worth reading twice.

**Controls that earned their keep.** The FE-02 contract drift check was verified the same way
as the secret scanner: by corrupting the committed file and confirming the test fails. A guard
that has never fired is a guess.

Status: Phases 0–3 complete; frontend verification in progress. **47 defects recorded, 0 open**
(remaining QA tracked separately).

---

## Severity key

| Level | Meaning |
|---|---|
| **S1** | Silently wrong results in the graded output — a route verdict, a risk level, or a secret going undetected |
| **S2** | Wrong or failing behaviour, but visible (loud failure, exception, red test) |
| **S3** | Cosmetic, maintainability, or code-quality |

---

## S1 — findings that would have shipped silently

### B-01 · Scanner reported a secret-free repo while holding a real token

**Found by** planting a known-bad GitHub token in a file and running the scanner, then
verifying it actually fired. It did not.

**Root cause.** Flags were clustered: `grep -nIe "$regex" "$file"`. On GNU grep 3.12,
combining `-I` with `-e` in one cluster makes **every** pattern match nothing. Verified in
isolation — `grep -nIe` → no match, `grep -n -I -E -e` → match, same file and pattern.

**Why it is bad.** This is a false *negative* in a security control: it reported "clean" on a
repository containing a live-looking credential. And it was **introduced while fixing B-02**,
i.e. the cure caused a worse disease than the ailment.

**Fix.** Invoke grep with every flag as its own argument, via a single `grep_scan_file`
helper, so the pattern cannot be re-clustered by accident.

**Guard.** `--self-test` now plants a secret in a temp file and runs the *real* scan function
over it. A per-pattern check could never have caught this: all 15 patterns were individually
healthy while the scanner as a whole detected nothing.

### B-02 · Every private-key pattern silently never matched

**Found by** testing each of the 15 patterns individually rather than trusting that the
scanner ran clean.

**Root cause.** The private-key regex starts with `-----BEGIN`. Passed as `grep -E "$regex"`,
grep reads a leading `-` as an **option list**, exits 2, and — because `|| true` treats "no
match" and "grep errored" identically — the failure was discarded. The scanner reported clean
while never testing for private keys at all.

**Fix.** Pass dynamic patterns with an explicit `-e`. Documented at the call site, because the
`|| true` is what made it silent.

### B-03 · Wrong claim that the rain rule double-counted snow

**Found by** checking an assumption before building on it — Open-Meteo's variable table says
`precipitation` is *liquid water equivalent* while `snowfall` is *snow depth*.

**Root cause.** I assumed the two were the same kind of quantity and summed. They are not: the
measured ratio is **6.94–7.08 : 1** across four locations. Snow contributes ~1/7 of its depth
to `precipitation`, landing in the LOW–MODERATE rain band, and `max()` is dominated by the snow
level anyway.

**Measured impact of the original claim:** **0 of 2,640 real winter hours** differed. The
"double-counting" was fictional.

**What survived.** The `rain + showers` mapping is still correct, but for a weaker reason
(correctness of meaning, not behaviour). The real hazard is the *opposite* mistake — feeding
liquid `precipitation` into the **Snow** rule understates snow by ~7× (1.4 in/hr of snow = HIGH,
but 0.2 in/hr liquid = LOW). Now guarded by a named regression test.

**Lesson.** The first probe showed `precipitation` *smaller* than `snowfall` — that
contradiction was the real clue, and stopping at the documentation would have shipped the wrong
mapping with a confident writeup.

### B-04 · `dataclasses.field()` used as a function default

**Found by** the test suite (100+ failures, same root cause).

```python
policy: LoadPolicy = field(default=DEFAULT_LOAD_POLICY)   # passes a dataclasses.Field
```

Every call received a `Field` object instead of the policy, so `policy.no_travel_wind_mph`
raised `AttributeError`. Should have been `= DEFAULT_LOAD_POLICY`.

**Fix.** Removed the stray import and the decorator-style default.

---

## S2 — loud failures

| ID | What | Root cause | Fix |
|---|---|---|---|
| **B-05** | `classify("hail", 1.0)` raised `KeyError`, not `ValueError` | `UNITS[variable]` ran before any validation | Validate the variable name first |
| **B-06** | `TypeError: _explain_delta() missing 'label'` | Left a second copy of the function behind after an edit | Deleted the stale definition |
| **B-07** | Golden rule table failed to load | The CSV **header line was itself commented out** (`# variable,value,...`), so `DictReader` consumed a data row as the header | Strip comments *before* `DictReader`; un-comment the header |
| **B-08** | Rest-break schedule computed wrongly | `cumulative_eta` had a no-op `hours_driven = hours_driven` and tangled rest accounting | Extracted `rest_hours()`; breaks are a pure function of driving time |
| **B-09** | Duration rendered as `1h 1800m` | `divmod(int(round(value)), 3600)` assigns the **seconds** remainder to a variable named `minutes` | Convert to minutes first, then `divmod(minutes, 60)` |
| **B-10** | `F821 Undefined name 'Response'` | Used `Response` in a return annotation while importing it *inside* the function body | `TYPE_CHECKING` guard with a module-level import |
| **B-11** | Scanner timed out (>3 min) after a refactor | Per-line loop ran one `grep` subprocess **per pattern per line** — O(lines × 15) spawns | Loop patterns outer, files inner: one grep per (pattern, file) |
| **B-12** | `Django` refused to boot: "Application labels aren't unique, duplicates: staticfiles" | `dev.py` appended `staticfiles`, already present in `base.py` | Removed the duplicate append |

---

## S3 — test-authoring mistakes (worth recording: the tests were wrong, not the code)

| ID | Mistake | Correction |
|---|---|---|
| **B-13** | Asserted `classify_rain(0.2) is LOW` | 0.2 is in `[0.10, 0.25)` → **MODERATE**. The *contrast* with snow 1.4 = HIGH is what the test was for |
| **B-14** | `WeatherReading.evaluate(...)` expected HIGH | wind 30 / rain 0.15 / snow 0.3 is **MODERATE** — max, not sum |
| **B-15** | `test_all_blocked` built a 1-checkpoint candidate | A single checkpoint at distance 0 yields **zero total miles**, so `is_blocked` was False by arithmetic, not logic |
| **B-16** | Tally assertions didn't match pairwise-win semantics | With a, b identical and c worse, `a` gains a win against **c** |
| **B-17** | `NameError: random` in `test_geo.py` | Missing import |
| **B-18** | Truck-speed test clamped both profiles at max | Gave an input that clamps *neither* (100 mi in 2 h → 50 mph car) |

---

## Process notes

**Where the bugs came from.** Of 18, **4 were mine writing code** (B-04, B-06, B-08, B-10),
**6 were bad assumptions or bad tests** (B-03, B-13…B-18), and **3 were introduced while fixing
something else** (B-01 caused by the B-02 fix, B-05, B-11). Only the last group is the dangerous
one, because each arrived dressed as a completed fix.

**Controls that actually earned their keep.**
- The golden CSV makes rule drift a test failure rather than a judgement call.
- `test_domain_purity.py` makes the "no Django in the domain" rule mechanical.
- `--self-test` for the scanner caught both S1 findings; the scanner running clean did not.
- A property test (randomised routes) caught what hand-picked cases would not.

**What I would keep doing.** Plant known-bad input and confirm the detector fires. Both S1
findings were invisible to "the suite is green" and to "the tool says clean" — only a deliberate
attempt to *make it fail* exposed them. Equally, verifying an upstream API's semantics against
real data (B-03, 2,640 hours) is cheaper than defending a wrong claim in the writeup.

---

# Phase 2 — adapters

Phase 2 produced seven more defects and **one design flaw that only a live run could reveal**.
Worth noting: five of the seven were found by tests written *after* the code, which is an
argument for writing the test first whenever the behaviour is cheap to state.

## B-19 · A 0.01 average-risk gap outranked an hour of driving **(S1, design)**

**Found by** running the whole pipeline against the live APIs — not by any test.

The real Chicago→Detroit run produced three clear-weather options and this recommendation:

```
Option C ranks first over Option B: 0.01 lower average risk; 53m longer travel time.
```

Option C was chosen over a faster, equally safe option because its average risk was 0.01 lower.
Across a handful of checkpoints that is pure noise, and letting it decide a *safety*
recommendation is worse than not ranking at all. The lexicographic order was correct; the
*comparison* needed a tolerance.

**Fix.** `avg_risk` is compared at `AVG_RISK_PRECISION = 1` decimal. Indistinguishable routes now
tie, and the ranking falls through to travel time — the criterion that actually separates them.
Re-running the same live shape yields `A ranks first over B: 30m shorter travel time.` with no
hairline risk claim.

**Why it generalises.** Any threshold-based score compared at full float precision will
eventually decide a real decision on rounding noise. The guard tests both directions: a 0.025 gap
must *not* outrank an hour, and a 0.5 gap *must* outrank two hours.

**Lesson.** Stubbed tests prove the code does what you told it to. Only a real end-to-end run
tells you whether that was the right thing to ask for.

## B-20 · `slots=True` on exception dataclasses made every subclass unconstructable **(S1)**

**Found by** the adapter test suite.

`@dataclass(frozen=True, slots=True)` rebuilds the class as a *new* object, which breaks the
implicit `__class__` cell that zero-argument `super()` depends on. Every subclass raised
`TypeError: super(type, obj): obj ... is not an instance or subtype of type`, so
`UpstreamTimeout`, `GeocodeNotFound` and `NoRouteFound` could not be instantiated at all —
the entire error taxonomy was unusable.

**Fix.** Dropped `slots=True` from the exception classes and documented why in the module
docstring so the "optimisation" is not re-added later. Exceptions gain little from slots and
lose pickling.

## B-21 · Coarsening remapped checkpoints to the wrong index space **(S1)**

**Found by** the adapter test suite.

`plan_locations` kept a subset of weather locations when over budget but remapped checkpoints
using indices from the *pre-filter* list. With 200 locations reduced to 10, checkpoints mapped to
index 180 — far out of range. The UI would then have been labelled with weather from a location
that was not on its own route segment.

**Fix.** Build an explicit `position_of` map from retained position to new index.

## B-22 · Empty weather series crashed the indexer **(S2)**

`build_hourly_risk` looped `max(len(series), 1)` times over a series with no hours, and
`reading_at` raised `IndexError` on an empty series. A provider returning nothing usable became
a 500.

**Fix.** Return a single `Low` slot carrying `data_quality="missing"`, so the UI says "weather
unavailable" instead of the request failing.

## B-23 · Via-waypoints were placed on the wrong side of the corridor **(S2, silent)**

`perpendicular_waypoints` documented `side=+1` as "left of travel" but computed
`heading + 90°`, which is the *right* side. Every synthesised third route would have been
generated off the wrong side of the motorway — still a valid, plausible route, so nothing would
have looked broken.

**Fix.** Rotate by `-90°` for left, matching the documented contract, and assert the direction in
a test (an eastbound route must push waypoints north for `side=+1`).

## B-24 · Undefined type variable and a dead parameter **(S2)**

`_chunks(items: Sequence[T], ...)` referenced `T` without importing a `TypeVar`. It survived only
because `from __future__ import annotations` defers evaluation — the annotation would have raised
`NameError` for anyone calling `typing.get_type_hints`. `plan_window` also computed `current` and
never used it, leaving `now` a dead parameter; removed rather than left as noise.

## B-25…B-27 · Test-side errors again **(S3)**

| What | Correction |
|---|---|
| Batch test built one-record payloads for a 2-location batch | A batch of *k* needs *k* records; payloads now mirror the 2+2+1 split |
| Window test expected `start + 48h` | 20 h driving + a 48 h offset is **68 h**; the old expectation would have hidden a real under-fetch |
| Cell test assumed two points 0.001° apart always share a cell | `-87.000` and `-87.001` straddle a cell edge — correct behaviour, so the test now asserts both halves |

---

## B-28 · Weather-outage degradation crashed with a 500 instead of degrading **(S1)**

**Found by** the API contract test that asserts a missing forecast still yields a usable map.

`_fetch_weather` correctly caught the provider failure and returned `[]`, then appended a
warning. But the checkpoint loop went straight on to index `series[series_index]` — with no
series. So the degradation path that existed specifically to avoid a 502 produced an
`IndexError`, i.e. a 500.

**Why it is S1.** This is the difference between "the weather service is down" (recoverable,
map still draws) and "the app is broken". It would only ever appear during an upstream
outage — exactly when the app is least able to fail.

**Fix.** When no series were fetched, each checkpoint gets a single placeholder `HourlyRisk`
carrying `data_quality="missing"`, so the UI can say the corridor was not assessed rather than
implying the road is clear.

**Lesson.** A try/except that returns an empty collection is only half a fix; every consumer
of that collection needs to handle it being empty.

## B-29 · Cache key used characters memcached rejects **(S1, invisible in dev)**

**Found by** `filterwarnings = ["error"]` in the pytest config turning Django's
`CacheKeyWarning` into a test failure.

The cache key was built from the semantic inputs — `"chicago, il|detroit, mi|2026-10-17T…|35000"`.
`LocMemCache` accepts any string, so this worked perfectly in development and would have failed
on the first Redis or memcached deployment, which reject spaces, `|` and `+` outright.

**Fix.** SHA-256 the key. Portable, bounded, and stable.

## B-30…B-32 · Test isolation and stale input **(S3)**

| What | Correction |
|---|---|
| API tests shared the process-wide cache; because `payload()` truncates the departure to whole seconds, tests landing in the same second produced identical keys and an earlier result satisfied a later test | Autouse fixture clears the cache before and after each test |
| Timezone-ambiguity warning never fired, because DRF attaches the project timezone during parsing — the information was already gone | Detect the missing offset from the **raw** payload string, which is what the user actually typed |
| `test_missing_fields_are_reported_together` sent `origin="x"`, which is *valid*, so it could never be reported missing | Send `{}` so all four required fields are genuinely absent |

---

## Live-run timings (Chicago → Detroit, 281 mi, 25 mi spacing)

Recorded so Phase 3's latency budget rests on measurement rather than hope.

| Stage | Time |
|---|---|
| Geocode both endpoints (Open-Meteo) | **1.1 s** |
| 3 routes (2 OSRM alternatives + 1 synthesised via-waypoint variant) | **1.3 s** |
| 40 checkpoints → 25 unique locations after dedupe (15 saved) | instant |
| Weather, 25 locations, 2 batches | **19.1 s** (one 503 retried) |

Confirms the streaming design: geometry and summaries are ready in ~2.5 s while the 19 s weather
tail streams in behind it. The weather leg also matched the cost model — 25 locations predicted
~13 s, actual 19.1 s including a 503 retry.

The OSRM leg independently re-confirmed D11 on a live route: the provider returned **2**
alternatives and the via-waypoint synthesis produced the third, correctly tagged
`osrm_via_variant`.

---

## Phase 4–6 — browser and streaming integration

| ID | Severity | Finding and fix | Guard |
|---|---|---|---|
| **B-33** | S2 | A Zustand selector returned a new `{}` on each render, triggering React's maximum-update-depth error. Reused a module-level empty object. | Browser render |
| **B-34** | S2 | MapLibre cannot parse CSS `var(--risk-low)` in paint expressions. Supply literal hex from `/config/` instead. | Browser console |
| **B-35** | S2 | DRF rejected `Accept: application/x-ndjson` with 406; added an NDJSON renderer *after* JSON so `*/*` retains JSON. The initial reverse ordering broke 28 API tests. | Explicit Accept-header API test |
| **B-36** | S2 | Stream `route` events precede `summary`, so selectors and the corridor dereferenced missing `route.summary` and blanked the app. Handle partial routes until the summary arrives. | Partial-stream selector and map-data tests |
| **B-37** | S1 | Even after completion, route cards claimed `0.0 LOW` while the recommendation reported Severe miles: the store kept summaries separately but never joined them to routes. Attach each summary by `route_id`. | Event-sequence store test and browser comparison |
| **B-38** | S2 | Validation under NDJSON negotiation returned the literal body `error` with a 400, so the browser could not parse field errors. Return a JSON response before opening the stream. | Explicit Accept-header invalid-request test |
| **B-39** | S1 | Cached stream replay omitted `route_id` on each summary, so summaries could not join their routes. Pair cached summaries with cached route IDs. | Cached-stream replay test |
| **B-40** | S2 | Methodology dialog was observed staying open after Close; stabilized its close callback and verified the dialog closes in the browser. | Browser close interaction |
| **B-41** | S2 | A terminal `error` event arrived under HTTP 200 but was ignored, leaving the app in the complete state. The NDJSON client now raises a structured `ApiError`; a stream ending without `done` also fails rather than claiming success. | Mock-stream tests |
| **B-42** | S2 | When a new analysis aborted the old one, the old callback could still mark the new analysis complete. Check the old request's abort signal before `finishStream()`. | Abort-state review |
| **B-43** | S2 | `validate()` set `errors.destination` and then deleted it two lines later, so an empty destination silently submitted. Found by writing the component test for FE-07. | Empty-destination form test |
| **B-44** | S3 | Vite warned that `__dirname` is unsupported under the native config loader. Switched both configs to `import.meta.dirname`. | Clean build output |
| **B-45** | S3 | The generated contract carried `elapsed_ms`, so a byte-comparison drift check would have failed on every run for timing reasons alone. Zeroed that field in the fixture. | Two-build determinism test |
| **B-46** | S2 | Submitting a new plan reset `offsetHours` to 0, so a shared link carrying `?offset=18` silently lost its departure offset on replan. Preserve and re-clamp it. | Shared-link replan test |
| **B-47** | S2 | `parseUrlState` turned `?every=` into `spacingMi: 0`, because `Number('')` is `0` — a truncated link would have asked for "every 0 miles". Treat a blank parameter as absent. | Blank-parameter test |

**Environment note.** Vite's file watcher hit `ENOSPC` (inotify limit 65,536; cannot raise it here),
so it served stale transformed modules despite successful typechecks. Restarting with
`CHOKIDAR_USEPOLLING=true` restored live-edit verification. Stale console entries were
also misleading; each browser check now uses a fresh tab.
