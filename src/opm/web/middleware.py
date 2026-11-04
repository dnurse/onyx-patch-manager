from __future__ import annotations

from django.utils import timezone
from zoneinfo import ZoneInfo

from .timeprefs import get_configured_timezone

class OPMTimezoneMiddleware:
    """Activate the configured local timezone for all rendered timestamps."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        timezone.activate(ZoneInfo(get_configured_timezone()))
        return self.get_response(request)
