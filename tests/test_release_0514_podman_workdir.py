"""Regress private /home/opm -> rootless Podman inherited-cwd failure."""
import ast
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from opm.remote import REMOTE_INSPECT


def remote_functions(*names, namespace):
    tree = ast.parse(REMOTE_INSPECT)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<remote>', 'exec'), namespace)
    return namespace


def test_subprocess_run_can_set_safe_workdir_without_changing_user_home():
    calls = []
    def fake_subprocess_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    sub = SimpleNamespace(run=fake_subprocess_run, PIPE=object(), TimeoutExpired=TimeoutError)
    ns = remote_functions('run', namespace={'subprocess': sub, 'OSError': OSError})
    assert ns['run'](['runuser', '-u', 'uosserver', '--', 'env'], cwd='/')[0] == 0
    assert calls[0][1]['cwd'] == '/'


def test_rootless_ps_and_inspect_both_execute_outside_private_opm_home():
    calls = []
    def simulated_run(command, timeout=22, cwd=None):
        calls.append((command, cwd))
        assert cwd == '/'
        if command[-2:] == ['ps', '-aq']:
            return 0, 'abcdef123456\n', ''
        return 0, json.dumps([{'Id':'abcdef123456','Name':'/service',
                               'Config':{'Image':'service:latest','Labels':{}},
                               'State':{'Status':'running'}}]), ''
    ns = remote_functions('containers', namespace={
        'shutil': SimpleNamespace(which=lambda name: '/usr/bin/' + name),
        'run': simulated_run, 'json':json,
        'image_status': lambda *a: (_ for _ in ()).throw(AssertionError('No registry queries'))
    })
    prefix = ['runuser','-u','uosserver','--','env','-i',
              'HOME=/home/uosserver','XDG_RUNTIME_DIR=/run/user/1000']
    result = ns['containers']('podman', prefix, 'Podman user uosserver (UID 1000)',
                              registry_checks=False, working_directory='/')
    assert result['available'] is True and len(result['containers']) == 1
    assert len(calls) == 2 and all(cwd == '/' for _,cwd in calls)
    assert all(command[:len(prefix)] == prefix for command,_ in calls)
    assert all('pull' not in command and 'restart' not in command for command,_ in calls)


def test_all_rootless_accounts_request_readonly_safe_workdir():
    calls=[]
    def fake_containers(engine, prefix, scope, **kw):
        calls.append((engine,prefix,scope,kw))
        return {'available':True,'installed':True,'containers':[], 'scope':scope}
    ns=remote_functions('all_rootless_podman',namespace={
        'shutil': SimpleNamespace(which=lambda _: '/usr/bin/podman'),
        'os': SimpleNamespace(geteuid=lambda:0,path=SimpleNamespace(isdir=lambda _:False)),
        'time': SimpleNamespace(monotonic=lambda: 100),
        'discover_podman_accounts':lambda: [SimpleNamespace(pw_name='uosserver',pw_uid=1000,pw_dir='/home/uosserver'),
                                           SimpleNamespace(pw_name='another',pw_uid=1001,pw_dir='/srv/another')],
        'containers':fake_containers
    })
    result,warning=ns['all_rootless_podman']()
    assert warning=='' and len(result)==2
    assert all(call[3]['working_directory']=='/' for call in calls)
    assert 'HOME=/home/uosserver' in calls[0][1]
    assert 'HOME=/srv/another' in calls[1][1]
