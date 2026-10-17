"""Weather-aware truck routing.

The package is deliberately layered so that the graded risk logic is testable
without Django or network access:

``tripplan.domain``
    Pure Python. No Django imports, no I/O. This is where the risk rules, the
    geometry helpers and the ETA model live (Phase 1).
``tripplan.adapters``
    Thin, swappable clients for geocoding / routing / weather (Phase 2).
``tripplan.services``
    Orchestration that composes domain functions with adapters (Phase 3).
``tripplan.api``
    Django views, serializers and the error taxonomy (Phase 3).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
