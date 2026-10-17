# Weather-Aware Truck Routing

A React + Django web app that helps a truck driver choose the safest route based on **weather,
load weight and travel time**.

<!-- Full README (architecture, API reference, screenshots, live URL) lands in DOC-10. -->

## What it does

1. You enter **origin, destination, departure date/time and load weight**.
2. It generates **3 route options** via a routing API.
3. It samples **weather every 10 / 25 / 50 miles**, each sample taken at the truck's **estimated
   arrival time** for that point on the route.
4. It classifies risk at each checkpoint with the supplied **wind / rain / snow** thresholds and the
   **load-based escalation** rules.
5. It **recommends the best route**, trading severe miles against travel time.
6. It draws the routes, checkpoints, risk levels and ETAs on a map, with a **weather heatmap** along
   the corridor and a **0–48 h departure-offset slider**.

## Current status

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundations | done |
| 1 | Domain core (risk / geometry / ETA) + tests | done |
| 2 | External adapters (geocoding, routing, weather) | pending |
| 3 | Orchestration + API | pending |
| 4–6 | Frontend | pending |
| 7–8 | Quality, deploy, docs | pending |

## Docs

| File | Contents |
|---|---|
| [`PLAN.md`](PLAN.md) | Architecture, verified API benchmarks, roadmap, risks |
| [`docs/TASKS.md`](docs/TASKS.md) | 94 micro-tasks with dependencies and acceptance criteria |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 17 numbered decisions on rule semantics |

## Quickstart

```bash
make install     # create the venv and install backend + frontend deps
make test        # run the backend test suite
make lint        # ruff + eslint + tsc
make security    # secret-scan every tracked file
```

Requires Python ≥ 3.12 and Node ≥ 20.

## Secret scanning

`scripts/security-check.sh` runs as the pre-commit and pre-push hook and as a CI
step, so all three share one implementation.

```bash
make security          # scan tracked files
make security-selftest # prove every pattern still fires
```

`--self-test` is not ceremonial. Two real bugs were found this way, both of which
made the scanner *silently* pass:

- Patterns beginning with `-` (the private-key pattern) were read by `grep` as an
  option list, so they never matched. `|| true` hid grep's exit-2 error.
- Combining `-I` with `-e` in a single flag cluster (`grep -nIe`) makes **no**
  pattern match on GNU grep 3.12, so a repository containing a real token was
  reported clean.

The self-test now also plants a secret in a temp file and runs the *real* scan
path over it, because a per-pattern check cannot catch a bug in the file-scanning
code itself. Intentional test fixtures can be exempted per line with a trailing
`# ok: <reason>`.
