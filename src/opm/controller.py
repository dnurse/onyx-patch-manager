"""Local-only controller API and persistent background execution authority.

Protocol: one JSON object per Unix-socket connection, one JSON reply. Peer
credentials restrict access to processes owned by this Unix account. The API
never exposes arbitrary remote command execution. Only known operations are
accepted, and all patch jobs require a recorded, explicit approval.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
import re
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import __version__
from .config import Config
from .remote import accept_host_key, inspect_host, probe_host_key, probe_icmp_reachability, test_connection, verify_sudo_password, verify_compose_source
from .onboarding import bootstrap_account, ensure_managed_key, probe_managed_account
from .runtime import check_ansible_runtime, AnsibleDependencyError
from .store import Store
from .compose_inventory import docker_compose_projects
from .compose_updates import update_compose_service
from .validation import sanitize, validate_host, USER_RE

MAX_MESSAGE = 256_000
MAX_REPLY = 2_000_000  # Inventory replies can span multiple CSV import batches.
SUDO_AUTH_TTL_SECONDS = 600  # Ten minutes from a successful authenticated scan.
_CREDENTIAL_SCOPE_RE = re.compile(r'^[a-f0-9]{64}$')


class Controller:
    """Thin asynchronous coordination layer over a durable SQLite repository."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config.load()
        self.config.ensure()
        self.store = Store(self.config.database)
        # Every non-patch operation is owned by the long-lived controller event
        # loop, not a Django request. Keep references until completion.
        self._scans: set[asyncio.Task] = set()
        self._job_watch_task: asyncio.Task | None = None
        # Dashboard probes are cached in the central controller, not per
        # browser session. Opening several tabs must not multiply network
        # checks against every managed host. A 110-second cache complements
        # the browser's 120-second polling interval.
        self._reachability: dict[str, tuple[float, dict]] = {}
        self._reachability_lock = asyncio.Lock()
        # Serialize onboarding to prevent two browser requests provisioning the same host.
        self._enrollment_lock = asyncio.Lock()
        # Memory-only credential broker. Secrets never enter the database,
        # systemd unit arguments, environment variables or persistent files.
        # A worker claims credentials once over the owner-only Unix socket.
        self._job_passwords: dict[str, dict[str, str]] = {}
        # Temporary sudo authorization is bound to one authenticated Django
        # login session, one host, and a strict 10-minute expiry. A different
        # browser session or operator cannot reuse these credentials. Nothing
        # here is serialized into SQLite, HTTP cookies or browser storage.
        self._sudo_cache: dict[tuple[str, str], tuple[float, str]] = {}

    def _cached_password(self, scope: str, host_id: str) -> str | None:
        """Return a scoped, unexpired sudo credential from controller RAM."""
        if not _CREDENTIAL_SCOPE_RE.fullmatch(scope):
            return None
        key = (scope, host_id)
        record = self._sudo_cache.get(key)
        if not record:
            return None
        if record[0] <= time.monotonic():
            self._sudo_cache.pop(key, None)
            return None
        return record[1]

    def _remember_sudo(self, scope: str, host_id: str, password: str) -> None:
        """Retain credentials after a validated sudo check or authorized scan.

        The timer expires the actual plaintext reference at the deadline even
        when no further requests arrive. Reauthorization installs a new timer
        and the older timer cannot delete the newer authorization.
        """
        if not _CREDENTIAL_SCOPE_RE.fullmatch(scope):
            return
        key = (scope, host_id)
        expiry = time.monotonic() + SUDO_AUTH_TTL_SECONDS
        self._sudo_cache[key] = (expiry, password)

        def expire() -> None:
            record = self._sudo_cache.get(key)
            if record and record[0] <= expiry:
                self._sudo_cache.pop(key, None)

        asyncio.get_running_loop().call_later(SUDO_AUTH_TTL_SECONDS, expire)

    async def start(self) -> None:
        """Bind a private socket and recover abandoned jobs without replaying."""
        path = self.config.socket
        if path.exists():
            # Starting twice must not silently steal another controller's
            # socket. The installer runs one instance via a systemd user unit.
            try:
                reader, writer = await asyncio.open_unix_connection(str(path))
                writer.close()
                await writer.wait_closed()
                raise RuntimeError("A controller is already running")
            except (ConnectionRefusedError, FileNotFoundError):
                path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.handle, path=str(path))
        path.chmod(0o600)
        self.store.reconcile_activities()
        await self.reconcile_jobs(startup=True)
        self._job_watch_task = asyncio.create_task(self._watch_patch_jobs())
        try:
            async with server:
                await server.serve_forever()
        finally:
            if self._job_watch_task:
                self._job_watch_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._job_watch_task
            path.unlink(missing_ok=True)

    async def _watch_patch_jobs(self) -> None:
        """Detect vanished detached workers without requiring controller restart."""
        while True:
            await asyncio.sleep(12)
            try:
                await self.reconcile_jobs()
            except Exception:
                # A temporary systemd/SQLite error must not stop the controller.
                # The next pass retries; ambiguous unit state is never treated
                # as proof that a worker stopped.
                continue

    async def _worker_exit_detail(self, unit: str) -> str:
        """Report systemd exit metadata only when still available and trusted."""
        cmd = ['systemctl', '--user', 'show', unit, '--property=Result',
               '--property=ExecMainStatus', '--property=ExecMainCode', '--no-pager']
        try:
            p = await asyncio.to_thread(subprocess.run, cmd, capture_output=True,
                                        text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            return ''
        if p.returncode:
            return ''
        values = dict(line.split('=', 1) for line in p.stdout.splitlines() if '=' in line)
        result = values.get('Result', '')
        code = values.get('ExecMainCode', '')
        status = values.get('ExecMainStatus', '')
        if result not in {'success', 'exit-code', 'signal', 'core-dump', 'timeout',
                          'watchdog', 'oom-kill', 'resources', 'start-limit-hit'}:
            return ''
        parts = ['systemd result: ' + result]
        if code.isdecimal() and len(code) <= 3:
            parts.append('exit code type: ' + code)
        if status.isdecimal() and len(status) <= 3:
            parts.append('exit status: ' + status)
        return ', '.join(parts)

    async def reconcile_jobs(self, *, startup: bool = False) -> None:
        """Reconcile orphan workers; never restart a potentially partial patch.

        A newly queued job may not have reached systemd-run yet. Allow a
        startup grace period for queued jobs on periodic checks, and refuse
        to infer a stopped worker from a systemd communication error.
        """
        for job in self.store.jobs():
            if job['state'] not in ('queued', 'running'):
                continue
            if (not startup and job['state'] == 'queued' and
                    time.time() - job['created'] < 45):
                continue
            unit = f"opm-job-{job['id']}.service"
            cmd = ['systemctl', '--user', 'is-active', unit]
            try:
                p = await asyncio.to_thread(subprocess.run, cmd, capture_output=True,
                                            text=True, timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                continue  # Unknown status is not proof of interruption.
            state = p.stdout.strip().lower()
            if state in ('active', 'activating', 'reloading') and p.returncode == 0:
                continue
            if state not in ('inactive', 'failed', 'unknown'):
                continue  # E.g. deactivating or user-bus error; worker may still be running.
            detail = await self._worker_exit_detail(unit)
            self.store.interrupt_job(job['id'], 'Patch worker no longer active', detail)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Authenticate the Unix peer and process one bounded JSON request."""
        try:
            transport_socket = writer.get_extra_info("socket")
            credentials = transport_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, uid, _ = struct.unpack("3i", credentials)
            if uid != os.getuid():
                raise PermissionError("Not authorized to access the local controller")
            line = await asyncio.wait_for(reader.readline(), timeout=120)
            if len(line) >= MAX_MESSAGE:
                raise ValueError("Request is too large")
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get("method"), str):
                raise ValueError("Invalid request")
            result = await self.dispatch(request["method"], request.get("params") or {})
            reply = {"ok": True, "result": result}
        except (ValueError, PermissionError, RuntimeError, sqlite3.Error, OSError, KeyError) as exc:
            reply = {"ok": False, "error": sanitize(str(exc))}
        except Exception as exc:
            reply = {"ok": False, "error": "Internal controller error: " + sanitize(str(exc))}
        try:
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def dispatch(self, method: str, params: dict) -> object:
        """Whitelist operations rather than accepting client-supplied commands."""
        if method == "ping":
            return {"status": "ready", "version": __version__}
        if method == "activities.list":
            # The browser sees an integrated list, including independent
            # systemd patch workers. No privileged action is exposed here.
            records = self.store.recent_activities(30)
            for record in records:
                record['attention'] = (record['state'] in ('failed','partial','interrupted')
                                       and not record.get('acknowledged'))
            for job in self.store.jobs()[:30]:
                targets = self.store.job(job['id'])['targets']
                done = sum(t['state'] not in ('queued', 'running') for t in targets)
                records.append({
                    'id': job['id'], 'kind': 'patch',
                    'label': 'OS dry run' if job.get('mode') == 'dry_run' else 'OS patch job',
                    'state': job['state'],
                    'attention': (job['state'] in ('failed','interrupted','partially_completed','completed_with_warnings')
                                  and not job.get('acknowledged')),
                    'detail': job['error'] or
                        ('Running Ansible tasks' if job['state'] == 'running' else job['state'].replace('_', ' ').title()),
                    'total': len(targets), 'completed': done,
                    'failed': sum(t['state'] in ('failed','interrupted') for t in targets),
                    'created': job['created'], 'updated': job['finished'] or job['started'] or job['created'],
                    'finished': job['finished'], 'result': {},
                })
            return sorted(records, key=lambda item: item['created'], reverse=True)[:30]
        if method == "activities.get":
            return self.store.activity(str(params.get('id', '')))
        if method == "activities.history":
            return self.store.recent_activities(250, include_events=True)
        if method == "web.access.configure":
            # The authenticated Django administrator is responsible for
            # authorizing this privileged local change. The controller only
            # accepts the fixed schema and invokes the trusted helper.
            mode = str(params.get('mode', ''))
            hostname = str(params.get('hostname', ''))
            proxy_ip = str(params.get('proxy_ip', ''))
            if mode not in ('lan', 'proxy', 'localhost'):
                raise ValueError('Invalid web access mode')
            if len(hostname) > 253 or len(proxy_ip) > 64 or any(
                    x in hostname + proxy_ip for x in ('\n', '\r', '\x00')):
                raise ValueError('Invalid web access parameters')
            # Validate values using the same trusted, standalone helper that
            # applies them. Its file is installed root-owned, not from the
            # editable application source directory.
            helper = Path('/usr/local/share/opm-installer/OPMWebAccess.py')
            if not helper.is_file() or helper.is_symlink():
                raise ValueError('Web access helper missing. Upgrade OPM with the installer first')
            activity = self.store.begin_activity('web_access', 'Web access configuration',
                                                 ['config:web-access'])
            self._launch_activity(self.configure_web_background(
                activity['id'], str(helper), mode, hostname, proxy_ip))
            return {'activity_id': activity['id']}
        if method == "hosts.test.start":
            host = self.store.host(str(params['id']))
            if not host['verified']:
                raise ValueError('Complete server setup before testing SSH')
            activity = self.store.begin_activity('ssh_test', f"Testing SSH: {host['name']}",
                                                 ['host:' + host['id']])
            self._launch_activity(self.test_host_background(activity['id'], host))
            return {'activity_id': activity['id'], 'scheduled': 1}
        if method == 'hosts.ssh_repair.start':
            # Only an existing verified OPM host can use this non-destructive
            # recovery path. The Django view separately requires Administrator.
            host = self.store.host(str(params.get('id', '')))
            if not host['verified'] or host['ssh_user'] != 'opm':
                raise ValueError('Repair SSH is only available for verified OPM-managed servers')
            fingerprint = str(params.get('fingerprint', ''))
            if (not fingerprint.startswith('SHA256:') or len(fingerprint) > 120
                    or params.get('trust_ack') is not True):
                raise ValueError('Approve the current SSH host fingerprint before repair')
            if self.store.is_locked(host['id']):
                raise ValueError('A patch job is using this server. Retry after it finishes')
            credentials = {key: str(params.get(key, '')) for key in
                ('admin_user', 'login_password', 'admin_sudo_password', 'opm_password', 'new_opm_password')}
            credentials['create_if_missing'] = params.get('create_if_missing') is True
            if credentials['create_if_missing'] and len(credentials['new_opm_password']) < 12:
                raise ValueError('A new opm password of at least 12 characters is required')
            if not USER_RE.fullmatch(credentials['admin_user']) or not credentials['login_password']:
                raise ValueError('Administrator SSH username and password are required')
            activity = self.store.begin_activity('ssh_repair',
                'Repairing SSH: ' + host['name'], ['host:' + host['id']])
            self._launch_activity(self.repair_ssh_background(
                activity['id'], host['id'], fingerprint, credentials))
            return {'activity_id': activity['id'], 'scheduled': 1}
        if method == 'hosts.account_probe':
            # Credentials are held only in this RPC and a short-lived SSH
            # request. Only verify a user-approved SSH fingerprint; never
            # blindly send passwords to an unauthenticated ssh-keyscan host.
            host = validate_host({
                'name': 'candidate-onboarding', 'ssh_user': 'opm',
                'address': params.get('address', ''),
                'ssh_port': params.get('ssh_port', 22),
            })
            fingerprint = str(params.get('fingerprint', ''))
            if not fingerprint.startswith('SHA256:') or len(fingerprint) > 120:
                raise ValueError('Approve the SSH host fingerprint before account discovery')
            return await asyncio.to_thread(probe_managed_account, host,
                str(params.get('admin_user', '')),
                str(params.get('login_password', '')), fingerprint)
        if method == "hosts.enroll.start":
            # Enforce non-secret shape and host identity before recording an
            # operation. Full credential validation still occurs inside the
            # existing enrollment procedure.
            fields = validate_host({**params.get('host', {}),
                'ssh_user': 'opm', 'sudo_mode': 'password', 'key_path': ''})
            if not str(params.get('fingerprint', '')).startswith('SHA256:'):
                raise ValueError('Approve a server fingerprint before setup')
            if params.get('account_mode', 'create') not in ('create', 'reuse', 'replace'):
                raise ValueError('Select a valid OPM account setup action')
            if (params.get('account_mode') == 'replace' and
                    params.get('replace_ack') is not True):
                raise ValueError('Confirm deletion of the old OPM account before proceeding')
            lock_keys = [f"candidate:{fields['address']}:{fields['ssh_port']}"]
            if params.get('existing_id'):
                original = self.store.host(str(params['existing_id']))
                if original['verified']:
                    raise ValueError('This server has already completed setup')
                lock_keys.append('host:' + original['id'])
            activity = self.store.begin_activity('enrollment',
                f"Configuring server: {fields['name']}", lock_keys)
            # The temporary administrator and remote account passwords stay
            # in controller memory only until the activity completes. Never
            # use systemd-run or environment variables for those secrets.
            self._launch_activity(self.enroll_background(activity['id'], params))
            return {'activity_id': activity['id'], 'scheduled': 1}
        if method == "keys.ensure":
            identity = await asyncio.to_thread(ensure_managed_key, self.config)
            # Never expose private key content to web clients.
            return {"key_path": identity["key_path"]}
        if method == "hosts.probe_candidate":
            # Browser discovery occurs before a server has been registered.
            # Validate the target with the same strict rules as stored inventory.
            candidate = validate_host({
                "name": "pending-onboarding", "ssh_user": "opm",
                "address": params.get("address", ""),
                "ssh_port": params.get("ssh_port", 22),
            })
            return await asyncio.to_thread(probe_host_key, candidate)
        if method == "hosts.enroll":
            # One controlled operation replaces the disconnected add, trust,
            # generate key, provision, and test actions. No secrets persist.
            async with self._enrollment_lock:
                return await asyncio.to_thread(self.enroll_host, params)
        if method == "hosts.bootstrap":
            host = self.store.host(params["id"])
            if self.store.is_locked(host["id"]):
                raise ValueError("Cannot provision a host while a patch job holds its lock")
            # Host keys must be trusted first. Paramiko enforces known_hosts
            # and will refuse unknown or changed server identities.
            result = await asyncio.to_thread(
                bootstrap_account, host, self.config,
                str(params.get("admin_user", "")), str(params.get("login_password", "")),
                str(params.get("admin_sudo_password", "")) or str(params.get("login_password", "")),
                str(params.get("opm_password", "")))
            updated = self.store.update_host(host["id"], {**host, "ssh_user": "opm",
                "key_path": result["key_path"], "sudo_mode": "password"})
            # Key login is a separate operation; do not mark verified on the
            # strength of provisioning success alone.
            try:
                check = await asyncio.to_thread(test_connection, updated, self.config)
                sudo_ok = await asyncio.to_thread(
                    verify_sudo_password, updated, self.config,
                    str(params.get("opm_password", "")))
                if sudo_ok:
                    self.store.verified(host["id"], True)
                    return {"provisioned": True, "verified": True, "sudo": "password confirmed"}
                return {"provisioned": True, "verified": False,
                        "warning": "SSH works, but sudo password verification failed. Inspect the target"}
            except Exception:
                return {"provisioned": True, "verified": False,
                        "warning": "Account created, but key login is not yet verified. Use Test SSH"}
        if method == "hosts.compose_verifications":
            host = self.store.host(str(params.get('id', '')))
            return list(self.store.compose_verifications(host['id']).values())
        if method == "hosts.compose_verify.start":
            # Only admin-authorized Django views can reach this operation.
            # Independently validate the project against the latest controller
            # inventory; never accept a path or command from the web client.
            host = self.store.host(str(params.get('id', '')))
            if not host['verified']:
                raise ValueError('Complete SSH setup before Compose verification')
            project = params.get('project')
            if not isinstance(project, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,119}', project):
                raise ValueError('Invalid Docker Compose project')
            if self.store.is_locked(host['id']):
                raise ValueError('A patch job is using this server. Retry later')
            scan = self.store.last_scan(host['id'])
            if not scan or scan['state'] != 'completed':
                raise ValueError('Scan this server before verifying its Compose configuration')
            projects = docker_compose_projects((scan.get('result') or {}).get('engines'))
            candidates = [p for p in projects if p['name'] == project]
            if len(candidates) != 1:
                raise ValueError('Compose project is missing or ambiguous. Run a fresh scan')
            candidate = candidates[0]
            if candidate['vendor_managed'] or candidate['oneoff']:
                raise ValueError('Vendor-managed or one-off Compose projects are excluded')
            if not candidate['config_evidence'] or candidate['legacy_evidence']:
                raise ValueError('Compose project has insufficient source evidence. Run a fresh scan')
            if not (candidate['scope'] == 'root' or candidate['scope'] == 'OPM SSH user'):
                raise ValueError('Only rootful Docker Compose workloads are supported for verification')
            supplied = params.get('sudo_password', '')
            if not isinstance(supplied, str) or len(supplied) > 2048 or any(
                    c in supplied for c in '\r\n\x00'):
                raise ValueError('Invalid sudo authorization')
            scope = str(params.get('credential_scope', ''))
            password = supplied or self._cached_password(scope, host['id'])
            if host.get('sudo_mode') == 'password' and not password:
                raise ValueError('Enter the server sudo password to verify Docker Compose')
            activity = self.store.begin_activity('compose_verify',
                'Verifying Docker Compose: ' + host['name'] + ' / ' + project,
                ['host:' + host['id']])
            self._launch_activity(self.verify_compose_background(
                activity['id'], host, project, candidate['scope'], password, bool(supplied), scope))
            return {'activity_id': activity['id'], 'scheduled': 1}
        if method == "hosts.compose_update.start":
            # Administrator-approved POSTs only. The controller enforces all
            # operational prerequisites even if the browser is bypassed.
            host = self.store.host(str(params.get('id', '')))
            project, service = params.get('project'), params.get('service')
            if (not isinstance(project, str) or
                not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,119}', project) or
                not isinstance(service, str) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', service)):
                raise ValueError('Invalid Docker Compose project or service')
            if not host['verified'] or host['sudo_mode'] == 'read_only':
                raise ValueError('Verified, writable SSH access is required')
            if params.get('confirm_update') is not True:
                raise ValueError('Acknowledge the container restart and data risk before updating')
            if host.get('protected') and params.get('protected_ack') is not True:
                raise ValueError('Protected server approval is required')
            scan = self.store.last_scan(host['id'])
            if not scan or scan['state'] != 'completed':
                raise ValueError('Scan the server before updating a Docker Compose service')
            candidates = [p for p in docker_compose_projects(
                (scan.get('result') or {}).get('engines')) if p['name'] == project]
            if len(candidates) != 1:
                raise ValueError('Compose project is missing or ambiguous; scan the server again')
            candidate = candidates[0]
            if (candidate['vendor_managed'] or candidate['oneoff'] or
                candidate['legacy_evidence'] or not candidate['config_evidence'] or
                candidate['scope'] not in ('root', 'OPM SSH user')):
                raise ValueError('This Compose project cannot be updated by OPM')
            services = [s for s in candidate['services'] if s['name'] == service]
            if not services or any(s['vendor_managed'] for s in services):
                raise ValueError('Selected service is not an eligible Compose service')
            if any('@sha256:' in s['image'] for s in services):
                raise ValueError('Digest-pinned services are review-only')
            record = self.store.compose_verifications(host['id']).get(
                (project, candidate['scope']))
            if (not record or record['status'] != 'verified' or
                    time.time() - record['checked'] > 3600):
                raise ValueError('Verify Configuration for this project before applying an update')
            supplied = params.get('sudo_password', '')
            if (not isinstance(supplied, str) or len(supplied) > 2048 or
                    any(c in supplied for c in '\r\n\x00')):
                raise ValueError('Invalid sudo authorization')
            scope = str(params.get('credential_scope', ''))
            password = supplied or self._cached_password(scope, host['id'])
            if host['sudo_mode'] == 'password' and not password:
                raise ValueError('Enter the server sudo password to update its Compose service')
            activity = self.store.begin_activity('compose_update',
                'Updating Docker Compose: ' + host['name'] + ' / ' + project + ' / ' + service,
                ['host:' + host['id']])
            self._launch_activity(self.update_compose_background(
                activity['id'], host, project, service, candidate['scope'],
                password, bool(supplied), scope))
            return {'activity_id': activity['id'], 'scheduled': 1}
        if method == "hosts.logo_info":
            # One bounded read-only RPC per image request, not two full
            # hosts/scans lists. Never expose arbitrary inspection metadata.
            host = self.store.host(str(params.get('id', '')))
            scan = self.store.last_scan(host['id'])
            result = (scan or {}).get('result') or {}
            engines = result.get('engines') or {}
            # Expose only minimum logo metadata. Include consolidated root and
            # rootless Podman observations without exposing owners or scopes.
            icon_engines = {}
            for engine in ('docker', 'podman'):
                inventory = engines.get(engine) or {}
                containers = inventory.get('containers') or []
                icon_engines[engine] = {'containers': [
                    {'image': c.get('image', ''), 'managed_by': c.get('managed_by', '')}
                    for c in containers[:500] if isinstance(c, dict)]}
            return {
                'id': host['id'], 'name': host['name'],
                'logo_choice': host.get('logo_choice') or 'auto',
                'protected': bool(host['protected']),
                'os': result.get('os') or {},
                'engines': icon_engines,
            }
        if method == "hosts.list":
            return self.store.hosts()
        if method == "hosts.reachability":
            # A distinct, read-only operation deliberately avoids APT, Docker,
            # SSH authentication and changes to historical scan records.
            return await self.dashboard_reachability()
        if method == "hosts.add":
            return self.store.add_host(params)
        if method in ("hosts.csv_preview", "hosts.csv_import"):
            return self.store.import_hosts(params.get("profiles"), preview=method == "hosts.csv_preview")
        if method == "hosts.csv_export":
            return self.store.host_csv_page(str(params.get("after", "")))
        if method == "hosts.update":
            return self.store.update_host(params["id"], params["data"])
        if method == "hosts.logo.set":
            from .server_logos import choices_for_host
            host = self.store.host(str(params.get('id', '')))
            scan = self.store.last_scan(host['id'])
            host['os'] = ((scan or {}).get('result') or {}).get('os') or {}
            host['engines'] = ((scan or {}).get('result') or {}).get('engines') or {}
            choice = params.get('choice')
            valid = {item['value'] for item in choices_for_host(host)}
            if not isinstance(choice, str) or choice not in valid:
                raise ValueError('Logo is not available for this server. Run a fresh scan')
            return self.store.set_host_logo(host['id'], choice)
        if method == "hosts.remove":
            self.store.remove_host(params["id"])
            return {"removed": True}
        if method == "hosts.probe":
            return await asyncio.to_thread(probe_host_key, self.store.host(params["id"]))
        if method == "hosts.trust":
            host = self.store.host(params["id"])
            data = await asyncio.to_thread(accept_host_key, host, self.config, params["fingerprint"])
            # Approval authenticates the presented key, not the user's login.
            # Connection status becomes verified only after a real SSH test.
            return data
        if method == "hosts.test":
            host = self.store.host(params["id"])
            result = await asyncio.to_thread(test_connection, host, self.config)
            self.store.verified(host["id"], True)
            return result
        if method == "scans.start":
            # A patch-review refresh may scan only its selected stale hosts.
            # Validate the complete target list before any remote work starts.
            if params.get("ids") is not None:
                ids = params["ids"]
                if (params.get("id") or not isinstance(ids, list) or not ids
                        or len(ids) > 100 or len(set(ids)) != len(ids)
                        or any(not isinstance(hid, str) for hid in ids)):
                    raise ValueError('Invalid selected scan targets')
                hosts = [self.store.host(hid) for hid in ids]
                if any(not h['verified'] for h in hosts):
                    raise ValueError('Complete SSH setup before scanning selected servers')
            elif params.get("id"):
                hosts = [self.store.host(params["id"])]
                if not hosts[0]["verified"]:
                    raise ValueError("Complete the SSH setup wizard before scanning this server")
            else:
                hosts = [h for h in self.store.hosts() if h["verified"]]
            if not hosts:
                return {"scheduled": 0}
            supplied = params.get('sudo_passwords') or {}
            if not isinstance(supplied, dict):
                raise ValueError('Invalid scan authorization data')
            scope = str(params.get('credential_scope', ''))
            targets = {h['id'] for h in hosts if h.get('sudo_mode') == 'password'}
            if set(supplied) - targets:
                raise ValueError('Unexpected sudo password for scan target')
            # A second scan within the same authorized session can reuse an
            # unexpired credential, but never one from another browser/user.
            resolved = {hid: supplied.get(hid) or self._cached_password(scope, hid)
                        for hid in targets}
            if any(not isinstance(resolved[hid], str) or not resolved[hid] or
                   len(resolved[hid]) > 2048 or
                   any(c in resolved[hid] for c in '\r\n\x00') for hid in targets):
                raise ValueError('Enter a sudo password for every password-protected host')
            label = f"Scanning {len(hosts)} server{'s' if len(hosts) != 1 else ''}"
            activity = self.store.begin_activity('scan', label,
                ['host:' + host['id'] for host in hosts], total=len(hosts))
            # Copy the dictionary and clear it when the task finishes; no
            # credential is saved in SQLite, scan rows, or audit events.
            self._launch_activity(self.scan_many(hosts, resolved, activity['id'], scope))
            return {"scheduled": len(hosts), 'activity_id': activity['id']}
        if method == "credentials.authorize":
            # No APT refresh or scan. Verify password using a real sudo -k
            # check over the managed SSH channel, then hold it in controller RAM.
            scope = str(params.get('credential_scope', ''))
            supplied = params.get('sudo_passwords')
            if not _CREDENTIAL_SCOPE_RE.fullmatch(scope):
                raise ValueError('Invalid sudo authorization session')
            if not isinstance(supplied, dict) or not supplied or len(supplied) > 100:
                raise ValueError('Enter a sudo password for at least one server')
            hosts = {h['id']: h for h in self.store.hosts() if
                     h.get('verified') and h.get('sudo_mode') == 'password'}
            if set(supplied) - set(hosts):
                raise ValueError('Unknown or unverified sudo authorization target')
            for hid, password in supplied.items():
                if not isinstance(password, str) or not password or len(password) > 2048 or any(
                        c in password for c in '\r\n\x00'):
                    raise ValueError('Invalid sudo credential')
            # All-or-nothing cache: validate all first.
            for hid, password in supplied.items():
                if not await asyncio.to_thread(verify_sudo_password, hosts[hid], self.config, password):
                    raise ValueError('Sudo authorization rejected for ' + sanitize(hosts[hid]['name'], 64))
            for hid, password in supplied.items():
                self._remember_sudo(scope, hid, password)
            return {'authorized': len(supplied), 'ttl_seconds': SUDO_AUTH_TTL_SECONDS}
        if method == "credentials.status":
            scope = str(params.get('credential_scope', ''))
            requested = params.get('host_ids') or []
            if not isinstance(requested, list) or len(requested) > 200:
                raise ValueError('Invalid credential status request')
            # Only expose the availability/expiry, not the password itself.
            # Host membership is verified against inventory.
            known = {host['id'] for host in self.store.hosts()}
            return {hid: max(0, int(self._sudo_cache[(scope, hid)][0] - time.monotonic()))
                    for hid in requested if hid in known and
                    self._cached_password(scope, hid) is not None}
        if method == "scans.list":
            return self.store.latest_scans()
        if method == "plans.create":
            # The execution mode is validated and snapshotted by Store, not
            # inferred from an editable browser field at worker runtime.
            return self.store.create_plan(
                list(params.get("host_ids", [])),
                mode=params.get('mode', 'apply'),
                refresh_metadata=params.get('refresh_metadata') is True,
                verbose_dry_run=params.get('verbose_dry_run') is True,
            )
        if method == "plans.get":
            return self.store.plan(params["id"])
        if method == "plans.approve":
            return self.store.approve_plan(params["id"], str(os.getuid()),
                                           bool(params.get("protected_ack", False)),
                                           str(params.get("confirmation", "")),
                                           bool(params.get("repo_warning_ack", False)))
        if method == "jobs.start":
            return await self.start_job(params["plan_id"], params.get("idempotency", ""),
                                        params.get("sudo_passwords", {}),
                                        str(params.get('credential_scope', '')))
        if method == "credentials.claim":
            # Only the controller-owned systemd worker needs this. The socket
            # requires the same Unix UID as the controller, not a web user.
            # Claims are consumed immediately to prevent repeated retrieval.
            job_id = str(params.get("job_id", ""))
            if not job_id or job_id not in self._job_passwords:
                raise ValueError("No temporary credentials available for this job. Reapproval required")
            return self._job_passwords.pop(job_id)
        if method == "alerts.acknowledge":
            return self.store.acknowledge_past_alerts()
        if method == "history.clear":
            result = self.store.clear_all_history()
            # Same constrained job-artifact deletion as legacy patch clearing.
            # No user-supplied paths are allowed.
            import shutil
            root = self.config.jobs.resolve()
            errors = 0
            for job_id in result.pop('job_ids'):
                path = root / job_id
                try:
                    if path.is_symlink():
                        path.unlink()
                    elif path.is_dir() and path.resolve().parent == root:
                        shutil.rmtree(path)
                except OSError:
                    errors += 1
            result['artifact_cleanup_errors'] = errors
            return result
        if method == "jobs.clear":
            # Database cleanup is transactional and refuses queued/running
            # workers. Preserve all non-patch history and the security audit.
            result = self.store.clear_patch_history()
            # Clear only artifact directories named by known job IDs. Do not
            # accept filesystem paths from the caller or follow symlinks.
            import shutil
            root = self.config.jobs.resolve()
            errors = 0
            for job_id in result.pop('job_ids'):
                path = root / job_id
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir() and path.resolve().parent == root:
                    try:
                        shutil.rmtree(path)
                    except OSError:
                        errors += 1
            result['artifact_cleanup_errors'] = errors
            return result
        if method == "jobs.list":
            return self.store.jobs()
        if method == "jobs.get":
            job = self.store.job(params["id"])
            job["events"] = self.store.events(params["id"])
            return job
        if method == "jobs.cancel":
            self.store.request_cancel(params["id"])
            return {"cancel_requested": True}
        raise ValueError("Unknown controller operation")

    async def dashboard_reachability(self) -> list[dict]:
        """Probe verified hosts once per cache window, even across web tabs.

        Reachability is a single ICMP echo observation only. It never
        overwrites the result or timestamp of a full package scan.
        """
        async with self._reachability_lock:
            hosts = self.store.hosts()
            now = time.monotonic()
            stale = [host for host in hosts if
                     (host["id"] not in self._reachability or
                      now - self._reachability[host["id"]][0] >= 110)]
            semaphore = asyncio.Semaphore(8)

            async def check(host: dict) -> None:
                async with semaphore:
                    try:
                        result = await asyncio.to_thread(probe_icmp_reachability, host)
                    except Exception:
                        # A probe failure must not fail the entire dashboard.
                        result = {"state": "unknown", "checked_at": int(time.time()),
                                  "detail": "ICMP check failed on controller"}
                    self._reachability[host["id"]] = (time.monotonic(), result)

            if stale:
                await asyncio.gather(*(check(host) for host in stale))

            # Do not return stale entries for removed hosts. Newly added hosts
            # are reported as setup required until enrollment has succeeded.
            results = []
            for host in hosts:
                # Ping is independent of SSH onboarding, so include hosts
                # that are registered but not yet configured for patching.
                state = self._reachability.get(host["id"], (None, None))[1]
                results.append({"host_id": host["id"], **(state or {
                    "state": "unknown", "checked_at": None,
                    "detail": "Not checked yet"})})
            return results

    def enroll_host(self, params: dict) -> dict:
        """Enroll a dedicated account while enforcing SSH trust and safe recovery.

        A successful remote modification cannot be rolled back reliably. If
        setup fails after host registration, leave an unverified inventory
        record so an operator can inspect the problem instead of silently
        losing track of a partially provisioned machine.
        """
        fields = validate_host({**params.get("host", {}),
            "ssh_user": "opm", "sudo_mode": "password", "key_path": ""})
        fingerprint = str(params.get("fingerprint", ""))
        if not fingerprint.startswith("SHA256:") or len(fingerprint) > 120:
            raise ValueError("Select a displayed SSH fingerprint before setup")
        admin_user = str(params.get("admin_user", ""))
        login_password = str(params.get("login_password", ""))
        sudo_password = str(params.get("admin_sudo_password", "")) or login_password
        opm_password = str(params.get("opm_password", ""))
        account_mode = str(params.get('account_mode', 'create'))
        replace_ack = params.get('replace_ack') is True
        if account_mode not in ('create', 'reuse', 'replace'):
            raise ValueError('Select a valid OPM account setup action')
        if account_mode == 'replace' and not replace_ack:
            raise ValueError('Confirm deletion of the old OPM account before proceeding')
        if not USER_RE.fullmatch(admin_user) or not login_password:
            raise ValueError("Enter your existing administrator SSH username and password")
        min_password_length = 1 if account_mode == 'reuse' else 12
        if len(opm_password) < min_password_length or len(opm_password) > 1024 or any(c in opm_password for c in "\r\n\x00"):
            raise ValueError('Enter the existing remote OPM sudo password' if account_mode == 'reuse'
                             else 'Choose a remote OPM sudo password of 12 or more characters')
        if any(c in sudo_password for c in "\r\n\x00"):
            raise ValueError("Invalid administrator sudo password")

        # Never silently take over an existing registered account. An
        # unverified registration from a previous version can be resumed.
        existing_id = str(params.get("existing_id", ""))
        hosts = self.store.hosts()
        if existing_id:
            old = self.store.host(existing_id)
            if old["verified"]:
                raise ValueError("This server is already verified. Use server settings instead")
            if old["address"] != fields["address"] or old["ssh_port"] != fields["ssh_port"]:
                raise ValueError("Address or port changed. Remove the old unverified record before starting again")
            if old["name"] != fields["name"]:
                raise ValueError("Keep the original server name when finishing pending setup")
            if self.store.is_locked(existing_id):
                raise ValueError("Server is locked by an active patch job")
            host = old
        else:
            if any(h["name"].casefold() == fields["name"].casefold() for h in hosts):
                raise ValueError("A server with this name already exists")
            if any(h["address"] == fields["address"] and h["ssh_port"] == fields["ssh_port"] for h in hosts):
                raise ValueError("This server is already registered. Open its profile to finish setup")
            host = None

        # Only an ED25519 host key is enrolled automatically. ssh-keyscan is
        # unauthenticated: the person approving it is explicitly warned of
        # first-use trust, and the fingerprint is checked again at acceptance.
        keys = probe_host_key(fields)
        selected = next((k for k in keys if k["fingerprint"] == fingerprint
                         and k["type"] == "ssh-ed25519"), None)
        if not selected:
            raise ValueError("ED25519 fingerprint changed or unavailable. Repeat identity discovery")
        identity = ensure_managed_key(self.config)
        accept_host_key(fields, self.config, fingerprint)
        if host is None:
            host = self.store.add_host({**fields, "key_path": identity["key_path"]})
        else:
            host = self.store.update_host(host["id"], {**fields, "key_path": identity["key_path"]},
                                          activity_owner=str(params.get("_activity_id", "")))
            # An earlier attempt might have created the remote OPM account
            # but failed during verification. If the managed key already
            # authenticates, recover using a nonmodifying sudo test instead
            # of attempting to create an existing account again.
            if account_mode != 'replace':
                try:
                    test_connection(host, self.config)
                except Exception:
                    pass
                else:
                    if verify_sudo_password(host, self.config, opm_password):
                        self.store.verified(host["id"], True)
                        return {"host_id": host["id"], "verified": True,
                                "recovered": True}
                    return {"host_id": host["id"], "verified": False,
                            "warning": "OPM key login works, but the supplied sudo password was rejected. No account changes were made."}
        try:
            if account_mode == 'create':
                result = bootstrap_account(host, self.config, admin_user, login_password,
                                           sudo_password, opm_password)
            else:
                result = bootstrap_account(host, self.config, admin_user, login_password,
                                           sudo_password, opm_password,
                                           mode=account_mode, replace_ack=replace_ack)
        except Exception as exc:
            # The account might have been partially created. We must not
            # automatically run provisioning again until its state is known.
            raise ValueError(f"{exc} Server saved as unverified. Open Servers and choose Finish setup after reviewing the failure. ") from None
        # The remote bootstrap has confirmed the account operation. Persist a
        # non-secret, accurately worded step in unified History even if the
        # following SSH/sudo verification fails. Never record passwords,
        # command output, or private/public SSH keys in the audit trail.
        activity_id = str(params.get('_activity_id', ''))
        if activity_id:
            account_event = {
                'replace': 'Existing remote opm account deleted and recreated; new controller SSH key installed',
                'reuse': 'Existing remote opm account reused; new controller SSH key authorized',
                'create': 'Dedicated remote opm account created; controller SSH key installed',
            }[account_mode]
            self.store.activity_progress(activity_id, account_event)
        updated = self.store.update_host(host["id"], {**host, **fields,
            "key_path": result["key_path"], "sudo_mode": "password"},
            activity_owner=str(params.get("_activity_id", "")))
        try:
            test_connection(updated, self.config)
            if not verify_sudo_password(updated, self.config, opm_password):
                return {"host_id": host["id"], "verified": False,
                        "warning": "Key login succeeded, but sudo password verification failed. Account was created."}
        except Exception:
            return {"host_id": host["id"], "verified": False,
                    "warning": "Account created, but SSH key test failed. Inspect the server before retrying."}
        self.store.verified(host["id"], True)
        return {"host_id": host["id"], "verified": True}

    def repair_ssh_host(self, host_id: str, fingerprint: str,
                        credentials: dict, activity_id: str) -> dict:
        """Restore only the controller public key on a verified, trusted host.

        A separate administrator explicitly approves password-authenticated
        recovery. Neither the existing opm account nor its sudo policy is
        changed. Normal scans never fall back to password-based SSH.
        """
        host = self.store.host(host_id)
        if not host['verified'] or host['ssh_user'] != 'opm':
            raise ValueError('Only previously verified opm hosts support SSH repair')
        if self.store.is_locked(host_id):
            raise ValueError('Cannot repair SSH while a patch job is active')
        # This re-checks the remote identity and refuses a changed known key.
        # It occurs BEFORE opening password-authenticated SSH.
        accept_host_key(host, self.config, fingerprint)
        admin_password = credentials['login_password']
        sudo_password = credentials['admin_sudo_password'] or admin_password
        # Use the dedicated key-only remote action, authorized by the separate
        # administrator's SSH and sudo credentials. This can restore SSH even
        # when the manually recreated opm account lacks sudo membership.
        # The opm password is NOT sent to remote provisioning.
        allow_create = credentials.get('create_if_missing') is True
        result = bootstrap_account(host, self.config,
            credentials['admin_user'], admin_password, sudo_password,
            credentials['new_opm_password'] if allow_create else '',
            mode='repair_or_create' if allow_create else 'repair_key',
            replace_ack=False, create_ack=allow_create)
        account_created = result.get('mode') == 'create_missing'
        self.store.activity_progress(activity_id,
            'Missing remote opm account created; sudo group assigned; controller SSH key installed'
            if account_created else
            'Existing remote opm account preserved; controller SSH public key authorized')
        candidate = {**host, 'key_path': result['key_path']}
        test_connection(candidate, self.config)
        if host['key_path'] != result['key_path']:
            self.store.update_host(host_id, candidate, activity_owner=activity_id)
        self.store.verified(host_id, True)
        verification_password = (credentials['new_opm_password'] if account_created
                                 else credentials['opm_password'])
        sudo_ok = bool(verification_password) and verify_sudo_password(
            candidate, self.config, verification_password)
        return {'host_id': host_id, 'ssh_recovered': True,
                'account_created': account_created, 'sudo_verified': sudo_ok}

    async def repair_ssh_background(self, activity_id: str, host_id: str,
                                    fingerprint: str, credentials: dict) -> None:
        try:
            async with self._enrollment_lock:
                self.store.activity_progress(activity_id,
                    'Rechecking trusted SSH host key and administrator credentials')
                result = await asyncio.to_thread(
                    self.repair_ssh_host, host_id, fingerprint, credentials, activity_id)
                if result['sudo_verified']:
                    self.store.activity_finish(activity_id, 'completed',
                        ('SSH restored: missing opm account recreated; controller key and sudo verified'
                         if result.get('account_created') else
                         'SSH restored: existing opm account and password preserved; controller key and sudo verified'), result)
                else:
                    self.store.activity_progress(activity_id,
                        'Controller SSH key verified; opm sudo access still requires attention')
                    self.store.activity_finish(activity_id, 'partial',
                        'SSH restored, but opm sudo access was not verified. Check the account sudo policy on the managed server before patching.', result)
        except Exception as exc:
            error = str(exc)
            for secret in credentials.values():
                if isinstance(secret, str) and secret:
                    error = error.replace(secret, '[REDACTED]')
            self.store.activity_progress(activity_id, 'SSH repair failed', completed=1, failed=1)
            self.store.activity_finish(activity_id, 'failed',
                'SSH recovery unsuccessful. No account deletion was performed. ' + sanitize(error, 2200))
        finally:
            for key in list(credentials):
                credentials.pop(key, None)

    def _launch_activity(self, coroutine) -> None:
        """Run a coroutine after the HTTP response has already returned.

        Keep a strong task reference, otherwise request-level tasks may be
        unexpectedly discarded. The persistent SQLite activity record is
        reconciled as interrupted if the controller process dies.
        """
        task = asyncio.create_task(coroutine)
        self._scans.add(task)
        task.add_done_callback(self._scans.discard)

    async def configure_web_background(self, activity_id: str, helper: str,
                                       mode: str, hostname: str, proxy_ip: str) -> None:
        """Change only web access, never controller or patch-worker services.

        This runs after the POST returns, so the browser can show the new URL.
        The root-owned helper performs the atomic backup and rollback test.
        """
        self.store.activity_progress(activity_id, 'Validating and applying web access')
        # Allow the current HTTP response to flush before restarting Gunicorn.
        await asyncio.sleep(2)
        try:
            command = [sys.executable, helper, '--controller-apply',
                       '--mode', mode, '--hostname', hostname, '--proxy-ip', proxy_ip]
            result = await asyncio.to_thread(subprocess.run, command,
                         capture_output=True, text=True, timeout=65)
            if result.returncode:
                raise RuntimeError(sanitize(result.stderr or result.stdout or
                                           'Web service health check failed', 220))
            self.store.activity_finish(activity_id, 'completed',
                'Web access configuration applied. Use the selected address to reconnect.')
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.store.activity_finish(activity_id, 'failed',
                'Web access change failed: ' + sanitize(str(exc), 170))

    async def update_compose_background(self, activity_id: str, host: dict,
                                        project: str, service: str, docker_scope: str,
                                        password: str | None, supplied: bool,
                                        credential_scope: str) -> None:
        """One selected Docker Compose service, with non-replaying activity history."""
        self.store.activity_progress(activity_id,
            'Validating deployment, then pulling the selected service image')
        try:
            if supplied and password and host.get('sudo_mode') == 'password':
                if not await asyncio.to_thread(verify_sudo_password, host, self.config, password):
                    raise ValueError('Sudo authorization rejected for Compose update')
                self._remember_sudo(credential_scope, host['id'], password)
            outcome = await asyncio.to_thread(update_compose_service, host,
                                               self.config, project, service, password)
            if outcome['status'] == 'updated':
                # Source validation must be explicitly repeated after a write.
                self.store.save_compose_verification(host['id'], project, docker_scope,
                    'unverified', 'Reverify Compose after the service update')
                self.store.activity_progress(activity_id,
                    'New image deployed; service running/health checks passed', completed=1)
                self.store.activity_finish(activity_id, 'completed',
                    f'Docker Compose {project} / {service}: {outcome["detail"]}',
                    {'host_id': host['id']})
            elif outcome['status'] == 'unchanged':
                self.store.activity_progress(activity_id, 'Image unchanged; no restart', completed=1)
                self.store.activity_finish(activity_id, 'completed',
                    f'Docker Compose {project} / {service}: {outcome["detail"]}',
                    {'host_id': host['id']})
            else:
                # A failed recreate can leave partial changes. Do not retry
                # automatically, and revoke the old verification record.
                self.store.save_compose_verification(host['id'], project, docker_scope,
                    'unverified', 'Reverify Compose before any further changes')
                self.store.activity_progress(activity_id, outcome['detail'], completed=1, failed=1)
                self.store.activity_finish(activity_id, 'failed',
                    f'Docker Compose {project} / {service}: {outcome["detail"]}',
                    {'host_id': host['id']})
        except Exception as exc:
            # After an SSH interruption, a pull or recreate may have happened.
            # Do not claim a rollback and never log untrusted subprocess output.
            self.store.save_compose_verification(host['id'], project, docker_scope,
                'unverified', 'Update interrupted; inspect deployment and reverify')
            detail = ('Sudo authorization rejected; no update started'
                      if 'Sudo authorization rejected' in str(exc) else
                      'Update interrupted or unavailable; inspect the managed host before retrying')
            self.store.activity_finish(activity_id, 'failed', detail,
                                       {'host_id': host['id']})
        finally:
            password = None

    async def verify_compose_background(self, activity_id: str, host: dict,
                                        project: str, docker_scope: str,
                                        password: str | None,
                                        supplied: bool, credential_scope: str) -> None:
        """Read-only, non-replaying Compose source validation and audit."""
        self.store.activity_progress(activity_id, 'Checking original Docker Compose source')
        try:
            if supplied and password and host.get('sudo_mode') == 'password':
                if not await asyncio.to_thread(verify_sudo_password, host, self.config, password):
                    raise ValueError('Sudo authorization rejected for Compose verification')
                self._remember_sudo(credential_scope, host['id'], password)
            result = await asyncio.to_thread(verify_compose_source, host, self.config,
                                             project, password)
            stored = self.store.save_compose_verification(host['id'], project,
                        docker_scope, result['status'], result['detail'],
                        result['service_count'], result['container_count'])
            if result['status'] == 'verified':
                detail = f"Docker Compose {project}: source verified for {result['service_count']} services"
                self.store.activity_progress(activity_id, 'Original source and running services match', completed=1)
                self.store.activity_finish(activity_id, 'completed', detail,
                                           {'host_id': host['id'], 'verified': True})
            else:
                detail = 'Docker Compose ' + project + ': ' + result['detail']
                self.store.activity_progress(activity_id, detail, completed=1, failed=1)
                self.store.activity_finish(activity_id, 'failed', detail,
                                           {'host_id': host['id'], 'verified': False})
        except Exception as exc:
            message = str(exc)
            if password:
                message = message.replace(password, '[REDACTED]')
            # No raw file paths, Compose contents, or subprocess stdout are
            # kept by the controller. Avoid errors containing file paths.
            if 'Sudo authorization rejected' not in message:
                message = 'Remote Compose verification unavailable. Check SSH, sudo and Docker access.'
            self.store.activity_finish(activity_id, 'failed', message,
                                       {'host_id': host['id'], 'verified': False})
        finally:
            password = None

    async def test_host_background(self, activity_id: str, host: dict) -> None:
        """Perform the real SSH authentication check without blocking Django."""
        self.store.activity_progress(activity_id, 'Authenticating with SSH key')
        try:
            await asyncio.to_thread(test_connection, host, self.config)
            self.store.verified(host['id'], True)
            self.store.activity_progress(activity_id, 'SSH authentication successful', completed=1)
            self.store.activity_finish(activity_id, 'completed', 'SSH key login verified',
                                       {'host_id': host['id']})
        except Exception as exc:
            self.store.activity_progress(activity_id, 'SSH authentication failed',
                                         completed=1, failed=1)
            self.store.activity_finish(activity_id, 'failed',
                                       'SSH test failed: ' + sanitize(str(exc), 180),
                                       {'host_id': host['id']})

    async def enroll_background(self, activity_id: str, params: dict) -> None:
        """Provision one host with controller-memory-only bootstrap secrets.

        The existing enrollment function handles identity revalidation,
        account creation, key login, and sudo verification. A failure can
        leave a partial remote account, so never retry it automatically.
        """
        try:
            async with self._enrollment_lock:
                # Explicitly associate updates to an existing unverified
                # inventory record with the enrollment activity that owns
                # the host lock. No browser can supply this internal token.
                params['_activity_id'] = activity_id
                self.store.activity_progress(activity_id,
                    'Verifying host fingerprint and configuring dedicated account')
                result = await asyncio.to_thread(self.enroll_host, params)
                if result.get('verified'):
                    # Enrollment and scanning have separate persistent activity
                    # records. End enrollment first so resuming a previously
                    # registered host releases its host: lock before the new
                    # scan claims it. Never expose/bootstrap passwords in logs.
                    self.store.activity_progress(activity_id, 'SSH and sudo verified', completed=1)
                    detail = ('Existing opm account replaced; SSH and sudo verified; scheduling initial scan'
                              if params.get('account_mode') == 'replace'
                              else 'Server enrollment completed; scheduling initial scan')
                    self.store.activity_finish(activity_id, 'completed', detail,
                        {'host_id': result['host_id'], 'verified': True})
                    host = self.store.host(result['host_id'])
                    try:
                        scan = self.store.begin_activity('scan',
                            'Initial scan: ' + host['name'], ['host:' + host['id']])
                    except ValueError:
                        # A separately authorized operator may have started a
                        # scan immediately as setup finished. Avoid duplicates.
                        pass
                    else:
                        password = str(params.get('opm_password') or '')
                        # A new, isolated dictionary is owned and erased by
                        # scan_many. The enrollment params are erased below.
                        self._launch_activity(self.scan_many(
                            [host], {host['id']: password}, scan['id']))
                else:
                    self.store.activity_progress(activity_id, 'Setup needs attention', completed=1, failed=1)
                    self.store.activity_finish(activity_id, 'partial',
                        result.get('warning', 'Enrollment requires manual review'),
                        {'host_id': result.get('host_id'), 'verified': False})
        except Exception as exc:
            # Generic exceptions from SSH libraries occasionally include
            # usernames or connection strings. Scrub submitted passwords
            # before persisting any diagnostic, even if they were embedded
            # in an unexpected library exception.
            error = str(exc)
            for key in ('login_password', 'admin_sudo_password', 'opm_password'):
                secret = params.get(key)
                if isinstance(secret, str) and secret:
                    error = error.replace(secret, '[REDACTED]')
            self.store.activity_progress(activity_id, 'Server enrollment failed', completed=1, failed=1)
            self.store.activity_finish(activity_id, 'failed',
                'Provisioning failed. Review the server before retrying. ' + sanitize(error, 3000))
        finally:
            for key in ('login_password', 'admin_sudo_password', 'opm_password', '_activity_id'):
                params.pop(key, None)

    async def scan_many(self, hosts: list[dict], passwords: dict[str, str] | None = None,
                        activity_id: str | None = None, credential_scope: str = '') -> None:
        """Run up to three scans concurrently, reporting real host progress."""
        limit = asyncio.Semaphore(3)
        secrets = passwords if passwords is not None else {}
        completed = 0
        failed = 0
        failed_names: list[str] = []
        warnings = 0
        self.store.activity_progress(activity_id, 'Connecting to managed servers') if activity_id else None

        async def one(host: dict) -> None:
            nonlocal completed, failed, warnings
            async with limit:
                if activity_id:
                    self.store.activity_progress(activity_id, 'Scanning ' + host['name'])
                scan_id = self.store.begin_scan(host['id'])
                try:
                    data = await asyncio.to_thread(inspect_host, host, self.config, secrets.get(host['id']))
                    self.store.finish_scan(scan_id, data)
                    apt_warning = data.get('os', {}).get('apt_error')
                    if apt_warning:
                        warnings += 1
                        if activity_id:
                            self.store.activity_progress(activity_id,
                                'Scan warning for ' + host['name'] + ': ' + sanitize(str(apt_warning), 380))
                    else:
                        if activity_id:
                            self.store.activity_progress(activity_id, 'Scan succeeded: ' + host['name'])
                    if not apt_warning and (credential_scope and host.get('sudo_mode') == 'password'
                          and data.get('os', {}).get('metadata') == 'fresh'
                          and secrets.get(host['id'])):
                        # Do not cache a guessed or failed sudo password.
                        self._remember_sudo(credential_scope, host['id'], secrets[host['id']])
                except Exception as exc:
                    diagnostic = str(exc)
                    if secrets.get(host['id']):
                        diagnostic = diagnostic.replace(secrets[host['id']], '[REDACTED]')
                    safe_error = sanitize(diagnostic, 520)
                    self.store.finish_scan(scan_id, None, safe_error)
                    failed_names.append(host['name'])
                    failed += 1
                    if activity_id:
                        # Log host identity and a bounded sanitized reason so
                        # another administrator can diagnose partial Scan all.
                        # No SSH/sudo passwords, raw commands or private keys.
                        self.store.activity_progress(activity_id,
                            'Scan failed for ' + host['name'] + ': ' + safe_error)
                finally:
                    secrets.pop(host['id'], None)
                    completed += 1
                    if activity_id:
                        self.store.activity_progress(activity_id,
                            f'Checked {completed} of {len(hosts)} servers',
                            completed=completed, failed=failed)
        try:
            await asyncio.gather(*(one(host) for host in hosts))
            if activity_id:
                state = ('failed' if failed == len(hosts) else
                         'partial' if failed or warnings else 'completed')
                detail = f'{completed} scanned, {failed} failed, {warnings} warnings'
                if failed_names:
                    detail += '; failed servers: ' + ', '.join(sorted(failed_names))
                self.store.activity_finish(activity_id, state, detail)
        except Exception as exc:
            if activity_id:
                self.store.activity_finish(activity_id, 'failed',
                    'Scan controller error: ' + sanitize(str(exc), 170))
        finally:
            secrets.clear()

    async def start_job(self, plan_id: str, idempotency: str, passwords: dict | None = None,
                        credential_scope: str = '') -> dict:
        """Only start workers in the independent systemd user service manager.

        We intentionally do not fall back to a subprocess owned by the
        controller, since that would weaken the promised restart isolation.
        """
        # Check systemd access before claiming locks or creating an approved
        # job. This also prevents operating in unsafe test environments.
        try:
            p = await asyncio.to_thread(subprocess.run,
                ["systemctl", "--user", "show-environment"],
                capture_output=True, timeout=8)
            if p.returncode:
                raise ValueError("systemd user manager unavailable. Enable user lingering and start the controller unit")
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("systemd user manager unavailable") from None
        plan = self.store.plan(plan_id)
        password_hosts = [t["host_id"] for t in plan["snapshot"]["targets"]
                          if t.get("sudo_mode") == "password"]
        supplied = passwords or {}
        if not isinstance(supplied, dict) or set(supplied) - set(password_hosts):
            raise ValueError("Invalid sudo credentials")
        # A newly supplied password takes precedence over an approved cached
        # scan credential. Resolve all secrets BEFORE claiming hosts/jobs.
        resolved = {hid: supplied.get(hid) or self._cached_password(credential_scope, hid)
                    for hid in password_hosts}
        if any(not isinstance(resolved[hid], str) or not resolved[hid] or
               len(resolved[hid]) > 2048 or
               any(c in resolved[hid] for c in '\r\n\x00') for hid in password_hosts):
            raise ValueError("Sudo password authorization expired. Enter a password to patch this host")
        # Reject a patch before creating a job or claiming host locks if the
        # controller cannot actually execute ansible-playbook. The detached
        # worker independently repeats this check as a defense against a
        # dependency disappearing between submission and execution.
        try:
            await asyncio.to_thread(check_ansible_runtime)
        except AnsibleDependencyError as exc:
            raise ValueError(str(exc)) from None
        job = self.store.create_job(plan_id, idempotency)
        # An idempotent duplicate request must not launch another transient
        # service, even while the first worker is still in its queued state.
        if job.get("existing") or job["state"] != "queued":
            return self.store.job(job["id"])
        if password_hosts:
            self._job_passwords[job["id"]] = dict(resolved)
            # Bounded availability limits how long temporary credentials
            # remain in RAM if worker startup fails before it claims them.
            async def expire_credentials() -> None:
                await asyncio.sleep(120)
                self._job_passwords.pop(job["id"], None)
            asyncio.create_task(expire_credentials())
        unit = f"opm-job-{job['id']}"
        cmd = ["systemd-run", "--user", "--collect", f"--unit={unit}",
               "--property=Type=exec", f"--setenv=OPM_HOME={self.config.base}",
               "--", sys.executable, "-m", "opm.worker", job["id"]]
        try:
            p = await asyncio.to_thread(subprocess.run, cmd, capture_output=True,
                                        text=True, timeout=20)
            if p.returncode:
                raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "systemd-run failed")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self._job_passwords.pop(job["id"], None)
            self.store.job_state(job["id"], "failed", sanitize(str(exc)))
            raise ValueError("Unable to start isolated patch worker: " + sanitize(str(exc))) from None
        return self.store.job(job["id"])


async def rpc(method: str, params: dict | None = None, config: Config | None = None) -> object:
    """One-shot client RPC used by internal web and command-line operations."""
    config = config or Config.load()
    reader, writer = await asyncio.open_unix_connection(str(config.socket), limit=MAX_REPLY)
    try:
        writer.write((json.dumps({"method": method, "params": params or {}}) + "\n").encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=130)
        answer = json.loads(raw)
        if not answer["ok"]:
            raise ValueError(answer["error"])
        return answer["result"]
    finally:
        writer.close()
        await writer.wait_closed()
