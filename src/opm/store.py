"""SQLite-backed host inventory, findings, plans, approvals and job history.

Database transactions are the single authority for modification eligibility.
The controller and detached workers use separate SQLite connections with WAL
and busy-timeout enabled. Plans are immutable snapshots of scanned targets.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .validation import validate_host

SCHEMA = """
CREATE TABLE IF NOT EXISTS hosts (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 address TEXT NOT NULL, ssh_user TEXT NOT NULL, ssh_port INTEGER NOT NULL,
 key_path TEXT NOT NULL DEFAULT '', group_name TEXT NOT NULL,
 sudo_mode TEXT NOT NULL DEFAULT 'passwordless',
 protected INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
 id TEXT PRIMARY KEY, host_id TEXT NOT NULL, started REAL NOT NULL,
 finished REAL, state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
 result_json TEXT NOT NULL DEFAULT '{}',
 FOREIGN KEY(host_id) REFERENCES hosts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS scans_by_host ON scans(host_id,started DESC);
CREATE TABLE IF NOT EXISTS plans (
 id TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL,
 snapshot_json TEXT NOT NULL, digest TEXT NOT NULL,
 approved_at REAL, approved_by TEXT, protected_ack INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, state TEXT NOT NULL,
 idempotency TEXT NOT NULL UNIQUE, created REAL NOT NULL, started REAL, finished REAL,
 error TEXT NOT NULL DEFAULT '', cancel_requested INTEGER NOT NULL DEFAULT 0,
 acknowledged INTEGER NOT NULL DEFAULT 0,
 mode TEXT NOT NULL DEFAULT 'apply',
 FOREIGN KEY(plan_id) REFERENCES plans(id)
);
CREATE TABLE IF NOT EXISTS job_targets (
 job_id TEXT NOT NULL, host_id TEXT NOT NULL, state TEXT NOT NULL,
 error TEXT NOT NULL DEFAULT '', reboot_required INTEGER,
 PRIMARY KEY(job_id,host_id)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
 host_id TEXT, timestamp REAL NOT NULL, level TEXT NOT NULL,
 task TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_job ON events(job_id,id);
CREATE TABLE IF NOT EXISTS host_locks (
 host_id TEXT PRIMARY KEY, job_id TEXT NOT NULL
);
-- Non-patching activity is durable so the browser can reconnect after a
-- navigation and interrupted controller operations are not reported as OK.
CREATE TABLE IF NOT EXISTS activities (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
 state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
 total INTEGER NOT NULL DEFAULT 1, completed INTEGER NOT NULL DEFAULT 0,
 failed INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
 finished REAL, result_json TEXT NOT NULL DEFAULT '{}',
 acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS activities_recent ON activities(created DESC);
-- Durable operator-facing diagnostics. Entries contain only curated,
-- sanitized messages, never SSH command output or submitted passwords.
CREATE TABLE IF NOT EXISTS activity_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 activity_id TEXT NOT NULL, timestamp REAL NOT NULL,
 message TEXT NOT NULL,
 FOREIGN KEY(activity_id) REFERENCES activities(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS activity_events_by_activity ON activity_events(activity_id,id);
-- Per-host activity locks avoid concurrent scans, SSH tests or enrollment
-- on the same target. These are separate from modifying patch-job locks.
CREATE TABLE IF NOT EXISTS activity_targets (
 target_key TEXT PRIMARY KEY, activity_id TEXT NOT NULL,
 FOREIGN KEY(activity_id) REFERENCES activities(id) ON DELETE CASCADE
);
"""


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def as_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


class Store:
    """Small, thread-safe-by-connection repository for the controller API."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Every operation gets its own connection to avoid cross-task sharing."""
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
        # Older v0.1.1 databases predate the sudo policy column. Retain the
        # original noninteractive policy for those existing host records.
        with self.connection() as db:
            names = {row[1] for row in db.execute("PRAGMA table_info(hosts)")}
            if "sudo_mode" not in names:
                db.execute("ALTER TABLE hosts ADD COLUMN sudo_mode TEXT NOT NULL DEFAULT 'passwordless'")
            # Historical job/activity rows are not deleted by migration.
            # Acknowledge state is separate from execution state so an old
            # error can be dismissed without falsifying the audit record.
            for table in ('jobs', 'activities'):
                columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                if 'acknowledged' not in columns:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0')
            job_columns = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            if 'mode' not in job_columns:
                # Older jobs remain real patch attempts. Only new plans can
                # explicitly opt into non-mutating simulation.
                db.execute("ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'apply'")
        self.path.chmod(0o600)

    def hosts(self) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM hosts ORDER BY name")]

    def host(self, host_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
        if not row:
            raise ValueError("Unknown host")
        return dict(row)

    def add_host(self, data: dict) -> dict:
        fields = validate_host(data)
        timestamp = now()
        with self.connection() as db:
            db.execute(
                "INSERT INTO hosts(id,name,address,ssh_user,ssh_port,key_path,group_name,sudo_mode,protected,verified,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,?,?)",
                (new_id(), fields["name"], fields["address"], fields["ssh_user"],
                 fields["ssh_port"], fields["key_path"], fields["group_name"],
                 fields["sudo_mode"], int(fields["protected"]), timestamp, timestamp),
            )
            row = db.execute("SELECT * FROM hosts WHERE name=?", (fields["name"],)).fetchone()
            return dict(row)

    def update_host(self, host_id: str, data: dict, *, activity_owner: str = "") -> dict:
        """Changing SSH details invalidates prior verification and scan readiness."""
        fields = validate_host(data)
        old = self.host(host_id)
        # Mutating a connection profile during an approved patch run could
        # redirect a worker or invalidate the audit trail. Reject the edit.
        with self.connection() as db:
            if db.execute("SELECT 1 FROM host_locks WHERE host_id=?", (host_id,)).fetchone():
                raise ValueError("Host is locked by an active patch job")
            owner = db.execute("SELECT activity_id FROM activity_targets WHERE target_key=?",
                               ('host:' + host_id,)).fetchone()
            # An existing unverified host's own enrollment worker must be
            # allowed to save its newly provisioned key and sudo policy.
            # All other callers, including ordinary web edits, are blocked.
            if owner and owner['activity_id'] != activity_owner:
                raise ValueError("Host is locked by an active operation")
        verified = int(old["verified"] and all(old[k] == fields[k]
                  for k in ("address", "ssh_port", "ssh_user", "key_path")))
        with self.connection() as db:
            db.execute(
                "UPDATE hosts SET name=?,address=?,ssh_user=?,ssh_port=?,key_path=?,"
                "group_name=?,sudo_mode=?,protected=?,verified=?,updated_at=? WHERE id=?",
                (fields["name"], fields["address"], fields["ssh_user"],
                 fields["ssh_port"], fields["key_path"], fields["group_name"],
                 fields["sudo_mode"], int(fields["protected"]), verified, now(), host_id),
            )
        return self.host(host_id)

    def verified(self, host_id: str, value: bool = True) -> None:
        with self.connection() as db:
            db.execute("UPDATE hosts SET verified=?,updated_at=? WHERE id=?",
                       (int(value), now(), host_id))

    def is_locked(self, host_id: str) -> bool:
        """Check whether a modifying job has claimed the host."""
        with self.connection() as db:
            return db.execute("SELECT 1 FROM host_locks WHERE host_id=?", (host_id,)).fetchone() is not None

    def remove_host(self, host_id: str) -> None:
        """Inventory removal is local only; running jobs retain their history."""
        with self.connection() as db:
            if (db.execute("SELECT 1 FROM host_locks WHERE host_id=?", (host_id,)).fetchone()
                    or db.execute("SELECT 1 FROM activity_targets WHERE target_key=?",
                                  ('host:' + host_id,)).fetchone()):
                raise ValueError("Host is locked by an active operation")
            db.execute("DELETE FROM hosts WHERE id=?", (host_id,))

    def begin_scan(self, host_id: str) -> str:
        scan_id = new_id()
        with self.connection() as db:
            db.execute("INSERT INTO scans(id,host_id,started,state) VALUES(?,?,?,?)",
                       (scan_id, host_id, now(), "running"))
        return scan_id

    def finish_scan(self, scan_id: str, result: dict | None, error: str = "") -> None:
        with self.connection() as db:
            db.execute("UPDATE scans SET finished=?,state=?,error=?,result_json=? WHERE id=?",
                       (now(), "failed" if error else "completed", error,
                        json.dumps(result or {}), scan_id))

    def latest_scans(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("""SELECT s.* FROM scans s JOIN (
                SELECT host_id,MAX(started) AS newest FROM scans GROUP BY host_id
                ) v ON s.host_id=v.host_id AND s.started=v.newest ORDER BY s.started DESC""").fetchall()
        return [self._scan_dict(r) for r in rows]

    def last_scan(self, host_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM scans WHERE host_id=? ORDER BY started DESC LIMIT 1", (host_id,)).fetchone()
        return self._scan_dict(row) if row else None

    @staticmethod
    def _scan_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["result"] = json.loads(data.pop("result_json"))
        return data

    def begin_activity(self, kind: str, label: str, target_keys: list[str],
                       *, total: int = 1) -> dict:
        """Atomically register a read-only/provisioning operation and locks.

        A duplicate button click must not start a second remote request. A
        patch worker also holds a separate host lock, so prevent scans and
        enrollment from racing changes on the same existing host. Staging
        credentials must never be included in the label or database rows.
        """
        if not target_keys or len(set(target_keys)) != len(target_keys):
            raise ValueError("No unique operation targets were provided")
        if kind not in {"scan", "ssh_test", "enrollment", "ssh_repair", "web_access"}:
            raise ValueError("Unsupported background activity")
        activity_id = new_id()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for key in target_keys:
                if db.execute("SELECT 1 FROM activity_targets WHERE target_key=?", (key,)).fetchone():
                    raise ValueError("An operation is already running for this server")
                if key.startswith('host:') and db.execute(
                        "SELECT 1 FROM host_locks WHERE host_id=?", (key[5:],)).fetchone():
                    raise ValueError("A patch job is already operating on this server")
            db.execute("INSERT INTO activities (id,kind,label,state,detail,total,created,updated) "
                       "VALUES(?,?,?,'queued','Waiting for controller',?,?,?)",
                       (activity_id, kind, label[:128], total, now(), now()))
            db.execute("INSERT INTO activity_events(activity_id,timestamp,message) VALUES(?,?,?)",
                       (activity_id, now(), 'Queued: ' + kind))
            for key in target_keys:
                db.execute("INSERT INTO activity_targets(target_key,activity_id) VALUES(?,?)",
                           (key, activity_id))
        return self.activity(activity_id)

    def activity(self, activity_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
        if not row:
            raise ValueError("Unknown activity")
        item = dict(row)
        item['result'] = json.loads(item.pop('result_json'))
        with self.connection() as db:
            entries = db.execute("SELECT timestamp,message FROM activity_events "
                                 "WHERE activity_id=? ORDER BY id",
                                 (activity_id,)).fetchall()
        item['events'] = [dict(entry) for entry in entries]
        return item

    def recent_activities(self, limit: int = 30, *, include_events: bool = False) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM activities ORDER BY created DESC LIMIT ?",
                              (min(max(limit, 1), 250),)).fetchall()
            events = {}
            if include_events and rows:
                ids = [row['id'] for row in rows]
                placeholders = ','.join('?' for _ in ids)
                for entry in db.execute(f"SELECT activity_id,timestamp,message FROM activity_events "
                                        f"WHERE activity_id IN ({placeholders}) ORDER BY id", ids):
                    entries = events.setdefault(entry['activity_id'], [])
                    if len(entries) < 250:
                        entries.append({'timestamp':entry['timestamp'], 'message':entry['message']})
        result = [{**dict(row), 'result': json.loads(row['result_json'])} for row in rows]
        if include_events:
            for item in result:
                item['events'] = events.get(item['id'], [])
        return result

    def activity_progress(self, activity_id: str, detail: str, *, completed: int | None = None,
                          failed: int | None = None) -> None:
        """Set task progress without storing raw SSH, sudo or command output."""
        from .validation import sanitize
        updates = ["state='running'", "detail=?", "updated=?"]
        values: list = [sanitize(detail, 180), now()]
        if completed is not None:
            updates.append('completed=?')
            values.append(completed)
        if failed is not None:
            updates.append('failed=?')
            values.append(failed)
        values.append(activity_id)
        with self.connection() as db:
            updated = db.execute(f"UPDATE activities SET {','.join(updates)} "
                                 "WHERE id=? AND state IN ('queued','running')", values).rowcount
            if updated:
                db.execute("INSERT INTO activity_events(activity_id,timestamp,message) VALUES(?,?,?)",
                           (activity_id, now(), sanitize(detail, 1200)))

    def activity_finish(self, activity_id: str, state: str, detail: str,
                        result: dict | None = None) -> None:
        """Always release activity locks, including after failures and cancellation."""
        from .validation import sanitize
        if state not in {'completed', 'failed', 'partial', 'interrupted'}:
            raise ValueError("Invalid terminal activity state")
        # Store only deliberate, non-secret metadata, never remote command
        # output, submitted passwords, SSH keys or form request bodies.
        result = result or {}
        safe_result = {k: v for k, v in result.items()
                       if k in {'host_id', 'verified', 'scheduled', 'ssh_recovered', 'sudo_verified', 'account_created'}}
        with self.connection() as db:
            finished = db.execute("UPDATE activities SET state=?,detail=?,result_json=?,updated=?,finished=? "
                       "WHERE id=? AND state IN ('queued','running')",
                       (state, sanitize(detail, 240), json.dumps(safe_result), now(), now(), activity_id)).rowcount
            if finished:
                db.execute("INSERT INTO activity_events(activity_id,timestamp,message) VALUES(?,?,?)",
                           (activity_id, now(), state.title() + ': ' + sanitize(detail, 3000)))
            db.execute("DELETE FROM activity_targets WHERE activity_id=?", (activity_id,))

    def reconcile_activities(self) -> None:
        """After controller restart, mark in-flight operations interrupted.

        A remote operation might already have made changes. We never replay
        it automatically or present it as successful merely because it was
        queued before the restart.
        """
        with self.connection() as db:
            interrupted = [row['id'] for row in db.execute(
                "SELECT id FROM activities WHERE state IN ('queued','running')")]
            db.execute("UPDATE activities SET state='interrupted', "
                       "detail='Controller restarted. Check remote state before retrying', "
                       "updated=?,finished=? WHERE state IN ('queued','running')", (now(), now()))
            for activity_id in interrupted:
                db.execute("INSERT INTO activity_events(activity_id,timestamp,message) VALUES(?,?,?)",
                           (activity_id, now(), 'Interrupted: controller restarted; inspect the remote state before retrying'))
            db.execute("DELETE FROM activity_targets")
            # A controller restart can interrupt a scan after begin_scan but
            # before finish_scan. Resolve those rows too, otherwise the UI
            # would misleadingly show a host stuck in Scanning forever.
            db.execute("UPDATE scans SET state='failed',finished=?, "
                       "error='Controller restarted before scan finished' "
                       "WHERE state='running'", (now(),))

    def create_plan(self, host_ids: list[str], mode: str = 'apply',
                    refresh_metadata: bool = False,
                    verbose_dry_run: bool = True) -> dict:
        """Capture approved targets in an immutable, mode-bound snapshot.

        A zero-update host is eligible for a simulated validation run only.
        A live plan still requires scanned package candidates, so changing a
        browser control cannot bypass existing live-patch safeguards.
        """
        if mode not in {'apply', 'dry_run'}:
            raise ValueError('Invalid execution mode')
        if mode == 'apply' and refresh_metadata:
            raise ValueError('Refresh consent applies only to dry-run operations')
        if not host_ids or len(host_ids) != len(set(host_ids)):
            raise ValueError("Select at least one unique host")
        targets = []
        for host_id in host_ids:
            host = self.host(host_id)
            scan = self.last_scan(host_id)
            if not host["verified"]:
                raise ValueError(f"{host['name']}: SSH not yet verified")
            if not scan or scan["state"] != "completed" or now() - scan["finished"] > 3600:
                raise ValueError(f"{host['name']}: run a fresh scan (within 1 hour)")
            result = scan["result"]
            if result.get("os", {}).get("family") not in ("debian", "ubuntu"):
                raise ValueError(f"{host['name']}: unsupported operating system")
            # Password-required sudo cannot refresh APT indexes in a
            # background scan. Permit cached metadata only with an explicit
            # warning; the worker refreshes and compares candidates with the
            # approved snapshot before changing any packages.
            if result.get("os", {}).get("apt_error") and result.get("os", {}).get("metadata") != "cached":
                raise ValueError(f"{host['name']}: package inspection failed")
            if result.get("os", {}).get("metadata") not in ("fresh", "cached"):
                raise ValueError(f"{host['name']}: package metadata unavailable")
            if host["sudo_mode"] == "read_only":
                raise ValueError(f"{host['name']}: this account is read-only")
            if result.get("os", {}).get("package_query_ok", True) is False:
                raise ValueError(f"{host['name']}: package query failed")
            packages = result.get("os", {}).get("packages", [])
            if not packages and mode == 'apply':
                raise ValueError(f"{host['name']}: no pending OS updates")
            targets.append({"host_id": host_id, "name": host["name"], "scan_id": scan["id"],
                            "packages": packages, "protected": bool(host["protected"]),
                            "address": host["address"], "user": host["ssh_user"],
                            "port": host["ssh_port"], "key_path": host["key_path"],
                            "sudo_mode": host["sudo_mode"]})
        snapshot = {
            "targets": targets,
            "mode": mode,
            "strategy": "apt-safe-upgrade" if mode == 'apply' else 'apt-simulated-validation',
            "reboot": False,
            "refresh_metadata": bool(refresh_metadata) if mode == 'dry_run' else False,
            "verbose_dry_run": bool(verbose_dry_run) if mode == 'dry_run' else False,
        }
        blob = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(blob.encode()).hexdigest()
        plan = {"id": new_id(), "created": now(), "expires": now() + 900,
                "snapshot": snapshot, "digest": digest}
        with self.connection() as db:
            db.execute("INSERT INTO plans(id,created,expires,snapshot_json,digest) VALUES(?,?,?,?,?)",
                       (plan["id"], plan["created"], plan["expires"], blob, digest))
        return plan

    def plan(self, plan_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("Unknown plan")
        data = dict(row)
        data["snapshot"] = json.loads(data.pop("snapshot_json"))
        return data

    def approve_plan(self, plan_id: str, operator: str, ack: bool,
                     confirmation: str = '') -> dict:
        """Record explicit approval of the reviewed, immutable patch plan.

        The authenticated, CSRF-protected web submit action is the explicit
        approval. A protected host still requires separate protected approval
        via the clearly labeled protected-host submission control. Retaining
        the optional parameter supports existing callers without asking users
        to type an arbitrary word in the UI.
        """
        plan = self.plan(plan_id)
        if plan["expires"] < now():
            raise ValueError("Plan expired. Scan and review again")
        if any(x["protected"] for x in plan["snapshot"]["targets"]) and not ack:
            raise ValueError("Protected host acknowledgement required")
        with self.connection() as db:
            db.execute("UPDATE plans SET approved_at=?,approved_by=?,protected_ack=? WHERE id=? AND approved_at IS NULL",
                       (now(), operator, int(ack), plan_id))
        return self.plan(plan_id)

    def create_job(self, plan_id: str, idempotency: str) -> dict:
        """Claim all hosts atomically to prevent overlapping modifying jobs."""
        plan = self.plan(plan_id)
        if not plan["approved_at"] or plan["expires"] < now():
            raise ValueError("An unexpired approved plan is required")
        if not idempotency or len(idempotency) > 100:
            raise ValueError("Invalid idempotency key")
        job_id = new_id()
        with self.connection() as db:
            # BEGIN IMMEDIATE serializes competing approvals across concurrent
            # UI requests before either can claim the same host lock.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM jobs WHERE idempotency=?", (idempotency,)).fetchone()
            if existing:
                return {**dict(existing), "existing": True}
            if db.execute("SELECT 1 FROM jobs WHERE plan_id=?", (plan_id,)).fetchone():
                raise ValueError("This plan has already been submitted")
            for target in plan["snapshot"]["targets"]:
                if db.execute("SELECT 1 FROM host_locks WHERE host_id=?", (target["host_id"],)).fetchone():
                    raise ValueError(f"Target {target['name']} is already being patched")
                if db.execute("SELECT 1 FROM activity_targets WHERE target_key=?",
                              ('host:' + target['host_id'],)).fetchone():
                    raise ValueError(f"Target {target['name']} has an active scan or SSH operation")
            db.execute("INSERT INTO jobs(id,plan_id,state,idempotency,created,mode) VALUES(?,?,?,?,?,?)",
                       (job_id, plan_id, "queued", idempotency, now(),
                        plan['snapshot'].get('mode', 'apply')))
            for target in plan["snapshot"]["targets"]:
                db.execute("INSERT INTO host_locks(host_id,job_id) VALUES(?,?)", (target["host_id"], job_id))
                db.execute("INSERT INTO job_targets(job_id,host_id,state) VALUES(?,?,?)",
                           (job_id, target["host_id"], "queued"))
        return self.job(job_id)

    def job(self, job_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            targets = db.execute("SELECT * FROM job_targets WHERE job_id=?", (job_id,)).fetchall()
        if not row:
            raise ValueError("Unknown job")
        result = dict(row)
        result["targets"] = [dict(x) for x in targets]
        return result

    def jobs(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100").fetchall()
        return [dict(x) for x in rows]

    def acknowledge_past_alerts(self) -> dict:
        """Dismiss terminal failure notifications without changing job states.

        Users must be able to stop a historical error from permanently
        coloring the Activity indicator. The evidence remains accessible in
        History until separately cleared. Active operations are untouched.
        """
        with self.connection() as db:
            jobs = db.execute("UPDATE jobs SET acknowledged=1 WHERE acknowledged=0 "
                              "AND state IN ('failed','interrupted','partially_completed')").rowcount
            activities = db.execute("UPDATE activities SET acknowledged=1 WHERE acknowledged=0 "
                                    "AND state IN ('failed','partial','interrupted')").rowcount
        return {'job_alerts': jobs, 'activity_alerts': activities}

    def clear_patch_history(self) -> dict:
        """Remove finished patch jobs and their SQL logs in one transaction.

        Never clear jobs that might still be executing. Delete children
        before parents and keep host inventory, system scans, background
        enrollment, database login accounts, and security audit records.
        Pending unsubmitted plans are retained; a plan used by a deleted job
        is removed so obsolete approvals do not accumulate indefinitely.
        """
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
                raise ValueError('Cannot clear history while patch jobs are active')
            rows = db.execute('SELECT id,plan_id FROM jobs').fetchall()
            ids = [r['id'] for r in rows]
            plans = {r['plan_id'] for r in rows}
            if ids:
                # Every job is terminal, so the associated host locks should
                # already be released. Clean any stale lock records as well.
                db.execute('DELETE FROM events WHERE job_id IN (SELECT id FROM jobs)')
                db.execute('DELETE FROM job_targets WHERE job_id IN (SELECT id FROM jobs)')
                db.execute('DELETE FROM host_locks WHERE job_id IN (SELECT id FROM jobs)')
                db.execute('DELETE FROM jobs')
                db.executemany('DELETE FROM plans WHERE id=?', [(p,) for p in plans])
        return {'deleted': len(ids), 'job_ids': ids}

    def job_state(self, job_id: str, state: str, error: str = "") -> None:
        with self.connection() as db:
            db.execute("UPDATE jobs SET state=?,error=?,started=COALESCE(started,?),"
                       "finished=CASE WHEN ? IN ('completed','failed','interrupted','partially_completed','cancelled','simulated') THEN ? ELSE NULL END WHERE id=?",
                       (state, error, now(), state, now(), job_id))
            if state in {"completed", "simulated", "failed", "interrupted", "partially_completed", "cancelled"}:
                db.execute("DELETE FROM host_locks WHERE job_id=?", (job_id,))

    def target_state(self, job_id: str, host_id: str, state: str, error: str = "", reboot: bool | None = None) -> None:
        with self.connection() as db:
            db.execute("UPDATE job_targets SET state=?,error=?,reboot_required=? WHERE job_id=? AND host_id=?",
                       (state, error, None if reboot is None else int(reboot), job_id, host_id))

    def event(self, job_id: str, host_id: str | None, level: str, task: str, message: str) -> None:
        from .validation import sanitize
        with self.connection() as db:
            db.execute("INSERT INTO events(job_id,host_id,timestamp,level,task,message) VALUES(?,?,?,?,?,?)",
                       (job_id, host_id, now(), level, sanitize(task, 300), sanitize(message)))

    def events(self, job_id: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT 250", (job_id,)).fetchall()
        return [dict(x) for x in reversed(rows)]

    def request_cancel(self, job_id: str) -> None:
        """A cooperative cancellation flag, never a signal that kills apt."""
        with self.connection() as db:
            db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=? AND state IN ('queued','running')", (job_id,))
