"""Read-only, isolated Compose verification tests for v0.6.6."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from opm.remote import REMOTE_COMPOSE_VERIFY, verify_compose_source, RemoteError
from opm.store import Store

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'cameras'
HASH = 'a' * 64


def remote_run(monkeypatch, tmp_path, *, missing=False, bad_hash=False, vendor=False):
    work = tmp_path / 'project'
    work.mkdir()
    path = work / 'compose.yaml'
    if not missing:
        path.write_text('services:\n  frigate:\n    image: ghcr.io/example/frigate:stable\n')
    labels = {
        'com.docker.compose.project': PROJECT,
        'com.docker.compose.service': 'frigate',
        'com.docker.compose.oneoff': 'False',
        'com.docker.compose.project.working_dir': str(work),
        'com.docker.compose.project.config_files': str(path),
        'com.docker.compose.config-hash': HASH,
    }
    observed = []
    def fake_run(argv, **kwargs):
        observed.append(tuple(argv))
        assert argv[0] == 'docker'
        if argv[:3] == ['docker', 'ps', '-aq']:
            out = 'test-container-id\n'
        elif argv[:2] == ['docker', 'inspect']:
            out = json.dumps([{'Config': {'Labels': labels,
                     'Image': 'docker.io/library/uosserver:latest' if vendor else 'ghcr.io/example/frigate:stable'}}])
        elif argv[-2:] == ['config', '--quiet']:
            out = ''
        elif argv[-2:] == ['config', '--services']:
            out = 'frigate\n'
        elif argv[-2:] == ['config', '--hash=frigate']:
            out = 'frigate ' + ('b' * 64 if bad_hash else HASH) + '\n'
        else:
            raise AssertionError(f'Unrecognized operation {argv}')
        return SimpleNamespace(returncode=0, stdout=out, stderr='')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    code = REMOTE_COMPOSE_VERIFY.replace("EXPECTED_PROJECT = ''", "EXPECTED_PROJECT = 'cameras'")
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        exec(compile(code, '<compose-script>', 'exec'), {'__name__': '__main__'})
        result = json.loads(output.getvalue())
    return result, observed


def test_compose_readonly_success(monkeypatch, tmp_path):
    result, commands = remote_run(monkeypatch, tmp_path)
    assert result == {'status':'verified', 'detail':'Compose source matches deployed services',
                      'service_count':1, 'container_count':1}
    assert all('pull' not in cmd and 'up' not in cmd and 'restart' not in cmd
               for cmd in commands)
    assert all('ghcr' not in ' '.join(cmd) for cmd in commands)
    assert sum(command[-2:] == ('config', '--hash=frigate') for command in commands) == 1
    assert all('--hash' not in command for command in commands)


@pytest.mark.parametrize('kind, detail', [
    ('missing', 'Original Compose file is missing or inaccessible'),
    ('bad_hash', 'Running container configuration differs from Compose source'),
    ('vendor', 'Vendor-controlled workload is excluded'),
])
def test_compose_rejects_unsafe_or_changed_deployments(monkeypatch, tmp_path, kind, detail):
    result, commands = remote_run(monkeypatch, tmp_path, **{kind: True})
    assert result['status'] == 'unverified'
    assert result['detail'] == detail
    assert not any('pull' in cmd or 'up' in cmd for cmd in commands)


def test_remote_verification_rejects_injected_project(monkeypatch):
    from opm import remote
    with pytest.raises(RemoteError):
        verify_compose_source({}, None, 'hello; curl attacker', None)
    recorded = {}
    def fake_ssh(host, config, argv, stdin, timeout):
        recorded['argv'] = argv
        recorded['stdin'] = stdin
        return json.dumps({'status':'verified','detail':'Compose source matches deployed services',
                           'service_count':2,'container_count':3})
    monkeypatch.setattr(remote, 'ssh_command', fake_ssh)
    result = verify_compose_source({'sudo_mode':'passwordless'}, None, 'cameras')
    assert result['status'] == 'verified'
    assert recorded['argv'] == ['sudo', '-n', 'python3', '-']
    assert 'EXPECTED_PROJECT = "cameras"' in recorded['stdin']
    assert 'pull' not in recorded['argv']


def test_store_records_only_sanitized_status(repo):
    store, _ = repo
    host = store.add_host({'name':'cameras', 'address':'192.0.2.3',
        'ssh_user':'opm','ssh_port':22,'group_name':'Linux VMs'})
    record = store.save_compose_verification(host['id'], 'cameras', 'root', 'verified',
          'Compose source matches deployed services', 1, 1)
    assert record['status']=='verified' and record['service_count']==1
    assert ("cameras", "root") in store.compose_verifications(host['id'])
    with store.connection() as db:
        row = db.execute('SELECT * FROM compose_verifications').fetchone()
        assert not any('password' in str(value).lower() or 'compose.yaml' in str(value)
                       for value in row)


def test_admin_only_endpoint_and_no_update_action():
    views = (ROOT/'src/opm/web/views.py').read_text()
    template = (ROOT/'src/opm/web/templates/web/host_detail.html').read_text()
    assert '@require_POST\ndef verify_compose_configuration(' in views
    assert '_admin(request)' in views[views.index('def verify_compose_configuration'):views.index('def server_detail')]
    assert 'Verify Configuration' in template
    assert 'compose_project_list' in template
    assert 'docker compose up -d' not in template.lower()


def test_controller_requires_known_verified_project():
    code = (ROOT/'src/opm/controller.py').read_text()
    assert "if method == \"hosts.compose_verify.start\":" in code
    assert 'docker_compose_projects' in code
    assert "candidate['vendor_managed'] or candidate['oneoff']" in code
    assert "self.store.begin_activity('compose_verify'" in code


def test_verification_is_not_update_authorization():
    from opm.compose_inventory import docker_compose_projects
    engines = {'docker': {'containers': [
        {'project':'cameras','service':'frigate','compose_origin':'docker-compose',
         'scope':'root', 'compose_has_config_files':True, 'compose_has_working_dir':True}]}}
    assert docker_compose_projects(engines)[0]['update_enabled'] is False
    assert 'docker compose up' not in REMOTE_COMPOSE_VERIFY.lower()
    assert 'docker compose pull' not in REMOTE_COMPOSE_VERIFY.lower()
