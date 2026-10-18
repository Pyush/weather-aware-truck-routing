"""Contract freshness (FE-02).

The frontend's types are checked against a committed ``contract.json``. If the
backend renames a field and nobody regenerates that file, the UI would read
``undefined`` at runtime with no type error -- TypeScript only checks the
hand-written ``api.ts``. So the generated fixture is compared against a fresh
build here, and a stale file is a test failure rather than a silent break.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tripplan.management.commands.export_contract import DEFAULT_OUT, render_contract

pytestmark = pytest.mark.django_db


def test_committed_contract_matches_a_fresh_build() -> None:
    """The checked-in fixture is what the backend produces right now."""
    if not DEFAULT_OUT.exists():  # pragma: no cover - the file is committed
        pytest.fail(f"{DEFAULT_OUT} is missing; run `make gen-types`")

    rendered, _ = render_contract()
    committed = DEFAULT_OUT.read_text()

    assert committed == rendered, (
        f"{DEFAULT_OUT.name} is stale relative to the backend. Run `make gen-types`."
    )


def test_contract_is_deterministic_across_runs() -> None:
    """Two builds must agree, or the drift check above would be flaky.

    `elapsed_ms` is wall-clock, so it is the first thing that would drift. This
    pins that the fixture excludes it, which is what makes the comparison
    meaningful.
    """
    first, first_payload = render_contract()
    second, _ = render_contract()

    assert first == second
    assert first_payload["meta"]["elapsed_ms"] == 0.0


def test_example_payload_carries_everything_the_slider_needs() -> None:
    """The frontend indexes `hourly_risk`, so the fixture must have it."""
    _, payload = render_contract()
    contract: dict[str, Any] = json.loads(json.dumps(payload, default=str))

    assert contract["routes"], "the example must include routes"
    for bundle in contract["checkpoints"]:
        for point in bundle["points"]:
            assert point["hourly_risk"], "every checkpoint needs an hourly risk array"
            assert 0 <= point["eta_hour_index"] < len(point["hourly_risk"])

    ids = {route["id"] for route in contract["routes"]}
    assert set(contract["recommendation"]["ranking"]) == ids
