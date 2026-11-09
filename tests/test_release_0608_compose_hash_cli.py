"""v0.6.8: Compose config --hash needs a service selector (string flag).

Simulate the actual CLI contract instead of teaching the mock to accept the
invalid, previously issued bare `--hash` command.
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from opm.remote import REMOTE_COMPOSE_VERIFY
from opm.compose_updates import REMOTE_COMPOSE_UPDATE

HASHES = {'frigate': 'a' * 64, 'worker': 'b' * 64}


def _execute(monkeypatch, tmp_path: Path, script: str, *, services=('frigate',),
             hash_status='valid', mismatch=False):
    """Remote-only fixture: invalid bare --hash behaves like real Compose."""
    directory = tmp_path / 'private-config'
    directory.mkdir()
    source = directory / 'compose.yaml'
    source.write_text('services:\n' + ''.join(f'  {service}:\n    image: example/test:stable\n'
                                           for service in services))
    containers = [
        {'Config': {'Image': 'example/test:stable', 'Labels': {
            'com.docker.compose.project': 'cameras',
            'com.docker.compose.service': service,
            'com.docker.compose.oneoff': 'False',
            'com.docker.compose.project.working_dir': str(directory),
            'com.docker.compose.project.config_files': str(source),
            'com.docker.compose.config-hash': ('c' * 64 if mismatch else HASHES[service]),
        }}, 'State': {'Status': 'running'}, 'Image': 'sha256:' + '1'*64}
        for service in services
    ]
    calls = []

    def fake_run(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        assert argv[0] == 'docker'
        rc, stdout = 0, ''
        if argv[:3] == ['docker', 'ps', '-aq']:
            stdout = '\n'.join(f'container-{i}' for i in range(len(containers))) + '\n'
        elif argv[:2] == ['docker', 'inspect']:
            stdout = json.dumps(containers)
        elif argv[-2:] == ['config', '--quiet']:
            pass
        elif argv[-2:] == ['config', '--services']:
            stdout = '\n'.join(services) + '\n'
        elif 'config' in argv and any(str(a).startswith('--hash') for a in argv):
            selector = argv[-1]
            if selector == '--hash':  # This is the regression in 0.6.7.
                rc = 1
            elif selector.startswith('--hash='):
                requested = selector.removeprefix('--hash=').split(',')
                assert requested == sorted(services)
                if hash_status == 'error':
                    rc = 1
                elif hash_status == 'malformed':
                    stdout = 'secret env contents must not be echoed\n'
                else:
                    stdout = ''.join(f'{name} {HASHES[name]}\n' for name in requested)
            else:
                raise AssertionError(f'Unexpected Compose --hash invocation: {argv!r}')
        elif ('pull' in argv or 'up' in argv):
            raise AssertionError('Do not pull or recreate when verification fails')
        else:
            raise AssertionError(f'Unexpected command {argv!r}')
        return SimpleNamespace(returncode=rc, stdout=stdout,
                               stderr='private path: ' + str(source) + ' TOKEN=secret')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    code = script.replace("EXPECTED_PROJECT = ''", 'EXPECTED_PROJECT = "cameras"')
    code = code.replace("EXPECTED_SERVICE = ''", 'EXPECTED_SERVICE = "frigate"')
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        exec(compile(code, '<remote-compose-hash>', 'exec'), {'__name__': '__main__'})
        result = json.loads(output.getvalue())
    return result, calls, str(source)


def test_uses_explicit_service_hash_selector(monkeypatch, tmp_path):
    result, calls, _ = _execute(monkeypatch, tmp_path, REMOTE_COMPOSE_VERIFY)
    assert result['status'] == 'verified'
    assert sum(args[-2:] == ['config', '--hash=frigate'] for args in calls) == 1
    assert not any('--hash' in args for args in calls)
    assert not any('pull' in args or 'up' in args for args in calls)


def test_multiple_services_use_comma_selected_hashes(monkeypatch, tmp_path):
    result, calls, _ = _execute(monkeypatch, tmp_path, REMOTE_COMPOSE_VERIFY,
                                services=('worker', 'frigate'))
    assert result['status'] == 'verified'
    assert result['service_count'] == 2
    assert any(args[-2:] == ['config', '--hash=frigate,worker'] for args in calls)


@pytest.mark.parametrize('mode', ['error', 'malformed'])
def test_hash_cli_failure_stays_unverified_and_never_leaks_stderr(monkeypatch, tmp_path, mode):
    result, calls, source = _execute(monkeypatch, tmp_path, REMOTE_COMPOSE_VERIFY,
                                     hash_status=mode)
    assert result['status'] == 'unverified'
    assert 'secret' not in json.dumps(result).lower()
    assert source not in json.dumps(result)
    assert not any('pull' in args or 'up' in args for args in calls)


def test_real_config_mismatch_remains_blocked(monkeypatch, tmp_path):
    result, calls, _ = _execute(monkeypatch, tmp_path, REMOTE_COMPOSE_VERIFY,
                                mismatch=True)
    assert result['status'] == 'unverified'
    assert result['detail'] == 'Running container configuration differs from Compose source'
    assert not any('pull' in args or 'up' in args for args in calls)


def test_update_script_uses_same_fixed_hash_command_and_blocks_on_error(monkeypatch, tmp_path):
    result, calls, _ = _execute(monkeypatch, tmp_path, REMOTE_COMPOSE_UPDATE,
                                hash_status='error')
    assert result['status'] == 'blocked'
    assert sum(args[-2:] == ['config', '--hash=frigate'] for args in calls) == 1
    assert not any('pull' in args or 'up' in args for args in calls)
