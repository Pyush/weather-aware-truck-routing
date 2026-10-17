"""Thin, swappable clients for the three third-party services.

Adapters speak small protocols (:class:`~tripplan.adapters.geocoding.Geocoder`,
:class:`~tripplan.adapters.routing.Router`,
:class:`~tripplan.adapters.weather.WeatherProvider`) and raise
:class:`~tripplan.adapters.errors.UpstreamError` subclasses, so the orchestration
layer depends on capabilities rather than on any one provider (D10).
"""

from __future__ import annotations
