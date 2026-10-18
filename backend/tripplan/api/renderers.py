"""Renderers for the API.

DRF's content negotiation picks a renderer from the request's ``Accept`` header.
The streaming endpoint advertises ``application/x-ndjson``, which no built-in
renderer claims, so negotiation fails with **406 Not Acceptable** before the view
ever runs. This renderer makes that media type negotiable.
"""

from __future__ import annotations

import json

from rest_framework.renderers import BaseRenderer


class NDJSONRenderer(BaseRenderer):
    """Claims the ``application/x-ndjson`` media type.

    The view encodes and writes each event itself -- it has to, in order to
    stream -- so this renderer's primary job is to make the media type negotiable.
    Without it, DRF answers 406 to ``Accept: application/x-ndjson`` and the
    streaming endpoint is unreachable from a browser.
    """

    media_type = "application/x-ndjson"
    format = "ndjson"
    charset = None

    def render(
        self,
        data: object,
        accepted_media_type: str | None = None,
        renderer_context: object = None,
    ) -> bytes:
        """Encode DRF-generated errors as a complete JSON line."""
        return (json.dumps(data) + "\n").encode("utf-8")
