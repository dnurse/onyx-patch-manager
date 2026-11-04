"""Protect against Ansible's valid-EXIT-0 / no-hosts-matched false success.

All tests run locally. No remote patching or SSH is executed.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from opm.inventory import ansible_inventory
from opm.remote import RemoteError
from opm.store import Store
from opm.worker import _validated_inventory_file, run_job


def _setup(repo, host_data):
    store, config = repo
    host = store.add_host({**host_data, "sudo_mode": "passwordless"})
    store.verified(host["id"])
    scan = store.begin_scan(host["id"])
    store.finish_scan(scan, {"os": {
        "family": "debian", "metadata": "fresh", "package_query_ok": True,
        "packages": [{"name": "curl", "current": "1", "candidate": "2"}],
    }})
    plan = store.create_plan([host["id"]])
    store.approve_plan(plan["id"], "opm-test", False)
    job = store.create_job(plan["id"], "runner-test")
    return store, config, host, job


def test_inventory_is_native_mapping_not_dynamic_inventory_list(repo, host_data):
    store, config = repo
    host = store.add_host(host_data)
    inventory = ansible_inventory([host], str(config.known_hosts))
    assert isinstance(inventory["all"]["hosts"], dict)
    assert list(inventory["all"]["hosts"]) == [host["name"]]
    assert inventory["all"]["hosts"][host["name"]]["ansible_host"] == host["address"]
    assert "_meta" not in inventory
    assert "password" not in json.dumps(inventory).lower()


def test_ansible_inventory_cli_proves_single_matching_host(repo, host_data, tmp_path, monkeypatch):
    store, config = repo
    host = store.add_host(host_data)
    inventory = ansible_inventory([host], str(config.known_hosts))
    binary = tmp_path / "ansible-playbook"
    inventory_bin = tmp_path / "ansible-inventory"
    inventory_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    inventory_bin.chmod(0o755)

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == [str(inventory_bin), "-i"]
        assert cmd[-1] == "--list"
        recorded = json.loads(Path(cmd[2]).read_text())
        assert recorded == inventory
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({
            "_meta": {"hostvars": {host["name"]: recorded["all"]["hosts"][host["name"]]}}
        }), stderr="")

    monkeypatch.setattr("opm.worker.subprocess.run", fake_run)
    file = _validated_inventory_file(host, inventory, tmp_path / "job", str(binary), "/mock/bin")
    assert file.name == "hosts.json"
    assert file.stat().st_mode & 0o777 == 0o600


def test_malformed_inventory_never_invokes_ansible(repo, host_data, tmp_path, monkeypatch):
    store, config = repo
    host = store.add_host(host_data)
    inventory = {"all": {"hosts": [host["name"]]}}
    with pytest.raises(RemoteError, match="mapping of host variables"):
        _validated_inventory_file(host, inventory, tmp_path, "/mock/bin/ansible-playbook", "/mock/bin")
    assert not (tmp_path / "inventory").exists()


def test_ansible_cli_missing_approved_host_fails_before_patch(repo, host_data, tmp_path, monkeypatch):
    store, config = repo
    host = store.add_host(host_data)
    inventory = ansible_inventory([host], str(config.known_hosts))
    binary = tmp_path / "ansible-playbook"
    inventory_bin = tmp_path / "ansible-inventory"
    inventory_bin.write_text("#!/bin/sh\nexit 0\n")
    inventory_bin.chmod(0o755)
    monkeypatch.setattr("opm.worker.subprocess.run", lambda *a, **k:
                        types.SimpleNamespace(returncode=0, stdout=json.dumps({
                            "_meta": {"hostvars": {}}
                        }), stderr=""))
    with pytest.raises(RemoteError, match="approved host"):
        _validated_inventory_file(host, inventory, tmp_path, str(binary), "/mock/bin")


def test_exit_zero_no_hosts_matched_is_failure_not_verification(repo, host_data, monkeypatch):
    store, config, host, job = _setup(repo, host_data)
    monkeypatch.setattr("opm.worker.check_ansible_runtime", lambda: ("/mock/bin/ansible-playbook", "/mock/bin:/usr/bin"))
    monkeypatch.setattr("opm.worker._validated_inventory_file", lambda h, i, d, a, p: d / "inventory" / "hosts.json")
    monkeypatch.setattr("opm.worker._fresh_preflight", lambda *a: None)
    def no_verification(*args, **kwargs):
        raise AssertionError("Post-patch verification should never run for an empty play")
    monkeypatch.setattr("opm.worker.inspect_os", no_verification)

    def fake_runner_run(**kwargs):
        kwargs["event_handler"]({"event": "playbook_on_no_hosts_matched", "event_data": {}})
        kwargs["event_handler"]({"event": "playbook_on_stats", "event_data": {"ok": {}}})
        return types.SimpleNamespace(rc=0, status="successful")

    monkeypatch.setitem(sys.modules, "ansible_runner", types.SimpleNamespace(run=fake_runner_run))
    run_job(job["id"], config)
    assert store.job(job["id"])["state"] == "failed"
    assert store.job(job["id"])["targets"][0]["state"] == "failed"
    messages = [event["message"] for event in store.events(job["id"])]
    assert any("zero hosts matched" in message for message in messages)
    assert not any("Verification" == event["task"] for event in store.events(job["id"]))


def test_missing_upgrade_task_even_with_valid_recap_is_failure(repo, host_data, monkeypatch):
    store, config, host, job = _setup(repo, host_data)
    monkeypatch.setattr("opm.worker.check_ansible_runtime", lambda: ("/mock/bin/ansible-playbook", "/mock/bin:/usr/bin"))
    monkeypatch.setattr("opm.worker._validated_inventory_file", lambda h, i, d, a, p: d / "inventory" / "hosts.json")
    monkeypatch.setattr("opm.worker._fresh_preflight", lambda *a: None)
    monkeypatch.setattr("opm.worker.inspect_os", lambda *a: {"package_query_ok": True})

    def fake_runner_run(**kwargs):
        callback = kwargs["event_handler"]
        callback({"event": "playbook_on_stats", "event_data": {"ok": {host["name"]: 2}}})
        return types.SimpleNamespace(rc=0, status="successful")

    monkeypatch.setitem(sys.modules, "ansible_runner", types.SimpleNamespace(run=fake_runner_run))
    run_job(job["id"], config)
    assert store.job(job["id"])["state"] == "failed"
