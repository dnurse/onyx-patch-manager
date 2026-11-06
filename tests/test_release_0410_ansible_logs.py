"""Regressions for the CLI-missing failure and expandable task diagnostics.

No test launches a real patch or runs SSH. The Ansible executable check is
exercised with controlled fake entry points in a temporary virtualenv tree.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import types
from pathlib import Path

import pytest

from opm.controller import Controller
from opm.runtime import AnsibleDependencyError, ansible_runtime, check_ansible_runtime
from opm.worker import run_job

PROJECT = Path(__file__).resolve().parents[1]


def _approved_job(repo, host_data):
    store, config = repo
    host = store.add_host({**host_data, "sudo_mode": "passwordless"})
    store.verified(host["id"])
    scan_id = store.begin_scan(host["id"])
    store.finish_scan(scan_id, {"os": {
        "family": "debian", "metadata": "fresh", "package_query_ok": True,
        "packages": [{"name": "curl", "current": "1", "candidate": "2"}],
    }})
    plan = store.create_plan([host["id"]])
    store.approve_plan(plan["id"], "test-operator", False)
    return store, config, host, plan


def test_missing_ansible_playbook_is_clear_error(tmp_path):
    binary = tmp_path / "opm-venv" / "bin" / "python3"
    binary.parent.mkdir(parents=True)
    with pytest.raises(AnsibleDependencyError, match="Ansible Core is unavailable") as exc:
        ansible_runtime(str(binary))
    assert str(binary.parent / "ansible-playbook") in str(exc.value)
    assert "Repair Installation" in str(exc.value)


def test_an_unresolved_venv_python_path_is_used(tmp_path):
    bindir = tmp_path / "opm-venv" / "bin"
    bindir.mkdir(parents=True)
    binary = bindir / "ansible-playbook"
    binary.write_text("#!/bin/sh\necho 'ansible-playbook [core test]'\n")
    binary.chmod(0o755)
    executable, path = check_ansible_runtime(str(bindir / "python3"))
    assert executable == binary
    assert path.split(":")[0] == str(bindir)


def test_controller_refuses_to_launch_missing_ansible(repo, host_data, monkeypatch):
    store, config, host, plan = _approved_job(repo, host_data)
    controller = Controller(config)
    monkeypatch.setattr("opm.controller.subprocess.run", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, "", ""))

    def absent():
        raise AnsibleDependencyError("Missing Ansible playbook binary")

    monkeypatch.setattr("opm.controller.check_ansible_runtime", absent)
    async def run():
        with pytest.raises(ValueError, match="Missing Ansible"):
            await controller.start_job(plan["id"], "not-created", {})
    asyncio.run(run())
    assert store.jobs() == []
    assert store.host(host["id"])


def test_worker_missing_ansible_does_not_run_remote_preflight(repo, host_data, monkeypatch):
    store, config, _, plan = _approved_job(repo, host_data)
    job = store.create_job(plan["id"], "missing-binary")
    monkeypatch.setitem(sys.modules, "ansible_runner", types.SimpleNamespace(run=lambda **kw: None))
    monkeypatch.setattr("opm.worker.check_ansible_runtime", lambda: (_ for _ in ()).throw(
        AnsibleDependencyError("Ansible executable missing")))
    def must_not_patch(*args, **kwargs):
        raise AssertionError("Remote SSH/APT preflight must not run if Ansible is missing")
    monkeypatch.setattr("opm.worker._fresh_preflight", must_not_patch)
    run_job(job["id"], config)
    assert store.job(job["id"])["state"] == "failed"
    events = store.events(job["id"])
    assert events[0]["task"] == "Controller dependency preflight"
    assert "Ansible executable missing" in events[0]["message"]


def test_job_task_errors_expand_with_previous_events():
    views = (PROJECT / "src/opm/web/views.py").read_text()
    page = (PROJECT / "src/opm/web/templates/web/job_detail.html").read_text()
    style = (PROJECT / "src/opm/web/static/web/styles.css").read_text()
    assert "event['context_events'] = events[max(0, index - 6):index]" in views
    assert '<details class="log-event log-event-error">' in page
    assert '{{ e.message }}' in page
    assert 'Preceding task events' in page
    assert '.log-event[open] .log-expand-icon' in style


def test_installer_checks_and_repairs_missing_ansible():
    installer = (PROJECT / "OPMInstall.sh").read_text()
    maintenance = (PROJECT / "OPMMaintenance.sh").read_text()
    assert 'Verifying Ansible Core executable' in installer
    assert '$VENV/bin/ansible-playbook' in installer
    assert 'install:ansible-core' in maintenance
    assert 'as_opm "$VENV/bin/pip" install -e "$APP_DIR"' in maintenance
    assert 'TOTAL_STEPS=15' in installer
