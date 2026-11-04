"""Unit/integration contracts for Whiptail, using a fake binary, never APT.

These tests verify actual Bash wizard state transitions and avoid touching
system accounts, the host configuration, and privileged installation stages.
"""
from pathlib import Path
import subprocess
import os
import textwrap

from test_release_053_network_selection import tool

ROOT = Path(__file__).resolve().parents[1]


def fake_whiptail(tmp_path, *, cancel_first=False):
    program = tmp_path / 'whiptail'
    program.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import os, sys
        args = sys.argv[1:]
        log = os.environ.get('OPM_WHIPTAIL_CALLS')
        if log:
            with open(log, 'a', encoding='utf-8') as out:
                out.write(' '.join(a.splitlines()[0][:100] for a in args) + '\\n')
        if '--gauge' in args:
            data = sys.stdin.read()
            if os.environ.get('OPM_GAUGE_LOG'):
                with open(os.environ['OPM_GAUGE_LOG'], 'w') as out: out.write(data)
        elif '--radiolist' in args:
            print('lan')
        elif '--inputbox' in args:
            title = args[args.index('--title')+1]
            if 'Host address' in title: print('192.168.55.10')
            elif 'Timezone' in title: print('Etc/UTC')
            elif 'Administrator' in title: print('admin')
            else: print('admin')
        elif '--passwordbox' in args:
            print('aUniqueLongPa$$wordForOPM')
        elif '--yesno' in args and os.environ.get('OPM_CANCEL_WELCOME') == '1':
            if any('Install Onyx Patch Manager' in a for a in args): sys.exit(1)
        sys.exit(0)
    '''))
    program.chmod(0o755)
    return program


def run_bash(source, tmp_path, *, cancel=False):
    fake_whiptail(tmp_path)
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'],
               OPM_WHIPTAIL_CALLS=str(tmp_path/'calls.log'))
    if cancel: env['OPM_CANCEL_WELCOME'] = '1'
    return subprocess.run(['bash','-c', 'set -Eeuo pipefail; source ./OPMTUI.sh; '
        'OPM_UI_ACTIVE=1; opm_ui_first_install . Etc/UTC; '
        'printf "RESULT:%s:%s:%s:%s\\n" "$OPM_UI_MODE" "$OPM_UI_HOST" '
        '"$OPM_UI_TIMEZONE" "$OPM_UI_ADMIN"'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)


def test_first_run_wizard_with_fake_whiptail(tmp_path):
    result = run_bash('', tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'RESULT:lan:192.168.55.10:Etc/UTC:admin' in result.stdout
    calls=(tmp_path/'calls.log').read_text()
    assert '--radiolist' in calls
    assert '--passwordbox' in calls
    assert '--yesno' in calls
    assert 'Review installation' in calls
    assert 'System requirements' in calls
    assert 'aUniqueLongPa$$wordForOPM' not in calls


def test_cancelled_welcome_does_not_continue(tmp_path):
    result = run_bash('', tmp_path, cancel=True)
    assert result.returncode != 0
    calls=(tmp_path/'calls.log').read_text()
    assert 'Step 1 of 4' not in calls


def test_whiptail_gauge_receives_progress(tmp_path):
    fake_whiptail(tmp_path)
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'],
               OPM_GAUGE_LOG=str(tmp_path/'gauge.log'))
    result = subprocess.run(['bash','-c',
        'set -Eeuo pipefail; source ./OPMTUI.sh; OPM_UI_ACTIVE=1; '
        'opm_ui_gauge_start; opm_ui_gauge_update 20 "Installing dependencies"; '
        'opm_ui_gauge_update 100 "Installation complete"; opm_ui_gauge_stop'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    content=(tmp_path/'gauge.log').read_text()
    assert '20\nInstalling dependencies' in content
    assert '100\nInstallation complete' in content


def test_explicit_tui_access_selection_reuses_strict_backend(tmp_path, monkeypatch):
    m=tool()
    monkeypatch.setattr(m, 'ENV_PATH', tmp_path/'web.env')
    m.ENV_PATH.write_text('OPM_WEB_SECRET_KEY='+'Z'*70+'\n')
    monkeypatch.setattr(m, 'LOG_DIR', tmp_path/'private')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0)
    assert m.main(['--initial','--mode','lan','--hostname','192.168.55.10']) == 0
    result=m.read_entries(m.ENV_PATH)
    assert result['OPM_WEB_ACCESS_MODE']=='lan'
    assert result['OPM_WEB_ALLOWED_HOSTS'].startswith('192.168.55.10')
    assert result['OPM_WEB_SECRET_KEY']=='Z'*70
    assert m.main(['--initial','--mode','lan','--hostname','8.8.8.8']) == 1


def test_maintenance_uses_whiptail_and_keeps_cli_safeguards():
    install=(ROOT/'OPMInstall.sh').read_text()
    maintenance=(ROOT/'OPMMaintenance.sh').read_text()
    assert 'OPMTUI.sh' in install
    assert 'OPMTUI.sh' in maintenance
    assert 'opm_ui_first_install' in install
    assert 'opm_ui_gauge_start' in install
    assert 'no_active_jobs || return 1' in maintenance
    assert 'TRUSTED_TUI' in maintenance
    assert 'sudo opm-installer --status' in maintenance
    assert 'python3 "$WEB_ACCESS_TOOL" --configure --mode' in maintenance
