"""v0.5.19: safe account replacement, read-only authorization and mobile status."""
from pathlib import Path
import asyncio
import io
import json
import sys
from types import SimpleNamespace
import pytest
from opm.controller import Controller
from opm.onboarding import REMOTE_CREATE_ACCOUNT

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'


def test_unverified_setup_is_prominent_in_both_server_lists():
    dashboard = (WEB/'templates/web/dashboard.html').read_text()
    servers = (WEB/'templates/web/servers.html').read_text()
    detail = (WEB/'templates/web/host_detail.html').read_text()
    css = (WEB/'static/web/styles.css').read_text()
    assert 'class="setup-incomplete"' not in dashboard
    assert 'class="setup-incomplete"' in servers
    assert 'if not h.verified' in dashboard
    assert 'Setup required' in dashboard and 'Setup incomplete' in servers
    assert 'table-action setup-action' in servers
    assert 'btn setup-action' in detail
    assert '.status.setup-needed' in css and '.data-table tr.setup-incomplete' in css


def test_mobile_connection_status_alignment_is_right_side():
    css = (WEB/'static/web/styles.css').read_text()
    tail = css.split('/* v0.5.19:',1)[1]
    assert '@media(max-width:680px)' in tail
    assert 'margin-left:auto;' in tail and 'margin-right:0;' in tail
    assert 'align-self:flex-end;' in tail


def test_sudo_only_authorization_checks_real_sudo_without_scanning(repo, host_data, monkeypatch):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode':'password'})
    store.verified(host['id'], True)
    controller = Controller(config)
    scope = 'f'*64
    secret = 'SECRET-TEST-ONLY-12345'
    checked = []
    def verify(target, configuration, password):
        checked.append((target['id'], password))
        return password == secret
    monkeypatch.setattr('opm.controller.verify_sudo_password', verify)
    monkeypatch.setattr('opm.controller.inspect_host', lambda *a: pytest.fail('Must not scan APT'))
    async def run():
        with pytest.raises(ValueError, match='rejected'):
            await controller.dispatch('credentials.authorize', {'credential_scope':scope,
                'sudo_passwords':{host['id']:'wrong'}})
        assert controller._cached_password(scope, host['id']) is None
        response = await controller.dispatch('credentials.authorize', {'credential_scope':scope,
            'sudo_passwords':{host['id']:secret}})
        assert response['authorized'] == 1
        assert controller._cached_password(scope, host['id']) == secret
        assert controller._cached_password('a'*64,host['id']) is None
        with pytest.raises(ValueError, match='unverified'):
            await controller.dispatch('credentials.authorize', {'credential_scope':scope,
                'sudo_passwords':{'not-a-server':secret}})
    asyncio.run(run())
    assert checked == [(host['id'], 'wrong'), (host['id'],secret)]
    assert secret.encode() not in config.database.read_bytes()


def test_remote_replacement_stops_linger_without_force_or_home_removal(monkeypatch):
    import pathlib
    import pwd
    import subprocess
    user = SimpleNamespace(pw_uid=1001, pw_dir='/home/opm')
    events = []
    original_is_file = pathlib.Path.is_file
    monkeypatch.setattr(pathlib.Path, 'is_file', lambda self: str(self)=='/usr/bin/loginctl' or original_is_file(self))
    monkeypatch.setattr(pwd, 'getpwnam', lambda user_name: user)
    def fake_run(argv, **kwargs):
        events.append(argv)
        return SimpleNamespace(returncode=1 if argv[0]=='/usr/sbin/userdel' else 0)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'account':'opm','account_mode':'replace',
        'public_key':'ssh-ed25519 TESTKEY', 'sudo_password':'password-for-test-12345','replace_ack':True})+'\n'))
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', output)
    with pytest.raises(SystemExit) as e:
        exec(compile(REMOTE_CREATE_ACCOUNT,'<remote>', 'exec'), {'__name__':'__main__'})
    assert e.value.code == 1
    assert json.loads(output.getvalue())['error_code']=='ACCOUNT_DELETE_FAILED'
    assert events[0]==['/usr/bin/loginctl','disable-linger','opm']
    assert events[1]==['/usr/bin/loginctl','terminate-user','opm']
    assert ['/usr/sbin/userdel','-r','opm'] in events
    assert all('-f' not in argv for argv in events)
    assert not any('useradd' in argv[0] for argv in events)


def test_scan_authorization_only_offers_scan_and_keeps_credential_api_compatible():
    page=(WEB/'templates/web/scan_auth.html').read_text()
    patch=(WEB/'templates/web/patch.html').read_text()
    views=(WEB/'views.py').read_text()
    controller=(ROOT/'src/opm/controller.py').read_text()
    assert 'Authorize sudo only' not in page
    assert '>Authorize sudo</a>' not in patch
    assert 'Start scan' in page and 'Scan all' in patch
    assert "request.POST.get('action') == 'authorize'" in views
    assert "'credentials.authorize'" in views
    assert 'if method == "credentials.authorize"' in controller
    assert "await asyncio.to_thread(verify_sudo_password" in controller
