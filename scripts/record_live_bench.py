"""Append live-API latencies to ``docs/BENCHMARKS.md``.

Run as ``make bench-integration``. The test run measures the latencies and
writes JSON; this turns that into a dated table in the benchmarks document and
replaces the previous entry rather than appending forever -- a benchmark file
that grows on every run stops being readable within a week.

Kept as a script rather than test code so that *recording* a measurement is a
deliberate act, not something that happens whenever the suite runs.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parents[1] / "docs" / "BENCHMARKS.md"

BEGIN = "<!-- live-api:begin -->"
END = "<!-- live-api:end -->"

# How the numbers are read. Recorded rather than asserted: a threshold in a
# test makes a third party's slow day into a red build.
LEGS = ("geocode", "route", "weather")


def render(report: dict) -> str:
    """Return the markdown block for one measurement run."""
    recorded = report.get("recorded_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    notes = report.get("_notes", {})

    rows = [
        f"\n### Live API latencies — recorded {recorded}\n",
        "| Leg | Samples | Median | Slowest |",
        "|---|---:|---:|---:|",
    ]
    for leg in LEGS:
        entry = report.get(leg)
        if not entry:
            continue
        rows.append(
            f"| `{leg}` | {entry['samples']} | **{entry['median_s']} s** | {entry['max_s']} s |"
        )

    extras = [
        f"{notes['routes_returned']} route options returned",
        f"{notes['locations']} weather locations",
        f"{notes['window_hours']}-hour forecast window",
    ]
    rows.append(f"\nMeasured against the live APIs: {', '.join(str(e) for e in extras)}.")
    rows.append(
        "\nReproduce with `make bench-integration`. These are *recorded, not asserted* — "
        "a hard threshold would turn a provider's slow day into a red build."
    )
    return "\n".join(rows) + "\n"


def main() -> int:
    """Replace the live-API section with the newest measurement."""
    if len(sys.argv) < 2:
        print("usage: record_live_bench.py <report.json>", file=sys.stderr)
        return 2

    report = json.loads(Path(sys.argv[1]).read_text())
    block = f"{BEGIN}\n{render(report)}{END}\n"

    text = BENCHMARKS.read_text() if BENCHMARKS.exists() else "# Benchmarks\n"
    pattern = re.compile(
        re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(block, text, count=1)
    else:
        text = text.rstrip("\n") + "\n\n## Live third-party APIs\n\n" + block

    BENCHMARKS.write_text(text)
    print(f"recorded live latencies in {BENCHMARKS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())