"""Geometry, checkpoint sampling and the truck ETA model."""

from __future__ import annotations

from .eta import TruckProfile, cumulative_eta, eta_plan, hour_index, rest_hours
from .haversine import (
    cumulative_distances,
    haversine_miles,
    line_length_miles,
    point_at_distance,
)
from .sampling import Checkpoint, sample_checkpoints, sample_distances, sampling_plan

__all__ = [
    "Checkpoint",
    "TruckProfile",
    "cumulative_distances",
    "cumulative_eta",
    "eta_plan",
    "haversine_miles",
    "hour_index",
    "line_length_miles",
    "point_at_distance",
    "rest_hours",
    "sample_checkpoints",
    "sample_distances",
    "sampling_plan",
]
