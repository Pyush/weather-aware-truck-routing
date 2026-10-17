"""URL routing for the public API."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "tripplan"

urlpatterns = [
    path("v1/trips/analyze/stream/", views.AnalyzeStreamView.as_view(), name="analyze-stream"),
    path("v1/trips/analyze/", views.AnalyzeView.as_view(), name="analyze"),
    path("v1/config/", views.ConfigView.as_view(), name="config"),
    path("v1/health/", views.HealthView.as_view(), name="health"),
]
