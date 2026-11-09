"""v0.6.12 partial-repository tests: mocked APT and mocked Ansible only.

No real package-index refresh, installation, SSH or sudo operation is run.
"""
import contextlib
import io
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from opm.partial_apt import (
    APT_SECURITY, PartialAptPlan, REMOTE_APT_HELPER,
    cleanup_verified_subset, partial_upgrade_argv, plan_verified_subset,
    installed_versions,
)
from opm.remote import RemoteError
from opm.worker import _fresh_preflight, _partial_playbook, run_job


def pkg(name, version='2'):
    return {'name': name, 'current': '1', 'candidate': version}


def remote_definitions():
    """Execute only helper definitions, not its root-guarded entrypoint."""
    declarations = REMOTE_APT_HELPER.split('if os.geteuid() != 0:\n')[0]
    ns = {'__name__': 'opm_partial_test'}
    exec(compile(declarations, '<remote-apt-verifier>', 'exec'), ns)
    return ns


def test_remote_helper_accepts_only_new_signed_indexes(tmp_path, monkeypatch):
    """Simulate one broken Cloudflare repo and one successfully signed Debian repo."""
    ns = remote_definitions()
    work = tmp_path / 'opm-apt-aBc12345'
    work.mkdir()
    monkeypatch.setattr(ns['tempfile'], 'mkdtemp', lambda **kw: str(work))
    ns['reject_untrusted_sources'] = lambda: True
    monkeypatch.setattr(ns['os'], 'chown', lambda *args: None)
    commands = []

    def fake_run(command, timeout=180):
        commands.append(command)
        if command[0:2] == ['apt-get', '-o'] and 'update' in command:
            lists = work / 'lists'
            (lists / 'deb.debian.org_debian_dists_trixie_InRelease').write_text('SIGNED')
            (lists / 'deb.debian.org_debian_dists_trixie_main_binary-amd64_Packages.lz4').write_text('PACKAGES')
            return 100, '', 'Warning: Failed to fetch https://pkg.cloudflare.com/cloudflared/dists/any/InRelease'
        if command[0] == 'apt':
            return 0, 'Listing...\ncurl/stable 2 amd64 [upgradable from: 1]\n', ''
        if command[0] == 'apt-get' and '-s' in command:
            return 0, 'Inst curl [1] (2 Debian:13 [amd64])\nConf curl (2 Debian:13 [amd64])', ''
        raise AssertionError(f'Unexpected command: {command}')

    ns['run'] = fake_run
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns['prepare']()
    result = json.loads(output.getvalue())
    assert result['ok'] is True and result['partial'] is True
    assert result['directory'] == str(work)
    assert result['upgrades'] == [pkg('curl')]
    assert all('Dir::State::lists=' in ' '.join(cmd) for cmd in commands)
    assert all('AllowUnauthenticated=false' in ' '.join(cmd) for cmd in commands)


def test_remote_helper_rejects_unsigned_or_absent_indexes(tmp_path, monkeypatch):
    ns = remote_definitions()
    work = tmp_path / 'opm-apt-aBc12346'
    work.mkdir()
    monkeypatch.setattr(ns['tempfile'], 'mkdtemp', lambda **kw: str(work))
    ns['reject_untrusted_sources'] = lambda: True
    monkeypatch.setattr(ns['os'], 'chown', lambda *args: None)

    def fake_run(command, timeout=180):
        if 'update' in command:
            (work / 'lists' / 'cloudflare_dists_any_main_binary-amd64_Packages.lz4').write_text('UNSIGNED')
            return 100, '', 'Failed to fetch repository'
        raise AssertionError('No candidate query may follow an unsigned refresh')

    ns['run'] = fake_run
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns['prepare']()
    result = json.loads(output.getvalue())
    assert not result['ok'] and 'signed' in result['error']
    assert not work.exists()  # private indexes cleaned after failed verification


def test_remote_helper_rejects_insecure_source_policy_without_fetch(tmp_path):
    ns = remote_definitions()
    ns['reject_untrusted_sources'] = lambda: False
    ns['run'] = lambda *a, **k: (_ for _ in ()).throw(AssertionError('APT must not execute'))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns['prepare']()
    assert 'insecure' in json.loads(output.getvalue())['error']


def test_partial_plan_excludes_unverified_candidates_and_held_packages(monkeypatch):
    source = [pkg('curl'), pkg('cloudflared'), pkg('openssh-server')]
    response = {'ok': True, 'directory': '/var/tmp/opm-apt-aBc12345',
                'candidates': [pkg('curl'), pkg('openssh-server')],
                'upgrades': [pkg('curl')], 'warning': '1 failing vendor repo'}
    import opm.partial_apt as mod
    monkeypatch.setattr(mod, '_invoke', lambda *a: response)
    plan = plan_verified_subset({}, None, None, source)
    assert [x['name'] for x in plan.to_upgrade] == ['curl']
    assert [x['name'] for x in plan.excluded] == ['cloudflared']
    assert [x['name'] for x in plan.held] == ['openssh-server']
    command = partial_upgrade_argv(plan)
    assert command[0] == 'apt-get' and '--no-remove' in command
    assert 'Dir::State::lists=' + plan.directory + '/lists' in command
    assert '--allow-unauthenticated' not in command
    assert 'Acquire::AllowInsecureRepositories=false' in command
    assert 'APT::Get::AllowUnauthenticated=false' in command


@pytest.mark.parametrize('deviation', ['changed_version', 'changed_current', 'new_package', 'unverified_transaction', 'no_safe_packages', 'invalid_sandbox', 'duplicate_name'])
def test_rejects_unapproved_or_unverified_transaction(monkeypatch, deviation):
    import opm.partial_apt as mod
    payload = {'ok': True, 'directory': '/var/tmp/opm-apt-aBc12345',
               'candidates': [pkg('curl')], 'upgrades': [pkg('curl')]}
    if deviation == 'changed_version':
        payload['candidates'] = [pkg('curl', '3')]
    if deviation == 'changed_current':
        payload['candidates'] = [{'name': 'curl', 'current': '0', 'candidate': '2'}]
    if deviation == 'new_package':
        payload['candidates'].append(pkg('rogue'))
    if deviation == 'unverified_transaction':
        payload['upgrades'] = [pkg('rogue')]
    if deviation == 'no_safe_packages':
        payload['upgrades'] = []
    if deviation == 'invalid_sandbox':
        payload['directory'] = '/etc'
    if deviation == 'duplicate_name':
        payload['candidates'].append(pkg('curl'))
    calls = []
    monkeypatch.setattr(mod, '_invoke', lambda *a: payload)
    monkeypatch.setattr(mod, 'cleanup_verified_subset', lambda *a: calls.append(a))
    with pytest.raises(RemoteError):
        plan_verified_subset({}, None, None, [pkg('curl')])
    assert len(calls) == 1


def test_password_not_in_privileged_argv_and_cleanup_whitelist(monkeypatch):
    import opm.partial_apt as mod
    traces = []
    def fake_ssh(host, config, args, input_text=None, timeout=30):
        traces.append((args, input_text))
        return json.dumps({'ok': True})
    monkeypatch.setattr(mod, 'ssh_command', fake_ssh)
    host = {'sudo_mode': 'password'}
    mod._invoke(host, None, 'MY-PRIVATE-PASSWORD', 'cleanup', '/var/tmp/opm-apt-aBc12345')
    assert all('MY-PRIVATE-PASSWORD' not in ' '.join(args) for args, _ in traces)
    assert 'MY-PRIVATE-PASSWORD\n' in traces[0][1]
    with pytest.raises(ValueError):
        mod._invoke(host, None, 'SECRET', 'cleanup', '/etc')


def test_partial_playbook_contains_only_fixed_argv(tmp_path):
    plan = PartialAptPlan('/var/tmp/opm-apt-aBc12345', (pkg('curl'),), (pkg('vendor'),), (), 'warning')
    path = tmp_path / 'approved.json'
    _partial_playbook(path, plan)
    tasks = json.loads(path.read_text())[0]['tasks']
    assert 'ansible.builtin.command' in tasks[0]
    assert tasks[0]['ansible.builtin.command']['argv'] == partial_upgrade_argv(plan)
    assert 'ansible.builtin.shell' not in path.read_text()
    assert 'update' not in tasks[0]['ansible.builtin.command']['argv']


def test_preflight_falls_back_only_when_normal_refresh_failed(repo, host_data, monkeypatch):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'passwordless'})
    store.verified(host['id'])
    target = {'packages': [pkg('curl')], 'sudo_mode': 'passwordless', 'address': host['address'],
              'port': host['ssh_port'], 'user': host['ssh_user'], 'key_path': host['key_path']}
    import opm.worker as mod
    monkeypatch.setattr(mod, 'test_connection', lambda *a: {'sudo': 'passwordless'})
    commands = []
    def bad_refresh(*a, **kw):
        commands.append(a[2])
        raise RemoteError('Package indexes were not refreshed: signature failed')
    monkeypatch.setattr(mod, 'ssh_command', bad_refresh)
    example = PartialAptPlan('/var/tmp/opm-apt-aBc12345', (pkg('curl'),), (), (), 'repository warning')
    monkeypatch.setattr(mod, 'plan_verified_subset', lambda *a: example)
    assert _fresh_preflight(store.host(host['id']), target, config, None) == example
    assert len(commands) == 1


def test_store_requires_checkbox_for_cached_metadata_and_keeps_dry_run(repo, host_data):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'passwordless'})
    store.verified(host['id'])
    scan = store.begin_scan(host['id'])
    store.finish_scan(scan, {'os': {'family': 'debian', 'package_query_ok': True,
        'metadata': 'cached', 'apt_error': 'Cloudflare signing key rejected',
        'packages': [pkg('curl')]}})
    plan = store.create_plan([host['id']])
    assert plan['snapshot']['targets'][0]['scan_warning'] == 'Cloudflare signing key rejected'
    with pytest.raises(ValueError, match='Acknowledge'):
        store.approve_plan(plan['id'], 'operator', False)
    assert store.approve_plan(plan['id'], 'operator', False, repo_warning_ack=True)['approved_at']
    dry = store.create_plan([host['id']], mode='dry_run')
    assert store.approve_plan(dry['id'], 'operator', False)['approved_at']


def test_installed_versions_does_not_put_shell_expansion_in_command(monkeypatch):
    import opm.partial_apt as mod
    calls = []
    def fake_ssh(host, config, command, timeout):
        calls.append(command)
        return 'curl\t2\n'
    monkeypatch.setattr(mod, 'ssh_command', fake_ssh)
    assert installed_versions({}, None, [pkg('curl')]) == {'curl': '2'}
    assert "-f='${binary:Package}\\t${Version}\\n'" in calls[0]
    assert not any('sh -c' in arg for arg in calls[0])


def test_complete_with_warnings_persists_and_releases_lock(repo, host_data, monkeypatch):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'passwordless'})
    store.verified(host['id'])
    scan = store.begin_scan(host['id'])
    store.finish_scan(scan, {'os': {'family': 'debian', 'metadata': 'cached',
                      'package_query_ok': True, 'packages': [pkg('curl'), pkg('cloudflared')],
                      'apt_error': 'cloudflare signing key'}})
    plan = store.create_plan([host['id']])
    store.approve_plan(plan['id'], 'operator', False, repo_warning_ack=True)
    job = store.create_job(plan['id'], 'partial-case')
    import opm.worker as w
    fake_plan = PartialAptPlan('/var/tmp/opm-apt-aBc12345', (pkg('curl'),),
                               (pkg('cloudflared'),), (), 'cloudflare signing failure')
    monkeypatch.setattr(w, '_fresh_preflight', lambda *args: fake_plan)
    monkeypatch.setattr(w, 'check_ansible_runtime', lambda: ('/mock/bin/ansible-playbook', '/mock/bin:/usr/bin'))
    monkeypatch.setattr(w, '_validated_inventory_file', lambda *args: config.jobs / 'hosts.json')
    monkeypatch.setattr(w, 'installed_versions', lambda *args: {'curl': '2', 'cloudflared': '1'})
    monkeypatch.setattr(w, 'inspect_os', lambda *args: {'package_query_ok': True, 'packages': [], 'reboot_required': False})
    monkeypatch.setattr(w, '_refresh_host_after_patch', lambda *args: True)
    cleanup = []
    monkeypatch.setattr(w, 'cleanup_verified_subset', lambda *args: cleanup.append(args))
    runner_calls = []
    from opm.inventory import inventory_alias
    alias = inventory_alias(host)
    def fake_run(**kwargs):
        runner_calls.append(kwargs)
        playbook = json.loads(Path(kwargs['playbook']).read_text())
        assert 'ansible.builtin.command' in playbook[0]['tasks'][0]
        handler = kwargs['event_handler']
        handler({'event': 'runner_on_ok', 'event_data': {'host': alias, 'task':
                 'Upgrade verified packages from isolated APT indexes', 'res': {'changed': True}}})
        handler({'event': 'playbook_on_stats', 'event_data': {'ok': {alias: 1}}})
        return types.SimpleNamespace(rc=0, status='successful')
    monkeypatch.setitem(sys.modules, 'ansible_runner', types.SimpleNamespace(run=fake_run))
    run_job(job['id'], config)
    assert store.job(job['id'])['state'] == 'completed_with_warnings'
    assert store.job(job['id'])['targets'][0]['state'] == 'completed_with_warnings'
    assert 'unavailable from verified indexes' in store.job(job['id'])['error']
    assert any('cloudflared' in event['message'] for event in store.events(job['id']))
    assert cleanup and runner_calls
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM host_locks WHERE host_id=?', (host['id'],)).fetchone()[0] == 0


def test_old_plan_table_migrates_without_deleting_jobs(tmp_path):
    import sqlite3
    from opm.store import Store
    db_path = tmp_path / 'state.db'
    Store(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute('ALTER TABLE plans DROP COLUMN repo_warning_ack')
    Store(db_path)
    with sqlite3.connect(db_path) as db:
        cols = {row[1] for row in db.execute('PRAGMA table_info(plans)')}
    assert 'repo_warning_ack' in cols


def test_missing_partial_approval_rejected_by_worker_before_any_ssh(repo, host_data, monkeypatch):
    import opm.worker as worker
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'passwordless'})
    store.verified(host['id'])
    scan = store.begin_scan(host['id'])
    store.finish_scan(scan, {'os': {'family': 'debian', 'metadata': 'cached',
                                  'package_query_ok': True, 'packages': [pkg('curl')]}})
    plan = store.create_plan([host['id']])
    # Simulate a historical approval made before the new checkbox existed.
    with store.connection() as conn:
        conn.execute('UPDATE plans SET approved_at=?,approved_by=? WHERE id=?',
                     (1234567890.0, 'legacy', plan['id']))
    job = store.create_job(plan['id'], 'legacy-approval')
    monkeypatch.setattr(worker, 'ssh_command', lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError('SSH must not be reached')))
    run_job(job['id'], config)
    assert store.job(job['id'])['state'] == 'failed'
    assert 'acknowledgement' in store.job(job['id'])['error']


def test_signature_validation_requires_matching_release_for_each_index(tmp_path):
    ns = remote_definitions()
    directory = tmp_path / 'lists'
    directory.mkdir()
    (directory / 'good_dists_trixie_InRelease').write_bytes(b'SIGNED')
    (directory / 'good_dists_trixie_main_binary-amd64_Packages.lz4').write_bytes(b'INDEX')
    assert ns['verified_lists'](str(directory))
    (directory / 'unsigned_dists_any_main_binary-amd64_Packages.lz4').write_bytes(b'UNSIGNED')
    assert not ns['verified_lists'](str(directory))
