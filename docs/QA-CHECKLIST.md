# Frontend QA Checklist (FE-25, FE-34)

Manual browser QA against the mock weather provider (`WEATHER_PROVIDER=mock`), Chromium at
the default viewport, verified 2026-10-18. Record what was checked and what it showed — a
checklist with no findings is not evidence.

**Legend** — `✓` pass · `✗` fail (with a bug ID) · `—` not yet checked

---

## Layout and responsiveness

| Viewport | Expected | Result |
|---|---|---|
| 1440 px | 3-region layout, panel ~360 px, map fills the rest | ✓ |
| 986 px | Panel narrows to 340 px; option rows stay single-line | ✓ — found B-50 (wrapped mid-value), fixed |
| 820 px | Panel moves below the map, single column | ✓ |
| 375 px | Stacked, form controls usable | ✓ |
| Any | No horizontal scrollbar | ✓ |

## Interaction

| Check | Result |
|---|---|
| Submit starts a stream; progress shows "finding routes…" then checkpoint count | ✓ |
| Routes appear before weather (geometry at ~2.5 s) | ✓ |
| Error event renders an alert card, not a "complete" state | ✓ |
| Validation blocks submit and shows inline field errors | ✓ — found B-43, fixed |
| Slider 0 → 48 h updates numbers with **zero** network requests | ✓ |
| Slider reset button returns to the planned departure | ✓ |
| Route card click selects; map emphasises it | ✓ |
| Heatmap and checkpoint toggles work | ✓ |
| Methodology modal opens and closes | ✓ — found B-40, fixed |
| Empty state offers example trips and populates the form | ✓ |
| Shareable link restores query + offset on a cold load | ✓ — found B-48, fixed |
| Copied link reproduces the view on a fresh visit | ✓ |

## Accessibility

| Check | Result |
|---|---|
| Full task completable by keyboard alone | ✓ — 26 focusable stops in a sensible order |
| Slider operable by arrow keys, with `aria-valuetext` | ✓ |
| Route cards announce rank, distance, duration, arrival, risk | ✓ — found B-51 (run-together text), fixed |
| Slider changes announced via a polite live region | ✓ |
| Map failure announced, not just drawn | ✓ |
| Text contrast ≥ 4.5:1 | ✓ — found `--text-dim` at 3.93:1, raised to 4.90:1 |
| Focus visible against the dark background | ✓ |
| `prefers-reduced-motion` honoured | ✓ (CSS) |

## Robustness

| Check | Result |
|---|---|
| WebGL unavailable | ✓ — fallback message, route panel still usable |
| No console errors during a full analysis | ✓ |
| Truncated stream fails loudly rather than reporting success | ✓ — found B-41, fixed |
| Cached response replays summaries against the right routes | ✓ — found B-39, fixed |

## Not yet checked

- Firefox and Safari (no local install; the app uses no browser-specific APIs beyond
  `ReadableStream` and `showModal`, both long-standing, but this is unverified).
- WebGL-absent path exercised in a real browser (jsdom cannot create a context).
- 1,000-mile route with 120 checkpoints at 60 fps — FE-17/FE-33, needs the perf pass.
- Print stylesheet.

## Open issues

None. Every failure found during this pass is recorded in `docs/BUGS.md` with a fix and a
regression guard; B-43, B-40, B-39, B-41, B-48, B-50 and B-51 were all found by the checks
above rather than by reading the code.