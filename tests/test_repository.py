"""Test approval, expiration, idempotence and host locking safety."""

import sqlite3
import time

import pytest


def ready_host(store, host_data, package="curl"):
    host = store.add_host(host_data)
    store.verified(host["id"])
    scan = store.begin_scan(host["id"])
    store.finish_scan(scan, {"os": {"family": "debian", "metadata": "fresh", "packages": [
        {"name": package, "current": "1", "candidate": "2"}
    ], "apt_error": ""}, "engines": {}})
    return host


def test_host_management(repo, host_data):
    store, _ = repo
    host = store.add_host(host_data)
    assert store.host(host["id"])["verified"] == 0
    store.verified(host["id"])
    changed = {**host_data, "address": "192.0.2.25"}
    assert store.update_host(host["id"], changed)["verified"] == 0
    with pytest.raises(sqlite3.IntegrityError):
        store.add_host(host_data)
    store.remove_host(host["id"])
    assert store.hosts() == []


def test_approval_and_idempotent_job(repo, host_data):
    store, _ = repo
    host = ready_host(store, host_data)
    plan = store.create_plan([host["id"]])
    # The explicit authenticated confirmation button authorizes an ordinary
    # host. Typed PATCH text is intentionally no longer required.
    store.approve_plan(plan["id"], "operator", False)
    job = store.create_job(plan["id"], "one-request")
    repeated = store.create_job(plan["id"], "one-request")
    assert job["id"] == repeated["id"]
    with pytest.raises(ValueError, match="locked"):
        store.remove_host(host["id"])
    store.job_state(job["id"], "completed")
    store.remove_host(host["id"])
    assert store.job(job["id"])["state"] == "completed"


def test_protected_hosts_require_separate_ack(repo, host_data):
    store, _ = repo
    host = ready_host(store, {**host_data, "protected": True})
    plan = store.create_plan([host["id"]])
    with pytest.raises(ValueError, match="Protected"):
        store.approve_plan(plan["id"], "operator", False, "PATCH")
    assert store.approve_plan(plan["id"], "operator", True, "PATCH")["protected_ack"] == 1


def test_stale_scans_and_unsupported_os_are_not_patchable(repo, host_data):
    store, _ = repo
    host = store.add_host(host_data)
    store.verified(host["id"])
    scan = store.begin_scan(host["id"])
    store.finish_scan(scan, {"os": {"family": "unraid", "packages": [{"name": "x"}]}})
    with pytest.raises(ValueError, match="unsupported"):
        store.create_plan([host["id"]])


def test_job_events_are_persistent_and_redacted(repo, host_data):
    store, _ = repo
    host = ready_host(store, host_data)
    plan = store.create_plan([host["id"]])
    store.approve_plan(plan["id"], "operator", False, "PATCH")
    job = store.create_job(plan["id"], "test-events")
    store.event(job["id"], host["id"], "error", "APT", "token=secret123")
    assert "secret123" not in str(store.events(job["id"]))


def test_active_job_rejects_connection_edits(repo, host_data):
    """Prevent changes to the SSH identity after a patch acquires its lock."""
    store, _ = repo
    host = ready_host(store, host_data)
    plan = store.create_plan([host["id"]])
    store.approve_plan(plan["id"], "operator", False, "PATCH")
    job = store.create_job(plan["id"], "locked-edit")
    with pytest.raises(ValueError, match="locked"):
        store.update_host(host["id"], {**host_data, "address": "192.0.2.99"})
    store.job_state(job["id"], "completed")
    assert store.update_host(host["id"], {**host_data, "address": "192.0.2.99"})["verified"] == 0


def test_duplicate_job_request_is_flagged_as_existing(repo, host_data):
    """The controller uses this flag to avoid launching duplicate workers."""
    store, _ = repo
    host = ready_host(store, host_data)
    plan = store.create_plan([host["id"]])
    store.approve_plan(plan["id"], "operator", False, "PATCH")
    store.create_job(plan["id"], "dedup")
    assert store.create_job(plan["id"], "dedup")["existing"] is True
