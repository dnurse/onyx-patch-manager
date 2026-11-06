"""Controller-side executable checks for detached patch workers.

Ansible Runner is a Python package, while ``ansible-playbook`` is a command-line
entry point installed by ansible-core. The runner's child processes must see
that entry point in PATH even when systemd launches the OPM controller without
an interactive shell. We always prefer the currently executing OPM venv over
unrelated system-wide Ansible installations.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class AnsibleDependencyError(RuntimeError):
    """The OPM controller cannot start the required Ansible executable."""


def ansible_runtime(python_executable: str | None = None) -> tuple[Path, str]:
    """Return the verified executable and an environment PATH for Runner.

    Do not resolve the Python interpreter's symlinks: venv/bin/python3 often
    points to /usr/bin/python3. Its *unresolved* parent is the venv/bin
    directory that should contain ansible-playbook.
    """
    interpreter = Path(python_executable or sys.executable)
    bindir = interpreter.parent
    executable = bindir / "ansible-playbook"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise AnsibleDependencyError(
            f"Ansible Core is unavailable: {executable} is missing or not executable. "
            "Run OPM Installer > Repair Installation to reinstall application dependencies."
        )
    runner_path = os.pathsep.join((str(bindir), os.environ.get("PATH", "/usr/bin:/bin")))
    return executable, runner_path


def check_ansible_runtime(python_executable: str | None = None) -> tuple[Path, str]:
    """Check the entry point itself, not merely an importable Python module.

    This check is bounded and read-only. It runs before an approved job can
    reach the remote APT preflight; no remote host or credentials are used.
    """
    executable, runner_path = ansible_runtime(python_executable)
    environment = {**os.environ, "PATH": runner_path}
    try:
        result = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True,
            timeout=15, env=environment, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnsibleDependencyError(
            "Ansible Core could not start. Repair the OPM Python environment."
        ) from exc
    if result.returncode != 0:
        # Avoid leaking environment diagnostics into audit logs. Detailed
        # installer output remains in its root-only installation log.
        raise AnsibleDependencyError(
            "Ansible Core failed its startup check. Repair the OPM Python environment."
        )
    return executable, runner_path
