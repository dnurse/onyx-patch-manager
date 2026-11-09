"""v0.6.7: explicit Docker Compose updates, safe revalidation and auditing."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from opm.compose_updates import REMOTE_COMPOSE_UPDATE, update_compose_service
from opm.controller import Controller
from opm.remote import RemoteError

ROOT = Path(__file__).resolve().parents[1]
HASH = 'a' * 64
OLD = 'sha256:' + '1'*64
NEW = 'sha256:' + '2'*64
IMAGE = 'ghcr.io/example/frigate:stable'


def simulated_remote(monkeypatch, tmp_path, *, changed=True, mismatch=False,
                     pinned=False, build=False, fail_pull=False, bad_health=False,
                     drift_after_pull=False):
    work = tmp_path/'configs'; work.mkdir()
    compose = work/'compose.yaml'
    compose.write_text('services:\n  frigate:\n    image: '+IMAGE+'\n')
    labels = {
        'com.docker.compose.project': 'cameras',
        'com.docker.compose.service': 'frigate',
        'com.docker.compose.oneoff': 'False',
        'com.docker.compose.project.working_dir': str(work),
        'com.docker.compose.project.config_files': str(compose),
        'com.docker.compose.config-hash': 'b'*64 if mismatch else HASH,
    }
    state = {'pulled': False, 'recreated': False}
    calls = []

    def fake_run(args, **kwargs):
        args = list(args)
        calls.append(args)
        assert args[0] == 'docker'  # No shell, path or arbitrary command.
        result = ''
        code = 0
        if args[1:3] == ['ps', '-aq']:
            result = 'container-id\n'
        elif args[1] == 'inspect':
            current_id = NEW if state['recreated'] and changed else OLD
            status = {'Status':'running'}
            if state['recreated']:
                status['Health'] = {'Status': 'unhealthy' if bad_health else 'healthy'}
            result = json.dumps([{'Config':{'Labels':labels,
                            'Image': IMAGE}, 'State':status, 'Image':current_id}])
        elif args[-2:] == ['config', '--quiet']:
            result = ''
        elif args[-2:] == ['config', '--services']:
            result = 'frigate\n'
        elif args[-2:] == ['config', '--hash=frigate']:
            result = 'frigate ' + ('b'*64 if state['pulled'] and drift_after_pull else HASH) + '\n'
        elif args[-3:] == ['config', '--format', 'json']:
            service = {'image': IMAGE + ('@sha256:'+'c'*64 if pinned else '')}
            if build: service['build'] = '.'
            result = json.dumps({'services': {'frigate':service}})
        elif args[1:3] == ['image', 'inspect']:
            result = json.dumps([{'Id':NEW if state['pulled'] and changed else OLD}])
        elif 'pull' in args:
            assert args[-1] == 'frigate'
            state['pulled'] = True
            code = 1 if fail_pull else 0
        elif 'up' in args:
            assert args[-1] == 'frigate'
            assert '--no-deps' in args and '--no-build' in args
            assert '--pull' in args and args[args.index('--pull')+1] == 'never'
            state['recreated'] = True
        else:
            raise AssertionError('Unexpected command: '+repr(args))
        return SimpleNamespace(returncode=code, stdout=result, stderr='SECRET NEVER LEAKS')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    script = REMOTE_COMPOSE_UPDATE.replace("EXPECTED_PROJECT = ''", "EXPECTED_PROJECT = 'cameras'")
    script = script.replace("EXPECTED_SERVICE = ''", "EXPECTED_SERVICE = 'frigate'")
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        exec(compile(script, '<remote-compose-update>', 'exec'), {'__name__': '__main__'})
        result = json.loads(output.getvalue())
    return result, calls, state


def test_single_service_pulls_then_recreates_and_checks_health(monkeypatch, tmp_path):
    result, calls, state = simulated_remote(monkeypatch, tmp_path)
    assert result['status'] == 'updated'
    assert state == {'pulled':True, 'recreated':True}
    assert any('pull' in c for c in calls) and any('up' in c for c in calls)
    assert not any('SECRET' in ' '.join(c) for c in calls)
    assert not any('--include-deps' in c or '--remove-orphans' in c for c in calls)
    assert sum(c[-2:] == ['config', '--hash=frigate'] for c in calls) >= 2
    assert not any('--hash' in c for c in calls)


def test_same_digest_skips_recreation(monkeypatch, tmp_path):
    result, calls, state = simulated_remote(monkeypatch, tmp_path, changed=False)
    assert result['status'] == 'unchanged'
    assert state['pulled'] and not state['recreated']
    assert not any('up' in c for c in calls)


@pytest.mark.parametrize('cause', ['mismatch', 'pinned', 'build'])
def test_unsafe_or_changed_deployment_never_pulls(monkeypatch, tmp_path, cause):
    result, calls, state = simulated_remote(monkeypatch, tmp_path, **{cause:True})
    assert result['status'] == 'blocked'
    assert not state['pulled'] and not state['recreated']
    assert not any('pull' in c or 'up' in c for c in calls)


def test_pull_failure_does_not_recreate(monkeypatch, tmp_path):
    result, calls, state = simulated_remote(monkeypatch, tmp_path, fail_pull=True)
    assert result['status'] == 'failed'
    assert state['pulled'] and not state['recreated']
    assert not any('up' in c for c in calls)


def test_config_drift_after_pull_blocks_recreation(monkeypatch, tmp_path):
    result, calls, state = simulated_remote(monkeypatch, tmp_path, drift_after_pull=True)
    assert result['status'] == 'blocked'
    assert state['pulled'] and not state['recreated']
    assert not any('up' in c for c in calls)


def test_unhealthy_service_does_not_claim_success(monkeypatch, tmp_path):
    result, calls, state = simulated_remote(monkeypatch, tmp_path, bad_health=True)
    assert result['status'] == 'failed'
    assert state['recreated']
    assert 'manual recovery' in result['detail']


def test_remote_ssh_request_contains_only_verified_script(monkeypatch):
    from opm import compose_updates as updates
    with pytest.raises(RemoteError):
        update_compose_service({}, None, 'cameras;exit', 'frigate')
    with pytest.raises(RemoteError):
        update_compose_service({}, None, 'cameras', '--bad')
    seen = {}
    def fake_ssh(host, config, command, stdin, timeout):
        seen.update(command=command, stdin=stdin, timeout=timeout)
        return json.dumps({'status':'unchanged',
                           'detail':'Registry image matches the running service; nothing restarted'})
    monkeypatch.setattr(updates, 'ssh_command', fake_ssh)
    output = update_compose_service({'sudo_mode':'password'}, None, 'cameras', 'frigate', 'test-password')
    assert output['status'] == 'unchanged'
    assert 'test-password' not in ' '.join(seen['command'])
    assert seen['stdin'].startswith('test-password\n')
    assert seen['timeout'] >= 1000
    monkeypatch.setattr(updates, 'ssh_command', lambda *a,**k: '{"status":"updated","detail":"/etc/secret.env"}')
    with pytest.raises(RemoteError, match='invalid sanitized'):
        update_compose_service({}, None, 'cameras', 'frigate')


def setup_controller(repo, host_data, *, protected=False, verified=True):
    store, config = repo
    host = store.add_host({**host_data, 'protected':protected, 'sudo_mode':'passwordless'})
    if verified: store.verified(host['id'], True)
    scan_id = store.begin_scan(host['id'])
    store.finish_scan(scan_id, {'engines':{'docker':{'containers':[{
        'name':'frigate','image':IMAGE,'project':'cameras','service':'frigate',
        'scope':'root', 'compose_origin':'docker-compose',
        'compose_has_config_files':True,'compose_has_working_dir':True,
        'update_status':'changed', 'compose_oneoff':False,
    }]}}})
    return store, config, host


def test_controller_requires_verified_source_approval_and_protected_ack(repo, host_data):
    store, config, host = setup_controller(repo, host_data, protected=True)
    controller = Controller(config)
    payload = {'id':host['id'],'project':'cameras','service':'frigate',
               'confirm_update':True, 'protected_ack':True}
    async def run():
        for patch in ({'confirm_update':False}, {'protected_ack':False},
                      {'service':'other'}, {'service':'; id'}):
            with pytest.raises(ValueError):
                await controller.dispatch('hosts.compose_update.start', {**payload, **patch})
        with pytest.raises(ValueError, match='Verify Configuration'):
            await controller.dispatch('hosts.compose_update.start', payload)
    asyncio.run(run())
    assert not store.recent_activities()


def test_controller_update_audits_and_revokes_verification(repo, host_data, monkeypatch):
    store, config, host = setup_controller(repo, host_data)
    store.save_compose_verification(host['id'], 'cameras', 'root', 'verified',
        'Compose source matches deployed services', 1, 1)
    calls=[]
    def fake_update(h,c,p,s,password):
        calls.append((p,s))
        return {'status':'updated', 'detail':'Service image refreshed; running/health checks passed'}
    monkeypatch.setattr('opm.controller.update_compose_service', fake_update)
    async def run():
        ctl = Controller(config)
        payload = {'id':host['id'],'project':'cameras','service':'frigate',
                   'confirm_update':True,'protected_ack':False}
        result = await ctl.dispatch('hosts.compose_update.start', payload)
        with pytest.raises(ValueError, match='operation'):
            await ctl.dispatch('hosts.compose_update.start', payload)
        await asyncio.gather(*tuple(ctl._scans))
        activity = store.activity(result['activity_id'])
        assert activity['state'] == 'completed'
        assert store.compose_verifications(host['id'])[('cameras','root')]['status'] == 'unverified'
        assert not store.is_locked(host['id'])
    asyncio.run(run())
    assert calls==[('cameras','frigate')]


def test_web_has_opt_in_confirmation_and_csrf():
    views = (ROOT/'src/opm/web/views.py').read_text()
    template = (ROOT/'src/opm/web/templates/web/host_detail.html').read_text()
    urls = (ROOT/'src/opm/web/urls.py').read_text()
    assert '@require_POST\ndef update_compose_configuration(' in views
    assert '_admin(request)' in views.split('def update_compose_configuration(',1)[1].split('def server_detail',1)[0]
    assert 'name="confirm_update" value="yes" required' in template
    assert 'name="protected_ack" value="yes" required' in template
    assert '{% csrf_token %}' in template
    assert 'update_compose_configuration' in urls
    assert 'Compose updating remains disabled' not in template
