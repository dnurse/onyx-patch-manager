from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIME_ZONE = "America/Toronto"
ENV_FILE = Path.home() / ".config" / "opm" / "web.env"

def _read_env_entries() -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                value = value[1:-1]
            data[key] = value
    except OSError:
        pass
    return data

def get_configured_timezone() -> str:
    value = (_read_env_entries().get('OPM_WEB_TIME_ZONE') or '').strip()
    if not value:
        return DEFAULT_TIME_ZONE
    try:
        ZoneInfo(value)
        return value
    except ZoneInfoNotFoundError:
        return DEFAULT_TIME_ZONE

def update_configured_timezone(tz_name: str) -> str:
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError('Unknown timezone') from exc
    entries = _read_env_entries()
    entries['OPM_WEB_TIME_ZONE'] = tz_name
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in sorted(entries.items())]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tz_name


def get_validation_preferences() -> dict[str, bool]:
    """Read safe UI defaults from the existing private web configuration.

    These values only preselect controls in the browser. The controller
    independently validates the submitted job mode before creating its
    immutable execution snapshot.
    """
    values = _read_env_entries()
    return {
        'default_dry_run': values.get('OPM_WEB_DEFAULT_DRY_RUN', '0') == '1',
        'verbose_dry_run': values.get('OPM_WEB_VERBOSE_DRY_RUN', '1') == '1',
    }


def update_validation_preferences(default_dry_run: bool, verbose_dry_run: bool) -> None:
    """Store only boolean preferences, never operation credentials or keys."""
    entries = _read_env_entries()
    entries['OPM_WEB_DEFAULT_DRY_RUN'] = '1' if default_dry_run else '0'
    entries['OPM_WEB_VERBOSE_DRY_RUN'] = '1' if verbose_dry_run else '0'
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Match the existing timezone setting's private-file storage. Keep the
    # file mode restrictive after updates and do not create temp public data.
    ENV_FILE.write_text('\n'.join(f'{k}={v}' for k, v in sorted(entries.items())) + '\n', encoding='utf-8')
    ENV_FILE.chmod(0o600)
