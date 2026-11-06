"""Exercise the real worker against mocked SSH and Ansible interfaces."""
import asyncio
import json
import sys
import types

from opm.worker import run_job


def test_approved_password_run_uses_pexpect_without_saving_password(repo, host_data, monkeypatch):
    store, config = repo
    host = store.add_host({**host_data, "sudo_mode": "password"})
    store.verified(host["id"])
    scan_id = store.begin_scan(host["id"])
    approved_packages = [{"name": "curl", "current": "1", "candidate": "2"}]
    store.finish_scan(scan_id, {"os": {"family": "debian", "metadata": "cached",
                                       "package_query_ok": True, "packages": approved_packages}})
    plan = store.create_plan([host["id"]])
    store.approve_plan(plan["id"], "opm", False, "PATCH")
    job = store.create_job(plan["id"], "fake-run")
    password = "WORKER-SECRET-123456"

    async def fake_rpc(method, params, config):
        assert method == "credentials.claim"
        return {host["id"]: password}

    monkeypatch.setattr("opm.worker.rpc", fake_rpc)
    monkeypatch.setattr("opm.worker.check_ansible_runtime", lambda: ("/mock/bin/ansible-playbook", "/mock/bin:/usr/bin"))
    monkeypatch.setattr("opm.worker._validated_inventory_file", lambda h, inv, d, e, p: d / "inventory" / "hosts.json")
    monkeypatch.setattr("opm.worker.ssh_command", lambda *a, **k: "")
    monkeypatch.setattr("opm.worker.test_connection", lambda *a, **k: {"sudo": "password required or unavailable"})
    monkeypatch.setattr("opm.worker.inspect_os", lambda *a, **k: {
        "family": "debian", "metadata": "cached", "package_query_ok": True,
        "packages": approved_packages, "reboot_required": False})
    calls = []
    def fake_runner_run(**kwargs):
        calls.append(kwargs)
        callback = kwargs["event_handler"]
        callback({"event": "runner_on_ok", "event_data": {
            "host": host["name"],
            "task": "Upgrade installed packages without automatic removals",
            "res": {"changed": True},
        }})
        callback({"event": "playbook_on_stats", "event_data": {
            "ok": {host["name"]: 1}
        }})
        return types.SimpleNamespace(rc=0, status="successful")
    monkeypatch.setitem(sys.modules, "ansible_runner", types.SimpleNamespace(run=fake_runner_run))

    run_job(job["id"], config)
    assert store.job(job["id"])["state"] == "completed"
    assert "runner_mode" not in calls[0]
    assert calls[0]["suppress_env_files"] is True
    assert calls[0]["cmdline"] == "--ask-become-pass"
    assert list(calls[0]["passwords"].values()) == [password]
    assert password not in json.dumps(store.events(job["id"]))
    assert password not in json.dumps(store.job(job["id"]))
