"""Regression tests for initial enrollment scan and vendor-owned Podman contexts.

No test creates a remote account, changes APT, or invokes a real Podman host.
"""
from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path
from unittest.mock import patch

from opm.controller import Controller
from opm.remote import REMOTE_INSPECT, _merge_container_scopes, inspect_host


def test_authorized_scan_merges_unifi_os_service_user_containers(repo, host_data):
    store, cfg = repo
    host = store.add_host({**host_data, 'sudo_mode': 'password'})
    normal = {'os': {'family': 'debian', 'packages': []}, 'system': {}, 'engines': {
        'docker': {'available': False, 'installed': False, 'error': '', 'containers': [], 'scope': 'OPM SSH user'},
        'podman': {'available': True, 'installed': True, 'error': '', 'containers': [], 'scope': 'OPM SSH user'}}}
    uos = {'name': 'uosserver', 'id': 'abc123', 'state': 'running',
           'scope': 'Podman user uosserver (UID 1031)', 'update_status': 'vendor-managed',
           'managed_by': 'unifi-os-server', 'deployment': 'vendor-managed (UniFi OS Server)'}
    privileged = {'engines': {
        'docker': {'available': False, 'installed': False, 'error': '', 'containers': [], 'scope': 'root'},
        'podman': {'available': True, 'installed': True, 'error': '', 'containers': [], 'scope': 'root'},
        'podman_users': [{'available': True, 'installed': True, 'error': '',
                            'containers': [uos], 'scope': 'Podman user uosserver (UID 1031)'}]}}
    def fake_ssh(_host, _cfg, args, input_text=None, timeout=45):
        if args[-1] == '--privileged-engines':
            assert input_text.startswith('TEST-PRIVATE-SUDO\n')
            return json.dumps(privileged)
        if args[-1] == '--read-only':
            return json.dumps(normal)
        return 'Reading package lists...'
    with patch('opm.remote.ssh_command', side_effect=fake_ssh) as executor:
        result = inspect_host(host, cfg, 'TEST-PRIVATE-SUDO')
    containers = result['engines']['podman']['containers']
    assert len(containers) == 1
    assert containers[0]['name'] == 'uosserver'
    assert containers[0]['scope'] == 'Podman user uosserver (UID 1031)'
    assert containers[0]['managed_by'] == 'unifi-os-server'
    assert len(executor.call_args_list) == 3


def test_unauthorized_scan_does_not_enumerate_other_podman_users(repo, host_data):
    store, cfg = repo
    host = store.add_host({**host_data, 'sudo_mode': 'password'})
    normal = {'os': {'family': 'debian', 'packages': []}, 'system': {}, 'engines': {
        'docker': {'available': False, 'installed': False, 'containers': []},
        'podman': {'available': True, 'installed': True, 'containers': [], 'scope': 'OPM SSH user'}}}
    with patch('opm.remote.ssh_command', return_value=json.dumps(normal)) as executor:
        result = inspect_host(host, cfg)
    assert executor.call_count == 1
    assert 'authorized sudo' in result['engines']['podman']['scope_warning']


def test_failure_to_inspect_service_context_is_not_reported_as_empty_all_clear():
    engines = {'podman': {'available': True, 'installed': True,
                          'scope': 'OPM SSH user', 'containers': []}}
    _merge_container_scopes(engines, 'podman', [
        {'available': True, 'scope': 'root', 'containers': []},
        {'available': False, 'scope': 'Podman user uosserver (UID 1031)',
         'error': 'runtime directory is unavailable', 'containers': []},
    ])
    assert engines['podman']['containers'] == []
    assert 'runtime directory' in engines['podman']['scope_warning']


def test_dynamic_discovery_has_no_hardcoded_account_and_no_lifecycle_changes():
    assert "getpwnam('uosserver')" not in REMOTE_INSPECT
    assert "pwd.getpwall()" in REMOTE_INSPECT
    assert "runuser', '-u', username" in REMOTE_INSPECT
    assert "'podman_users'" in REMOTE_INSPECT
    assert not any(term in REMOTE_INSPECT for term in (
        "'podman', 'pull'", "'podman', 'restart'", "'podman', 'rm'",
        "'loginctl', 'enable-linger'"))


def test_successful_enrollment_schedules_authenticated_initial_scan(repo, host_data, monkeypatch):
    store, config = repo
    captured = {}
    from opm import controller as module

    def fake_enroll(self, params):
        host = store.add_host({**host_data, 'name': 'uos-lxc', 'sudo_mode': 'password'})
        store.verified(host['id'], True)
        return {'host_id': host['id'], 'verified': True}

    def fake_scan(host, cfg, password):
        captured['sudo_password'] = password
        return {'os': {'family': 'debian', 'metadata': 'fresh', 'packages': []},
                'engines': {'podman': {'available': True, 'containers': []}}}

    monkeypatch.setattr(Controller, 'enroll_host', fake_enroll)
    monkeypatch.setattr(module, 'inspect_host', fake_scan)

    async def exercise():
        ctl = Controller(config)
        enrollment = ctl.store.begin_activity('enrollment', 'Enroll', ['candidate:192.0.2.24:22'])
        params = {'opm_password': 'TEMP-SUDO-SECRET', 'login_password': 'TEMP-SSH-SECRET'}
        await ctl.enroll_background(enrollment['id'], params)
        assert store.activity(enrollment['id'])['state'] == 'completed'
        await asyncio.gather(*tuple(ctl._scans))
        scan_records = [a for a in store.recent_activities() if a['kind'] == 'scan']
        assert len(scan_records) == 1
        assert scan_records[0]['state'] == 'completed'
        assert store.last_scan(store.hosts()[0]['id'])['state'] == 'completed'
        assert captured['sudo_password'] == 'TEMP-SUDO-SECRET'
        assert 'opm_password' not in params
        assert b'TEMP-SUDO-SECRET' not in config.database.read_bytes()
        assert b'TEMP-SSH-SECRET' not in config.database.read_bytes()
    asyncio.run(exercise())


def test_partial_enrollment_does_not_schedule_scan(repo, monkeypatch):
    store, config = repo
    def partial(self, params):
        return {'host_id': None, 'verified': False, 'warning': 'Sudo failed'}
    monkeypatch.setattr(Controller, 'enroll_host', partial)

    async def exercise():
        ctl = Controller(config)
        enrollment = ctl.store.begin_activity('enrollment', 'Enroll', ['candidate:192.0.2.24:22'])
        await ctl.enroll_background(enrollment['id'], {'opm_password': 'SECRET-SUDO-TEST'})
        assert store.activity(enrollment['id'])['state'] == 'partial'
        assert not any(a['kind'] == 'scan' for a in store.recent_activities())
    asyncio.run(exercise())


def test_rootless_probe_constructs_readonly_contexts_for_dynamic_users():
    """A different account name must work without a code edit."""
    import types
    parsed = ast.parse(REMOTE_INSPECT)
    fn = next(node for node in parsed.body if isinstance(node, ast.FunctionDef)
              and node.name == 'all_rootless_podman')
    program = compile(ast.Module(body=[fn], type_ignores=[]), '<rootless>', 'exec')
    calls = []
    def stub_containers(engine, prefix, scope, **kwargs):
        calls.append((engine, prefix, scope, kwargs))
        return {'available': True, 'containers': [], 'scope': scope}
    namespace = {
        'shutil': types.SimpleNamespace(which=lambda cmd: '/usr/bin/' + cmd),
        'os': types.SimpleNamespace(geteuid=lambda: 0, path=types.SimpleNamespace(isdir=lambda path: True),
                                    stat=lambda path: types.SimpleNamespace(st_uid=1500)),
        'time': types.SimpleNamespace(monotonic=lambda: 100),
        'discover_podman_accounts': lambda: [types.SimpleNamespace(
            pw_name='my-podman-app', pw_dir='/srv/my-podman-app', pw_uid=1500)],
        'containers': stub_containers}
    exec(program, namespace)
    sources, warning = namespace['all_rootless_podman']()
    assert warning == '' and len(sources) == 1
    engine, prefix, scope, kwargs = calls[0]
    assert engine == 'podman' and 'my-podman-app' in scope
    assert prefix[:7] == ['runuser', '-u', 'my-podman-app', '--', 'env', '-i',
                          'PATH=/usr/local/bin:/usr/bin:/bin']
    assert 'HOME=/srv/my-podman-app' in prefix
    assert 'XDG_RUNTIME_DIR=/run/user/1500' in prefix
    assert kwargs['registry_checks'] is False


def test_vendor_owned_container_inspection_skips_registry_lookups():
    import types
    parsed = ast.parse(REMOTE_INSPECT)
    fn = next(node for node in parsed.body if isinstance(node, ast.FunctionDef)
              and node.name == 'containers')
    program = compile(ast.Module(body=[fn], type_ignores=[]), '<fixed-podman>', 'exec')
    calls = []
    def fake_run(command, timeout=22):
        calls.append(command)
        if 'ps' in command:
            return 0, '1234\n', ''
        return 0, json.dumps([{'Id': '1234567890', 'Name': '/uosserver',
                                'Image': 'sha256:111', 'ImageName': 'docker.io/library/uosserver:5.0',
                                'Config': {'Image': 'docker.io/library/uosserver:5.0', 'Labels': {}},
                                'State': {'Status': 'running', 'Health': {'Status': 'healthy'}}}]), ''
    def forbidden_image_inspection(*args):
        raise AssertionError('Vendor-managed container must not query a registry')
    namespace = {'shutil': types.SimpleNamespace(which=lambda cmd: '/usr/bin/podman'),
                 'run': fake_run, 'json': json, 'image_status': forbidden_image_inspection}
    exec(program, namespace)
    result = namespace['containers']('podman',
                  ['runuser', '-u', 'uosserver', '--'], 'Podman user uosserver (UID 1031)', registry_checks=False)
    assert result['containers'][0]['name'] == 'uosserver'
    assert result['containers'][0]['health'] == 'healthy'
    assert result['containers'][0]['update_status'] == 'vendor-managed'
    assert all('pull' not in call and 'restart' not in call for call in calls)
