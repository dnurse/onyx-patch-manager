"""Local-only controller API and persistent background execution authority.

Protocol: one JSON object per Unix-socket connection, one JSON reply. Peer
credentials restrict access to processes owned by this Unix account. The API
never exposes arbitrary remote command execution. Only known operations are
accepted, and all patch jobs require a recorded, explicit approval.
"""

from __future__ import annotations

import asyncio
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
from .remote import accept_host_key, inspect_host, probe_host_key, probe_icmp_reachability, test_connection, verify_sudo_password
from .onboarding import bootstrap_account, ensure_managed_key
from .runtime import check_ansible_runtime, AnsibleDependencyError
from .store import Store
from .validation import sanitize, validate_host, USER_RE

MAX_MESSAGE = 256_000
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
        """Retain credentials only after a real authorized APT refresh.

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
        await self.reconcile_jobs()
        try:
            async with server:
                await server.serve_forever()
        finally:
            path.unlink(missing_ok=True)

    async def reconcile_jobs(self) -> None:
        """Do not requeue operations that may have partially changed a host."""
        for job in self.store.jobs():
            if job["state"] not in ("queued", "running"):
                continue
            cmd = ["systemctl", "--user", "is-active", f"opm-job-{job['id']}.service"]
            try:
                p = await asyncio.to_thread(subprocess.run, cmd, capture_output=True,
                                            text=True, timeout=8)
                alive = p.returncode == 0 and p.stdout.strip() in ("active", "activating")
            except (OSError, subprocess.TimeoutExpired):
                alive = False
            if not alive:
                self.store.event(job["id"], None, "warning", "Recovery",
                                 "Worker is not active. Remote state requires reconciliation before retry")
                self.store.job_state(job["id"], "interrupted", "Worker stopped or controller restarted")

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
                    'attention': (job['state'] in ('failed','interrupted','partially_completed')
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
        if method == "hosts.enroll.start":
            # Enforce non-secret shape and host identity before recording an
            # operation. Full credential validation still occurs inside the
            # existing enrollment procedure.
            fields = validate_host({**params.get('host', {}),
                'ssh_user': 'opm', 'sudo_mode': 'password', 'key_path': ''})
            if not str(params.get('fingerprint', '')).startswith('SHA256:'):
                raise ValueError('Approve a server fingerprint before setup')
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
            # Never expose private key content to the TUI.
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
        if method == "hosts.list":
            return self.store.hosts()
        if method == "hosts.reachability":
            # A distinct, read-only operation deliberately avoids APT, Docker,
            # SSH authentication and changes to historical scan records.
            return await self.dashboard_reachability()
        if method == "hosts.add":
            return self.store.add_host(params)
        if method == "hosts.update":
            return self.store.update_host(params["id"], params["data"])
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
            if params.get("id"):
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
                                           str(params.get("confirmation", "")))
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
        if not USER_RE.fullmatch(admin_user) or not login_password:
            raise ValueError("Enter your existing administrator SSH username and password")
        if len(opm_password) < 12 or len(opm_password) > 1024 or any(c in opm_password for c in "\r\n\x00"):
            raise ValueError("Choose a remote OPM sudo password of 12 or more characters")
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
                        "warning": "OPM key login works, but the supplied sudo password was rejected. Account creation was not repeated."}
        try:
            result = bootstrap_account(host, self.config, admin_user, login_password,
                                       sudo_password, opm_password)
        except Exception as exc:
            # The account might have been partially created. We must not
            # automatically run provisioning again until its state is known.
            raise ValueError(f"{exc} Server saved as unverified. Open Servers and choose Finish setup after reviewing the failure. ") from None
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
                                       'SSH test failed: ' + sanitize(str(exc), 180))

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
                    self.store.activity_finish(activity_id, 'completed',
                        'Server enrollment completed; scheduling initial scan',
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
                'Provisioning failed. Review the server before retrying. ' + sanitize(error, 160))
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
                    if data.get('os', {}).get('apt_error'):
                        warnings += 1
                    elif (credential_scope and host.get('sudo_mode') == 'password'
                          and data.get('os', {}).get('metadata') == 'fresh'
                          and secrets.get(host['id'])):
                        # Do not cache a guessed or failed sudo password.
                        self._remember_sudo(credential_scope, host['id'], secrets[host['id']])
                except Exception as exc:
                    diagnostic = str(exc)
                    if secrets.get(host['id']):
                        diagnostic = diagnostic.replace(secrets[host['id']], '[REDACTED]')
                    self.store.finish_scan(scan_id, None, sanitize(diagnostic))
                    failed += 1
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
    """One-shot client RPC used by the Textual UI and the command-line tools."""
    config = config or Config.load()
    reader, writer = await asyncio.open_unix_connection(str(config.socket))
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
