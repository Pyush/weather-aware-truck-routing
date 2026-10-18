"""Production host checks must accept Render's runtime hostname, not a Blueprint placeholder."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_render_host_is_allowed_at_runtime_without_blueprint_self_reference() -> None:
    """A new Render service must pass its health check without a manual host override."""
    host = "weather-aware-truck-routing-api-iy3w.onrender.com"
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": "test-only-secret",
        "DJANGO_ALLOWED_HOSTS": "localhost",
        "RENDER_EXTERNAL_HOSTNAME": host,
    }
    script = f"""
import django
from django.conf import settings
from django.test import Client

django.setup()
print(settings.ALLOWED_HOSTS)
print(Client().get('/api/v1/health/', HTTP_HOST={host!r}).status_code)
print(Client().get('/api/v1/health/', HTTP_HOST='other.onrender.com').status_code)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [f"['localhost', '{host}']", "200", "400"]
