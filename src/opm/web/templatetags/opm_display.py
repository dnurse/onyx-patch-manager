"""Presentation-only formatting of controller timestamps and scan ages."""
from datetime import datetime, timezone
from django import template
from django.utils import timezone as dj_timezone

register = template.Library()

@register.filter
def scan_time(value):
    """Controller stores Unix timestamps, Django stores aware datetimes."""
    try:
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        local = dj_timezone.localtime(dt)
        return local.strftime('%b %d, %Y %H:%M %Z')
    except (TypeError, ValueError, OverflowError, OSError):
        return 'Not scanned'

@register.filter
def short_id(value):
    return str(value or '')[:8]


@register.filter
def engine_not_installed(result, engine):
    """Normalize both new and historical Docker/Podman scan formats.

    Older scans stored an ENOENT exception string instead of a structured
    `installed` boolean. This filter preserves compatibility without making
    the operator rescan every host immediately after upgrading.
    """
    if not isinstance(result, dict):
        return False
    if result.get('installed') is False:
        return True
    error = str(result.get('error') or '').lower()
    return ("no such file or directory" in error and
            ("'" + str(engine).lower() + "'" in error or
             '"' + str(engine).lower() + '"' in error))


@register.filter
def engine_message(result, engine):
    """Keep low-level ENOENT wording out of the normal container view."""
    name = str(engine).capitalize()
    if engine_not_installed(result, engine):
        return f'{name} not installed'
    if not isinstance(result, dict):
        return 'Not scanned yet'
    error = str(result.get('error') or '').strip()
    if error:
        return name + ' inspection unavailable: ' + error[:240]
    if result.get('available'):
        if result.get('scope_warning'):
            return 'No containers visible in accessible contexts. Some contexts could not be inspected.'
        scope = result.get('scope', 'current SSH user')
        return 'No containers found in checked context(s): ' + str(scope)[:160]
    return name + ' is installed, but OPM cannot access its containers'
