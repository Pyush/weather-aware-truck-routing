"""Django app configuration."""

from __future__ import annotations

from django.apps import AppConfig


class TripplanConfig(AppConfig):
    """Configuration for the tripplan app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "tripplan"
    verbose_name = "Weather-aware truck routing"
