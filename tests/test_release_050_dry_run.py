"""Dry-run release contract: simulations cannot turn into live APT writes.

These tests use an isolated SQLite DB, mocked SSH, and mocked Ansible Runner.
No real managed server or Debian APT command is modified during the tests.
"""
import json
import sys
import types

import pytest

from opm.store import Store
from opm.worker import _validation_playbook, run_job


def prepare(repo, host_data, packages, *, mode='dry_run', refresh=False):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'passwordless'})
    store.verified(host['id'])
    scan = store.begin_scan(host['id'])
    store.finish_scan(scan, {'os': {
        'family': 'debian', 'metadata': 'fresh',
        'package_query_ok': True, 'packages': packages,
    }})
    plan = store.create_plan([host['id']], mode=mode, refresh_metadata=refresh)
    store.approve_plan(plan['id'], 'test-opm', False)
    job = store.create_job(plan['id'], f'job-{mode}')
    return store, config, host, job


def test_dry_run_allows_zero_updates_and_preserves_mode(repo, host_data):
    store, config, host, job = prepare(repo, host_data, [])
    assert store.job(job['id'])['mode'] == 'dry_run'
    assert store.plan(job['plan_id'])['snapshot']['mode'] == 'dry_run'
    assert store.plan(job['plan_id'])['snapshot']['refresh_metadata'] is False
    assert store.host(host['id'])['verified'] == 1


def test_live_patching_zero_updates_remains_disallowed(repo, host_data):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'passwordless'})
    store.verified(host['id'])
    scan = store.begin_scan(host['id'])
    store.finish_scan(scan, {'os': {'family': 'debian', 'metadata': 'fresh',
                                   'package_query_ok': True, 'packages': []}})
    with pytest.raises(ValueError, match='no pending OS updates'):
        store.create_plan([host['id']], mode='apply')
    with pytest.raises(ValueError, match='Refresh consent applies only'):
        store.create_plan([host['id']], mode='apply', refresh_metadata=True)


def test_validation_playbook_contains_no_apt_or_shell(tmp_path):
    path = tmp_path / 'readonly.json'
    _validation_playbook(path)
    play = json.loads(path.read_text())
    assert play[0]['tasks'][0]['ansible.builtin.stat'] == {'path': '/etc/os-release'}
    assert 'ansible.builtin.apt' not in path.read_text()
    assert 'ansible.builtin.shell' not in path.read_text()
    assert 'ansible.builtin.command' not in path.read_text()


def test_simulated_job_releases_host_lock_and_does_not_patch(repo, host_data, monkeypatch):
    store, config, host, job = prepare(repo, host_data, [])
    commands = []
    monkeypatch.setattr('opm.worker.check_ansible_runtime',
                        lambda: ('/mock/bin/ansible-playbook', '/mock/bin:/usr/bin'))
    monkeypatch.setattr('opm.worker._validated_inventory_file',
                        lambda h, inv, directory, exe, path: directory / 'inventory' / 'hosts.json')
    monkeypatch.setattr('opm.worker.test_connection',
                        lambda *a, **kw: {'connected': True, 'sudo': 'passwordless'})
    monkeypatch.setattr('opm.worker.inspect_os',
                        lambda *a, **kw: {'package_query_ok': True, 'packages': []})

    def fake_ssh_command(host, config, command, *args, **kwargs):
        commands.append(command)
        assert 'apt-get' not in command or command[-2:] == ['-s', 'upgrade']
        return '0 upgraded, 0 newly installed'

    monkeypatch.setattr('opm.worker.ssh_command', fake_ssh_command)
    runner_calls = []

    def fake_run(**kwargs):
        runner_calls.append(kwargs)
        play = json.loads(open(kwargs['playbook'], encoding='utf-8').read())
        assert 'ansible.builtin.stat' in play[0]['tasks'][0]
        assert kwargs['cmdline'].startswith('--check')
        callback = kwargs['event_handler']
        callback({'event': 'runner_on_ok', 'event_data': {
            'host': host['name'],
            'task': 'Validate privileged Ansible access in check mode',
            'res': {'stat': {'exists': True}},
        }})
        callback({'event': 'playbook_on_stats', 'event_data': {'ok': {host['name']: 1}}})
        return types.SimpleNamespace(status='successful', rc=0)

    monkeypatch.setitem(sys.modules, 'ansible_runner', types.SimpleNamespace(run=fake_run))
    run_job(job['id'], config)
    assert store.job(job['id'])['state'] == 'simulated'
    assert store.job(job['id'])['targets'][0]['state'] == 'simulated'
    assert runner_calls
    assert any(cmd[-2:] == ['-s', 'upgrade'] for cmd in commands)
    assert not any('update' in cmd or ('apt-get' in cmd and '-s' not in cmd) for cmd in commands)
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM host_locks WHERE host_id=?', (host['id'],)).fetchone()[0] == 0


def test_explicit_metadata_refresh_only_in_opted_in_simulation(repo, host_data, monkeypatch):
    store, config, host, job = prepare(repo, host_data, [], refresh=True)
    assert store.plan(job['plan_id'])['snapshot']['refresh_metadata'] is True


def test_dry_run_metadata_refresh_is_explicit(repo, host_data, monkeypatch):
    """The worker must not refresh indexes during simulations by default."""
    from opm.worker import _simulate_preflight
    store, config, host, job = prepare(repo, host_data, [])
    target = store.plan(job['plan_id'])['snapshot']['targets'][0]
    host = store.host(host['id'])
    commands = []
    monkeypatch.setattr('opm.worker.test_connection', lambda *a, **k:
                        {'connected': True, 'sudo': 'passwordless'})
    monkeypatch.setattr('opm.worker.inspect_os', lambda *a, **k:
                        {'package_query_ok': True, 'packages': []})
    monkeypatch.setattr('opm.worker.ssh_command', lambda h, c, cmd, *a, **k:
                        commands.append(cmd) or '0 upgraded')
    _simulate_preflight(host, target, config, None, store, job['id'], refresh_metadata=False)
    assert not any('update' in command for command in commands)
    assert any(command[-2:] == ['-s', 'upgrade'] for command in commands)
    commands.clear()
    _simulate_preflight(host, target, config, None, store, job['id'], refresh_metadata=True)
    assert any('update' in command for command in commands)


def test_dry_run_without_ansible_target_success_is_failure(repo, host_data, monkeypatch):
    """Never label a skipped/empty Ansible run as Simulated successfully."""
    store, config, host, job = prepare(repo, host_data, [])
    monkeypatch.setattr('opm.worker.check_ansible_runtime',
                        lambda: ('/mock/bin/ansible-playbook', '/mock/bin:/usr/bin'))
    monkeypatch.setattr('opm.worker._validated_inventory_file',
                        lambda h, inv, directory, exe, path: directory / 'inventory' / 'hosts.json')
    monkeypatch.setattr('opm.worker._simulate_preflight', lambda *a, **kw: None)
    def fake_run(**kwargs):
        callback = kwargs['event_handler']
        callback({'event': 'playbook_on_no_hosts_matched', 'event_data': {}})
        callback({'event': 'playbook_on_stats', 'event_data': {'ok': {}}})
        return types.SimpleNamespace(rc=0, status='successful')
    monkeypatch.setitem(sys.modules, 'ansible_runner', types.SimpleNamespace(run=fake_run))
    run_job(job['id'], config)
    assert store.job(job['id'])['state'] == 'failed'
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM host_locks WHERE host_id=?', (host['id'],)).fetchone()[0] == 0
