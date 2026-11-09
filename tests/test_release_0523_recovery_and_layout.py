"""v0.5.23: exact UI requests and safe administrator-only SSH key recovery.
All remote operations are monkeypatched; no actual SSH or account mutations occur.
"""
import asyncio
from pathlib import Path

import pytest
from opm.controller import Controller

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT/'src/opm/web'
T = WEB/'templates/web'


def test_layout_account_creation_and_protected_icon():
    dashboard=(T/'dashboard.html').read_text()
    css=(WEB/'static/web/styles.css').read_text()
    settings=(T/'settings.html').read_text()
    enroll=(T/'enroll.html').read_text()
    host_form=(T/'host_form.html').read_text()
    assert "include 'web/partials/server_logo.html' with server=h" in dashboard
    assert '.server-logo-lock' in css
    assert 'color: #fff;' in css
    assert settings.index('<h2>Accounts</h2>') < settings.index('<h2>Security</h2>')
    assert 'Create New Account' in settings
    assert 'data-account-create-toggle' not in settings
    assert "{% url 'create_account' %}" in settings
    assert (T/'account_create.html').exists()
    assert 'class="surface enroll-shell"' in enroll
    assert '.enroll-shell { max-width:none; width:100%;' in css
    assert 'server-registration-panel' in host_form
    assert enroll.index('enroll-first-actions') < enroll.index('Next: OPM account')
    assert enroll.index('enroll-first-actions') > enroll.index('Administrator sudo password')
    assert 'Protect server' in enroll and 'Require separate approval before patching this server.' in enroll
    assert '.enroll-protected-option { margin:0 auto 0 0; padding:0;' in css


def test_create_is_separate_admin_page_and_repair_route_admin_only():
    urls=(WEB/'urls.py').read_text()
    views=(WEB/'views.py').read_text()
    assert "name='create_account'" in urls
    assert "name='server_repair_ssh'" in urls
    assert 'def create_account(request):\n    """Administrator-only' in views
    assert 'def server_repair_ssh(request, host_id):\n    """Explicit recovery' in views
    assert '_admin(request)' in views.split('def server_repair_ssh(request, host_id):')[1].split('def server_edit(')[0]
    form=(T/'repair_ssh.html').read_text()
    assert "enrollment_fingerprint" in form
    assert 'current opm sudo password' in form.lower()
    assert 'id="repair-submit"' in form
    assert "{% url 'server_repair_ssh' host.id %}" in (T/'host_detail.html').read_text()


def _host(store):
    host=store.add_host({'name':'oldhost', 'address':'192.0.2.8', 'ssh_user':'opm',
                         'key_path':'/home/opm/.local/share/opm/keys/opm_ed25519'})
    store.verified(host['id'], True)
    return store.host(host['id'])


def test_repair_checks_trusted_key_before_credential_use(monkeypatch, repo):
    store, config=repo
    host=_host(store)
    ctl=Controller(config)
    calls=[]
    def refuse(*args):
        calls.append('key rejected')
        raise ValueError('SSH host identity changed')
    monkeypatch.setattr('opm.controller.accept_host_key', refuse)
    monkeypatch.setattr('opm.controller.bootstrap_account', lambda *args, **kwargs: calls.append('bad'))
    with pytest.raises(ValueError, match='identity changed'):
        ctl.repair_ssh_host(host['id'], 'SHA256:invalid', {'admin_user':'admin','login_password':'xxx',
            'admin_sudo_password':'','opm_password':'xxx'}, 'activity')
    assert calls==['key rejected']
    assert store.host(host['id'])['verified'] == 1


def test_repair_reuses_existing_opm_without_replacing_it(monkeypatch, repo):
    store, config=repo
    host=_host(store)
    ctl=Controller(config)
    activity=store.begin_activity('ssh_repair','Repair: oldhost',['host:'+host['id']])
    calls=[]
    monkeypatch.setattr('opm.controller.accept_host_key', lambda *args: calls.append('approved'))
    def bootstrap(*args, **kwargs):
        calls.append('bootstrap')
        assert kwargs == {'mode':'repair_key', 'replace_ack':False, 'create_ack':False}
        assert args[-1] == ''
        return {'key_path':host['key_path']}
    monkeypatch.setattr('opm.controller.bootstrap_account', bootstrap)
    monkeypatch.setattr('opm.controller.test_connection', lambda *args: calls.append('key verified'))
    monkeypatch.setattr('opm.controller.verify_sudo_password', lambda *args: True)
    creds={'admin_user':'admin','login_password':'admin-password','admin_sudo_password':'',
           'opm_password':'current-opm-password'}
    asyncio.run(ctl.repair_ssh_background(activity['id'],host['id'],'SHA256:approved',creds))
    outcome=store.activity(activity['id'])
    assert outcome['state']=='completed'
    assert calls == ['approved','bootstrap','key verified']
    assert any('controller SSH public key authorized' in e['message'] for e in outcome['events'])
    assert 'current-opm-password' not in str(outcome)
    assert creds == {}
    assert store.host(host['id'])['verified'] == 1


def test_repair_failure_preserves_account_and_redacts_password(monkeypatch, repo):
    store, config=repo
    host=_host(store)
    ctl=Controller(config)
    activity=store.begin_activity('ssh_repair','Repair: oldhost',['host:'+host['id']])
    monkeypatch.setattr('opm.controller.accept_host_key', lambda *args: None)
    def invalid_password(*args, **kwargs):
        raise ValueError('Wrong sudo: private-secret-value')
    monkeypatch.setattr('opm.controller.bootstrap_account', invalid_password)
    creds={'admin_user':'admin','login_password':'admin-pw','admin_sudo_password':'',
           'opm_password':'private-secret-value'}
    asyncio.run(ctl.repair_ssh_background(activity['id'],host['id'],'SHA256:approved',creds))
    outcome=store.activity(activity['id'])
    assert outcome['state']=='failed'
    assert 'private-secret-value' not in str(outcome)
    assert 'REDACTED' in str(outcome)
    assert store.host(host['id'])['verified'] == 1
    assert creds=={}


def test_dispatch_rejects_unverified_and_missing_fingerprint(monkeypatch, repo):
    store, config=repo
    host=_host(store)
    ctl=Controller(config)
    with pytest.raises(ValueError, match='Approve'):
        asyncio.run(ctl.dispatch('hosts.ssh_repair.start', {'id':host['id'],'trust_ack':False}))
    store.verified(host['id'], False)
    with pytest.raises(ValueError, match='verified'):
        asyncio.run(ctl.dispatch('hosts.ssh_repair.start', {'id':host['id'],
            'trust_ack':True,'fingerprint':'SHA256:example'}))


def test_dispatch_repair_locks_host_against_overlapping_operations(monkeypatch, repo):
    store, config=repo
    host=_host(store)
    ctl=Controller(config)
    scheduled=[]
    def fake_launch(coro):
        scheduled.append(True)
        coro.close()  # no actual background work in this test
    monkeypatch.setattr(ctl, '_launch_activity', fake_launch)
    data={'id':host['id'],'fingerprint':'SHA256:test','trust_ack':True,
          'admin_user':'admin','login_password':'admin-pass','opm_password':'password'}
    result=asyncio.run(ctl.dispatch('hosts.ssh_repair.start',data))
    assert result['scheduled']==1 and scheduled==[True]
    with pytest.raises(ValueError,match='already running'):
        asyncio.run(ctl.dispatch('hosts.ssh_repair.start',data))


def test_repair_key_payload_no_remote_sudo_password():
    from opm.onboarding import _payload, REMOTE_CREATE_ACCOUNT
    import json
    payload=json.loads(_payload('ssh-ed25519 AAAA original', '', mode='repair_key'))
    assert payload['account_mode']=='repair_key'
    assert payload['sudo_password']==''
    assert "elif action == 'repair_key' or (action == 'repair_or_create' and account is not None):" in REMOTE_CREATE_ACCOUNT
    assert 'authorized_existed = authorized.exists()' in REMOTE_CREATE_ACCOUNT
    assert 'if reuse and authorized_existed and info.st_uid != account.pw_uid:' in REMOTE_CREATE_ACCOUNT
    assert 'write_authorized_key(account, key, reuse=True)' in REMOTE_CREATE_ACCOUNT
    assert "elif action == 'repair_key'" in REMOTE_CREATE_ACCOUNT


def test_ssh_repair_can_be_partial_without_sudo(monkeypatch, repo):
    store,config=repo
    host=_host(store)
    ctl=Controller(config)
    activity=store.begin_activity('ssh_repair','Repair: oldhost',['host:'+host['id']])
    monkeypatch.setattr('opm.controller.accept_host_key',lambda *args:None)
    monkeypatch.setattr('opm.controller.bootstrap_account',lambda *args,**kwargs:{'key_path':host['key_path']})
    monkeypatch.setattr('opm.controller.test_connection',lambda *args:None)
    monkeypatch.setattr('opm.controller.verify_sudo_password',lambda *args:False)
    creds={'admin_user':'admin','login_password':'secret','admin_sudo_password':'','opm_password':''}
    asyncio.run(ctl.repair_ssh_background(activity['id'],host['id'],'SHA256:approved',creds))
    result=store.activity(activity['id'])
    assert result['state']=='partial'
    assert result['result']['ssh_recovered'] is True
    assert result['result']['sudo_verified'] is False
    assert 'sudo' in result['detail']
    assert store.host(host['id'])['verified']==1
    assert creds=={}
