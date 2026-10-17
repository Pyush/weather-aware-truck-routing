"""Application services.

Composition only: :mod:`tripplan.services.trip_analysis` wires adapters to the
domain and yields events. Providers are injected via
:class:`~tripplan.services.trip_analysis.ServiceRegistry`, so tests can substitute
a mock without monkeypatching.
"""

from __future__ import annotations
