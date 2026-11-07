"""Detached, systemd-supervised Ansible patch worker.

Each worker operates on one approved, immutable OS patch plan and writes state
and events to SQLite. Closing the web browser does not affect this process. Jobs are
not automatically replayed after an interruption, because APT is not atomic.

V0.1 intentionally does not execute container updates. Docker and Podman are
read-only discoveries until registry identity and Compose safety checks pass.
"""

from __future__ import annotations

import json
import sys
import asyncio
import os
import subprocess
from pathlib import Path

from .config import Config
from .controller import rpc
from .inventory import ansible_inventory
from .runtime import check_ansible_runtime, AnsibleDependencyError
from .remote import RemoteError, inspect_os, inspect_host, ssh_command, test_connection
from .store import Store
from .validation import sanitize


def _playbook(path: Path) -> None:
    """Create a static, conservative package-upgrade playbook as JSON/YAML."""
    # Ansible accepts JSON as YAML. No user-provided data is interpolated into
    # a shell command, playbook task or package name during execution.
    play = [{
        "name": "Approved operating system maintenance",
        "hosts": "all",
        "gather_facts": False,
        "serial": 1,
        "tasks": [{
            "name": "Upgrade installed packages without automatic removals",
            "become": True,
            "ansible.builtin.apt": {
                # The preflight refresh already loaded exactly the candidate
                # versions approved by the operator. A second refresh during
                # the modifying task could silently expand the patch plan.
                "update_cache": False,
                "upgrade": "safe",
                "force_apt_get": True,
                "fail_on_autoremove": True,
                "autoremove": False,
                "purge": False,
            },
        }],
    }]
    path.write_text(json.dumps(play, indent=2), encoding="utf-8")
    path.chmod(0o600)



def _validation_playbook(path: Path) -> None:
    """Write a fixed non-mutating playbook used ONLY for simulations.

    The stat module supports Ansible check mode and does not modify the
    remote filesystem. Never reuse the live apt-upgrade playbook as the
    dry-run target; this keeps a fail-closed separation of capabilities.
    """
    play = [{
        "name": "Dry-run privileged Ansible validation",
        "hosts": "all", "gather_facts": False, "serial": 1,
        "tasks": [{
            "name": "Validate privileged Ansible access in check mode",
            "become": True,
            "ansible.builtin.stat": {"path": "/etc/os-release"},
        }],
    }]
    path.write_text(json.dumps(play, indent=2), encoding="utf-8")
    path.chmod(0o600)


def _simulate_preflight(host: dict, target: dict, config: Config,
                        sudo_password: str | None, store: Store,
                        job_id: str, refresh_metadata: bool = False,
                        verbose: bool = True) -> None:
    """Verify connectivity, sudo, and package simulation without installing.

    The default path never invokes apt-get update. That operation modifies
    APT's local indexes and is performed only when separately approved in
    the immutable simulation plan. `apt-get -s upgrade` is a read-only
    calculation using currently installed package metadata.
    """
    if not host['verified']:
        raise RemoteError('SSH verification was revoked')
    for field, approved in (("address", "address"), ("ssh_port", "port"),
                            ("ssh_user", "user"), ("key_path", "key_path")):
        if host[field] != target[approved]:
            raise RemoteError('SSH connection parameters changed after plan approval')
    if host['sudo_mode'] != target['sudo_mode']:
        raise RemoteError('Sudo policy changed since approval')
    mode = host['sudo_mode']
    if mode == 'read_only':
        raise RemoteError('Read-only hosts cannot perform privileged validation')
    connected = test_connection(host, config)
    if not connected.get('connected', True):
        raise RemoteError('SSH connection is unavailable')
    if mode == 'password':
        if not sudo_password:
            raise RemoteError('Sudo authorization expired. Reopen the dry-run review')
        ssh_command(host, config, ['sudo', '-k', '-S', '-p', "''", 'true'],
                    sudo_password + '\n', timeout=22)
    elif mode == 'passwordless' and connected['sudo'] != 'passwordless':
        raise RemoteError('Passwordless sudo is unavailable')

    if refresh_metadata:
        store.event(job_id, host['id'], 'info', 'Dry-run metadata refresh',
                    'Operator authorized APT index refresh without installing packages')
        refresh = ['apt-get', 'update', '-o', 'APT::Update::Error-Mode=any']
        if mode == 'root':
            ssh_command(host, config, refresh, timeout=120)
        elif mode == 'password':
            ssh_command(host, config, ['sudo', '-k', '-S', '-p', "''", *refresh],
                        sudo_password + '\n', timeout=120)
        else:
            ssh_command(host, config, ['sudo', '-n', *refresh], timeout=120)
    else:
        store.event(job_id, host['id'], 'info', 'Dry-run metadata',
                    'Using cached APT indexes. No apt-get update was performed')

    os_result = inspect_os(host, config)
    if not os_result.get('package_query_ok', False):
        raise RemoteError('APT package query failed during dry-run validation')
    packages = os_result.get('packages', [])
    store.event(job_id, host['id'], 'info', 'Dry-run package candidates',
                f'{len(packages)} OS upgrade candidate(s) using current package indexes')
    # The -s switch simulates APT actions; it does not install packages.
    command = ['apt-get', '-s', 'upgrade']
    if mode == 'root':
        output = ssh_command(host, config, command, timeout=120)
    elif mode == 'password':
        output = ssh_command(host, config, ['sudo', '-k', '-S', '-p', "''", *command],
                             sudo_password + '\n', timeout=120)
    else:
        output = ssh_command(host, config, ['sudo', '-n', *command], timeout=120)
    store.event(job_id, host['id'], 'success', 'APT simulation',
                'APT upgrade simulation completed. No packages were installed')
    if verbose and output.strip():
        # A bounded stored log avoids huge APT output in a single row. This
        # is diagnostic data only; it never flows into shell arguments.
        store.event(job_id, host['id'], 'info', 'APT simulation output',
                    sanitize(output, 5000))


def _validated_inventory_file(host: dict, inventory: dict, host_dir: Path,
                              ansible_executable: str, runner_path: str) -> Path:
    """Write a private inventory, then have Ansible itself parse that file.

    Do this BEFORE refreshing APT indexes on the managed server. Checking only
    that our dict looks plausible would miss Ansible plugin incompatibilities.
    The same validated file is passed to Runner, preventing differences
    between preflight and patch execution inventory content.
    """
    host_name = str(host["name"])
    groups = inventory.get("all") or {}
    host_mapping = groups.get("hosts")
    if not isinstance(host_mapping, dict) or set(host_mapping) != {host_name}:
        raise RemoteError(
            "Generated Ansible inventory is invalid: expected exactly the approved host "
            "with a mapping of host variables. No packages were patched."
        )

    inventory_dir = host_dir / "inventory"
    inventory_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = inventory_dir / "hosts.json"
    path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    path.chmod(0o600)

    inventory_executable = Path(ansible_executable).with_name("ansible-inventory")
    if not inventory_executable.is_file() or not os.access(inventory_executable, os.X_OK):
        raise RemoteError(
            "ansible-inventory is missing in the OPM virtual environment. "
            "Run OPM Installer > Repair Installation."
        )
    try:
        result = subprocess.run(
            [str(inventory_executable), "-i", str(path), "--list"],
            capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "PATH": runner_path, "ANSIBLE_HOST_KEY_CHECKING": "True"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteError("Could not validate the Ansible inventory. No packages were patched.") from exc
    if result.returncode != 0:
        raise RemoteError(
            "Ansible rejected the generated inventory. No packages were patched. "
            + sanitize((result.stderr or result.stdout or "parse error")[:260], 260)
        )
    try:
        parsed = json.loads(result.stdout)
        hosts = parsed["_meta"]["hostvars"]
        if set(hosts) != {host_name}:
            raise ValueError("approved host not present or extra hosts discovered")
        variables = hosts[host_name]
        expected = host_mapping[host_name]
        if not isinstance(variables, dict) or any(
            variables.get(key) != expected.get(key)
            for key in ("ansible_host", "ansible_user", "ansible_port")
        ):
            raise ValueError("Ansible resolved unexpected connection variables")
    except (ValueError, TypeError, KeyError) as exc:
        raise RemoteError(
            "Ansible inventory did not resolve exactly the approved host. "
            "Patch execution stopped before changing packages."
        ) from exc
    return path


def _fresh_preflight(host: dict, target: dict, config: Config, sudo_password: str | None) -> None:
    """Detect changes between approval and execution before any APT mutation."""
    if not host["verified"]:
        raise RemoteError("SSH verification was revoked")
    for field, approved_field in (("address", "address"), ("ssh_port", "port"),
                                  ("ssh_user", "user"), ("key_path", "key_path")):
        if host[field] != target[approved_field]:
            raise RemoteError("SSH connection parameters changed after plan approval")
    if host.get("sudo_mode") != target.get("sudo_mode"):
        raise RemoteError("Sudo policy changed since approval")
    mode = host.get("sudo_mode", "passwordless")
    status = test_connection(host, config)
    if mode == "password":
        if not sudo_password:
            raise RemoteError("Sudo authorization is missing; approve this patch run again")
        # Feed the password only over SSH stdin. It never appears in command
        # arguments, systemd unit properties, Ansible inventory or log text.
        ssh_command(host, config, ["sudo", "-k", "-S", "-p", "''", "true"],
                    sudo_password + "\n", timeout=22)
    elif mode == "passwordless" and status["sudo"] != "passwordless":
        raise RemoteError("Passwordless sudo is unavailable on this host")
    elif mode == "read_only":
        raise RemoteError("Read-only hosts cannot be patched")
    # Refresh metadata with the approved privilege mode. Scans performed
    # without a sudo password may have used cached metadata, so compare the
    # actual fresh candidates before modifying any installed package.
    update = ["apt-get", "update", "-o", "APT::Update::Error-Mode=any"]
    if mode == "root":
        ssh_command(host, config, update, timeout=120)
    elif mode == "password":
        ssh_command(host, config, ["sudo", "-k", "-S", "-p", "''", *update],
                    sudo_password + "\n", timeout=120)
    else:
        ssh_command(host, config, ["sudo", "-n", *update], timeout=120)
    current = inspect_os(host, config)
    if not current.get("package_query_ok", False):
        raise RemoteError(f"APT package query failed: {current.get('apt_error', '')}")
    if sorted(current.get("packages", []), key=lambda p: p["name"]) != sorted(
        target["packages"], key=lambda p: p["name"]
    ):
        raise RemoteError("Package candidates changed since approval. Run a new scan and review")
    # The simulation is a separate read-only gate. We do not delete APT lock
    # files or auto-repair an interrupted dpkg transaction.
    simulation = ["apt-get", "-s", "upgrade"]
    if mode == "root":
        ssh_command(host, config, simulation, timeout=120)
    elif mode == "password":
        ssh_command(host, config, ["sudo", "-k", "-S", "-p", "''", *simulation],
                    sudo_password + "\n", timeout=120)
    else:
        ssh_command(host, config, ["sudo", "-n", *simulation], timeout=120)


def _refresh_host_after_patch(store: Store, job_id: str, host: dict,
                              config: Config, sudo_password: str | None) -> bool:
    """Refresh dashboard state after Ansible and OS verification succeed.

    A successful patch does not automatically guarantee a successful inventory
    scan. Use the same already-claimed worker credential for one read-only
    rescan, create a durable scan record, and report scan problems separately
    without rewriting a genuine Ansible success as a patch failure.
    """
    host_id = host["id"]
    scan_id: str | None = None
    try:
        # Record a new scan before sending remote requests so a failed refresh
        # can never leave the old package counts presented as the latest data.
        scan_id = store.begin_scan(host_id)
        store.event(job_id, host_id, "info", "Post-patch scan",
                    "Refreshing OS packages and container findings for the dashboard")
        result = inspect_host(host, config, sudo_password)
        store.finish_scan(scan_id, result)
        os_data = result.get("os", {})
        if not os_data.get("package_query_ok", False):
            store.event(job_id, host_id, "warning", "Post-patch scan",
                        "Package query was incomplete. Review the new scan before further patching")
            return False
        if os_data.get("metadata") != "fresh" or os_data.get("apt_error"):
            store.event(job_id, host_id, "warning", "Post-patch scan",
                        "Package metadata was not refreshed. Dashboard totals are unverified")
            return False
        remaining = len(os_data.get("packages", []))
        store.event(job_id, host_id, "success", "Post-patch scan",
                    f"Dashboard inventory refreshed; {remaining} OS update(s) remain")
        return True
    except Exception as exc:
        # A failure to start or finish the read-only refresh must not change
        # the already verified result of the approved Ansible task.
        diagnostic = sanitize(str(exc), 250)
        if sudo_password:
            diagnostic = diagnostic.replace(sudo_password, "[REDACTED]")
        if scan_id is not None:
            try:
                store.finish_scan(scan_id, None, diagnostic)
            except Exception:
                pass  # Log the original scan failure, never overwrite patch result.
        try:
            store.event(job_id, host_id, "warning", "Post-patch scan",
                        "Patch completed but inventory refresh failed: " + diagnostic)
        except Exception:
            pass
        return False


def run_job(job_id: str, config: Config | None = None) -> None:
    """Execute one approved job with per-target failures and cooperative cancel.

    Active APT tasks are intentionally not interrupted by cancellation. The
    worker checks the request before starting the next host. Any worker crash
    leaves a nonterminal state for controller reconciliation on restart.
    """
    config = config or Config.load()
    store = Store(config.database)
    job = store.job(job_id)
    plan = store.plan(job["plan_id"])
    mode = plan['snapshot'].get('mode', 'apply')
    if mode not in ('apply', 'dry_run'):
        store.job_state(job_id, 'failed', 'Unknown immutable execution mode')
        return
    if not plan["approved_at"] or plan["expires"] < __import__("time").time():
        store.job_state(job_id, "failed", "Approval missing or expired before execution")
        return

    # Lazy import keeps inventory and scanning usable even when the machine is
    # still missing the external Ansible runtime dependency.
    try:
        import ansible_runner
    except ImportError:
        error = "Ansible Runner is not installed in OPM's Python environment. Run OPM Installer > Repair Installation."
        store.event(job_id, None, "error", "Controller dependency preflight", error)
        store.job_state(job_id, "failed", error)
        return

    # Validate the real ansible-playbook entry point before consuming sudo
    # credentials or executing remote APT preflight. A Python import of
    # ansible-runner alone does not prove that ansible-core's CLI is installed.
    try:
        ansible_executable, runner_path = check_ansible_runtime()
    except AnsibleDependencyError as exc:
        error = sanitize(str(exc), 400)
        store.event(job_id, None, "error", "Controller dependency preflight", error)
        store.job_state(job_id, "failed", error)
        return

    # Put the virtual environment's bin directory first, regardless of the
    # minimal PATH inherited from systemd-run. Runner also receives PATH as
    # an explicit envvar so child playbook processes find the same executable.
    os.environ["PATH"] = runner_path

    # Claim ephemeral passwords once, before executing the first host. A browser
    # disconnect does not affect this worker. If the controller restarted
    # before the claim, do not assume authorization or retry the job.
    needed = [target["host_id"] for target in plan["snapshot"]["targets"]
              if target.get("sudo_mode") == "password"]
    try:
        sudo_passwords = asyncio.run(rpc("credentials.claim", {"job_id": job_id}, config)) if needed else {}
    except Exception:
        store.job_state(job_id, "failed", "Temporary sudo authorization unavailable. Reapproval required")
        return
    store.job_state(job_id, "running")
    failed = 0
    completed = 0
    try:
        for target in plan["snapshot"]["targets"]:
            host_id = target["host_id"]
            if store.job(job_id)["cancel_requested"]:
                store.event(job_id, host_id, "warning", "Cancellation", "Skipped remaining queued targets")
                store.target_state(job_id, host_id, "cancelled")
                continue
            store.target_state(job_id, host_id, "running")
            host_dir = config.jobs / job_id / host_id
            host_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                host = store.host(host_id)
                inventory = ansible_inventory([host], str(config.known_hosts))
                inventory_file = _validated_inventory_file(
                    host, inventory, host_dir, str(ansible_executable), runner_path
                )
                store.event(job_id, host_id, "info", "Inventory validation",
                            "Ansible parsed the inventory and resolved exactly one approved host")
                sudo_password = sudo_passwords.get(host_id)
                if mode == 'dry_run':
                    store.event(job_id, host_id, 'info', 'Dry-run preflight',
                                'Checking SSH, sudo, package query and APT simulation')
                    _simulate_preflight(
                        host, target, config, sudo_password, store, job_id,
                        refresh_metadata=plan['snapshot'].get('refresh_metadata', False),
                        verbose=plan['snapshot'].get('verbose_dry_run', True),
                    )
                    play_path = host_dir / 'read_only_validation.json'
                    _validation_playbook(play_path)
                else:
                    store.event(job_id, host_id, 'info', 'Preflight',
                                'Checking SSH, sudo, packages and APT simulation')
                    _fresh_preflight(host, target, config, sudo_password)
                    play_path = host_dir / 'approved_upgrade.json'
                    _playbook(play_path)

                # Runner manages Ansible task events. The job's snapshots live
                # in a private directory. Remote modules are the only path to
                # change the managed host, and the playbook is fixed in code.
                execution = {"no_hosts": False, "upgrade_task_hosts": set(), "recap_hosts": set()}
                expected_task = ('Validate privileged Ansible access in check mode'
                                 if mode == 'dry_run' else
                                 'Upgrade installed packages without automatic removals')

                def on_event(event: dict) -> bool:
                    event_type = event.get("event")
                    event_data = event.get("event_data") or {}
                    if event_type == "playbook_on_no_hosts_matched":
                        execution["no_hosts"] = True
                    if event_type == 'runner_on_ok' and event_data.get('task') == expected_task:
                        # For simulation, confirm that the read-only stat
                        # module actually observed the target OS release.
                        result = event_data.get('res') or {}
                        if (mode != 'dry_run' or (isinstance(result, dict) and
                                (result.get('stat') or {}).get('exists') is True)):
                            execution['upgrade_task_hosts'].add(event_data.get('host'))
                    if event_type == "playbook_on_stats":
                        recap_ok = event_data.get("ok") or {}
                        execution["recap_hosts"].update(
                            name for name, count in recap_ok.items() if count > 0
                        )
                    result = event_data.get("res") or {}
                    if not isinstance(result, dict):
                        result = {}
                    message = str(event.get("stdout") or result.get("msg") or "")
                    # Avoid accidental credential disclosure in persisted task
                    # events if a remote tool echoes an authentication value.
                    if sudo_password:
                        message = message.replace(sudo_password, "[REDACTED]")
                    if message.strip() and (mode != 'dry_run' or
                        plan['snapshot'].get('verbose_dry_run', True) or
                        event_type in ('runner_on_failed', 'runner_on_unreachable',
                                       'playbook_on_no_hosts_matched')):
                        store.event(job_id, host_id, "error" if event.get("event") in
                                    ("runner_on_failed", "runner_on_unreachable") else "info",
                                    str(event_data.get("task") or event.get("event", "Ansible")), message)
                    return True

                runner = ansible_runner.run(
                    private_data_dir=str(host_dir), inventory=str(inventory_file),
                    playbook=str(play_path), ident='simulation' if mode == 'dry_run' else 'upgrade',
                    event_handler=on_event,
                    # The Runner Python API accepts prompt/response mappings
                    # directly. Do not write env/passwords or extra vars.
                    passwords={r"^BECOME password.*:\s*?$": sudo_password} if sudo_password else None,
                    cmdline=" ".join(part for part in (
                        "--check" if mode == 'dry_run' else "",
                        "--ask-become-pass" if sudo_password else "",
                    ) if part),
                    # ansible_runner.run() does not accept a ``runner_mode``
                    # keyword in the supported 2.4 API. It selects the
                    # interactive pexpect runner for ordinary playbook runs,
                    # which can answer the become-password prompt. Passing
                    # runner_mode previously raised BaseConfig.__init__ here.
                    # Do not serialize the transient password mapping to
                    # private_data_dir/env/passwords or runner artifacts.
                    suppress_env_files=True,
                    envvars={"PATH": runner_path,
                             "ANSIBLE_HOST_KEY_CHECKING": "True",
                             "ANSIBLE_RETRY_FILES_ENABLED": "False",
                             "ANSIBLE_NOCOLOR": "True"},
                )
                if runner.rc != 0 or runner.status != "successful":
                    # Exit 127 specifically denotes that a child command was
                    # missing. Give the operator an actionable diagnosis in
                    # the same job log instead of a generic failure code.
                    if runner.rc == 127:
                        raise RemoteError(
                            "Ansible could not execute a required command (exit 127). "
                            f"Expected playbook executable: {ansible_executable}. "
                            "Run OPM Installer > Repair Installation and review the "
                            "expanded Ansible task log before retrying."
                        )
                    raise RemoteError(
                        f"Ansible returned {runner.status}, exit code {runner.rc}. "
                        "Expand the failed task row for details."
                    )
                # Ansible can exit successfully when no host matches the
                # inventory. Never infer patch success from exit code 0 alone.
                # Require both an actual approved APT task-success callback
                # and a matching host in Ansible's final recap.
                if (execution["no_hosts"] or
                        host["name"] not in execution["upgrade_task_hosts"] or
                        host["name"] not in execution["recap_hosts"]):
                    raise RemoteError(
                        ('Ansible did not validate the selected host in check mode'
                         if mode == 'dry_run' else
                         'Ansible did not execute the approved upgrade on the selected host') +
                        ' (zero hosts matched or expected task result missing).'
                    )
                if mode == 'dry_run':
                    store.target_state(job_id, host_id, 'simulated')
                    store.event(job_id, host_id, 'success', 'Dry run',
                                'Validation succeeded. No packages installed and no services restarted')
                    completed += 1
                    continue
                store.target_state(job_id, host_id, "verifying")
                after = inspect_os(host, config)
                if not after.get("package_query_ok"):
                    raise RemoteError("Post-patch APT package query failed; verification is incomplete")
                reboot = bool(after.get("reboot_required", False))
                store.target_state(job_id, host_id, "completed", reboot=reboot)
                store.event(job_id, host_id, "success", "Verification",
                            "SSH reachable; package query completed; " +
                            ("OS reboot is pending" if reboot else "no OS reboot flag detected"))
                # Update the cached dashboard only after the Ansible task and
                # post-patch verification have actually succeeded. Any refresh
                # failure is a distinct warning, not a false patch failure.
                _refresh_host_after_patch(store, job_id, host, config, sudo_password)
                completed += 1
            except Exception as exc:
                # Do not auto-repeat a partially completed APT transaction.
                failed += 1
                message = sanitize(str(exc))
                secret = sudo_passwords.get(host_id)
                if secret:
                    message = message.replace(secret, "[REDACTED]")
                store.target_state(job_id, host_id, "failed", message)
                store.event(job_id, host_id, "error", "Execution failed", message)
        if failed and completed:
            store.job_state(job_id, "partially_completed")
        elif failed:
            store.job_state(job_id, "failed")
        elif store.job(job_id)["cancel_requested"]:
            store.job_state(job_id, "cancelled")
        else:
            store.job_state(job_id, 'simulated' if mode == 'dry_run' else 'completed')
    except BaseException as exc:
        # An unexpected worker failure must never be displayed as success.
        store.event(job_id, None, "error", "Worker crashed", sanitize(str(exc)))
        store.job_state(job_id, "interrupted", sanitize(str(exc)))
        raise


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m opm.worker JOB_ID")
    run_job(sys.argv[1])


if __name__ == "__main__":
    main()
