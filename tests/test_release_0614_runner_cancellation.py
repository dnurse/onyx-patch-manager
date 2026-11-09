"""Regression for GM cancellation: Ansible Runner canceled/254 is interruption.

All remote and Ansible calls are mocked. No SSH, package, or systemd writes.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from opm.worker import run_job
from test_repository import ready_host


def setup_execution(repo, host_data, monkeypatch, count=1):
    store, config = repo
    hosts = [ready_host(store, {**host_data, 'name': 'Test Server ' + str(i),
                                'address': '192.0.2.' + str(50+i)})
             for i in range(count)]
    plan = store.create_plan([h['id'] for h in hosts])
    store.approve_plan(plan['id'], 'internal tester', False)
    job = store.create_job(plan['id'], 'test-runner-cancel')
    monkeypatch.setattr('opm.worker.check_ansible_runtime',
                        lambda: ('/fake/bin/ansible-playbook', '/fake/bin:/usr/bin'))
    monkeypatch.setattr('opm.worker._validated_inventory_file',
                        lambda h, inv, d, e, p: d / 'inventory' / 'hosts.json')
    monkeypatch.setattr('opm.worker._fresh_preflight', lambda *a, **k: None)
    async def fake_credentials(method, params, cfg):
        assert method == 'credentials.claim'
        return {h['id']: 'TEST-ONLY-SUDO-PASSWORD' for h in hosts}
    monkeypatch.setattr('opm.worker.rpc', fake_credentials)
    monkeypatch.setattr('opm.worker.inspect_os', lambda *a, **k: {
        'package_query_ok': True, 'reboot_required': False, 'packages': []})
    monkeypatch.setattr('opm.worker._refresh_host_after_patch', lambda *a, **k: True)
    return store, config, hosts, job


@pytest.mark.parametrize('runner_state', ['canceled', 'cancelled'])
def test_canceled_ansible_runner_reconciles_job_and_target(repo, host_data,
                                                          monkeypatch, runner_state):
    store, config, hosts, job = setup_execution(repo, host_data, monkeypatch)

    def fake_runner(**kw):
        kw['event_handler']({'event': 'playbook_on_play_start', 'stdout':
                             'PLAY [Approved operating system maintenance]', 'event_data': {}})
        kw['event_handler']({'event': 'playbook_on_task_start', 'stdout':
                             'TASK [Upgrade installed packages without automatic removals]',
                             'event_data': {'task':
                             'Upgrade installed packages without automatic removals'}})
        return SimpleNamespace(status=runner_state, rc=254)

    monkeypatch.setitem(sys.modules, 'ansible_runner', SimpleNamespace(run=fake_runner))
    run_job(job['id'], config)

    updated = store.job(job['id'])
    assert updated['state'] == 'interrupted'
    assert updated['targets'][0]['state'] == 'interrupted'
    assert 'canceled' in updated['error'].lower()
    assert 'Last recorded step' in updated['targets'][0]['error']
    assert updated['finished'] is not None
    events = store.events(job['id'])
    assert any(e['task'] == 'Recovery' and e['level'] == 'warning' for e in events)
    assert not any(e['task'] == 'Execution failed' for e in events)
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM host_locks WHERE job_id=?',
                          (job['id'],)).fetchone()[0] == 0


@pytest.mark.parametrize('status,code', [('failed', 254), ('failed', 2), ('timeout', 254)])
def test_non_cancellation_ansible_failure_stays_failed(repo, host_data,
                                                       monkeypatch, status, code):
    store, config, hosts, job = setup_execution(repo, host_data, monkeypatch)
    monkeypatch.setitem(sys.modules, 'ansible_runner', SimpleNamespace(
        run=lambda **kwargs: SimpleNamespace(status=status, rc=code)))
    run_job(job['id'], config)
    updated = store.job(job['id'])
    assert updated['state'] == 'failed'
    assert updated['targets'][0]['state'] == 'failed'
    assert any(e['task'] == 'Execution failed' for e in store.events(job['id']))


def test_canceled_second_target_preserves_first_completed(repo, host_data, monkeypatch):
    store, config, hosts, job = setup_execution(repo, host_data, monkeypatch, count=2)
    call_count = 0

    def fake_runner(**kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # The first target ran and was verified before the interruption.
            from opm.inventory import inventory_alias
            name = inventory_alias(hosts[0])
            kw['event_handler']({'event': 'runner_on_ok', 'event_data': {
                'host': name, 'task': 'Upgrade installed packages without automatic removals',
                'res': {'changed': True}}})
            kw['event_handler']({'event': 'playbook_on_stats', 'event_data': {
                'ok': {name: 1}}})
            return SimpleNamespace(status='successful', rc=0)
        return SimpleNamespace(status='canceled', rc=254)

    monkeypatch.setitem(sys.modules, 'ansible_runner', SimpleNamespace(run=fake_runner))
    run_job(job['id'], config)
    updated = store.job(job['id'])
    assert call_count == 2
    assert updated['state'] == 'interrupted'
    targets = {t['host_id']: t for t in updated['targets']}
    assert targets[hosts[0]['id']]['state'] == 'completed'
    assert targets[hosts[1]['id']]['state'] == 'interrupted'
    assert not any(e['task'] == 'Execution failed' for e in store.events(job['id']))
