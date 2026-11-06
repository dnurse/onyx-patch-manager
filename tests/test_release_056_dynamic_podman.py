"""Security and regression cases for multi-account rootless Podman discovery.

Tests execute the embedded remote functions in a controlled stub namespace;
none contact SSH or alter containers, Linux accounts, or login sessions.
"""
import ast
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from opm.remote import REMOTE_INSPECT, _merge_container_scopes, inspect_host


def remote_functions(*names, namespace=None):
    tree = ast.parse(REMOTE_INSPECT)
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    env = namespace or {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<remote>', 'exec'), env)
    return env


def fake_user(name, uid, home):
    return SimpleNamespace(pw_name=name, pw_uid=uid, pw_dir=home)


def test_dynamic_discovery_includes_system_accounts_and_custom_configuration():
    accounts = [fake_user('uosserver', 799, '/home/uosserver'),
                fake_user('media', 1450, '/home/media'),
                fake_user('ordinary', 1300, '/home/ordinary')]
    existing = {'/home/uosserver/.config/containers/storage.conf',
                '/home/media/.local/share/containers/storage'}
    env = remote_functions('discover_podman_accounts', namespace={
        'pwd': SimpleNamespace(getpwall=lambda: accounts),
        'os': SimpleNamespace(path=SimpleNamespace(exists=lambda path: path in existing,
                                                  join=os.path.join)),
        '_podman_subid_users': lambda: set(), '_podman_process_owners': lambda: set()})
    result = env['discover_podman_accounts']()
    assert {x.pw_name for x in result} == {'uosserver', 'media'}
    assert all(x.pw_name != 'ordinary' for x in result)


def test_dynamic_discovery_detects_subuid_only_user_and_running_process():
    accounts = [fake_user('root', 0, '/root'), fake_user('service-a', 525, '/srv/a'),
                fake_user('service-b', 631, '/srv/b'), fake_user('unused', 700, '/srv/unused')]
    env = remote_functions('discover_podman_accounts', namespace={
        'pwd': SimpleNamespace(getpwall=lambda: accounts),
        'os': SimpleNamespace(path=SimpleNamespace(exists=lambda path: False, join=os.path.join)),
        '_podman_subid_users': lambda: {'service-b'},
        '_podman_process_owners': lambda: {525}})
    result = [x.pw_name for x in env['discover_podman_accounts']()]
    assert result == ['service-a', 'service-b']  # active context first


def test_rootless_unavailable_runtime_is_a_warning_not_a_clean_empty_result():
    engines = {'podman': {'available': True, 'installed': True,
                          'containers': [], 'scope': 'OPM SSH user'}}
    _merge_container_scopes(engines, 'podman', [
        {'available': False, 'installed': True, 'containers': [],
         'scope': 'Podman user apps (UID 1234)', 'error': 'no user session'}])
    assert 'no user session' in engines['podman']['scope_warning']
    assert 'apps' in engines['podman']['inspected_scopes'][1]


def test_partial_inspect_failure_also_surfaces_warning():
    engines = {'podman': {'available': True, 'containers': []}}
    _merge_container_scopes(engines, 'podman', [
        {'available': True, 'containers': [], 'scope': 'Podman user apps',
         'error': 'inspect returned invalid JSON'}])
    assert 'invalid JSON' in engines['podman']['scope_warning']


def test_duplicate_ssh_user_container_is_only_displayed_once():
    c = {'id': 'abcde1234', 'name': 'nginx', 'scope': 'OPM SSH user'}
    engines = {'podman': {'available': True, 'containers': [dict(c)]}}
    _merge_container_scopes(engines, 'podman', [
        {'available': True, 'scope': 'Podman user opm (UID 1000)',
         'containers': [dict(c, scope='Podman user opm (UID 1000)')]}])
    assert len(engines['podman']['containers']) == 1
    assert len(engines['podman']['inspected_scopes']) == 2


def test_vendor_identification_based_on_image_not_account():
    inspected = [
        {'Id': 'a' * 64, 'Name': '/uosserver', 'Image': 'sha256:123',
         'ImageName': 'docker.io/library/uosserver:0.0.56',
         'Config': {'Image': 'sha256:123', 'Labels': {}},
         'State': {'Status': 'running'}},
        {'Id': 'b' * 64, 'Name': '/homepage', 'Image': 'sha256:456',
         'ImageName': 'ghcr.io/example/homepage:1',
         'Config': {'Image': 'ghcr.io/example/homepage:1', 'Labels': {}},
         'State': {'Status': 'running'}},
    ]
    def execute(cmd, timeout=22):
        if 'ps' in cmd:
            return 0, 'a' * 64 + '\n' + 'b' * 64 + '\n', ''
        return 0, json.dumps(inspected), ''
    env = remote_functions('containers', namespace={
        'shutil': SimpleNamespace(which=lambda cmd: '/usr/bin/' + cmd),
        'run': execute, 'json': json,
        'image_status': lambda *args: (_ for _ in ()).throw(
            AssertionError('Cross-user registry inspection is disabled'))})
    result = env['containers']('podman', ['runuser', '-u', 'different-user', '--'],
                               'Podman user different-user (UID 520)', registry_checks=False)
    vendor, app = result['containers']
    assert vendor['managed_by'] == 'unifi-os-server'
    assert vendor['update_status'] == 'vendor-managed'
    assert vendor['deployment'] == 'vendor-managed (UniFi OS Server)'
    assert app['managed_by'] == ''
    assert app['update_status'] == 'unknown'


def test_privileged_scan_merges_multiple_unrelated_podman_users(repo, host_data):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'password'})
    standard = {'os': {'family': 'debian', 'packages': []}, 'engines': {
        'docker': {'available': False, 'containers': []},
        'podman': {'available': True, 'containers': [], 'scope': 'OPM SSH user'}}}
    def source(name, identity):
        return {'available': True, 'scope': 'Podman user ' + name,
                'containers': [{'id': identity, 'name': name + '-app'}]}
    privileged = {'engines': {'docker': {'available': False, 'containers': []},
                              'podman': {'available': True, 'containers': [], 'scope': 'root'},
                              'podman_users': [source('service-one', '1111'),
                                               source('service-two', '2222')],
                              'podman_discovery_warning': ''}}
    def ssh(_host, _config, command, input_text=None, timeout=45):
        return json.dumps(privileged if command[-1] == '--privileged-engines' else standard) \
            if command[-1] in ('--privileged-engines', '--read-only') else 'OK'
    with patch('opm.remote.ssh_command', side_effect=ssh):
        result = inspect_host(host, config, 'PASSWORD-ONLY-IN-MEMORY')
    assert {c['name'] for c in result['engines']['podman']['containers']} == {
        'service-one-app', 'service-two-app'}


def test_privileged_scan_preserves_budget_warning(repo, host_data):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'passwordless'})
    standard = {'os': {'family': 'debian', 'packages': []}, 'engines': {
        'docker': {'available': False, 'containers': []},
        'podman': {'available': True, 'containers': [], 'scope': 'OPM SSH user'}}}
    elevated = {'engines': {
        'docker': {'available': False, 'containers': []},
        'podman': {'available': True, 'containers': [], 'scope': 'root'},
        'podman_users': [],
        'podman_discovery_warning': 'Rootless Podman scan time budget reached; 3 context(s) were not inspected'}}
    with patch('opm.remote.ssh_command', side_effect=lambda _h, _c, args, *a, **kw:
               json.dumps(elevated if args[-1] == '--privileged-engines' else standard)
               if args[-1] in ('--privileged-engines', '--read-only') else 'OK'):
        result = inspect_host(host, config)
    assert '3 context(s)' in result['engines']['podman']['scope_warning']


def test_probe_never_attempts_podman_mutation_or_password_storage():
    assert 'getpwnam(\'uosserver\')' not in REMOTE_INSPECT
    assert "'podman', 'pull'" not in REMOTE_INSPECT
    assert "'podman', 'stop'" not in REMOTE_INSPECT
    assert 'enable-linger' not in REMOTE_INSPECT
    assert 'chpasswd' not in REMOTE_INSPECT
