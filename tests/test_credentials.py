"""Transient sudo credential handling and old-database compatibility."""
import asyncio
import json
import sqlite3
import subprocess
from unittest.mock import patch

import pytest

from opm.controller import Controller
from opm.store import Store
from opm.worker import _fresh_preflight


def ready_password_plan(store, host_data):
    host = store.add_host({**host_data, "sudo_mode": "password"})
    store.verified(host["id"])
    scan = store.begin_scan(host["id"])
    store.finish_scan(scan, {"os": {"family": "debian", "metadata": "cached",
                                  "apt_error": "Package indexes were not refreshed",
                                  "package_query_ok": True,
                                  "packages": [{"name": "curl", "current": "1", "candidate": "2"}]}})
    plan = store.create_plan([host["id"]])
    store.approve_plan(plan["id"], "owner", False, "PATCH")
    return host, plan


def test_cached_scan_can_be_approved_but_requires_preflight(repo, host_data):
    store, _ = repo
    host, plan = ready_password_plan(store, host_data)
    assert plan["snapshot"]["targets"][0]["sudo_mode"] == "password"
    assert len(plan["snapshot"]["targets"]) == 1


def test_password_is_never_saved_in_job_or_inventory(repo, host_data, monkeypatch):
    store, config = repo
    host, plan = ready_password_plan(store, host_data)
    controller = Controller(config)
    secret = "NOT-PERSISTED-SECRET-77888"
    def fake_command(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "ok", "")
    monkeypatch.setattr("opm.controller.subprocess.run", fake_command)
    monkeypatch.setattr("opm.controller.check_ansible_runtime", lambda: ("/mock/ansible-playbook", "/mock/bin"))

    async def exercise():
        with pytest.raises(ValueError, match="[Ss]udo password"):
            await controller.start_job(plan["id"], "missing-password", {})
        job = await controller.start_job(plan["id"], "real-job", {host["id"]: secret})
        assert secret not in json.dumps(store.job(job["id"]))
        assert secret not in config.database.read_bytes().decode("latin1")
        claimed = await controller.dispatch("credentials.claim", {"job_id": job["id"]})
        assert claimed == {host["id"]: secret}
        with pytest.raises(ValueError, match="No temporary credentials"):
            await controller.dispatch("credentials.claim", {"job_id": job["id"]})
    asyncio.run(exercise())


def test_password_preflight_never_in_ssh_command_arguments(repo, host_data, monkeypatch):
    store, config = repo
    host, plan = ready_password_plan(store, host_data)
    host = store.host(host["id"])
    records = []
    secret = "SUPER-SECRET-APT-123"
    def fake_ssh(host_arg, config_arg, remote_args, input_text=None, timeout=30):
        records.append((remote_args, input_text))
        return ""
    monkeypatch.setattr("opm.worker.ssh_command", fake_ssh)
    monkeypatch.setattr("opm.worker.test_connection", lambda *a: {"connected": True, "sudo": "password required or unavailable"})
    monkeypatch.setattr("opm.worker.inspect_os", lambda *a: {"family": "debian", "metadata": "cached",
        "package_query_ok": True, "packages": [{"name": "curl", "current": "1", "candidate": "2"}]})
    _fresh_preflight(host, plan["snapshot"]["targets"][0], config, secret)
    assert len(records) == 3
    assert all(secret not in " ".join(args) for args, _ in records)
    assert all(payload == secret + "\n" for _, payload in records)
    assert any("apt-get" in args for args, _ in records)
