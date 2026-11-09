"""v0.6.10: Podman uses the same branding path; display names may contain spaces.

No network lookups, SSH, Podman or real Ansible execution are performed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path

import pytest

from opm.controller import Controller
from opm.inventory import ansible_inventory, inventory_alias
from opm.server_logos import choices_for_host, selected_repositories
from opm.store import Store
from opm.validation import validate_host
from opm.worker import _validated_inventory_file, run_job


def _host(repo):
    store, config = repo
    host = store.add_host({'name': 'Frigate NVR', 'address': '192.0.2.100',
                           'ssh_user': 'opm', 'sudo_mode': 'passwordless'})
    return store, config, host


def test_podman_only_apps_are_logo_choices_without_docker():
    host = {'os': {'family': 'debian'}, 'engines': {'podman': {'containers': [
        {'image': 'ghcr.io/immich-app/immich-server:release',
         'scope': 'Podman user media (UID 1234)'},
        {'image': 'docker.io/library/redis:7', 'scope': 'root'},
    ]}, 'docker': {'installed': False, 'containers': []}}}
    options = choices_for_host(host)
    assert [x['repo'] for x in options if x['kind'] == 'container'] == [
        'ghcr.io/immich-app/immich-server', 'library/redis']
    assert all('Podman' in x['label'] for x in options if x['kind'] == 'container')
    assert selected_repositories(host) == [
        'ghcr.io/immich-app/immich-server', 'library/redis']
    assert options[-1]['value'] == 'distro'


def test_docker_precedes_podman_and_shared_images_deduplicate():
    engines = {
        'podman': {'containers': [{'image': 'ghcr.io/org/podman-app:v3'},
                                  {'image': 'ghcr.io/shared/service:latest'}]},
        'docker': {'containers': [{'image': 'jc21/nginx-proxy-manager:latest'},
                                  {'image': 'ghcr.io/shared/service:1'},
                                  {'image': 'ghcr.io/org/vendor:1', 'managed_by': 'vendor-managed'}]}}
    options = choices_for_host({'engines': engines})
    repos = [x['repo'] for x in options if x['kind'] == 'container']
    assert repos == ['ghcr.io/shared/service', 'jc21/nginx-proxy-manager',
                     'ghcr.io/org/podman-app']
    assert 'Docker + Podman' in options[1]['label']
    assert selected_repositories({'engines': engines}) == repos
    assert len(set(x['value'] for x in options)) == len(options)


def test_podman_logo_id_accepted_by_database_and_controller(repo):
    store, config, host = _host(repo)
    scan = store.begin_scan(host['id'])
    store.finish_scan(scan, {'os': {'family': 'debian'}, 'engines': {
        'docker': {'containers': []}, 'podman': {'containers': [
            {'image': 'ghcr.io/linuxserver/jellyfin:latest', 'scope': 'root'},
            {'image': 'registry.local:5000/apps/paperless:2', 'scope': 'Podman user app'}]}}})
    controller = Controller(config)
    info = asyncio.run(controller.dispatch('hosts.logo_info', {'id': host['id']}))
    assert info['engines']['podman']['containers'][0]['image'].startswith('ghcr.io')
    assert info['engines']['docker']['containers'] == []
    # Only sanitized image and managed_by are passed to the logo service.
    assert set(info['engines']['podman']['containers'][0]) == {'image', 'managed_by'}
    for option in [c for c in choices_for_host(info) if c['kind'] == 'container']:
        updated = asyncio.run(controller.dispatch('hosts.logo.set', {
            'id': host['id'], 'choice': option['value']}))
        assert updated['logo_choice'] == option['value']
        assert selected_repositories({**info, 'logo_choice': option['value']}) == [option['repo']]
    with pytest.raises(ValueError, match='Logo is not available'):
        asyncio.run(controller.dispatch('hosts.logo.set', {
            'id': host['id'], 'choice': 'image:' + 'f'*24}))
    with pytest.raises(ValueError):
        store.set_host_logo(host['id'], 'image:' + 'g'*24)
    with pytest.raises(ValueError):
        store.set_host_logo(host['id'], 'image:' + 'a'*23)


def test_spaced_name_is_display_label_and_connection_is_unchanged(repo):
    store, config, host = _host(repo)
    assert host['name'] == 'Frigate NVR'
    alias = inventory_alias(host)
    assert alias == 'opm-' + host['id']
    assert alias != host['name']
    inventory = ansible_inventory([host], str(config.known_hosts))
    assert list(inventory['all']['hosts']) == [alias]
    assert inventory['all']['hosts'][alias]['ansible_host'] == '192.0.2.100'
    assert inventory['all']['hosts'][alias]['ansible_user'] == 'opm'
    updated = store.update_host(host['id'], {
        'name': 'Reverse Proxy Server', 'address': host['address'],
        'ssh_user': host['ssh_user'], 'ssh_port': host['ssh_port'],
        'key_path': host['key_path'], 'sudo_mode': host['sudo_mode'],
        'protected': bool(host['protected']), 'group_name': host['group_name']})
    assert updated['name'] == 'Reverse Proxy Server'
    assert inventory_alias(updated) == alias  # immutable host ID, not a slug


@pytest.mark.parametrize('bad', [
    '1 Server', 'My  Server', 'My\tServer', 'My\nServer', 'My/Server',
    'Frigate; rm -rf', 'Something$(id)', 'Server 🚀', 'A' * 65, ' .',
])
def test_display_name_rejects_unsafe_or_ambiguous_input(bad):
    with pytest.raises(ValueError, match='Server name'):
        validate_host({'name': bad, 'address': '192.0.2.99', 'ssh_user': 'opm'})


def test_legacy_names_still_use_original_inventory_identity(repo):
    store, config = repo
    host = store.add_host({'name': 'debian-vm', 'address': '192.0.2.24', 'ssh_user':'opm'})
    assert inventory_alias(host) == 'debian-vm'
    assert list(ansible_inventory([host], str(config.known_hosts))['all']['hosts']) == ['debian-vm']


def test_ansible_cli_validates_safe_alias_before_patch(repo, tmp_path, monkeypatch):
    from opm import worker
    store, config, host = _host(repo)
    inventory = ansible_inventory([host], str(config.known_hosts))
    alias = inventory_alias(host)
    binary = tmp_path / 'ansible-playbook'
    inventory_bin = tmp_path / 'ansible-inventory'
    inventory_bin.write_text('#!/bin/sh\nexit 0\n')
    inventory_bin.chmod(0o755)

    def fake_run(cmd, **kwargs):
        assert cmd[0] == str(inventory_bin)
        assert set(json.loads(Path(cmd[2]).read_text())['all']['hosts']) == {alias}
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({
            '_meta': {'hostvars': {alias: inventory['all']['hosts'][alias]}}}), stderr='')
    monkeypatch.setattr(worker.subprocess, 'run', fake_run)
    result = _validated_inventory_file(host, inventory, tmp_path / 'job', str(binary), '/mock/bin')
    assert result.is_file() and result.stat().st_mode & 0o777 == 0o600


def test_dry_run_uses_alias_in_runner_event_and_recap(repo, monkeypatch):
    from opm import worker
    store, config, host = _host(repo)
    store.verified(host['id'])
    scan = store.begin_scan(host['id'])
    store.finish_scan(scan, {'os': {'family': 'debian', 'metadata': 'fresh',
                          'package_query_ok': True, 'packages': []}})
    plan = store.create_plan([host['id']], mode='dry_run')
    store.approve_plan(plan['id'], 'tester', False)
    job = store.create_job(plan['id'], 'spaces-runner')
    alias = inventory_alias(host)
    monkeypatch.setenv('PATH', os.environ.get('PATH', ''))
    monkeypatch.setattr(worker, 'check_ansible_runtime', lambda: ('/mock/bin/ansible-playbook', '/mock/bin'))
    monkeypatch.setattr(worker, '_validated_inventory_file', lambda h, i, d, a, p: d / 'hosts.json')
    monkeypatch.setattr(worker, '_simulate_preflight', lambda *args, **kwargs: None)

    def fake_runner(**kwargs):
        cb = kwargs['event_handler']
        cb({'event': 'runner_on_ok', 'event_data': {
            'task': 'Validate privileged Ansible access in check mode',
            'host': alias, 'res': {'stat': {'exists': True}}}})
        cb({'event': 'playbook_on_stats', 'event_data': {'ok': {alias: 1}}})
        return types.SimpleNamespace(rc=0, status='successful')
    monkeypatch.setitem(sys.modules, 'ansible_runner', types.SimpleNamespace(run=fake_runner))
    run_job(job['id'], config)
    assert store.job(job['id'])['state'] == 'simulated'
    assert store.job(job['id'])['targets'][0]['state'] == 'simulated'
