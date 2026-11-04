"""Explicit, password-assisted provisioning of a dedicated remote OPM account.

Provisioning is intentionally separate from normal Ansible patching. The
operator first verifies the remote SSH host key out of band, then supplies
one-time administrative credentials. Those credentials are sent only over the
already authenticated SSH session and never written to inventory or disk.

The remote script is fixed and narrow: it creates one account named ``opm``,
installs the controller's public key, and enables password-required sudo via
the distribution's existing sudo group. It never grants NOPASSWD access,
disables password login, or changes the operator's account.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path

from .config import Config
from .validation import USER_RE


class OnboardingError(ValueError):
    """A user-facing bootstrap failure without credential disclosure."""


def ensure_managed_key(config: Config) -> dict:
    """Generate one dedicated Ed25519 keypair if it does not already exist.

    The private key has no passphrase because the controller's detached worker
    cannot request a passphrase after SSH disconnects. Its permissions and
    filesystem access are therefore security-critical. The remote sudo
    password remains a separate, interactive authorization factor.
    """
    config.ensure()
    private = config.managed_key
    public = Path(str(private) + ".pub")
    if private.exists() != public.exists():
        raise OnboardingError("OPM SSH keypair is incomplete. Repair it manually; no keys were overwritten")
    if not private.exists():
        result = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                                 "opm-controller", "-f", str(private)], capture_output=True,
                                text=True, timeout=20)
        if result.returncode:
            raise OnboardingError("Could not create the OPM SSH identity")
    private.chmod(0o600)
    public.chmod(0o644)
    value = public.read_text(encoding="utf-8").strip()
    if not value.startswith("ssh-ed25519 ") or "\n" in value:
        raise OnboardingError("The managed public key is invalid")
    return {"key_path": str(private), "public_key": value}


# This fixed script executes as root only after the operator explicitly
# authorizes provisioning. It accepts all variable data over SSH stdin,
# avoiding usernames, key contents, or passwords in remote process arguments.
REMOTE_CREATE_ACCOUNT = r'''
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

try:
    details = json.loads(sys.stdin.readline())
    if os.geteuid() != 0:
        raise RuntimeError('Administrator privileges are required')
    if details.get('account') != 'opm':
        raise RuntimeError('Unexpected account name')
    key = details.get('public_key', '')
    password = details.get('sudo_password', '')
    if not key.startswith('ssh-ed25519 ') or '\n' in key:
        raise RuntimeError('Invalid public key')
    if len(password) < 12 or '\n' in password or '\r' in password:
        raise RuntimeError('Invalid account password')
    try:
        pwd.getpwnam('opm')
    except KeyError:
        pass
    else:
        raise RuntimeError('The opm account already exists. No changes made')

    subprocess.run(['/usr/sbin/useradd', '--create-home', '--shell', '/bin/bash', 'opm'], check=True)
    subprocess.run(['/usr/sbin/chpasswd'], input='opm:' + password + '\n',
                   text=True, check=True, capture_output=True)
    subprocess.run(['/usr/sbin/usermod', '-aG', 'sudo', 'opm'], check=True)
    account = pwd.getpwnam('opm')
    directory = Path(account.pw_dir) / '.ssh'
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(directory, account.pw_uid, account.pw_gid)
    directory.chmod(0o700)
    authorized = directory / 'authorized_keys'
    # This is a new account, so only one SSH public key is installed.
    authorized.write_text(key + '\n', encoding='utf-8')
    os.chown(authorized, account.pw_uid, account.pw_gid)
    authorized.chmod(0o600)
    print(json.dumps({'ok': True, 'account': 'opm'}))
except Exception as exc:
    # Never include exception repr or subprocess input: it could contain
    # secrets. A partial failure requires administrator inspection.
    print(json.dumps({'ok': False,
                      'error': 'Account provisioning failed. Check the target console; partial setup is possible.'}))
    sys.exit(1)
'''


def _payload(public_key: str, password: str) -> str:
    """Build a validated stdin record for the fixed remote provisioning code."""
    if len(password) < 12 or len(password) > 1024 or "\n" in password or "\r" in password:
        raise OnboardingError("Choose an OPM sudo password of at least 12 characters without line breaks")
    if not public_key.startswith("ssh-ed25519 ") or "\n" in public_key:
        raise OnboardingError("Invalid OPM public key")
    return json.dumps({"account": "opm", "public_key": public_key, "sudo_password": password}) + "\n"


def bootstrap_account(host: dict, config: Config, admin_user: str, login_password: str,
                      admin_sudo_password: str, opm_password: str) -> dict:
    """Create the remote OPM account through verified password-based SSH.

    Paramiko has RejectPolicy enabled and loads only OPM's approved known_hosts
    file. The operator must already have approved a fingerprint through the
    host-key wizard. Neither the admin login nor password is persisted.

    Existing OPM accounts are not adopted or reset: account takeover and
    permission changes require an explicit, separately designed workflow.
    """
    if not USER_RE.fullmatch(admin_user):
        raise OnboardingError("Invalid administrator SSH username")
    if not login_password:
        raise OnboardingError("Provide the administrator SSH login password")
    # A line break in the sudo password could alter the separate JSON payload
    # sent over stdin, so reject it before opening any remote session.
    if any(c in admin_sudo_password for c in ("\n", "\r", "\x00")):
        raise OnboardingError("Invalid administrator sudo password")
    identity = ensure_managed_key(config)
    data = _payload(identity["public_key"], opm_password)
    try:
        import paramiko
    except ImportError as exc:
        raise OnboardingError("Paramiko is not installed; reinstall OPM with its dependencies") from exc

    client = paramiko.SSHClient()
    client.load_host_keys(str(config.known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(hostname=host["address"], port=int(host["ssh_port"]),
                       username=admin_user, password=login_password,
                       look_for_keys=False, allow_agent=False,
                       timeout=10, auth_timeout=15, banner_timeout=15)
        # The remote code is a fixed string, and the base64 form contains no
        # shell metacharacters. No credential is included in the command.
        encoded = base64.b64encode(REMOTE_CREATE_ACCOUNT.encode()).decode()
        launcher = ("/usr/bin/python3 -c 'import base64; "
                    f"exec(base64.b64decode(\"{encoded}\"))'")
        if admin_user == "root":
            command = launcher
            payload = data
        else:
            # Check whether the administrator already has passwordless sudo.
            # If so, sudo will not consume a password line from stdin, and
            # sending one would make the bootstrap Python script read the
            # password instead of its JSON configuration.
            _, privilege_out, _ = client.exec_command("sudo -n true", timeout=12, get_pty=False)
            sudo_without_password = privilege_out.channel.recv_exit_status() == 0
            if sudo_without_password:
                command = "sudo -n " + launcher
                payload = data
            else:
                command = "sudo -k -S -p '' " + launcher
                payload = admin_sudo_password + "\n" + data
        stdin, stdout, stderr = client.exec_command(command, timeout=40, get_pty=False)
        stdin.write(payload)
        stdin.flush()
        stdin.channel.shutdown_write()
        response = stdout.read(8192).decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise OnboardingError("Remote account creation failed; inspect the target console before retrying")
        try:
            parsed = json.loads(response.strip())
        except ValueError as exc:
            raise OnboardingError("Unexpected bootstrap response; inspect the target manually") from exc
        if parsed.get("ok") is not True:
            raise OnboardingError("Remote account provisioning did not complete")
    except paramiko.AuthenticationException:
        raise OnboardingError("Administrator SSH authentication failed. Check the username, password, and whether SSH password login is enabled.") from None
    except paramiko.BadHostKeyException:
        raise OnboardingError("The server SSH host key does not match the approved fingerprint. No setup attempted.") from None
    except (OSError, EOFError, paramiko.SSHException) as exc:
        # Avoid logging the exception body because dependencies can include
        # connection parameters. Never include submitted passwords.
        raise OnboardingError(f"Administrator SSH setup failed ({type(exc).__name__}). Check SSH reachability and authentication") from None
    finally:
        client.close()
    return {"account": "opm", "key_path": identity["key_path"],
            "sudo_mode": "password", "provisioned": True}
