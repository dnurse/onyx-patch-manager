"""ICMP dashboard checks are read-only and independent of SSH and patch scans."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opm.controller import Controller
from opm.remote import probe_icmp_reachability


def test_icmp_reply_means_up():
    """An exit code of zero is the only positive response to an ICMP probe."""
    with patch('opm.remote.subprocess.run', return_value=SimpleNamespace(returncode=0)) as command:
        result = probe_icmp_reachability({'address': '192.0.2.20', 'ssh_port': 2222})
    assert result['state'] == 'up'
    assert result['checked_at'] > 0
    args = command.call_args.args[0]
    assert args == ['ping', '-n', '-c', '1', '-W', '2', '192.0.2.20']
    assert command.call_args.kwargs['timeout'] == 4.0
    assert command.call_args.kwargs['check'] is False
    assert '2222' not in args, 'ICMP must not depend on the SSH port'


def test_icmp_no_reply_means_down():
    with patch('opm.remote.subprocess.run', return_value=SimpleNamespace(returncode=1)):
        assert probe_icmp_reachability({'address': '192.0.2.20'})['state'] == 'down'


def test_missing_ping_or_no_icmp_permission_means_unknown():
    for cause in (FileNotFoundError, PermissionError):
        with patch('opm.remote.subprocess.run', side_effect=cause):
            assert probe_icmp_reachability({'address': '192.0.2.20'})['state'] == 'unknown'
    with patch('opm.remote.subprocess.run', return_value=SimpleNamespace(returncode=2)):
        assert probe_icmp_reachability({'address': '192.0.2.20'})['state'] == 'unknown'


def test_controller_caches_ping_and_does_not_scan(repo, host_data, monkeypatch):
    store, config = repo
    verified = store.add_host(host_data)
    store.verified(verified['id'], True)
    unverified = store.add_host({**host_data, 'name': 'unconfigured-vm', 'address': '192.0.2.25'})
    calls = []

    def fake_probe(host):
        calls.append(host['id'])
        return {'state': 'up', 'checked_at': 100, 'detail': 'ICMP replied'}

    monkeypatch.setattr('opm.controller.probe_icmp_reachability', fake_probe)

    async def check():
        controller = Controller(config)
        first = await controller.dispatch('hosts.reachability', {})
        second = await controller.dispatch('hosts.reachability', {})
        assert first == second
        assert {r['host_id']: r['state'] for r in first} == {
            verified['id']: 'up', unverified['id']: 'up'}

    asyncio.run(check())
    assert calls == [verified['id'], unverified['id']]
    assert store.latest_scans() == [], 'Dashboard ping must not create full scan results'


def test_dashboard_contract_and_authentication():
    root = Path(__file__).resolve().parents[1] / 'src/opm/web'
    views = (root / 'views.py').read_text()
    dashboard = (root / 'templates/web/dashboard.html').read_text()
    detail = (root / 'templates/web/host_detail.html').read_text()
    urls = (root / 'urls.py').read_text()
    script = (root / 'static/web/dashboard.js').read_text()
    assert '@login_required\n@require_GET\n@never_cache\ndef dashboard_connectivity' in views
    assert "name='dashboard_connectivity'" in urls
    assert 'data-connection-monitor' in dashboard
    assert 'data-host-connection' in dashboard
    assert 'Last scan' in dashboard
    assert 'Connection' in dashboard
    assert "'hosts.reachability'" in views
    assert 'intervalMs = 120000' in script
    assert 'document.visibilityState' in script
    assert "up: 'Up', down: 'Down', unknown: 'Unknown'" in script
    assert 'SSH and account setup' not in detail
    assert 'Test SSH' in detail
    assert 'Host overview' in detail


def test_installer_includes_icmp_tools_and_quiet_stage_progress():
    root = Path(__file__).resolve().parents[1]
    installer = (root / 'OPMInstall.sh').read_text()
    assert 'iputils-ping' in installer
    assert 'render_progress()' in installer
    assert 'TOTAL_STEPS=15' in installer
    assert 'COMPLETED * 100 / TOTAL_STEPS' in installer
    assert 'LOG_DIR=/var/log/opm-installer' in installer
    assert 'run_logged' in installer
    assert 'Active or queued patch jobs' in installer
    assert 'WEB_BIND="127.0.0.1:8765"' in installer
