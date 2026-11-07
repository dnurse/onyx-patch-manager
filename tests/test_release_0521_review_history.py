"""v0.5.21: browser contract and sanitized remote replacement activity audit."""
from pathlib import Path
import pytest
from opm.controller import Controller

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'src/opm/web'
PROFILE={'name':'migration-test','address':'192.0.2.99','ssh_port':22,
         'group_name':'Lab','protected':False}
FP='SHA256:VerifiedTestFingerprint'


def fake_enrollment(monkeypatch, config, *, fail=False, sudo=True):
    monkeypatch.setattr('opm.controller.probe_host_key', lambda h:[
        {'fingerprint':FP,'type':'ssh-ed25519','key':'192.0.2.99 ssh-ed25519 TEST'}])
    monkeypatch.setattr('opm.controller.accept_host_key', lambda *a: {})
    monkeypatch.setattr('opm.controller.ensure_managed_key', lambda c:{'key_path':str(c.managed_key)})
    def bootstrap(*args, **kwargs):
        if fail:
            raise ValueError('ACCOUNT_DELETE_FAILED')
        assert kwargs == {'mode':'replace','replace_ack':True}
        return {'key_path':str(config.managed_key)}
    monkeypatch.setattr('opm.controller.bootstrap_account', bootstrap)
    monkeypatch.setattr('opm.controller.test_connection', lambda *a: True)
    monkeypatch.setattr('opm.controller.verify_sudo_password', lambda *a: sudo)


def test_replacement_activity_records_remote_change_without_secrets(repo,monkeypatch):
    store,config=repo
    fake_enrollment(monkeypatch,config)
    activity=store.begin_activity('enrollment','Configure migration-test', ['candidate:192.0.2.99:22'])
    secret='EXAMPLE-PRIVATE-ACCOUNT-PASSWORD'
    data={'host':PROFILE,'fingerprint':FP,'admin_user':'administrator',
          'login_password':'EXAMPLE-SSH-PASSWORD','opm_password':secret,
          'account_mode':'replace','replace_ack':True,'_activity_id':activity['id']}
    result=Controller(config).enroll_host(data)
    assert result['verified']
    history=store.activity(activity['id'])
    events='\n'.join(e['message'] for e in history['events'])
    assert 'Existing remote opm account deleted and recreated' in events
    assert secret not in events and data['login_password'] not in events
    assert secret.encode() not in config.database.read_bytes()


def test_replacement_failure_never_logs_successful_deletion(repo,monkeypatch):
    store,config=repo
    fake_enrollment(monkeypatch,config,fail=True)
    activity=store.begin_activity('enrollment','Configure migration-test', ['candidate:192.0.2.99:22'])
    data={'host':PROFILE,'fingerprint':FP,'admin_user':'administrator',
          'login_password':'EXAMPLE-SSH-PASSWORD','opm_password':'STRONG-REPLACEMENT-PASSWORD',
          'account_mode':'replace','replace_ack':True,'_activity_id':activity['id']}
    with pytest.raises(ValueError,match='ACCOUNT_DELETE_FAILED'):
        Controller(config).enroll_host(data)
    events='\n'.join(e['message'] for e in store.activity(activity['id'])['events'])
    assert 'deleted and recreated' not in events


def test_account_action_is_audited_even_if_sudo_check_fails(repo,monkeypatch):
    store,config=repo
    fake_enrollment(monkeypatch,config,sudo=False)
    activity=store.begin_activity('enrollment','Configure migration-test', ['candidate:192.0.2.99:22'])
    data={'host':PROFILE,'fingerprint':FP,'admin_user':'administrator',
          'login_password':'EXAMPLE-SSH-PASSWORD','opm_password':'STRONG-REPLACEMENT-PASSWORD',
          'account_mode':'replace','replace_ack':True,'_activity_id':activity['id']}
    result=Controller(config).enroll_host(data)
    assert not result['verified']
    events='\n'.join(e['message'] for e in store.activity(activity['id'])['events'])
    assert 'deleted and recreated' in events


def test_review_is_full_width_and_never_requires_cached_credentials():
    patch=(WEB/'templates/web/patch.html').read_text()
    scan=(WEB/'templates/web/scan_auth.html').read_text()
    review=(WEB/'templates/web/review.html').read_text()
    css=(WEB/'static/web/styles.css').read_text()
    views=(WEB/'views.py').read_text()
    assert '>Authorize sudo</a>' not in patch
    assert '>Authorize sudo only</button>' not in scan
    assert '>Scan all</a>' in patch
    assert 'patch-review-panel' in review
    assert '.patch-review-panel {max-width:none;width:100%' in css
    assert 'if not t.sudo_cached_seconds' in review
    assert 'cached.get(target[\'host_id\'], 0)' in views
    assert 'Confirm and patch' in review
