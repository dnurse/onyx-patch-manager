"""v0.6.13: detached patch worker recovery and legible per-target states.

Only temporary SQLite and mocked systemctl; never runs APT, Ansible,
SSH, changes packages, or starts a real systemd service.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from opm.controller import Controller
from opm.store import Store
from test_repository import ready_host

PROJECT = Path(__file__).resolve().parents[1]


def patch_job(repo, host_data, count=1):
    store, config = repo
    hosts = []
    for idx in range(count):
        hosts.append(ready_host(store, {**host_data, 'name': f'Host {idx + 1}',
                                        'address': f'192.0.2.{100+idx}'}))
    plan = store.create_plan([host['id'] for host in hosts])
    store.approve_plan(plan['id'], 'GM tester', False)
    job = store.create_job(plan['id'], 'test-reconcile')
    return store, config, hosts, job


def test_atomic_interruption_reconciles_active_and_untouched_targets(repo, host_data):
    store, _, hosts, job = patch_job(repo, host_data, count=4)
    first, second, third, fourth = [host['id'] for host in hosts]
    store.job_state(job['id'], 'running')
    store.target_state(job['id'], first, 'completed')
    store.target_state(job['id'], second, 'verifying')
    store.event(job['id'], second, 'info', 'Package verification', 'Checking installed versions')
    store.target_state(job['id'], third, 'running')
    store.event(job['id'], third, 'info', 'Preflight', 'Checking SSH, sudo and APT')
    assert store.interrupt_job(job['id'], 'Patch worker no longer active',
                               'systemd result: signal, exit status: 15')
    recovered = store.job(job['id'])
    states = {x['host_id']: x for x in recovered['targets']}
    assert recovered['state'] == 'interrupted' and recovered['finished'] is not None
    assert 'no longer active' in recovered['error']
    assert states[first]['state'] == 'completed'
    assert states[second]['state'] == 'interrupted'
    assert 'Package verification' in states[second]['error']
    assert states[third]['state'] == 'interrupted'
    assert 'Preflight' in states[third]['error']
    assert states[fourth]['state'] == 'not_started'
    assert 'before this target began' in states[fourth]['error']
    assert all(x['state'] not in ('queued', 'running', 'verifying') for x in states.values())
    assert all(x['task'] == 'Recovery' for x in store.events(job['id'])[-4:])
    assert any('systemd result: signal' in x['message'] for x in store.events(job['id']))
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM host_locks WHERE job_id=?',
                          (job['id'],)).fetchone()[0] == 0


def test_interruption_is_idempotent_and_preserves_evidence(repo, host_data):
    store, _, hosts, job = patch_job(repo, host_data)
    store.job_state(job['id'], 'running')
    store.target_state(job['id'], hosts[0]['id'], 'running')
    assert store.interrupt_job(job['id'], 'Worker stopped')
    events = store.events(job['id'])
    snapshot = store.job(job['id'])
    assert store.interrupt_job(job['id'], 'Second attempt') is False
    assert store.events(job['id']) == events
    assert store.job(job['id']) == snapshot


def test_worker_crash_path_also_reconciles_target(repo, host_data):
    store, _, hosts, job = patch_job(repo, host_data)
    store.job_state(job['id'], 'running')
    store.target_state(job['id'], hosts[0]['id'], 'verifying')
    store.event(job['id'], hosts[0]['id'], 'info', 'Post-patch verification', 'Checking packages')
    store.job_state(job['id'], 'interrupted', 'Worker crashed')
    target = store.job(job['id'])['targets'][0]
    assert target['state'] == 'interrupted'
    assert 'Post-patch verification' in target['error']


def test_terminal_targets_never_overwritten(repo, host_data):
    store, _, hosts, job = patch_job(repo, host_data, 3)
    store.job_state(job['id'], 'running')
    store.target_state(job['id'], hosts[0]['id'], 'failed', 'Original error')
    store.target_state(job['id'], hosts[1]['id'], 'completed_with_warnings',
                       'Original verification note', reboot=True)
    store.target_state(job['id'], hosts[2]['id'], 'cancelled')
    store.interrupt_job(job['id'], 'Stopped worker')
    recovered = {x['host_id']: x for x in store.job(job['id'])['targets']}
    assert recovered[hosts[0]['id']]['error'] == 'Original error'
    assert recovered[hosts[0]['id']]['state'] == 'failed'
    assert recovered[hosts[1]['id']]['state'] == 'completed_with_warnings'
    assert recovered[hosts[1]['id']]['reboot_required'] == 1
    assert recovered[hosts[1]['id']]['error'] == 'Original verification note'
    assert recovered[hosts[2]['id']]['state'] == 'cancelled'


def test_completed_job_cannot_be_interrupted_retroactively(repo, host_data):
    store, _, hosts, job = patch_job(repo, host_data)
    store.target_state(job['id'], hosts[0]['id'], 'completed')
    store.job_state(job['id'], 'completed')
    assert not store.interrupt_job(job['id'], 'Late systemd event')
    assert store.job(job['id'])['state'] == 'completed'
    assert not store.events(job['id'])


def test_controller_reconciles_stopped_worker_with_exit_status(repo, host_data, monkeypatch):
    store, config, hosts, job = patch_job(repo, host_data)
    store.job_state(job['id'], 'running')
    store.target_state(job['id'], hosts[0]['id'], 'running')
    store.event(job['id'], hosts[0]['id'], 'info', 'Preflight', 'Preparing APT')
    calls = []
    def systemctl(cmd, **kwargs):
        calls.append(cmd)
        if 'is-active' in cmd:
            return SimpleNamespace(returncode=3, stdout='inactive\n', stderr='')
        assert 'show' in cmd
        return SimpleNamespace(returncode=0, stdout='Result=signal\nExecMainStatus=15\nExecMainCode=2\n', stderr='')
    monkeypatch.setattr('opm.controller.subprocess.run', systemctl)
    asyncio.run(Controller(config).reconcile_jobs())
    assert any('is-active' in cmd for cmd in calls)
    assert any('show' in cmd for cmd in calls)
    assert store.job(job['id'])['state'] == 'interrupted'
    assert store.job(job['id'])['targets'][0]['state'] == 'interrupted'
    assert 'exit status: 15' in store.events(job['id'])[-1]['message']


@pytest.mark.parametrize('result', [
    SimpleNamespace(returncode=1, stdout='', stderr='Failed to connect to bus'),
    SimpleNamespace(returncode=3, stdout='mystery\n', stderr=''),
    SimpleNamespace(returncode=0, stdout='active\n', stderr=''),
    SimpleNamespace(returncode=3, stdout='deactivating\n', stderr=''),
])
def test_controller_refuses_to_reconcile_uncertain_or_active_units(repo, host_data, monkeypatch, result):
    store, config, hosts, job = patch_job(repo, host_data)
    store.job_state(job['id'], 'running')
    store.target_state(job['id'], hosts[0]['id'], 'running')
    monkeypatch.setattr('opm.controller.subprocess.run', lambda *a, **k: result)
    asyncio.run(Controller(config).reconcile_jobs())
    assert store.job(job['id'])['state'] == 'running'
    assert store.job(job['id'])['targets'][0]['state'] == 'running'


def test_controller_skips_new_queued_worker_until_launch_grace(repo, host_data, monkeypatch):
    store, config, _, job = patch_job(repo, host_data)
    def should_not_call(*args, **kwargs):
        raise AssertionError('systemctl queried before unit launch grace')
    monkeypatch.setattr('opm.controller.subprocess.run', should_not_call)
    asyncio.run(Controller(config).reconcile_jobs())
    assert store.job(job['id'])['state'] == 'queued'


def test_startup_reconciles_old_queued_job(repo, host_data, monkeypatch):
    store, config, _, job = patch_job(repo, host_data)
    monkeypatch.setattr('opm.controller.subprocess.run', lambda *a, **k:
                        SimpleNamespace(returncode=3, stdout='inactive\n', stderr=''))
    asyncio.run(Controller(config).reconcile_jobs(startup=True))
    assert store.job(job['id'])['state'] == 'interrupted'
    assert store.job(job['id'])['targets'][0]['state'] == 'not_started'


def test_reconciliation_no_replay_or_package_commands(repo, host_data, monkeypatch):
    store, config, hosts, job = patch_job(repo, host_data)
    store.job_state(job['id'], 'running')
    store.target_state(job['id'], hosts[0]['id'], 'running')
    calls = []
    def safe_commands(cmd, **kwargs):
        calls.append(cmd)
        assert cmd[:2] == ['systemctl', '--user']
        assert 'systemd-run' not in cmd
        return SimpleNamespace(returncode=3, stdout='inactive\n', stderr='')
    monkeypatch.setattr('opm.controller.subprocess.run', safe_commands)
    asyncio.run(Controller(config).reconcile_jobs())
    assert len(calls) == 2
    assert store.job(job['id'])['state'] == 'interrupted'
    # Reconciliation changes local SQLite evidence, never remote packages.
    assert 'completed' not in [x['state'] for x in store.job(job['id'])['targets']]


def test_recovery_text_and_human_readable_statuses():
    template = (PROJECT / 'src/opm/web/templates/web/job_detail.html').read_text()
    history = (PROJECT / 'src/opm/web/templates/web/history.html').read_text()
    dashboard = (PROJECT / 'src/opm/web/templates/web/dashboard.html').read_text()
    labels = (PROJECT / 'src/opm/web/templatetags/opm_display.py').read_text()
    assert 'sudo dpkg --audit' in template
    assert 'sudo apt-get check' in template
    assert 'No patch operation was initiated' in (PROJECT / 'src/opm/store.py').read_text()
    assert 't.state|job_state_label' in template
    assert 'item.state|job_state_label' in history
    assert 'a.state|job_state_label' in dashboard
    assert "'completed_with_warnings': 'Completed with warnings'" in labels
    assert 'Automatic retry' not in template
