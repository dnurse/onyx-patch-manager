"""Regression checks for OPM 0.4.12 post-patch refresh and UI fixes.

No real SSH, sudo, package or installer operations are performed by tests.
"""
from __future__ import annotations

from pathlib import Path

from opm.remote import RemoteError
from opm.worker import _refresh_host_after_patch

ROOT = Path(__file__).resolve().parents[1]


def _sample_host(repo, host_data):
    store, config = repo
    host = store.add_host({**host_data, "sudo_mode": "password"})
    store.verified(host["id"])
    old_id = store.begin_scan(host["id"])
    store.finish_scan(old_id, {"os": {
        "family": "debian", "metadata": "fresh", "package_query_ok": True,
        "packages": [{"name": "curl", "current": "1", "candidate": "2"}],
    }})
    return store, config, host, old_id


def test_successful_postpatch_refresh_replaces_old_dashboard_scan(repo, host_data, monkeypatch):
    store, config, host, old_id = _sample_host(repo, host_data)
    seen = []

    def fake_inspect(actual_host, actual_config, password):
        seen.append((actual_host["id"], actual_config, password))
        return {"os": {"family": "debian", "package_query_ok": True,
                       "metadata": "fresh", "packages": []},
                "engines": {"docker": {"available": False}}}

    monkeypatch.setattr("opm.worker.inspect_host", fake_inspect)
    ok = _refresh_host_after_patch(store, "mock-job", host, config, "SECRET-X")
    assert ok
    result = store.last_scan(host["id"])
    assert result["id"] != old_id
    assert result["state"] == "completed"
    assert result["result"]["os"]["packages"] == []
    assert seen == [(host["id"], config, "SECRET-X")]
    messages = [e["message"] for e in store.events("mock-job")]
    assert any("0 OS update(s) remain" in message for message in messages)
    assert all("SECRET-X" not in m for m in messages)


def test_failed_refresh_is_a_failed_scan_and_does_not_reuse_old_count(repo, host_data, monkeypatch):
    store, config, host, old_id = _sample_host(repo, host_data)
    monkeypatch.setattr("opm.worker.inspect_host", lambda *args: (_ for _ in ()).throw(
        RemoteError("refresh failed using SECRET-X")))
    ok = _refresh_host_after_patch(store, "mock-job", host, config, "SECRET-X")
    assert not ok
    new_scan = store.last_scan(host["id"])
    assert new_scan["id"] != old_id
    assert new_scan["state"] == "failed"
    assert new_scan["result"] == {}
    assert "SECRET-X" not in new_scan["error"]
    assert any(e["level"] == "warning" for e in store.events("mock-job"))


def test_partial_refresh_preserves_unverified_metadata(repo, host_data, monkeypatch):
    store, config, host, old_id = _sample_host(repo, host_data)
    monkeypatch.setattr("opm.worker.inspect_host", lambda *args: {
        "os": {"metadata": "cached", "package_query_ok": True,
               "packages": [{"name": "openssl", "current": "1", "candidate": "2"}],
               "apt_error": "refresh failed"}})
    assert not _refresh_host_after_patch(store, "mock-job", host, config, "secret")
    scan = store.last_scan(host["id"])
    assert scan["state"] == "completed"
    assert scan["result"]["os"]["metadata"] == "cached"
    assert any(e["level"] == "warning" for e in store.events("mock-job"))


def test_requested_visual_alignment_contract():
    css = (ROOT / "src/opm/web/static/web/styles.css").read_text()
    settings = (ROOT / "src/opm/web/templates/web/settings.html").read_text()
    assert 'settings-overview-grid' in settings
    assert '[data-connection-monitor] .data-table th:nth-child(2)' in css
    assert '.settings-timezone-form select' in css
    assert 'background-position:right 18px center' in css
    assert '.history-actions .history-card-footer .btn' in css
    assert '.history-actions .history-card-footer .confirm-inline' in css


def test_installer_whiptail_maintenance_and_completion_contract():
    script = (ROOT / "OPMMaintenance.sh").read_text()
    tui = (ROOT / "OPMTUI.sh").read_text()
    assert 'opm_ui_bootstrap' in script
    assert 'opm_ui_box --title' in script
    assert '--menu' in script
    assert 'Upgrade / Install OPM' in script
    assert '# A completed installation ends the utility; do not redisplay menu.' in script
    assert 'NEWT_COLORS' in tui
    assert '--gauge' in tui


def test_patch_worker_calls_refresh_only_after_confirmed_ansible_success(repo, host_data, monkeypatch):
    """Exercise the actual worker control flow with a mocked Ansible result."""
    import sys
    import types
    from opm.worker import run_job

    store, config = repo
    host = store.add_host({**host_data, "sudo_mode": "passwordless"})
    store.verified(host["id"])
    initial = store.begin_scan(host["id"])
    packages = [{"name": "curl", "current": "1", "candidate": "2"}]
    store.finish_scan(initial, {"os": {
        "family": "debian", "metadata": "fresh", "package_query_ok": True,
        "packages": packages,
    }})
    plan = store.create_plan([host["id"]])
    store.approve_plan(plan["id"], "opm", False)
    job = store.create_job(plan["id"], "postpatch-regression")
    monkeypatch.setattr("opm.worker.check_ansible_runtime", lambda: ("/fake/ansible-playbook", "/fake:/usr/bin"))
    monkeypatch.setattr("opm.worker._validated_inventory_file", lambda h, inv, directory, e, path: directory / "hosts.json")
    monkeypatch.setattr("opm.worker._fresh_preflight", lambda *a: None)
    monkeypatch.setattr("opm.worker.inspect_os", lambda *a: {"package_query_ok": True, "reboot_required": False})
    refreshed = []

    def fake_refresh(*args):
        refreshed.append(args[2]["id"])
        return True

    monkeypatch.setattr("opm.worker._refresh_host_after_patch", fake_refresh)

    def fake_run(**kwargs):
        callback = kwargs["event_handler"]
        callback({"event": "runner_on_ok", "event_data": {
            "task": "Upgrade installed packages without automatic removals",
            "host": host["name"], "res": {"changed": True},
        }})
        callback({"event": "playbook_on_stats", "event_data": {
            "ok": {host["name"]: 1},
        }})
        return types.SimpleNamespace(rc=0, status="successful")

    monkeypatch.setitem(sys.modules, "ansible_runner", types.SimpleNamespace(run=fake_run))
    run_job(job["id"], config)
    assert store.job(job["id"])["state"] == "completed"
    assert refreshed == [host["id"]]
