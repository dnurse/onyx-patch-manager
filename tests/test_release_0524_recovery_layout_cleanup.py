"""v0.5.24 regressions: template integrity, full-width forms, optional account recovery,
and removal of the retired Textual operator dashboard. No remote mutations occur.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest
from opm.controller import Controller
from opm.onboarding import _payload, OnboardingError, REMOTE_CREATE_ACCOUNT

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'


def test_settings_template_has_balanced_branches_and_no_orphan_account_form():
    for template in (WEB/'templates/web').glob('*.html'):
        stack = []
        for match in re.finditer(r'{%\s*(.*?)\s*%}', template.read_text(), re.S):
            token = match.group(1).split()[0]
            if token in ('if', 'for', 'block', 'with', 'autoescape', 'filter', 'comment'):
                stack.append(token)
            elif token in ('else', 'elif', 'empty'):
                assert stack and stack[-1] in ('if', 'for'), (template.name, token)
                if token == 'empty': assert stack[-1] == 'for'
                if token in ('else', 'elif'): assert stack[-1] == 'if'
            elif token.startswith('end') and token[3:] in ('if','for','block','with','autoescape','filter','comment'):
                assert stack and stack.pop() == token[3:], (template.name, token)
        assert not stack, (template.name, stack)
    settings = (WEB/'templates/web/settings.html').read_text()
    assert '{% for a in accounts %}' in settings
    assert 'data-account-create-cancel' not in settings
    assert settings.count('{% else %}') < settings.count('{% if ')


def test_full_width_form_cards_keep_mobile_layout_and_login_compact():
    css = (WEB/'static/web/styles.css').read_text()
    assert '.account-security-surface > form.form-stack' in css
    assert '.account-edit-surface > form.form-stack' in css
    assert '.form-panel { max-width:none; width:100%;' in css
    assert '.ssh-repair-form input:not([type=checkbox]):not([type=hidden])' in css
    assert '@media(max-width:680px)' in css
    assert '.login-card {' in css  # login remains intentionally compact


def test_repair_create_option_is_explicit_and_requires_new_password():
    with pytest.raises(OnboardingError, match='approval'):
        _payload('ssh-ed25519 AAAA test', 'a'*16, mode='repair_or_create')
    with pytest.raises(OnboardingError, match='12 characters'):
        _payload('ssh-ed25519 AAAA test', 'short', mode='repair_or_create', create_ack=True)
    data = json.loads(_payload('ssh-ed25519 AAAA test', 'a'*16,
                               mode='repair_or_create', create_ack=True))
    assert data['create_ack'] is True
    assert data['account_mode'] == 'repair_or_create'
    assert 'account_was_present' in REMOTE_CREATE_ACCOUNT
    assert "if action == 'repair_or_create' and details.get('create_ack') is not True:" in REMOTE_CREATE_ACCOUNT
    assert "actual_mode = 'create_missing'" in REMOTE_CREATE_ACCOUNT
    assert "if account is None and (Path('/home/opm').exists()" in REMOTE_CREATE_ACCOUNT


def _verified_host(repo):
    store, config = repo
    host = store.add_host({'name':'repair-target', 'address':'192.0.2.15',
                           'ssh_user':'opm', 'key_path':str(config.managed_key)})
    store.verified(host['id'], True)
    return store,config,store.host(host['id'])


def test_missing_account_creation_records_history_and_validates_new_sudo(monkeypatch, repo):
    store, config, host = _verified_host(repo)
    ctl = Controller(config)
    act = store.begin_activity('ssh_repair', 'Repair SSH', ['host:'+host['id']])
    calls = []
    monkeypatch.setattr('opm.controller.accept_host_key',lambda *args:calls.append('fingerprint'))
    def bootstrap(*args,**kwargs):
        assert calls == ['fingerprint']
        assert kwargs == {'mode':'repair_or_create','replace_ack':False,'create_ack':True}
        assert args[-1] == 'new-private-password'
        calls.append('account created')
        return {'key_path':host['key_path'], 'mode':'create_missing'}
    monkeypatch.setattr('opm.controller.bootstrap_account',bootstrap)
    monkeypatch.setattr('opm.controller.test_connection',lambda *args:calls.append('ssh test'))
    def sudo(*args):
        assert args[-1] == 'new-private-password'
        calls.append('sudo test')
        return True
    monkeypatch.setattr('opm.controller.verify_sudo_password',sudo)
    credentials = {'admin_user':'admin','login_password':'admin-private-password',
                   'admin_sudo_password':'', 'opm_password':'',
                   'create_if_missing':True,'new_opm_password':'new-private-password'}
    asyncio.run(ctl.repair_ssh_background(act['id'],host['id'],'SHA256:approved',credentials))
    record = store.activity(act['id'])
    assert record['state'] == 'completed'
    assert record['result']['account_created'] is True
    assert record['result']['sudo_verified'] is True
    assert any('account created' in ev['message'] for ev in record['events'])
    assert 'private-password' not in str(record)
    assert calls == ['fingerprint','account created','ssh test','sudo test']
    assert credentials == {}


def test_existing_account_remains_preserved_with_create_if_missing(monkeypatch, repo):
    store, config, host = _verified_host(repo)
    ctl = Controller(config)
    act = store.begin_activity('ssh_repair','Repair SSH',['host:'+host['id']])
    monkeypatch.setattr('opm.controller.accept_host_key',lambda *args:None)
    monkeypatch.setattr('opm.controller.bootstrap_account',lambda *args,**kwargs:{
        'key_path':host['key_path'], 'mode':'repair_or_create'})
    monkeypatch.setattr('opm.controller.test_connection',lambda *args:None)
    def sudo(*args):
        assert args[-1] == 'old-account-password'
        return True
    monkeypatch.setattr('opm.controller.verify_sudo_password',sudo)
    creds = {'admin_user':'admin','login_password':'admin-pass','admin_sudo_password':'',
             'opm_password':'old-account-password','create_if_missing':True,
             'new_opm_password':'new-unused-password'}
    asyncio.run(ctl.repair_ssh_background(act['id'],host['id'],'SHA256:approved',creds))
    record=store.activity(act['id'])
    assert record['state']=='completed'
    assert record['result']['account_created'] is False
    assert 'preserved' in str(record['events'])


def test_failed_recovery_redacts_secrets_and_handles_boolean_approval(monkeypatch,repo):
    store, config, host = _verified_host(repo)
    ctl=Controller(config)
    act=store.begin_activity('ssh_repair','Repair SSH',['host:'+host['id']])
    monkeypatch.setattr('opm.controller.accept_host_key',lambda *args:None)
    def fail(*args,**kwargs):
        raise ValueError('Failure for secret-password')
    monkeypatch.setattr('opm.controller.bootstrap_account',fail)
    creds={'admin_user':'admin','login_password':'secret-password','admin_sudo_password':'',
           'opm_password':'', 'new_opm_password':'', 'create_if_missing':True}
    asyncio.run(ctl.repair_ssh_background(act['id'],host['id'],'SHA256:approved',creds))
    record=store.activity(act['id'])
    assert record['state']=='failed'
    assert 'secret-password' not in str(record)
    assert creds == {}


def test_no_retired_textual_runtime_or_cli_entrypoint():
    import tomllib
    project=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']
    assert not any('textual' in dep.lower() for dep in project['dependencies'])
    assert not (ROOT/'src/opm/ui.py').exists()
    assert 'add_parser("tui"' not in (ROOT/'src/opm/cli.py').read_text()
    assert 'from .ui import' not in (ROOT/'src/opm/cli.py').read_text()
    assert (ROOT/'OPMTUI.sh').exists()  # Whiptail installer retained
