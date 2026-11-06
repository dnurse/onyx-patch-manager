"""Regression checks for OPM v0.5.2 internal reverse proxy access.

No test changes the real Linux opm account, OPM web environment or systemd.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_web_tool():
    spec = importlib.util.spec_from_file_location('opm_web_access_test', ROOT / 'OPMWebAccess.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_https_proxy_settings_are_coherent():
    m = load_web_tool()
    data = m.settings_for('proxy', 'opm.example.internal', '192.168.50.10')
    assert data['OPM_WEB_BIND'] == '0.0.0.0:8765'
    assert data['OPM_WEB_ALLOWED_HOSTS'].split(',')[0] == 'opm.example.internal'
    assert data['OPM_WEB_CSRF_TRUSTED_ORIGINS'] == 'https://opm.example.internal'
    assert data['OPM_WEB_SECURE_COOKIES'] == '1'
    assert data['OPM_WEB_TRUST_PROXY'] == '1'
    assert data['OPM_WEB_TRUSTED_PROXY_IPS'] == '192.168.50.10'
    assert data['OPM_WEB_ACCESS_MODE'] == 'proxy'


def test_local_and_lan_modes_do_not_trust_forwarded_headers():
    m = load_web_tool()
    local = m.settings_for('localhost')
    lan = m.settings_for('lan', '192.168.50.20')
    assert local['OPM_WEB_BIND'] == '127.0.0.1:8765'
    assert lan['OPM_WEB_BIND'] == '0.0.0.0:8765'
    for config in (local, lan):
        assert config['OPM_WEB_TRUST_PROXY'] == '0'
        assert config['OPM_WEB_SECURE_COOKIES'] == '0'
        assert not config['OPM_WEB_TRUSTED_PROXY_IPS']


@pytest.mark.parametrize('host', [
    'https://opm.example.com', 'opm.example.com:443', '*.example.com',
    'something/else', 'foo\nOPM_WEB_SECURE_COOKIES=0', '', 'opm evil.com',
])
def test_reject_unsafe_proxy_hostnames(host):
    with pytest.raises(ValueError):
        load_web_tool().settings_for('proxy', host, '192.168.1.10')


@pytest.mark.parametrize('ip', ['0.0.0.0', '127.0.0.1', '8.8.8.8', '192.168.0.0/16', 'proxy.example', ''])
def test_refuse_untrusted_proxy_sources(ip):
    with pytest.raises(ValueError):
        load_web_tool().settings_for('proxy', 'opm.home.example', ip)


def test_configuration_updates_preserve_secret_and_existing_settings(tmp_path):
    m = load_web_tool()
    env = tmp_path / 'web.env'
    existing = 'OPM_WEB_SECRET_KEY=' + 's' * 72 + '\nOPM_WEB_TIME_ZONE=America/Toronto\nOPM_WEB_BIND=127.0.0.1:8765\n'
    env.write_text(existing)
    env.chmod(0o600)
    m.LOG_DIR = tmp_path / 'private-backups'
    settings = m.settings_for('proxy', 'opm.example.internal', '192.168.1.11')
    m.write_entries(env, settings)
    after = m.read_entries(env)
    assert after['OPM_WEB_SECRET_KEY'] == 's' * 72
    assert after['OPM_WEB_TIME_ZONE'] == 'America/Toronto'
    assert after['OPM_WEB_ACCESS_MODE'] == 'proxy'
    assert env.stat().st_mode & 0o777 == 0o600
    backups = list(m.LOG_DIR.iterdir())
    assert len(backups) == 1
    assert backups[0].read_text() == existing
    assert backups[0].stat().st_mode & 0o777 == 0o600


def test_reject_symlink_and_missing_secret(tmp_path):
    m = load_web_tool()
    m.LOG_DIR = tmp_path / 'private-backups'
    missing = tmp_path / 'missing.env'
    missing.write_text('OPM_WEB_BIND=127.0.0.1:8765\n')
    with pytest.raises(ValueError, match='secret'):
        m.write_entries(missing, m.settings_for('localhost'))
    link = tmp_path / 'link.env'
    link.symlink_to(missing)
    with pytest.raises(ValueError, match='symlink'):
        m.write_entries(link, m.settings_for('localhost'))


def test_middleware_only_trusts_concrete_proxy_peer(monkeypatch):
    django = types.ModuleType('django')
    conf = types.ModuleType('django.conf')
    http = types.ModuleType('django.http')
    conf.settings = SimpleNamespace(OPM_WEB_ACCESS_MODE='proxy',
                                     OPM_WEB_TRUSTED_PROXY_IPS='192.168.1.10')
    class Forbidden:
        def __init__(self, message):
            self.status_code = 403
            self.message = message
    http.HttpResponseForbidden = Forbidden
    django.conf = conf
    django.http = http
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, 'django', django)
        patch.setitem(sys.modules, 'django.conf', conf)
        patch.setitem(sys.modules, 'django.http', http)
        spec = importlib.util.spec_from_file_location('opm_web_access_middleware_test',
                          ROOT / 'src/opm/web/access.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        middleware = module.InternalWebAccessMiddleware(lambda req: SimpleNamespace(status_code=200))
        assert module.trusted_proxy_request('192.168.1.10', '192.168.1.10')
        assert not module.trusted_proxy_request('192.168.1.11', '192.168.1.10')
        assert middleware(SimpleNamespace(META={
            'REMOTE_ADDR':'192.168.1.11', 'HTTP_X_FORWARDED_PROTO':'https'})).status_code == 403
        assert middleware(SimpleNamespace(META={
            'REMOTE_ADDR':'192.168.1.10', 'HTTP_X_FORWARDED_PROTO':'http'})).status_code == 403
        assert middleware(SimpleNamespace(META={
            'REMOTE_ADDR':'192.168.1.10', 'HTTP_X_FORWARDED_PROTO':'https'})).status_code == 200
        local = {'REMOTE_ADDR':'127.0.0.1', 'HTTP_X_FORWARDED_PROTO':'https'}
        assert middleware(SimpleNamespace(META=local)).status_code == 200
        assert 'HTTP_X_FORWARDED_PROTO' not in local


def test_network_configurator_is_persistent_and_root_owned():
    install = (ROOT/'OPMInstall.sh').read_text()
    maintenance = (ROOT/'OPMMaintenance.sh').read_text()
    settings = (ROOT/'src/opm/web/settings.py').read_text()
    assert '--initial' in install and 'if [[ ! -e "$ENV_FILE" ]]' in install
    assert 'OPMWebAccess.py" /usr/local/share/opm-installer/OPMWebAccess.py' in install
    assert 'WEB_ACCESS_TOOL=/usr/local/share/opm-installer/OPMWebAccess.py' in maintenance
    assert '--web-access' in maintenance
    assert "'opm.web.access.InternalWebAccessMiddleware'" in settings
    assert 'SECURE_PROXY_SSL_HEADER' in settings
    for shell_file in ('OPMInstall.sh', 'OPMMaintenance.sh', 'OPMInstaller.sh'):
        subprocess.run(['bash','-n',str(ROOT/shell_file)],check=True)


def test_failed_reconfiguration_backup_restores_owner_and_bytes(tmp_path):
    module = load_web_tool()
    module.LOG_DIR = tmp_path / 'backups'
    env = tmp_path / 'web.env'
    prior = 'OPM_WEB_SECRET_KEY=' + 'z' * 80 + '\nOPM_WEB_BIND=127.0.0.1:8765\n'
    env.write_text(prior)
    env.chmod(0o600)
    backup = module.write_entries(env, module.settings_for('proxy',
        'opm.example.internal', '10.1.2.3'))
    assert backup is not None
    assert module.read_entries(env)['OPM_WEB_ACCESS_MODE'] == 'proxy'
    module.restore_backup(env, backup)
    assert env.read_text() == prior
    assert env.stat().st_mode & 0o777 == 0o600


def test_no_proxy_header_trust_when_not_in_proxy_mode(monkeypatch):
    django = types.ModuleType('django')
    conf = types.ModuleType('django.conf')
    http = types.ModuleType('django.http')
    conf.settings = SimpleNamespace(OPM_WEB_ACCESS_MODE='localhost',
                                     OPM_WEB_TRUSTED_PROXY_IPS='10.0.0.5')
    http.HttpResponseForbidden = lambda msg: SimpleNamespace(status_code=403)
    django.conf = conf
    django.http = http
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, 'django', django)
        patch.setitem(sys.modules, 'django.conf', conf)
        patch.setitem(sys.modules, 'django.http', http)
        spec = importlib.util.spec_from_file_location('opm_web_access_middleware_no_proxy',
                          ROOT / 'src/opm/web/access.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        app = module.InternalWebAccessMiddleware(lambda req: SimpleNamespace(status_code=200))
        assert app(SimpleNamespace(META={'REMOTE_ADDR':'192.168.1.42'})).status_code == 200
