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
    """Return True only for an engine confirmed absent in a scan.

    Older scan results may encode absence as an ENOENT diagnostic rather than
    an ``installed`` flag. Inaccessible scopes and other errors are not proof
    of absence, even if an unprivileged probe found no engine binary.
    """
    if not isinstance(result, dict):
        return False
    if result.get('scope_warning') or result.get('available') or result.get('containers'):
        return False
    if result.get('installed') is True:
        return False
    error = str(result.get('error') or '').lower()
    legacy_absent = ('no such file or directory' in error and
                     ("'" + str(engine).lower() + "'" in error or
                      '"' + str(engine).lower() + '"' in error))
    if result.get('installed') is False:
        return not error or legacy_absent
    return legacy_absent


@register.filter
def visible_engines(engines):
    """Hide confirmed absent engines in the UI, never erase stored results."""
    if not isinstance(engines, dict):
        return {}
    return {name: result for name, result in engines.items()
            if not engine_not_installed(result, name)}


@register.filter
def engine_inventory_message(engines):
    """Explain an empty container card without confusing unscanned and absent."""
    if not isinstance(engines, dict) or not engines:
        return 'No container scan results yet.'
    if all(name in engines and engine_not_installed(engines[name], name)
           for name in ('docker', 'podman')):
        return 'Neither Docker nor Podman is installed.'
    return 'Container engine discovery is incomplete. Run a new scan.'


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
    if result.get('scope_warning') and not result.get('available'):
        return name + ' discovery incomplete; see warning below'
    if result.get('available'):
        if result.get('scope_warning'):
            return 'No containers visible in accessible contexts. Some contexts could not be inspected.'
        scope = result.get('scope', 'current SSH user')
        return 'No containers found in checked context(s): ' + str(scope)[:160]
    return name + ' is installed, but OPM cannot access its containers'
