"""Installer password retry and UI regression checks for v0.5.10.

The isolated admin-step test exercises the actual shell block with a fake
unprivileged Python command. Live Django/network integration is still needed.
"""
import os
import re
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_admin_password_rejection_prompts_again_without_reinstall(tmp_path):
    installer = (ROOT / 'OPMInstall.sh').read_text()
    marker = '    # Password policy is validated by the same Django validators'
    first = installer.index('    while :; do', installer.index(marker))
    last = installer.index('  elif [[ -t 0 && -t 1 ]]; then', first)
    block = installer[first:last]
    assert 'validate_password(password, user=user)' in block
    assert 'except ValidationError as exc:' in block
    assert 'opm_ui_message' in block
    assert 'opm_ui_get_administrator' in block
    assert 'admin_errors=$(mktemp' in block
    assert 'unset OPM_UI_ADMIN_PASSWORD' in block
    retry_log = tmp_path / 'admin-retry.log'
    attempts = tmp_path / 'attempts'
    script = tmp_path / 'exercise_admin_retry.sh'
    script.write_text(r'''#!/usr/bin/env bash
set -Eeuo pipefail
LOG_FILE="$1"
ATTEMPTS="$2"
OPM_UI_ADMIN='admin'
OPM_UI_ADMIN_PASSWORD='123456789012'
VENV='/tmp/fake-venv'
fail() { echo "Unexpected fatal error: $*" >&2; exit 88; }
as_opm() {
  cat >/dev/null
  if [[ ! -f "$ATTEMPTS" ]]; then
    echo 1 >"$ATTEMPTS"
    printf '%s\n' 'This password is too common.' 'This password is entirely numeric.'
    return 2
  fi
  echo 2 >"$ATTEMPTS"
  return 0
}
opm_ui_gauge_stop() { echo 'GAUGE_STOP'; }
opm_ui_gauge_start() { echo 'GAUGE_START'; }
opm_ui_message() { printf 'USER_ERROR: %s\n' "$2"; }
opm_ui_get_administrator() {
  OPM_UI_ADMIN_PASSWORD='Strong-random-secret-567!'
  echo 'PROMPT_RETRIED'
  return 0
}
''' + block + '''
echo 'ADMIN_CREATED'
test -z "${OPM_UI_ADMIN_PASSWORD:-}"
''')
    result = subprocess.run(['bash', str(script), str(retry_log), str(attempts)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert attempts.read_text().strip() == '2'
    assert 'This password is too common' in result.stdout
    assert 'This password is entirely numeric' in result.stdout
    assert 'PROMPT_RETRIED' in result.stdout
    assert 'ADMIN_CREATED' in result.stdout
    assert '123456789012' not in retry_log.read_text()
    assert 'Strong-random-secret-567!' not in retry_log.read_text()


def test_existing_install_routes_to_maintenance_and_new_default_is_wizard():
    install = (ROOT / 'OPMInstall.sh').read_text()
    maintenance = (ROOT / 'OPMMaintenance.sh').read_text()
    assert 'OPM_INSTALL_EXPLICIT" == 0' in install
    assert '-f "$ENV_FILE" && -d "$APP_DIR"' in install
    assert 'exec bash "$SRC_DIR/OPMMaintenance.sh" --source "$SRC_DIR" --menu' in install
    assert 'opm_ui_first_install "$SRC_DIR"' in install
    menu = maintenance.split("--menu 'Select an action", 1)[1]
    positions = [menu.index(f"{n} '{label}'") for n, label in [
        (1, 'Upgrade / Install OPM'), (2, 'System Status'),
        (3, 'Repair Installation'), (4, 'Configure Web Access'),
        (5, 'Uninstall OPM'),
    ]]
    assert positions == sorted(positions)
    assert 'Upgrade OPM from v${installed_version} to v${release_version}' in maintenance


def test_release_version_is_from_payload_and_template_context():
    with (ROOT / 'pyproject.toml').open('rb') as handle:
        release = tomllib.load(handle)['project']['version']
    assert tuple(map(int, release.split('.'))) >= (0, 5, 10)  # newer releases preserve this contract
    assert f'__version__ = "{release}"' in (ROOT / 'src/opm/__init__.py').read_text()
    assert f'OPM_UI_VERSION="{release}"' in (ROOT / 'OPMTUI.sh').read_text()
    assert 'tomllib.load(source)' in (ROOT / 'OPMInstall.sh').read_text()
    assert 'tomllib.load(f)' in (ROOT / 'OPMMaintenance.sh').read_text()
    assert 'opm.web.context.app_version' in (ROOT / 'src/opm/web/settings.py').read_text()
    base = (ROOT / 'src/opm/web/templates/web/base.html').read_text()
    assert 'v{{ opm_version }}' in base
    assert 'v0.5.7' not in base


def test_navigation_spacing_and_server_empty_states():
    ui = ROOT / 'src/opm/web'
    base = (ui / 'templates/web/base.html').read_text()
    styles = (ui / 'static/web/styles.css').read_text()
    assert 'Internal management' not in base
    assert 'class="avatar"' not in base
    assert '<button class="logout-link" type="submit">Sign out</button>' in base
    assert "background-position: right 17px center" in styles
    assert re.search(r'\.account-list\s*\{\s*padding:\s*12px var\(--surface-inset\)', styles)
    for name in ('servers.html', 'patch.html', 'dashboard.html'):
        content = (ui / 'templates/web' / name).read_text()
        assert 'No servers registered yet.' in content
        assert 'Add your first server' in content
        assert "{% if request.user.is_superuser %}<a href=\"{% url 'server_add' %}\"" in content
    assert 'Manage Linux hosts' in (ui / 'templates/web/patch.html').read_text()
