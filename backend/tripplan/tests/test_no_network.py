"""The default suite must never touch the network (BE-43).

`test_adapters.py` runs against recorded payloads precisely so CI cannot fail
because somebody else's server is down. That is a claim about the *whole* suite,
and a claim is not a control.

The guard itself lives in ``conftest.py`` -- an autouse fixture defined in a test
module would apply only to that module, which is exactly the bug this was first
written with. These tests check that the guard behaves, so its silence across the
rest of the suite means something.
"""

from __future__ import annotations

import socket

import pytest

# TEST-NET-3 (RFC 5737). Reserved for documentation, so this address can never
# be a real host whose behaviour might vary.
DOCUMENTATION_HOST = "203.0.113.1"


def test_the_guard_blocks_an_external_host() -> None:
    """A blocked connection raises, naming the host.

    The guard rejects on the hostname before any packet is sent, so this is
    deterministic: no DNS, no network, no flakiness.
    """
    with pytest.raises(OSError, match="blocked in the default suite"):
        socket.create_connection((DOCUMENTATION_HOST, 80), timeout=0.5)


def test_the_guard_allows_loopback() -> None:
    """Local sockets stay available, so the guard cannot break local tooling."""
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host, port = listener.getsockname()
        connection = socket.create_connection((host, port), timeout=1)
        connection.close()
    finally:
        listener.close()
