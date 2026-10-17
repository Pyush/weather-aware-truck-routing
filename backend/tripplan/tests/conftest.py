"""Shared fixtures.

Nothing here touches the network: the domain layer is pure Python, and the test
suite for it must stay that way so it runs in milliseconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def risk_table_csv() -> Path:
    """Path to the golden transcription of the assessment's rule table."""
    return FIXTURES / "risk_table.csv"
