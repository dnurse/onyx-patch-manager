"""Durable background activities, collision checks and secret protection."""
import asyncio
import sqlite3
import pytest
from opm.controller import Controller
from opm.store import Store


def test_activity_lifecycle_and_duplicate_prevention(repo, host_data):
    store, _ = repo
    host = store.add_host(host_data)
    activity = store.begin_activity('ssh_test', 'Testing SSH', ['host:' + host['id']])
    assert activity['state'] == 'queued'
    with pytest.raises(ValueError, match='already running'):
        store.begin_activity('scan', 'Scanning', ['host:' + host['id']])
    store.activity_progress(activity['id'], 'Checking credentials', completed=1)
    assert store.activity(activity['id'])['completed'] == 1
    store.activity_finish(activity['id'], 'failed', 'SSH unavailable')
    assert store.activity(activity['id'])['state'] == 'failed'
    assert store.begin_activity('ssh_test', 'Testing again', ['host:' + host['id']])


def test_restart_does_not_replay_inflight_remote_changes(repo):
    store, config = repo
    operation = store.begin_activity('enrollment', 'Configuring VM', ['candidate:192.0.2.5:22'])
    # Simulate reboot/restart. No background operation is launched again.
    Store(config.database).reconcile_activities()
    recovered = store.activity(operation['id'])
    assert recovered['state'] == 'interrupted'
    assert 'restarted' in recovered['detail']
    assert store.begin_activity('enrollment', 'Manual retry', ['candidate:192.0.2.5:22'])


def test_activity_records_exclude_bootstrap_secrets(repo):
    store, config = repo
    activity = store.begin_activity('enrollment', 'Configuring server', ['candidate:192.0.2.6:22'])
    store.activity_finish(activity['id'], 'failed', 'Provisioning requires review',
                          {'host_id': 'safe', 'login_password': 'SHOULD_NEVER_PERSIST'})
    assert b'SHOULD_NEVER_PERSIST' not in config.database.read_bytes()
    assert store.activity(activity['id'])['result'] == {'host_id': 'safe'}


def test_scan_start_returns_immediately_and_reports_real_progress(repo, host_data, monkeypatch):
    store, config = repo
    host = store.add_host(host_data)
    store.verified(host['id'], True)
    store.update_host(host['id'], {**host, 'sudo_mode': 'passwordless'})
    from opm import controller as c
    import time

    def read_only_scan(_host, _config, _password):
        time.sleep(.03)
        return {'os': {'family': 'debian', 'metadata': 'fresh', 'packages': []}, 'engines': {}}
    monkeypatch.setattr(c, 'inspect_host', read_only_scan)

    async def run():
        ctl = Controller(config)
        result = await ctl.dispatch('scans.start', {})
        assert result['scheduled'] == 1
        task = ctl.store.activity(result['activity_id'])
        assert task['state'] in ('queued', 'running')
        with pytest.raises(ValueError, match='already running'):
            await ctl.dispatch('scans.start', {})
        await asyncio.gather(*tuple(ctl._scans))
        task = ctl.store.activity(result['activity_id'])
        assert task['state'] == 'completed'
        assert task['completed'] == 1
        assert ctl.store.last_scan(host['id'])['state'] == 'completed'
        activities = await ctl.dispatch('activities.list', {})
        assert any(a['id'] == task['id'] for a in activities)
    asyncio.run(run())


def test_ssh_test_is_background_and_verified_only(repo, host_data, monkeypatch):
    store, config = repo
    host = store.add_host(host_data)
    from opm import controller as c
    monkeypatch.setattr(c, 'test_connection', lambda _h, _c: {'connected': True})

    async def run():
        ctl = Controller(config)
        with pytest.raises(ValueError, match='Complete server setup'):
            await ctl.dispatch('hosts.test.start', {'id': host['id']})
        ctl.store.verified(host['id'], True)
        submitted = await ctl.dispatch('hosts.test.start', {'id': host['id']})
        await asyncio.gather(*tuple(ctl._scans))
        state = ctl.store.activity(submitted['activity_id'])
        assert state['state'] == 'completed'
        assert state['result']['host_id'] == host['id']
    asyncio.run(run())


def test_scanning_and_patching_mutually_exclude_each_other(repo, host_data):
    """A scan can neither overlap a patch nor permit a patch to start."""
    from test_repository import ready_host
    store, _ = repo
    host = ready_host(store, host_data)
    plan = store.create_plan([host['id']])
    store.approve_plan(plan['id'], 'operator', False, 'PATCH')
    activity = store.begin_activity('scan', 'Checking APT', ['host:' + host['id']])
    with pytest.raises(ValueError, match='active scan'):
        store.create_job(plan['id'], 'while-scan')
    with pytest.raises(ValueError, match='locked'):
        store.remove_host(host['id'])
    store.activity_finish(activity['id'], 'completed', 'Scan finished')
    job = store.create_job(plan['id'], 'after-scan')
    with pytest.raises(ValueError, match='patch job'):
        store.begin_activity('scan', 'Another check', ['host:' + host['id']])
    store.job_state(job['id'], 'completed')


def test_controller_restart_closes_orphan_scan_row(repo, host_data):
    store, config = repo
    host = store.add_host(host_data)
    store.verified(host['id'], True)
    activity = store.begin_activity('scan', 'Checking', ['host:' + host['id']])
    scan_id = store.begin_scan(host['id'])
    Store(config.database).reconcile_activities()
    assert store.activity(activity['id'])['state'] == 'interrupted'
    last = store.last_scan(host['id'])
    assert last['id'] == scan_id
    assert last['state'] == 'failed'
    assert 'restarted' in last['error']


def test_existing_unverified_host_enrollment_can_save_its_own_profile(repo, host_data):
    """Finishing an older unverified host must not self-deadlock on its lock."""
    store, _ = repo
    host = store.add_host(host_data)
    activity = store.begin_activity('enrollment', 'Finish setup',
                                    ['candidate:192.0.2.24:22', 'host:' + host['id']])
    with pytest.raises(ValueError, match='locked'):
        store.update_host(host['id'], {**host_data, 'ssh_user': 'opm'})
    changed = store.update_host(host['id'], {**host_data, 'ssh_user': 'opm'},
                                activity_owner=activity['id'])
    assert changed['ssh_user'] == 'opm'
    store.activity_finish(activity['id'], 'completed', 'Registered')
