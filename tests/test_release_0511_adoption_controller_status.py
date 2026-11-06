"""v0.5.11 regression tests: real controller heartbeat and explicit account modes.

Network and account-mutating operations are mocked; live SSH is a separate
pre-GM acceptance test.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from opm.controller import Controller
from opm.onboarding import _payload, REMOTE_CREATE_ACCOUNT, OnboardingError

ROOT = Path(__file__).resolve().parents[1]
PROFILE = {'name': 'migration-node', 'address': '192.0.2.20', 'ssh_port': 22,
           'group_name': 'Testing', 'protected': False}
FP = 'SHA256:FakeApprovedFingerprint'


def params(**changes):
    p = {'host': PROFILE, 'fingerprint': FP, 'admin_user': 'admin',
         'login_password': 'TEMP-ADMIN-CREDENTIAL-SECRET',
         'admin_sudo_password': '', 'opm_password': 'ORIGINAL-OPM-PASSWORD'}
    p.update(changes)
    return p


def fake_remote(monkeypatch, mode):
    captured = {}
    monkeypatch.setattr('opm.controller.probe_host_key', lambda host: [
        {'fingerprint': FP, 'type': 'ssh-ed25519', 'key': '192.0.2.20 ssh-ed25519 TEST'}])
    monkeypatch.setattr('opm.controller.accept_host_key', lambda host, config, fingerprint: {})
    monkeypatch.setattr('opm.controller.ensure_managed_key', lambda config: {
        'key_path': str(config.managed_key), 'public_key': 'ssh-ed25519 AAAATEST'})
    def bootstrap(host, config, admin_user, login_pw, sudo_pw, opm_pw, **kwargs):
        captured.update({'admin_user': admin_user, 'opm_password': opm_pw, **kwargs})
        return {'key_path': str(config.managed_key)}
    monkeypatch.setattr('opm.controller.bootstrap_account', bootstrap)
    monkeypatch.setattr('opm.controller.test_connection', lambda host, config: {'ok': True})
    monkeypatch.setattr('opm.controller.verify_sudo_password', lambda host, config, password: True)
    return captured


def test_ping_is_real_controller_rpc_and_not_just_rendered_on_dashboard(monkeypatch):
    import opm.web.context as context
    called = []
    async def healthy(method):
        called.append(method)
        return {'status': 'ready'}
    monkeypatch.setattr(context, 'rpc', healthy)
    assert context.app_version(SimpleNamespace(user=SimpleNamespace(is_authenticated=True)))['controller']['status'] == 'ready'
    assert called == ['ping']
    async def offline(method):
        raise OSError('socket unavailable')
    monkeypatch.setattr(context, 'rpc', offline)
    assert context.app_version(SimpleNamespace(user=SimpleNamespace(is_authenticated=True)))['controller']['status'] == 'offline'
    called.clear()
    assert 'controller' not in context.app_version(SimpleNamespace(user=SimpleNamespace(is_authenticated=False)))
    assert called == []
    base = (ROOT/'src/opm/web/templates/web/base.html').read_text()
    routes = (ROOT/'src/opm/web/urls.py').read_text()
    client = (ROOT/'src/opm/web/static/web/controller_health.js').read_text()
    assert 'opm-controller-indicator' in base
    assert 'controller_health' in base and 'controller_health' in routes
    assert '20000' in client and 'status === \'ready\'' in client


def test_mode_payload_requires_valid_password_and_destructive_ack():
    from json import loads
    assert loads(_payload('ssh-ed25519 AAAA demo', 'old', mode='reuse'))['account_mode'] == 'reuse'
    with pytest.raises(OnboardingError, match='at least 12'):
        _payload('ssh-ed25519 AAAA demo', 'old', mode='replace', replace_ack=True)
    with pytest.raises(OnboardingError, match='Explicit approval'):
        _payload('ssh-ed25519 AAAA demo', 'new-password-12345', mode='replace')
    accepted = loads(_payload('ssh-ed25519 AAAA demo', 'new-password-12345',
                              mode='replace', replace_ack=True))
    assert accepted['replace_ack'] is True
    compile(REMOTE_CREATE_ACCOUNT, '<trusted-remote-account-script>', 'exec')
    assert REMOTE_CREATE_ACCOUNT.index('verify_existing_password(password)') < REMOTE_CREATE_ACCOUNT.index('write_authorized_key(account, key, reuse=True)')
    assert "if details.get('replace_ack') is not True:" in REMOTE_CREATE_ACCOUNT
    assert "['/usr/sbin/userdel', '-r', 'opm']" in REMOTE_CREATE_ACCOUNT
    assert "['/usr/sbin/runuser', '-u', 'opm', '--', 'sudo']" in REMOTE_CREATE_ACCOUNT


def test_reuse_existing_remote_account_with_old_password(repo, monkeypatch):
    store, config = repo
    capture = fake_remote(monkeypatch, 'reuse')
    result = Controller(config).enroll_host(params(account_mode='reuse', opm_password='old'))
    assert result['verified'] is True
    assert capture['mode'] == 'reuse'
    assert capture['opm_password'] == 'old'
    assert store.host(result['host_id'])['verified'] == 1
    assert b'TEMP-ADMIN-CREDENTIAL-SECRET' not in config.database.read_bytes()
    assert b'old' not in config.database.read_bytes()


def test_replace_requires_approval_before_remote_side_effects(repo, monkeypatch):
    store, config = repo
    capture = fake_remote(monkeypatch, 'replace')
    with pytest.raises(ValueError, match='Confirm deletion'):
        Controller(config).enroll_host(params(account_mode='replace', replace_ack=False))
    assert store.hosts() == []
    assert capture == {}
    result = Controller(config).enroll_host(params(account_mode='replace', replace_ack=True))
    assert result['verified'] is True
    assert capture['mode'] == 'replace'
    assert capture['replace_ack'] is True


def test_controller_account_probe_is_allowed_and_read_only(repo, monkeypatch):
    _, config = repo
    capture = {}
    def probe(host, user, password, fp):
        capture.update({'user': user, 'pw': password, 'fingerprint': fp, 'address': host['address']})
        return {'exists': True}
    monkeypatch.setattr('opm.controller.probe_managed_account', probe)
    async def check():
        ctl = Controller(config)
        with pytest.raises(ValueError, match='Approve the SSH host fingerprint'):
            await ctl.dispatch('hosts.account_probe', {'address': PROFILE['address'], 'ssh_port': 22})
        assert capture == {}
        status = await ctl.dispatch('hosts.account_probe', {'address': PROFILE['address'],
            'ssh_port': 22, 'fingerprint': FP, 'admin_user': 'admin', 'login_password': 'SENSITIVE'})
        assert status == {'exists': True}
    asyncio.run(check())
    assert capture['user'] == 'admin'
    assert capture['fingerprint'] == FP


def test_account_wizard_contains_explicit_reuse_and_delete_choices():
    page = (ROOT/'src/opm/web/templates/web/enroll.html').read_text()
    js = (ROOT/'src/opm/web/static/web/enroll.js').read_text()
    urls = (ROOT/'src/opm/web/urls.py').read_text()
    assert 'Reuse existing opm account' in page
    assert 'Delete and recreate the opm account' in page
    assert 'enrollment_account_probe' in page and 'enrollment_account_probe' in urls
    assert 'trustAck.value' in js
    assert "if (accountMode.value === 'replace' && !replaceAck.checked)" in js


def test_account_probe_pins_ed25519_fingerprint_before_sending_password(monkeypatch):
    """An impostor host cannot obtain the temporary administrator password."""
    import base64
    import hashlib
    import sys
    import types
    from opm.onboarding import probe_managed_account
    key_bytes = b'fake-ssh-ed25519-host-key-for-test'
    expected = 'SHA256:' + base64.b64encode(hashlib.sha256(key_bytes).digest()).decode().rstrip('=')
    calls = {'authorized': 0}

    class Key:
        def asbytes(self): return key_bytes
        def get_name(self): return 'ssh-ed25519'

    class Channel:
        def recv_exit_status(self): return 0

    class Stdout:
        channel = Channel()
        def read(self, *_): return b'1001\n'

    class Client:
        def set_missing_host_key_policy(self, policy): self.policy = policy
        def connect(self, **kwargs):
            self.policy.missing_host_key(self, kwargs['hostname'], Key())
            calls['authorized'] += 1
        def exec_command(self, command, **kwargs):
            assert command == 'id -u opm'
            return None, Stdout(), None
        def close(self): pass

    fake = types.SimpleNamespace(
        SSHClient=Client, MissingHostKeyPolicy=object,
        AuthenticationException=type('AuthError', (Exception,), {}),
        BadHostKeyException=type('BadHostKey', (Exception,), {}),
        SSHException=type('SSHError', (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, 'paramiko', fake)
    host = {'address': PROFILE['address'], 'ssh_port': 22}
    with pytest.raises(OnboardingError, match='fingerprint changed'):
        probe_managed_account(host, 'admin', 'SENSITIVE-SSH-PASSWORD', 'SHA256:WRONG')
    assert calls['authorized'] == 0
    result = probe_managed_account(host, 'admin', 'SENSITIVE-SSH-PASSWORD', expected)
    assert result == {'exists': True}
    assert calls['authorized'] == 1
