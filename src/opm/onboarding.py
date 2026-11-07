"""Explicit, password-assisted provisioning of a dedicated remote OPM account.

Provisioning is intentionally separate from normal Ansible patching. The
operator first verifies the remote SSH host key out of band, then supplies
one-time administrative credentials. Those credentials are sent only over the
already authenticated SSH session and never written to inventory or disk.

The remote script is fixed and narrow: it can create a fresh ``opm`` account,
reuse an existing account after verifying its sudo password, or replace an
existing account after explicit administrator authorization. New accounts use
password-required sudo via
the distribution's existing sudo group. It never grants NOPASSWD access,
disables password login, or changes the operator's account.
"""
from __future__ import annotations

import base64
import hashlib
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
import stat
import subprocess
import sys
from pathlib import Path

ERRORS = {
    'ACCOUNT_EXISTS', 'ACCOUNT_MISSING', 'PASSWORD_INVALID', 'WRONG_PASSWORD',
    'ACCOUNT_PASSWORDLESS', 'NONSTANDARD_ACCOUNT', 'DELETE_NOT_APPROVED',
    'ACCOUNT_BUSY', 'ACCOUNT_DELETE_FAILED', 'ACCOUNT_HOME_REMAINS',
    'KEY_PATH_UNSAFE', 'INVALID_ACTION', 'INVALID_KEY',
}

def fail(code):
    raise RuntimeError(code)


def write_authorized_key(account, key, reuse=False):
    # Never follow a symlink from a preexisting remote account into unrelated
    # files. Existing keys are preserved on reuse, appended only when missing.
    home = Path(account.pw_dir)
    if home != Path('/home/opm') or home.is_symlink():
        fail('NONSTANDARD_ACCOUNT')
    directory = home / '.ssh'
    if directory.is_symlink():
        fail('KEY_PATH_UNSAFE')
    existing_dir = directory.exists()
    if existing_dir and (not directory.is_dir() or directory.stat().st_uid != account.pw_uid):
        fail('KEY_PATH_UNSAFE')
    directory.mkdir(mode=0o700, exist_ok=True)
    if not directory.is_dir():
        fail('KEY_PATH_UNSAFE')
    if not existing_dir:
        os.chown(directory, account.pw_uid, account.pw_gid)
    directory.chmod(0o700)
    authorized = directory / 'authorized_keys'
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    if authorized.is_symlink():
        fail('KEY_PATH_UNSAFE')
    # A manually recreated opm account often has NO authorized_keys file.
    # Allow securely creating it. Reject ownership mismatches only for a file
    # that existed before we opened it; do not accept an existing root-owned
    # or foreign-owned key file during repair/reuse.
    authorized_existed = authorized.exists()
    # Only a regular file with one link can be safely modified in place.
    fd = os.open(authorized, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144:
            fail('KEY_PATH_UNSAFE')
        if reuse and authorized_existed and info.st_uid != account.pw_uid:
            fail('KEY_PATH_UNSAFE')
        os.fchown(fd, account.pw_uid, account.pw_gid)
        os.fchmod(fd, 0o600)
        # Compare key algorithm+data, ignoring any old comment label.
        material = key.split()[:2]
        os.lseek(fd, 0, os.SEEK_SET)
        existing = os.read(fd, info.st_size).decode('utf-8') if reuse else ''
        exists = any(line.split()[:2] == material for line in existing.splitlines())
        if not exists:
            prefix = '\n' if existing and not existing.endswith('\n') else ''
            os.write(fd, (prefix + key + '\n').encode('utf-8'))
    finally:
        os.close(fd)


def verify_existing_password(password):
    # Force expiration and reject an account that bypasses sudo password
    # prompts. Testing through root-run runuser does not depend on sshd
    # allowing password-based logins for the existing opm account.
    command = ['/usr/sbin/runuser', '-u', 'opm', '--', 'sudo']
    bypass = subprocess.run(command + ['-k', '-n', 'true'],
                            capture_output=True, text=True, timeout=16)
    if bypass.returncode == 0:
        fail('ACCOUNT_PASSWORDLESS')
    check = subprocess.run(command + ['-k', '-S', '-p', '', 'true'],
                           input=password + '\n', capture_output=True,
                           text=True, timeout=16)
    if check.returncode != 0:
        fail('WRONG_PASSWORD')

try:
    details = json.loads(sys.stdin.readline())
    if os.geteuid() != 0:
        fail('NONSTANDARD_ACCOUNT')
    if details.get('account') != 'opm':
        fail('INVALID_ACTION')
    action = details.get('account_mode', 'create')
    if action not in ('create', 'reuse', 'replace', 'repair_key', 'repair_or_create'):
        fail('INVALID_ACTION')
    key = details.get('public_key', '')
    password = details.get('sudo_password', '')
    if not isinstance(key, str) or not key.startswith('ssh-ed25519 ') or any(c in key for c in ('\n', '\r', '\x00')):
        fail('INVALID_KEY')
    if (not isinstance(password, str) or '\n' in password or '\r' in password
            or len(password) > 1024 or len(password) < (0 if action == 'repair_key' else 1 if action == 'reuse' else 12)):
        fail('PASSWORD_INVALID')
    try:
        account = pwd.getpwnam('opm')
    except KeyError:
        account = None

    details['account_was_present'] = account is not None
    if action == 'create' and account is not None:
        fail('ACCOUNT_EXISTS')
    if action in ('reuse', 'replace', 'repair_key') and account is None:
        fail('ACCOUNT_MISSING')
    if action == 'repair_or_create' and details.get('create_ack') is not True:
        fail('DELETE_NOT_APPROVED')
    if account is not None and (account.pw_uid == 0 or account.pw_dir != '/home/opm'):
        fail('NONSTANDARD_ACCOUNT')

    if action == 'reuse':
        verify_existing_password(password)  # MUST succeed before key mutation.
        write_authorized_key(account, key, reuse=True)
    elif action == 'repair_key' or (action == 'repair_or_create' and account is not None):
        # Administrator-approved, SSH-key-only repair. The existing account,
        # password and sudo policy are left unchanged. No fallback is used by
        # normal scans; an explicit administrator initiated this operation.
        write_authorized_key(account, key, reuse=True)
    else:
        if account is None and (Path('/home/opm').exists() or Path('/home/opm').is_symlink()):
            # Do not import keys or files from a stale home owned by an old UID.
            fail('ACCOUNT_HOME_REMAINS')
        if action == 'replace':
            if details.get('replace_ack') is not True:
                fail('DELETE_NOT_APPROVED')
            if account.pw_uid < 1000:  # refuse to delete a reserved/system UID
                fail('NONSTANDARD_ACCOUNT')
            # Explicit replacement may encounter the old OPM linger session.
            # Shut down only opm's logind user sessions before normal userdel.
            # Never use userdel -f or force-kill arbitrary processes.
            if Path('/usr/bin/loginctl').is_file():
                for operation in ('disable-linger', 'terminate-user'):
                    subprocess.run(['/usr/bin/loginctl', operation, 'opm'],
                                   capture_output=True, timeout=15, check=False)
            # Stop the dedicated opm user manager, but never issue killall or
            # userdel -f: these could destroy unrelated workloads. The account
            # manager may report a nonzero exit for harmless cleanup warnings
            # (for example, a missing /var/mail/opm spool) even though the
            # passwd entry was successfully deleted. Verify actual state.
            subprocess.run(['/usr/bin/systemctl', 'stop',
                            'user@' + str(account.pw_uid) + '.service'],
                           capture_output=True, timeout=15, check=False)
            removed = subprocess.run(['/usr/sbin/userdel', '-r', 'opm'],
                                     capture_output=True, timeout=30)
            try:
                pwd.getpwnam('opm')
            except KeyError:
                # Account truly removed, even if userdel returned a warning.
                pass
            else:
                # This error means the *account still exists*, not merely that
                # userdel printed a warning. Do not force-delete it.
                if removed.returncode == 8 or b'currently used by process' in (getattr(removed, 'stderr', None) or b''):
                    fail('ACCOUNT_BUSY')
                fail('ACCOUNT_DELETE_FAILED')
            # If the passwd entry is gone but its home remains, do not adopt
            # old SSH files into a different UID. Require manual review.
            home = Path('/home/opm')
            if home.exists() or home.is_symlink():
                fail('ACCOUNT_HOME_REMAINS')
        subprocess.run(['/usr/sbin/useradd', '--create-home', '--shell', '/bin/bash', 'opm'], check=True)
        subprocess.run(['/usr/sbin/chpasswd'], input='opm:' + password + '\n',
                       text=True, check=True, capture_output=True)
        subprocess.run(['/usr/sbin/usermod', '-aG', 'sudo', 'opm'], check=True)
        account = pwd.getpwnam('opm')
        write_authorized_key(account, key)
    actual_mode = 'create_missing' if action == 'repair_or_create' and not details.get('account_was_present', True) else action
    print(json.dumps({'ok': True, 'account': 'opm', 'mode': actual_mode}))
except Exception as exc:
    code = str(exc) if isinstance(exc, RuntimeError) else 'PROVISION_ERROR'
    if code not in ERRORS:
        code = 'PROVISION_ERROR'
    print(json.dumps({'ok': False, 'error_code': code}))
    sys.exit(1)
'''



def _payload(public_key: str, password: str, *, mode: str = 'create',
             replace_ack: bool = False, create_ack: bool = False) -> str:
    """Build a validated stdin record; reject an unapproved deletion early."""
    if mode not in ('create', 'reuse', 'replace', 'repair_key', 'repair_or_create'):
        raise OnboardingError('Invalid account setup choice')
    min_length = 0 if mode == 'repair_key' else 1 if mode == 'reuse' else 12
    if (not isinstance(password, str) or len(password) < min_length
            or len(password) > 1024 or any(c in password for c in "\n\r\x00")):
        raise OnboardingError("Choose an OPM sudo password of at least 12 characters without line breaks"
                              if mode != 'reuse' else "Enter the existing OPM sudo password")
    if not public_key.startswith("ssh-ed25519 ") or "\n" in public_key:
        raise OnboardingError("Invalid OPM public key")
    if mode == 'replace' and replace_ack is not True:
        raise OnboardingError('Explicit approval is required to delete the existing OPM account')
    if mode == 'repair_or_create' and create_ack is not True:
        raise OnboardingError('Explicit approval is required to create a missing OPM account')
    return json.dumps({"account": "opm", "public_key": public_key,
                       "sudo_password": password, "account_mode": mode,
                       "replace_ack": replace_ack, "create_ack": create_ack}) + "\n"


def probe_managed_account(host: dict, admin_user: str, login_password: str,
                          fingerprint: str) -> dict:
    """Read-only account check after user approved the actual host fingerprint.

    Authenticate the temporary administrator to the exact ED25519 host key the
    user approved. We intentionally do NOT persist that key before final setup.
    No privilege escalation or state mutation is performed by this probe.
    """
    if not USER_RE.fullmatch(admin_user) or not login_password:
        raise OnboardingError('Enter valid administrator SSH credentials')
    if not isinstance(fingerprint, str) or not fingerprint.startswith('SHA256:'):
        raise OnboardingError('Approve the server SSH fingerprint before checking its account')
    try:
        import paramiko
    except ImportError as exc:
        raise OnboardingError('Paramiko is not installed') from exc

    class ApprovedKeyOnly(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            actual = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
            if key.get_name() != 'ssh-ed25519' or actual != fingerprint:
                raise OnboardingError('SSH host fingerprint changed. No credentials were submitted')
            # Host key approved in-memory for this connection only.

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(ApprovedKeyOnly())
    try:
        client.connect(hostname=host['address'], port=int(host['ssh_port']),
                       username=admin_user, password=login_password,
                       look_for_keys=False, allow_agent=False,
                       timeout=10, auth_timeout=15, banner_timeout=15)
        _, stdout, _ = client.exec_command('id -u opm', timeout=12, get_pty=False)
        uid = stdout.read(80).decode('utf-8', errors='replace').strip()
        rc = stdout.channel.recv_exit_status()
        if rc == 0 and uid.isdecimal():
            return {'exists': True}
        if rc == 1:
            return {'exists': False}
        raise OnboardingError('Could not determine whether the remote opm account exists')
    except paramiko.AuthenticationException:
        raise OnboardingError('Administrator SSH authentication failed') from None
    except paramiko.BadHostKeyException:
        raise OnboardingError('SSH host fingerprint changed. No credentials were submitted') from None
    except (OSError, EOFError, paramiko.SSHException):
        raise OnboardingError('SSH account check failed. Verify server reachability and credentials') from None
    finally:
        client.close()


_PROVISION_ERRORS = {
    'ACCOUNT_EXISTS': 'The remote opm account already exists. Select Reuse or Delete and recreate.',
    'ACCOUNT_MISSING': 'The remote opm account no longer exists. Check the account again.',
    'WRONG_PASSWORD': 'Existing opm sudo password was rejected. Reuse made no SSH key changes.',
    'ACCOUNT_PASSWORDLESS': 'Existing opm account permits passwordless sudo. Review its configuration before reuse.',
    'NONSTANDARD_ACCOUNT': 'Existing opm account has an unexpected home directory or unsafe identity. Inspect it manually.',
    'DELETE_NOT_APPROVED': 'Account replacement was not explicitly approved.',
    'ACCOUNT_BUSY': 'The old opm account is still in use and was not removed. Check sudo ps -u opm -o pid,cmd on the target; stop the relevant processes before retrying. No forced deletion occurred.',
    'ACCOUNT_DELETE_FAILED': 'The old opm account still exists after the deletion attempt. Inspect userdel and account state on the target before retrying. No forced deletion occurred.',
    'ACCOUNT_HOME_REMAINS': 'The old opm account was removed, but /home/opm still exists. OPM stopped before recreating it so old SSH keys and files cannot silently be reused. Inspect or remove that home manually before retrying.',
    'KEY_PATH_UNSAFE': 'Existing SSH authorization path is unsafe; inspect it before retrying.',
    'PASSWORD_INVALID': 'Remote account password did not meet the selected mode requirements.',
    'INVALID_ACTION': 'Invalid remote account setup action.',
    'INVALID_KEY': 'New controller SSH public key is invalid.',
    'PROVISION_ERROR': 'Remote account setup failed. Inspect the target server before retrying.',
}


def bootstrap_account(host: dict, config: Config, admin_user: str, login_password: str,
                      admin_sudo_password: str, opm_password: str, *,
                      mode: str = 'create', replace_ack: bool = False,
                      create_ack: bool = False) -> dict:
    """Create, reuse, or explicitly replace the remote OPM account via SSH.

    Paramiko enforces the approved known_hosts key. Reuse checks the existing
    sudo password before modifying authorized_keys; replacement requires
    explicit consent. Administrator and OPM secrets are transient only.
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
    data = _payload(identity["public_key"], opm_password, mode=mode,
                    replace_ack=replace_ack, create_ack=create_ack)
    if mode in ('replace', 'repair_key', 'repair_or_create') and admin_user == 'opm':
        raise OnboardingError('Use a separate administrator account to repair or replace opm')
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
        try:
            parsed = json.loads(response.strip())
        except ValueError as exc:
            raise OnboardingError("Unexpected bootstrap response; inspect the target manually") from exc
        if exit_code != 0 or parsed.get('ok') is not True:
            code = parsed.get('error_code', 'PROVISION_ERROR')
            raise OnboardingError(_PROVISION_ERRORS.get(code, _PROVISION_ERRORS['PROVISION_ERROR']))
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
            "sudo_mode": "password", "provisioned": True, "mode": parsed.get('mode', mode)}
