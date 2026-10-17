"""Weather risk classification, mileage accounting and route ranking."""

from __future__ import annotations

from .classify import Classification, classify, classify_rain, classify_snow, classify_wind
from .enums import RiskLevel, level_catalog
from .evaluate import CheckpointRisk, WeatherReading, evaluate_checkpoint
from .load_rules import LoadAdjustment, LoadPolicy, apply_load_rules
from .miles import RouteRiskSummary, Segment, attribute_segments, summarise
from .recommend import Recommendation, RouteCandidate, rank_routes
from .thresholds import BUCKETS_BY_VARIABLE, Bucket, find_bucket, threshold_catalog
from .weather_vars import MAPPINGS, REQUESTED_FIELDS

__all__ = [
    "BUCKETS_BY_VARIABLE",
    "MAPPINGS",
    "REQUESTED_FIELDS",
    "Bucket",
    "CheckpointRisk",
    "Classification",
    "LoadAdjustment",
    "LoadPolicy",
    "Recommendation",
    "RiskLevel",
    "RouteCandidate",
    "RouteRiskSummary",
    "Segment",
    "WeatherReading",
    "apply_load_rules",
    "attribute_segments",
    "classify",
    "classify_rain",
    "classify_snow",
    "classify_wind",
    "evaluate_checkpoint",
    "find_bucket",
    "level_catalog",
    "rank_routes",
    "summarise",
    "threshold_catalog",
]
