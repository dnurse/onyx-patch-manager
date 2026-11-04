"""Integrated server enrollment: mocked SSH with a real temporary SQLite DB.

These tests prove that an identity failure cannot create a server or send
credentials, while successful enrollment persists only nonsecret host data.
"""
from pathlib import Path
import pytest
from opm.controller import Controller


PROFILE = {"name": "test-vm", "address": "192.0.2.88", "ssh_port": 22,
           "group_name": "Test Servers", "protected": True}
FINGERPRINT = "SHA256:ExampleForTestingOnly"


def parameters(**overrides):
    data = {"host": PROFILE, "fingerprint": FINGERPRINT,
            "admin_user": "admin", "login_password": "TEMPORARY-SECRET",
            "admin_sudo_password": "", "opm_password": "TEST-OPM-PASSWORD-123"}
    data.update(overrides)
    return data


def mock_remote(monkeypatch, *, fingerprint=FINGERPRINT, sudo=True):
    calls = {}
    monkeypatch.setattr('opm.controller.probe_host_key', lambda host: [
        {"fingerprint": fingerprint, "type": "ssh-ed25519", "key": "192.0.2.88 ssh-ed25519 TEST"}])
    monkeypatch.setattr('opm.controller.ensure_managed_key', lambda config: {
        "key_path": str(config.managed_key), "public_key": "ssh-ed25519 TEST"})
    monkeypatch.setattr('opm.controller.accept_host_key', lambda host, config, value: {
        "fingerprint": value})

    def provision(host, config, admin_user, login_password, admin_sudo_password, opm_password):
        calls.update({"admin_user": admin_user, "login_password": login_password,
                      "admin_sudo_password": admin_sudo_password, "opm_password": opm_password})
        return {"key_path": str(config.managed_key), "account": "opm"}

    monkeypatch.setattr('opm.controller.bootstrap_account', provision)
    monkeypatch.setattr('opm.controller.test_connection', lambda host, config: {"connected": True})
    monkeypatch.setattr('opm.controller.verify_sudo_password', lambda host, config, password: sudo)
    return calls


def test_successful_enrollment_persists_only_nonsecret_host_data(repo, monkeypatch):
    store, config = repo
    calls = mock_remote(monkeypatch)
    result = Controller(config).enroll_host(parameters())
    assert result['verified'] is True
    assert len(store.hosts()) == 1
    assert store.host(result['host_id'])['ssh_user'] == 'opm'
    assert store.host(result['host_id'])['verified'] == 1
    assert calls['admin_sudo_password'] == 'TEMPORARY-SECRET', 'Blank admin sudo should reuse SSH password'
    database = config.database.read_bytes()
    for password in ('TEMPORARY-SECRET', 'TEST-OPM-PASSWORD-123'):
        assert password.encode() not in database


def test_reject_fingerprint_change_without_registering_or_provisioning(repo, monkeypatch):
    store, config = repo
    calls = mock_remote(monkeypatch, fingerprint='SHA256:ChangedFingerprint')
    with pytest.raises(ValueError, match='fingerprint changed'):
        Controller(config).enroll_host(parameters())
    assert store.hosts() == []
    assert calls == {}


def test_invalid_password_is_rejected_before_creating_host(repo, monkeypatch):
    store, config = repo
    calls = mock_remote(monkeypatch)
    with pytest.raises(ValueError, match='12 or more'):
        Controller(config).enroll_host(parameters(opm_password='short'))
    assert store.hosts() == []
    assert calls == {}


def test_existing_unverified_host_can_finish_setup(repo, monkeypatch):
    store, config = repo
    old = store.add_host({**PROFILE, 'ssh_user':'opm'})
    mock_remote(monkeypatch)
    result = Controller(config).enroll_host(parameters(existing_id=old['id']))
    assert result['host_id'] == old['id']
    assert result['verified']
    assert len(store.hosts()) == 1


def test_existing_verified_host_cannot_be_reprovisioned(repo, monkeypatch):
    store, config = repo
    old = store.add_host({**PROFILE, 'ssh_user':'opm'})
    store.verified(old['id'], True)
    calls = mock_remote(monkeypatch)
    with pytest.raises(ValueError, match='already verified'):
        Controller(config).enroll_host(parameters(existing_id=old['id']))
    assert calls == {}


def test_sudo_failure_reports_partial_state_without_falsely_verifying(repo, monkeypatch):
    store, config = repo
    mock_remote(monkeypatch, sudo=False)
    result = Controller(config).enroll_host(parameters())
    assert result['verified'] is False
    assert store.host(result['host_id'])['verified'] == 0
    assert 'sudo password' in result['warning']


def test_integrated_wizard_and_backend_routes_are_present():
    root = Path(__file__).resolve().parents[1]
    template = (root/'src/opm/web/templates/web/enroll.html').read_text()
    for label in ('Administrator login','OPM account','Setup and test','Set up and test server'):
        assert label in template
    routes = (root/'src/opm/web/urls.py').read_text()
    assert "enrollment_fingerprint" in routes
    assert "server_enroll" in routes
    page = (root/'src/opm/web/templates/web/host_detail.html').read_text()
    assert 'Finish setup' in page
    assert 'SSH and account setup' not in page
    assert 'Host overview' in page
    assert 'Test SSH' in page


def test_unverified_server_cannot_be_scanned_before_setup(repo):
    import asyncio
    store, config = repo
    host = store.add_host({**PROFILE, 'ssh_user':'opm'})

    async def check():
        controller = Controller(config)
        with pytest.raises(ValueError, match='Complete the SSH setup wizard'):
            await controller.dispatch('scans.start', {'id': host['id']})
        assert await controller.dispatch('scans.start', {}) == {'scheduled':0}

    asyncio.run(check())
