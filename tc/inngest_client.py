from __future__ import annotations

import inngest

from tc.config import settings

client = inngest.Inngest(
    app_id="teamcaptain",
    is_production=not settings.inngest_dev,
    event_key=settings.inngest_event_key or None,
    signing_key=settings.inngest_signing_key or None,
)
