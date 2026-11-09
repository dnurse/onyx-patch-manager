"""v0.6.15: complete history deletion and bounded dashboard/history layouts.

Tests touch only temporary SQLite files and never connect to a managed host.
"""
from pathlib import Path

import pytest

from opm.store import Store

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'


def seed(tmp_path):
    store = Store(tmp_path / 'state.db')
    host = store.add_host({'name': 'Test Server', 'address': '192.0.2.6',
                           'ssh_user': 'opm', 'ssh_port': 22,
                           'key_path': '/tmp/opm-test-key', 'group_name': 'Test',
                           'sudo_mode': 'passwordless', 'protected': False})
    store.verified(host['id'])
    scan_id = store.begin_scan(host['id'])
    store.finish_scan(scan_id, {'os': {'family': 'debian', 'metadata': 'fresh',
                                     'package_query_ok': True,
                                     'packages': [{'name': 'curl', 'current': '1', 'candidate': '2'}]}})
    plan = store.create_plan([host['id']])
    store.approve_plan(plan['id'], 'admin', False)
    job = store.create_job(plan['id'], 'history-clear-test')
    store.event(job['id'], host['id'], 'error', 'Preflight', 'Example failure')
    store.job_state(job['id'], 'failed')
    activity = store.begin_activity('ssh_repair', 'Repair SSH: Test Server', ['host:' + host['id']])
    store.activity_progress(activity['id'], 'Checking SSH')
    store.activity_finish(activity['id'], 'failed', 'SSH repair failed')
    with store.connection() as db:
        # The actual Django model writes to the same database. This minimal
        # table simulates its presence without depending on installed Django.
        db.execute('CREATE TABLE opm_web_auditevent (id INTEGER PRIMARY KEY, action TEXT)')
        db.execute("INSERT INTO opm_web_auditevent(action) VALUES ('accounts.create')")
        db.execute("INSERT INTO opm_web_auditevent(action) VALUES ('hosts.ssh_repair.start')")
    return store, host, job, activity


def test_clear_all_deletes_patch_activity_ssh_repair_and_audits_but_preserves_inventory(tmp_path):
    store, host, job, activity = seed(tmp_path)
    result = store.clear_all_history()
    assert result == {'deleted': 4, 'jobs': 1, 'activities': 1, 'audits': 2, 'job_ids': [job['id']]}
    assert store.jobs() == []
    assert store.recent_activities(100, include_events=True) == []
    with store.connection() as db:
        for table in ('events', 'jobs', 'job_targets', 'activities', 'activity_events', 'opm_web_auditevent'):
            assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
    assert store.host(host['id'])['name'] == 'Test Server'
    assert store.last_scan(host['id'])['state'] == 'completed'
    assert store.clear_all_history()['deleted'] == 0


def test_clear_all_is_atomic_when_patch_active(tmp_path):
    store, host, job, activity = seed(tmp_path)
    with store.connection() as db:
        db.execute("UPDATE jobs SET state='running' WHERE id=?", (job['id'],))
    with pytest.raises(ValueError, match='patch jobs are active'):
        store.clear_all_history()
    assert store.jobs() and store.recent_activities()
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM opm_web_auditevent').fetchone()[0] == 2


def test_clear_all_blocks_background_activity_and_locks(tmp_path):
    store, host, job, activity = seed(tmp_path)
    active = store.begin_activity('ssh_test', 'Test SSH', ['host:' + host['id']])
    with pytest.raises(ValueError, match='background activities'):
        store.clear_all_history()
    store.activity_finish(active['id'], 'completed', 'Connected')
    with store.connection() as db:
        db.execute('INSERT INTO host_locks(host_id,job_id) VALUES (?,?)', (host['id'], job['id']))
    with pytest.raises(ValueError, match='locked'):
        store.clear_all_history()
    assert store.jobs()
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM opm_web_auditevent').fetchone()[0] == 2


def test_clear_all_handles_pre_migration_database_without_audit_table(tmp_path):
    store = Store(tmp_path / 'state.db')
    assert store.clear_all_history() == {'deleted':0,'jobs':0,'activities':0,'audits':0,'job_ids':[]}


def test_clear_alerts_acknowledges_warnings_but_keeps_history(tmp_path):
    store, host, job, activity = seed(tmp_path)
    with store.connection() as db:
        db.execute("UPDATE activities SET state='completed_with_warnings',acknowledged=0 WHERE id=?", (activity['id'],))
    assert store.acknowledge_past_alerts() == {'job_alerts':1,'activity_alerts':1}
    assert store.jobs()[0]['state'] == 'failed'
    assert store.activity(activity['id'])['state'] == 'completed_with_warnings'
    assert store.acknowledge_past_alerts() == {'job_alerts':0,'activity_alerts':0}


def test_history_template_scrolling_and_clear_all_confirmations():
    history = (WEB / 'templates/web/history.html').read_text()
    styles = (WEB / 'static/web/styles.css').read_text()
    assert 'class="table-scroll history-scroll"' in history
    assert 'overflow: auto;' in styles and '.history-scroll' in styles
    assert '.history-scroll .history-table thead th' in styles
    assert 'Clear all history' in history
    assert 'name="confirm_clear" value="yes" required' in history
    assert 'including security audits' in history
    assert 'Clear patch history' not in history


def test_header_clear_alerts_and_dashboard_sizing():
    base = (WEB / 'templates/web/base.html').read_text()
    dash = (WEB / 'templates/web/dashboard.html').read_text()
    styles = (WEB / 'static/web/styles.css').read_text()
    views = (WEB / 'views.py').read_text()
    assert 'class="activity-clear-alerts"' in base
    assert "{% csrf_token %}" in base
    assert "name=\"next\" value=\"{{ request.path }}\"" in base
    assert "request.user.is_superuser" in base
    assert "url_has_allowed_host_and_scheme" in views
    assert "_call('history.clear')" in views
    assert "_actor_action(request, 'history.clear'" not in views
    assert 'class="dashboard-layout"' in dash
    assert 'class="surface server-table-surface"' in dash
    assert 'class="surface side-panel"' in dash
    assert '.dashboard-layout > .surface {' in styles
    assert 'height: clamp(360px, 62vh, 660px)' in styles
    assert '.dashboard-layout .side-panel > .activity-list' in styles
