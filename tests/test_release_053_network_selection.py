"""Regression checks for first-install access choice and browser reconfiguration.

No test changes actual systemd units, Linux account state, or production config.
"""
from pathlib import Path
import importlib.util
import asyncio
import subprocess
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def tool():
    spec = importlib.util.spec_from_file_location('access053', ROOT / 'OPMWebAccess.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('answer,expected', [('', 'lan'), ('1', 'lan'), ('2', 'proxy'), ('3', 'localhost')])
def test_first_install_explicit_choice_default_lan(answer, expected, monkeypatch):
    m = tool()
    monkeypatch.setattr(m, 'detect_lan_ip', lambda: '192.168.45.10')
    values = iter([answer, 'opm.example.internal', '192.168.45.1'] if answer == '2' else
                  [answer, '192.168.45.10'] if answer in ('', '1') else [answer])
    monkeypatch.setattr('builtins.input', lambda _: next(values))
    result = m.interactive_settings()
    assert result['OPM_WEB_ACCESS_MODE'] == expected
    if expected == 'lan':
        assert result['OPM_WEB_BIND'] == '0.0.0.0:8765'
        assert result['OPM_WEB_ALLOWED_HOSTS'].startswith('192.168.45.10,')


def test_safe_noninteractive_lan_default(monkeypatch, capsys, tmp_path):
    m = tool()
    monkeypatch.setattr(m, 'ENV_PATH', tmp_path / 'web.env')
    m.ENV_PATH.write_text('OPM_WEB_SECRET_KEY=' + 'x' * 70 + '\n')
    monkeypatch.setattr(m, 'detect_lan_ip', lambda: '192.168.45.20')
    monkeypatch.setattr(m, 'LOG_DIR', tmp_path / 'private-logs')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(m.sys.stdin, 'isatty', lambda: False)
    assert m.main(['--initial']) == 0
    assert m.read_entries(m.ENV_PATH)['OPM_WEB_ACCESS_MODE'] == 'lan'


def test_noninteractive_missing_ip_preserves_localhost(monkeypatch, tmp_path):
    m = tool()
    monkeypatch.setattr(m, 'ENV_PATH', tmp_path / 'web.env')
    m.ENV_PATH.write_text('OPM_WEB_SECRET_KEY=' + 'x' * 70 + '\nOPM_WEB_BIND=127.0.0.1:8765\n')
    monkeypatch.setattr(m, 'detect_lan_ip', lambda: '')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(m.sys.stdin, 'isatty', lambda: False)
    assert m.main(['--initial']) == 0
    assert m.read_entries(m.ENV_PATH)['OPM_WEB_BIND'] == '127.0.0.1:8765'


def test_readonly_server_health_restarts_without_root(monkeypatch):
    m = tool()
    monkeypatch.setattr(m.os, 'geteuid', lambda: 1288)
    monkeypatch.setattr(m.pwd, 'getpwnam', lambda _: type('User', (), {'pw_uid': 1288})())
    calls = []
    monkeypatch.setattr(m.subprocess, 'check_output', lambda *args, **kwargs: '1288')
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(m.subprocess, 'run', run)
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(m, 'urlopen', lambda *args, **kwargs: Resp())
    m.restart_web()
    assert calls[0][:2] == ['systemctl', '--user']
    assert calls[0][-2:] == ['restart', 'opm-web.service']


def test_web_access_settings_have_admin_post_and_activity():
    views = (ROOT / 'src/opm/web/views.py').read_text()
    controller = (ROOT / 'src/opm/controller.py').read_text()
    template = (ROOT / 'src/opm/web/templates/web/settings.html').read_text()
    store = (ROOT / 'src/opm/store.py').read_text()
    assert "request.POST.get('action') == 'update_web_access'" in views
    assert "request.user.is_superuser" in views
    assert "_call('web.access.configure'" in views
    assert "if method == \"web.access.configure\"" in controller
    assert "'--controller-apply'" in controller
    assert 'web_access' in store
    assert 'name="action" value="update_web_access"' in template
    assert 'csrf_token' in template


def test_installer_retains_first_run_prompt_and_upgrade_config():
    script = (ROOT / 'OPMInstall.sh').read_text()
    assert 'if [[ ! -e "$ENV_FILE" ]]; then' in script
    assert 'OPMWebAccess.py" --initial' in script
    assert 'OPM_WEB_SECRET_KEY' in script


@pytest.mark.parametrize('address', ['localhost', '127.0.0.1', '8.8.8.8', 'opm.home', '0.0.0.0'])
def test_lan_mode_never_accepts_public_or_loopback_ip(address):
    with pytest.raises(ValueError):
        tool().settings_for('lan', address)
