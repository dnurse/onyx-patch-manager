"""Regression coverage for v0.5.20; no real remote account deletion."""
from pathlib import Path
import io
import json
import pwd
import subprocess
import sys
from types import SimpleNamespace

import pytest

from opm.store import Store
from opm.onboarding import REMOTE_CREATE_ACCOUNT

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'


def _run_remote_replace(monkeypatch, *, userdel_code, deletion_succeeds, home_remains=False):
    old = SimpleNamespace(pw_uid=1001, pw_dir='/home/opm')
    new = SimpleNamespace(pw_uid=1002, pw_gid=1002, pw_dir='/home/opm')
    created = False
    events = []

    def lookup(username):
        if username != 'opm':
            raise KeyError(username)
        if not deletion_succeeds:
            return old
        if created:
            return new
        if 'userdel' not in [Path(cmd[0]).name for cmd in events]:
            return old
        raise KeyError(username)

    def run(argv, **kwargs):
        nonlocal created
        events.append(argv)
        if '/usr/sbin/userdel' == argv[0]:
            return SimpleNamespace(returncode=userdel_code, stderr=b'opm mail spool not found')
        if argv[0].endswith('/useradd'):
            created = True
        return SimpleNamespace(returncode=0, stderr=b'')

    monkeypatch.setattr(pwd, 'getpwnam', lookup)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(Path, 'is_file', lambda p: str(p) == '/usr/bin/loginctl')
    monkeypatch.setattr(Path, 'exists', lambda p: home_remains if str(p) == '/home/opm' else False)
    monkeypatch.setattr(Path, 'is_symlink', lambda p: False)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({
        'account':'opm','account_mode':'replace','replace_ack':True,
        'public_key':'ssh-ed25519 TEST', 'sudo_password':'test-safe-strong-password-123',
    }) + '\n'))
    buf = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', buf)
    # Prevent mutating any real /home/opm while still testing the account
    # deletion/recreation control path inside the exact remote script.
    test_script = REMOTE_CREATE_ACCOUNT.replace('        write_authorized_key(account, key)\n',
                                                  '        pass  # stub only SSH key installation\n')
    try:
        exec(compile(test_script, '<remote-test>', 'exec'), {'__name__':'__main__'})
    except SystemExit:
        pass
    return json.loads(buf.getvalue()), events


def test_remote_account_removed_with_mail_spool_warning_still_recreates(monkeypatch):
    result, calls = _run_remote_replace(monkeypatch, userdel_code=12, deletion_succeeds=True)
    assert result == {'ok': True, 'account':'opm', 'mode':'replace'}
    assert ['/usr/sbin/userdel', '-r', 'opm'] in calls
    assert ['/usr/sbin/useradd', '--create-home', '--shell', '/bin/bash', 'opm'] in calls
    assert all('-f' not in argv for argv in calls)


def test_remote_account_still_exists_blocks_recreation(monkeypatch):
    result, calls = _run_remote_replace(monkeypatch, userdel_code=8, deletion_succeeds=False)
    assert result['error_code'] == 'ACCOUNT_BUSY'
    assert not any(Path(cmd[0]).name == 'useradd' for cmd in calls)


def test_remote_home_residue_blocks_unsafe_recreation(monkeypatch):
    result, calls = _run_remote_replace(monkeypatch, userdel_code=12, deletion_succeeds=True, home_remains=True)
    assert result['error_code'] == 'ACCOUNT_HOME_REMAINS'
    assert not any(Path(cmd[0]).name == 'useradd' for cmd in calls)


def test_activity_history_keeps_full_sanitized_error_and_progress(tmp_path):
    store = Store(tmp_path/'history.db')
    activity = store.begin_activity('enrollment', 'Enroll: old-host', ['host:old-host'])
    long_message = 'Provisioning failed. ' + ('Account cannot be safely replaced. ' * 20)
    store.activity_progress(activity['id'], 'Checking SSH identity')
    store.activity_progress(activity['id'], 'Replacing old opm account')
    store.activity_finish(activity['id'], 'failed', long_message)
    latest = store.activity(activity['id'])
    assert len(latest['detail']) == 240
    assert any(long_message in event['message'] for event in latest['events'])
    assert len(latest['events']) == 4  # queue, two steps, finish
    historical = store.recent_activities(100, include_events=True)
    assert historical[0]['events'] == latest['events']
    assert store.recent_activities(1)[0].get('events') is None  # small live feed
    assert long_message in (tmp_path/'history.db').read_bytes().decode('utf-8', errors='ignore')


def test_history_template_filters_expansion_and_setup_indicator_only_column():
    dashboard = (WEB/'templates/web/dashboard.html').read_text()
    history = (WEB/'templates/web/history.html').read_text()
    base = (WEB/'templates/web/base.html').read_text()
    assert 'class="setup-incomplete"' not in dashboard
    assert '<span class="status setup-needed">Setup incomplete</span>' not in dashboard
    assert '<td data-label="Last scan">{% if not h.verified %}<span class="status setup-needed">Setup required</span>' in dashboard
    assert '<h1>History</h1>' in history
    assert 'name="patches"' in history and 'name="activities"' in history
    assert 'name="sort"' in history
    assert '<details class="history-log">' in history
    assert 'for entry in item.events' in history
    assert 'Patch history</a>' not in base
    views = (WEB/'views.py').read_text()
    assert "_actor_action(request, 'sessions.login')" in views
    assert "_actor_action(request, 'sessions.logout')" in views
    assert "show_patches =" in views and "show_activities =" in views
    assert "sort_order =" in views and "_call('activities.history')" in views


def test_uninstall_exits_menu_only_when_successfully_completed():
    script = (ROOT/'OPMMaintenance.sh').read_text()
    assert 'OPM_UNINSTALL_COMPLETED=1' in script
    assert '[[ "${OPM_UNINSTALL_COMPLETED:-0}" == 1 ]] && return 0' in script
    assert 'userdel_output=$(userdel -r "$APP_USER" 2>&1)' in script
    assert 'userdel_rc' in script


def test_all_v0520_versions_agree():
    import tomllib
    with (ROOT/'pyproject.toml').open('rb') as f:
        version = tomllib.load(f)['project']['version']
    assert version == '0.6.0'  # current release; earlier v0.5.20 tests remain relevant
    assert f'__version__ = "{version}"' in (ROOT/'src/opm/__init__.py').read_text()
    assert f'OPM_UI_VERSION="{version}"' in (ROOT/'OPMTUI.sh').read_text()


def test_upgrade_existing_database_creates_log_table_without_erasing_history(tmp_path):
    import sqlite3
    db_path = tmp_path / 'old-activity.db'
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE activities (id TEXT PRIMARY KEY, kind TEXT, label TEXT, "
                     "state TEXT, detail TEXT, total INTEGER, completed INTEGER, failed INTEGER, "
                     "created REAL, updated REAL, finished REAL, result_json TEXT, acknowledged INTEGER)")
        conn.execute("INSERT INTO activities VALUES ('a1','scan','Old scan','completed','ok',1,1,0, "
                     "1000,1001,1001,'{}',0)")
    store = Store(db_path)
    assert store.activity('a1')['detail'] == 'ok'
    assert store.activity('a1')['events'] == []
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='activity_events'").fetchone()
    assert exists == (1,)


def test_new_history_redacts_key_value_secrets(tmp_path):
    store = Store(tmp_path/'secret.db')
    activity = store.begin_activity('ssh_test', 'Verify: sample', ['host:sample'])
    store.activity_finish(activity['id'], 'failed', 'Password=secret-token token=abc999 backend disconnected')
    events = store.activity(activity['id'])['events']
    assert 'secret-token' not in str(events)
    assert 'abc999' not in str(events)
    assert '[REDACTED]' in str(events)
