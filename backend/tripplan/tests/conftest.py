"""Shared fixtures.

Nothing here touches the network: the domain layer is pure Python, and the test
suite for it must stay that way so it runs in milliseconds.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def risk_table_csv() -> Path:
    """Path to the golden transcription of the assessment's rule table."""
    return FIXTURES / "risk_table.csv"


# Hosts the default suite may reach. `LocMemCache` and the Django test client do
# not use sockets, so in practice nothing needs an exemption; a future local
# service would, and blocking it would make the guard fail for the wrong reason.
ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost", "", "0.0.0.0"}


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Block outbound sockets for every test that is not marked integration.

    This lives in ``conftest.py`` rather than in a test module on purpose: an
    autouse fixture defined in a module applies only to that module, so
    defining it there would guard exactly one file and leave the rest of the
    suite free to call a live API. The whole suite has to be covered for the
    guarantee to mean anything.

    ``socket.socket.connect`` is the choke point every real outbound call passes
    through, whichever HTTP library is in use.
    """
    if request.node.get_closest_marker("integration") is not None:
        yield  # opt-in tests are allowed to talk to the internet
        return

    original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: object) -> object:
        host = str(address[0]) if isinstance(address, tuple) else str(address)
        if host not in ALLOWED_HOSTS:
            msg = (
                f"outbound connection to {host} is blocked in the default suite. "
                "Only tests marked `integration` may do that -- use a recorded "
                "payload or a stub client instead."
            )
            raise OSError(msg)
        return original_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield
