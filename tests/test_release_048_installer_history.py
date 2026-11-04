"""Regression coverage for the v0.4.8 installer and history controls.

No test executes a real patch, modifies remote SSH accounts, or installs a
systemd unit. This suite is deliberately safe to run in the build container.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opm.controller import Controller

PROJECT = Path(__file__).resolve().parents[1]


def _finished_patch(repo, host_data, *, state='failed'):
    store, _ = repo
    host = store.add_host(host_data)
    store.verified(host['id'])
    scan_id = store.begin_scan(host['id'])
    store.finish_scan(scan_id, {'os': {
        'family': 'debian', 'metadata': 'fresh', 'package_query_ok': True,
        'packages': [{'name':'curl','current':'1','candidate':'2'}]}})
    plan = store.create_plan([host['id']])
    store.approve_plan(plan['id'], 'test-operator', False)
    job = store.create_job(plan['id'], 'history-test')
    store.event(job['id'], host['id'], 'error', 'Preflight', 'TEST ERROR')
    if state != 'running':
        store.job_state(job['id'], state)
    return store, host, plan, job


def test_cannot_clear_running_patch_job(repo, host_data):
    store, host, plan, job = _finished_patch(repo, host_data, state='running')
    with pytest.raises(ValueError, match='active'):
        store.clear_patch_history()
    assert store.job(job['id'])['state'] == 'queued'
    assert store.events(job['id'])
    assert store.plan(plan['id'])


def test_clear_history_preserves_inventory_and_scans(repo, host_data):
    store, host, plan, job = _finished_patch(repo, host_data)
    result = store.clear_patch_history()
    assert result == {'deleted': 1, 'job_ids':[job['id']]}
    assert store.jobs() == []
    with pytest.raises(ValueError, match='Unknown plan'):
        store.plan(plan['id'])
    assert store.host(host['id'])['name'] == host['name']
    assert store.last_scan(host['id'])['state'] == 'completed'
    assert store.clear_patch_history()['deleted'] == 0


def test_dismiss_alerts_does_not_change_or_delete_errors(repo, host_data):
    store, host, plan, job = _finished_patch(repo, host_data)
    act = store.begin_activity('ssh_test', 'Test SSH', ['host:' + host['id']])
    store.activity_finish(act['id'], 'failed', 'Connection refused')
    result = store.acknowledge_past_alerts()
    assert result == {'job_alerts':1, 'activity_alerts':1}
    assert store.job(job['id'])['acknowledged'] == 1
    assert store.activity(act['id'])['acknowledged'] == 1
    assert store.job(job['id'])['state'] == 'failed'
    assert store.events(job['id'])[0]['message'] == 'TEST ERROR'
    assert store.acknowledge_past_alerts() == {'job_alerts':0,'activity_alerts':0}


@pytest.mark.asyncio
async def test_controller_attention_field_respects_acknowledgement(repo, host_data):
    store, host, plan, job = _finished_patch(repo, host_data)
    controller = Controller(repo[1])
    feed = await controller.dispatch('activities.list', {})
    assert next(item for item in feed if item['id'] == job['id'])['attention']
    store.acknowledge_past_alerts()
    feed = await controller.dispatch('activities.list', {})
    assert not next(item for item in feed if item['id'] == job['id'])['attention']


def test_patch_accordion_closed_and_css_driven():
    template = (PROJECT/'src/opm/web/templates/web/patch.html').read_text()
    css = (PROJECT/'src/opm/web/static/web/styles.css').read_text()
    assert '{% if forloop.first %}open{% endif %}' not in template
    assert 'class="accordion-chevron" aria-hidden="true"' in template
    assert '.host-accordion[open] .accordion-chevron' in css


def test_runner_does_not_pass_unsupported_runner_mode():
    source = (PROJECT/'src/opm/worker.py').read_text()
    assert 'runner_mode="pexpect"' not in source
    assert 'suppress_env_files=True' in source


def test_installer_menu_and_persistent_command():
    script = (PROJECT/'OPMMaintenance.sh').read_text()
    main = (PROJECT/'OPMInstall.sh').read_text()
    for entry in ('Install / Upgrade','Repair Installation','System Status',
                  'Uninstall','Help','Exit'):
        assert entry in script
    assert 'opm-installer' in main
    assert 'run_logged install -o root -g root -m 0755' in main
    assert 'no_active_jobs' in script
    assert 'PRAGMA quick_check' in script
    assert 'confirm' in script
    assert "rm -rf -- \"$APP_HOME/.local/share/opm\"" in script
    for file in ('OPMMaintenance.sh', 'OPMInstall.sh', 'OPMInstaller.sh'):
        check = subprocess.run(['bash','-n', str(PROJECT/file)], capture_output=True, text=True)
        assert check.returncode == 0, check.stderr
    help_out = subprocess.run(['bash', str(PROJECT/'OPMMaintenance.sh'), '--help'],
                              capture_output=True, text=True)
    assert help_out.returncode == 0
    assert 'sudo opm-installer' in help_out.stdout
