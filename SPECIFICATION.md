# OPM v0.4.0 Architecture Amendment: Web-first Interface

This amendment supersedes the interface selection in the original V1.8
specification below. The underlying controller, security and patching
requirements remain relevant.

- **Primary UI:** Responsive Django web app, server-rendered HTML/CSS.
- **Access:** Account-based login stored in the same SQLite database as the
  existing OPM inventory. Argon2id password hashing, CSRF protection, login
  throttling, admin-created accounts, Operator/Viewer roles and audit trail.
- **Exposure:** Loopback-only Gunicorn by default. Internal HTTPS via a
  deliberately restricted reverse proxy, with no WAN-exposed hostname.
- **Backend:** Reuse the existing private Unix-socket controller and detached
  systemd workers. No Ansible or SSH commands originate in web view handlers.
- **Operations:** Read-only OS and container scans, approved Debian/Ubuntu OS
  updates, host provisioning, job history and cooperative cancellation.
- **Container updates:** Docker and Podman scanning remains review-only until
  Compose compatibility and preflight checks are verified in a future release.
- **Secondary UI:** Original Textual TUI retained as a maintenance option.
- **Installer:** `OPMInstall.sh` on Debian 13 creates services and accounts,
  backs up SQLite, applies migrations and safely upgrades existing OPM data.
- **Documentation:** See README.md, SECURITY.md, UPGRADE.md and
  IMPLEMENTATION_STATUS.md for the current release contract.

---

# Onyx Patch Manager

## Technical specification, version 1.8

**Status:** Design approved, implementation not started  
**Date:** November 1, 2026  
**Primary platform:** Dedicated Debian controller VM  
**Interface:** Mouse-first local terminal user interface (TUI), with full keyboard support  
**Source code convention:** Comprehensive comments and docstrings; no emojis or em dashes in code, documentation, or interface copy.

## 1. Product definition

Onyx Patch Manager (OPM) is a centralized, agentless patch and update management application for a private homelab. An operator connects to a Debian controller over SSH, launches a modern terminal UI, maintains server inventory, scans operating systems and containers for updates, reviews proposed changes, approves patch runs, monitors execution, and inspects results or errors.

The controller operates independently of the operator's terminal session. Managed Linux machines do not run an OPM agent.

### 1.1 Objectives

1. Add, edit, group, disable, and remove servers without editing Ansible inventory YAML.
2. Detect available Debian and Ubuntu OS package updates.
3. Detect Docker and Podman container image changes using supported registry and engine metadata.
4. Update explicitly approved Docker Compose and compatible Podman Compose deployments after validation.
5. Display unsupported or uncertain deployments as review-only.
6. Provide clear, persistent per-host and per-operation status, including exact available error output and guided retries.
7. Continue accepted patch jobs after the TUI or the operator's SSH session closes.
8. Verify basic post-patch connectivity and container status without inspecting application internals.
9. Preserve an audit-friendly history of scans, approvals, jobs, failures, and retries.
10. Support complete mouse-driven use of the TUI in compatible terminals while retaining full keyboard navigation.

### 1.2 Out of scope for V1

- Automatic unattended patch installation.
- Automatic OS reboots.
- In-place distribution release upgrades.
- Automated Unraid OS or Home Assistant OS upgrades.
- Managed recreation of standalone `docker run` or `podman run` containers.
- Automated updates of Podman Quadlet or systemd-managed containers.
- Arbitrary application-specific health checks, such as Frigate camera FPS or API inspection.
- Automated database backup, schema rollback, or guaranteed application rollback.
- Full host monitoring dashboards for CPU, RAM, and storage trends.
- Any web UI in V1. An optional internal-only browser UI is deferred to V2; there will be no publicly exposed management interface.

## 2. Operating model

### 2.1 Process diagram

```text
Operator's SSH terminal
        |
        v
+-------------------------------+
| Textual TUI                   |
| Dashboard, Servers, Patching  |
| History, Settings             |
+---------------+---------------+
                | local Unix socket
                v
+-------------------------------+
| Central controller service    |
| Inventory, scans, scheduling  |
| Approval validation, events   |
+------------+------------------+
             |        |
             |        +---- SQLite and restricted job artifacts
             |
             v
+-------------------------------+
| Independent systemd-managed   |
| patch worker per active job   |
| Ansible Runner + adapters     |
+---------------+---------------+
                |
                | SSH, no remote OPM agents
                v
     Debian / Ubuntu hosts
       |                 |
       +-- APT          +-- Docker / Podman inspection
                           and approved Compose updates
```

### 2.2 Recommended implementation stack

| Layer | Choice | Design note |
|---|---|---|
| TUI | Python, Textual, Textual CSS | Mouse-first with full keyboard support, responsive layouts, clickable tables, modal confirmations, scrollable log views |
| Controller | Python background service | Single authority for inventory, approvals, locks, and scheduling |
| Local IPC | Unix-domain socket with authenticated peer credentials | Local access only; no listening TCP port by default |
| Automation | Ansible Core | Linux tasks and SSH transport |
| Runner | Ansible Runner | Structured task events, status, return codes, logs |
| Job isolation | Separate systemd-managed workers | A job survives TUI disconnect and controller UI restart; worker is not in TUI process |
| Database | SQLite in WAL mode | Schema migrations, durable transactions, indexes, retention |
| Registry inspection | Registry API or compatible platform-aware image inspection | Metadata-based checks without downloading image layers during ordinary scans |
| Configuration | TOML for controller policy only | Inventory remains in SQLite, not hand-managed YAML |

Textual, Ansible Runner, Ansible inventory scripts, and systemd transient services all have documented capabilities supporting this approach. Exact versions must be pinned and tested during implementation.

### 2.3 Process lifecycle and resilience

- The controller runs under a dedicated service account managed by systemd.
- The TUI is a client of the controller. Closing it never cancels active jobs.
- Each approved modifying job runs in an independently managed worker service, not merely an asynchronous thread inside the controller.
- The worker writes durable task events and state transitions. The controller streams these to any connected TUI.
- A controller restart must reconnect to still-active workers and replay recorded events.
- A controller VM reboot can interrupt remote operations. On startup, any unconfirmed job transitions to `Interrupted` or `Reconciliation Required`, never directly to `Completed`.
- Incomplete modifying jobs are not automatically rerun. The operator must review refreshed host state.
- No two modifying workers may operate simultaneously against the same host. Compose project and Podman user-context locks also apply.
- The controller's own OS is not an ordinary patch target in V1. Controller application updates are a separate, guarded process.

## 3. TUI visual and interaction design

### 3.1 Visual system

- Modern dark dashboard, slate backgrounds, teal focus and success accents, muted blue informational text.
- Amber for warning and intervention, red for failures. Always pair color with text or a recognizable symbol so color is not the only status signal.
- Wide terminals show a sidebar, main content, and optional details panel.
- At approximately 80 columns, collapse the sidebar to a compact header and open details in a dedicated view.
- Minimum target size is 80 x 24 terminal cells. Below that size, display a clear resize message instead of clipping destructive controls.
- Mouse interaction is a first-class V1 requirement: clickable navigation, rows, expanders, buttons, checkboxes, tabs, dropdowns, forms, scrollbars, and wheel scrolling.
- All mouse actions must have keyboard equivalents, including selection, review, approval, retry, error details, and cancellation.
- Visible hover/focus and selected states must make it clear what is clickable or currently active.
- Footer shows context-sensitive shortcuts without implying keyboard use is mandatory.
- Mouse support depends on terminal-emulator forwarding; validate Windows Terminal over SSH and document fallback for incompatible terminals.
- No emojis or em dashes in UI strings.

### 3.2 Top-level navigation

| Section | Responsibilities |
|---|---|
| Dashboard | Reachability, patch findings, scan recency, running jobs, warnings |
| Servers | Add, edit, remove, test, group, protect, and inspect hosts |
| Patch Manager | Scan, select, expand, review, preflight, approve, execute, verify |
| History | Search jobs, compare attempts, inspect errors, export sanitized reports |
| Settings | SSH profiles, scan intervals, global safety defaults, local app updates |

The Patch Manager combines operating-system and container workloads. No separate Docker or Podman top-level menu is required.

### 3.3 Dashboard wireframe

```text
ONYX PATCH MANAGER                              Controller: Ready

[Dashboard] [Servers] [Patch Manager] [History] [Settings]

 Hosts: 08    Reachable: 07    OS updates: 17    Image changes: 03

 Attention required
  - debian-vm       Unreachable, last checked 12:03
  - frigate-vm      1 protected image change

 Running jobs
  - Run 0142        2 of 3 hosts completed, open details

 Last scan: 12:05    [S] Scan all    [P] Patch Manager
```

Dashboard counts distinguish confirmed results from stale, failed, or unscanned findings. The UI must show the last successful check time.

### 3.4 Servers screens

**List:** columns for host, operating system, SSH status, patch readiness, engine types, pending updates, protected status, and last scan.

**Add Server, Step 1:** name, IP or DNS, SSH port, username, authentication profile, optional group.

**Add Server, Step 2:** connect, verify SSH host-key fingerprint, detect OS and Docker/Podman contexts, verify privilege escalation, classify update eligibility, save.

**Server detail tabs:**

- Overview: OS, inventory groups, last connection, last patch, pending reboot, container engines.
- Connection: SSH identity, port, host-key trust state, connection test, credential availability.
- Patch Policy: allowed update types, protected-host flag, reboot defaults, scan enablement, excluded workloads.

When connectivity verification is unavailable, allow an explicit `Save unverified` action. Unverified targets cannot be patched until validation succeeds.

**Remove host:** removes the active inventory record only, keeps historical reports, and never issues a remote deletion command.

### 3.5 Combined Patch Manager wireframe

```text
PATCH MANAGER                         Last scan: 12:05
[All] [OS] [Containers] [Review only]

frigate-vm          Debian 13          Patch ready
  [x] OS updates                     8 packages
  [ ] Docker: frigate                 Image changed, PROTECTED
  [i] Image: local-debug             Review only

openclaw-vm         Debian 13          Patch ready
  [x] OS updates                     4 packages
  [x] Docker: openclaw                Image changed

podman-vm           Debian             Patch ready
  [ ] Podman: worker                  Verified Compose project
  [i] Podman: backup.timer            Quadlet, review only

Selected: 3 operations
[S] Scan  [Space] Select  [Enter] Details  [R] Review
```

Selectable units are an OS patch operation per host and a verified Compose service or project. V1 does not support arbitrary per-package exclusion. Expanding a host shows current and candidate package versions, image references and digests, the matching engine context, and reasons for review-only classification.

### 3.6 Review and execution screens

**Review:** immutable, versioned plan with host targets, operation type, expected package actions, image reference and digest, restart scope, warnings, protected-target acknowledgements, and preflight results. Approval is explicit, and a stale plan requires refresh and renewed approval.

**Execution:** host cards with per-operation state; expandable task logs and exact error output; queued/running/completed counts; cancellation request; recheck or retry prompts when appropriate.

**Results:** separate patch outcome and post-patch verification result. Show pending OS reboots, container `running`/`healthy`/`unhealthy`/`starting`/`exited`, unknown states, and manual review notes.

### 3.7 Mouse interaction policy

| Interface element | Expected mouse behaviour |
|---|---|
| Sidebar and main tabs | Single-click changes the visible section or tab |
| Server and container rows | Click selects or opens details; avoid ambiguity between checkbox selection and row expansion |
| Checkboxes and filters | Click toggles selection; show disabled reasons for ineligible work |
| Add Server wizard | Click fields, dropdowns, Next/Back, and SSH trust confirmation |
| Patch review | Click to inspect packages, expand warnings, approve selected plan |
| Execution monitor | Click host/task cards, expand logs, open error details and retry/recheck dialogs |
| Scrolling | Support wheel/trackpad scrolling, visible scrollbars and sensible scroll focus |
| Modal actions | All controls clickable; destructive actions still require explicit confirmation |

Textual receives mouse clicks and scrolling events in compatible terminal emulators. UI tests must exercise pointer interactions as well as equivalent keyboard actions. Mouse convenience never relaxes the controller's approval and authorization checks.

### 3.8 Keyboard policy

| Shortcut | Action |
|---|---|
| Alt+1 through Alt+5 | Change primary section where terminal supports it |
| G then 1 through 5 | Reliable fallback for primary navigation |
| Up / Down | Move highlighted row |
| Tab / Shift+Tab | Move focus |
| Enter | Open selected detail or activate control |
| Space | Toggle current selection |
| / | Search or filter |
| A | Add a host in Servers, select all eligible in Patch Manager with screen-specific label |
| E / D / T | Edit, remove, or test a server in Servers |
| S | Start a scan in Patch Manager |
| O | Filter OS updates |
| C | Filter containers, except during execution when it requests cancellation |
| R | Review plan or recheck a failure, based on current context |
| L | Expand or collapse logs |
| Esc | Return, close modal, or clear search |
| ? | Help and shortcut reference |
| Q | Quit TUI only; never cancel an active job silently |

Single-key shortcuts are disabled while entering text. Terminal compatibility is tested, and no destructive action depends exclusively on a key combination.

## 4. Inventory and authentication

### 4.1 Inventory ownership

SQLite is the canonical source of hosts, groups, and per-host configuration. The controller generates a valid Ansible dynamic inventory response from this data for each job. The operator is not required to edit YAML. For reproducibility, each job stores a sanitized inventory snapshot and effective policy version.

The inventory adapter supports Ansible's documented `--list` and `--host` conventions. Host variables include address, port, remote user, connection mode, and privilege escalation configuration, but never raw passwords.

### 4.2 SSH profiles

A reusable connection profile defines username, port, identity reference, authentication preference, and privilege escalation policy. Hosts may override profile defaults.

- Reuse existing SSH keys or a managed SSH agent where available.
- Support guided public-key installation after independent fingerprint verification and explicit consent.
- Password-based registration and inspection are allowed when secure interactive credential prompts are available.
- A background patch run may start only when all required SSH and privilege escalation credentials are accessible to its independent worker for the full job lifetime.
- For password-based execution, use an explicit short-lived credential handoff to the worker using restricted runtime memory-backed storage or a suitable systemd credential facility. Never put passwords in SQLite, a generated inventory file, CLI argument, or persistent logs.
- If safe credential handoff is not yet implemented or prerequisites are not met, mark the host `Credentials Required`, and block modifying background jobs instead of silently degrading the safety guarantee.

SSH host-key verification is mandatory. New keys require approval; unexpected changes always block the connection pending manual verification. Do not automatically accept unknown host keys.

### 4.3 Access controls

- The controller should run as a dedicated, non-root service account.
- A narrowly scoped privileged installer or systemd integration may start validated worker units; do not expose unrestricted arbitrary command execution through the local socket.
- The Unix socket must permit only authorized local operators.
- Private keys, controller configuration, database, and raw job artifacts use restrictive filesystem permissions.
- Docker daemon access and privileged Podman contexts must be treated as high privilege.
- Commands must use argument arrays, strict input validation, and bounded timeouts. Do not interpolate untrusted hostnames, image tags, or file paths into shell strings.
- Apply Ansible `no_log` for sensitive steps and redact secrets before events reach persistent logs. No universal redactor can guarantee removal of secrets from arbitrary command output; avoid producing sensitive output in the first place.

## 5. Discovery and scan pipeline

### 5.1 Defaults

- Basic SSH reachability: every 15 minutes by default, configurable and disableable.
- OS and container update discovery: every 12 hours by default, configurable and disableable.
- Manual `Scan all`, `Scan group`, and `Scan host` actions.
- No scheduled installation of updates in V1.
- Cached observations must include freshness and data-source status. Unknown, denied, rate-limited, or stale findings cannot be labeled `Up to date`.

A scan is not a patch installation. APT metadata refreshes may update package index files on the host; they do not install packages. Registry checks request image metadata and should not download layers.

### 5.2 Linux package scanning

- Determine distribution and supported APT version.
- Gather pending upgrades with current and candidate version details.
- Show held packages and repository errors as distinct findings.
- Mark security updates only where distribution metadata reliably supports classification.
- Detect pre-existing pending reboot status and capture it as a baseline.
- Support dry-run or package-manager simulation for preflight; never assume a scan prediction is a guarantee.

### 5.3 Docker and Podman discovery

Each server may expose multiple `engine contexts`, not merely one boolean engine flag.

- Docker context includes daemon availability, effective privileges, container metadata, and Compose support.
- Podman context includes execution user, rootless or rootful mode, storage context, engine version, and any Compose provider.
- Scan each explicitly configured and accessible Podman context; rootless containers belonging to another account must not be silently omitted as if absent.
- Discover container ID, name, image reference, deployed local image ID, engine, project association, restart policy, state, health configuration, and health status.
- Identify deployment ownership and classify verified Compose projects, standalone containers, Quadlet/systemd workloads, locally built images, and unknown definitions.
- A workload with unknown ownership or incomplete deployment metadata is review-only.

### 5.4 Registry image change detection

- Resolve the currently configured image reference against registry metadata for the target OS and architecture.
- Compare comparable deployed and remote image digests. A changed multi-platform index alone is not sufficient to prove that a host-specific platform image changed.
- Treat registry authentication failures, unavailable metadata, unsupported manifests, and ambiguous digest mappings as `Unknown`, not `No updates`.
- For mutable tags such as `stable` or `latest`, report content changes at that same tag.
- For pinned version tags, do not choose a newer version tag automatically. Label `Pinned tag; version discovery not included in V1` where appropriate.
- For digest-pinned images, report the pin and require a deliberately approved configuration change for a different digest.
- Never pull an image or restart a container during an ordinary scan.

## 6. Patch policies and preflight

### 6.1 OS policy

The default OS action uses Ansible's APT module in a conservative upgrade mode, with no distribution release upgrade, automatic purge, forced downgrade, or automatic removal of held packages. Where supported, explicitly fail if a proposed operation requires autoremove. Package-manager dependency behavior must be checked immediately before execution.

Preflight checks include SSH trust and auth, sudo availability, free disk space, repository health, package-manager locks, relevant package holds, pending reboot baseline, and package simulation. An OS upgrade can restart system services through package-maintainer scripts even when automatic OS reboot is disabled. Display that warning.

### 6.2 Compose eligibility

An update is eligible for execution only if all relevant conditions hold:

1. Engine and relevant user context are reachable.
2. The deployment's original Compose files, project name, working directory, overrides, and required environment inputs can be reconstructed without exposing secrets.
3. A Compose provider and its supported options have been verified on that host and context.
4. Affected services and dependency scope can be determined before approval.
5. The resolved configuration matches the expected running project closely enough to avoid unintended drift.
6. The approved image reference and digest can be revalidated before mutation.
7. Service, project, and host locks are available.
8. Protected targets have explicit additional approval.

For Podman, `podman compose` delegates to an external provider. The application must detect the actual provider and not assume that it supports every Docker Compose option.

### 6.3 Protected targets

A host or an individual container workload can be marked `Protected`. A protected host can still be scanned. Selecting a modifying action presents an additional interruption warning and acknowledgement. A protected container need not block ordinary OS patching, but the OS update risk warning remains visible.

## 7. Execution sequence

### 7.1 Plan creation

1. Capture selected host operations and verified Compose targets.
2. Capture scan ID, policy version, resolved project identity, expected images, and currently deployed state.
3. Generate a canonical, immutable plan snapshot and unique plan ID.
4. Run read-only preflight checks and approved package simulations.
5. If expected state changed, invalidate the plan and require a refreshed review.
6. Record operator approval with timestamp and acknowledged protections.
7. Place the job in the execution queue and acquire target locks before mutation.

### 7.2 Per-host execution

- Default maximum concurrency is one modifying host at a time.
- A host selected for both OS and container updates receives OS updates first, then a verification gate before Compose updates.
- Different independent hosts continue if another host fails, unless a configured global stop policy has been triggered.
- Stop subsequent dependent actions on a failed host.
- Retain exact Ansible Runner task status and event IDs; map events to the application's job and target states.

### 7.3 Compose update sequence

1. Reconfirm ownership, project files, environment requirements, and image digest at execution time.
2. Record currently deployed image identity and current workload state.
3. Pull only approved images, without touching running containers.
4. Validate resolved project configuration again.
5. Recreate only approved services, or the explicitly approved project-level dependency set, using the actual provider's supported commands. Avoid unintended dependent service recreation.
6. Wait for running state and health if the service defines a health check. If no health check exists, confirm running state only.
7. Record new image identity, container state, actual changes, and warning messages.
8. If health fails, stop further updates in the affected project and offer recheck, logs, and informed manual recovery.

Do not force recreation of unrelated services or remove orphans by default. Do not assert that a previous image is a guaranteed rollback path for application databases.

### 7.4 Post-patch verification

Only these categories are included in the V1 default verifier:

| Target | Check | Result states |
|---|---|---|
| Linux host | SSH accessible | Connected, Unreachable, Unknown |
| OS patch | Package transaction completed and package state inspected | Completed, Failed, Requires Attention |
| Linux host | Reboot indicated by OS | Yes, No, Unknown |
| Docker | Daemon reachable | Available, Unavailable, Unknown |
| Podman | Selected user context query succeeds | Available, Unavailable, Unknown |
| Container | Expected container running | Running, Exited, Missing, Unknown |
| Container with built-in health check | Engine-reported health | Starting, Healthy, Unhealthy, Unknown |
| Container without health check | No health inference | Running, not `Healthy` |

Default container startup grace window: 120 seconds, configurable per workload. No Frigate camera checks, application HTTP probes, FPS inspection, or other deep application-specific tests.

## 8. State machine and retry

### 8.1 Job states

`Draft -> Preflight -> Awaiting Approval -> Queued -> Running -> Verifying -> Completed`

Alternative terminal or exception states: `Failed`, `Requires Attention`, `Interrupted`, `Cancelled`, `Partially Completed`. Each target operation also has its own state. `Reboot Required` is a result flag, not necessarily a failure state.

`Preflight Blocked` is not automatically `Failed`: it indicates no modifying operation started. A previously verified host may later become unavailable and must be reevaluated.

### 8.2 Failure dialog

Display:

- Host and selected engine context.
- Exact failed operation and task or Compose service.
- Ansible status and return code, Docker/Podman result, and any available sanitized stdout and stderr.
- Timestamp, retry attempt count, and whether mutation had already started.
- Human-readable explanation of likely cause, without claiming certainty when evidence is insufficient.
- Context-sensitive actions: `Recheck`, `Retry`, `View Logs`, `Copy Sanitized Error`, `Leave for Manual Review`.

### 8.3 Retry policy

| Error class | Default action |
|---|---|
| SSH timeout before mutation | Recheck connection, then allow target retry |
| Registry connection failure before pull completes | Retry pull after revalidation |
| APT lock | Recheck lock and package state; do not delete lock files |
| Interrupted or partially completed APT | Inspect and reconcile package state before any new upgrade |
| Failed Compose pull, old container still running | Retry download, without unnecessary recreation |
| New container is unhealthy | Recheck health and inspect state; never blindly recreate again |
| Missing Compose project configuration | Block updates, require repair |
| Worker or controller interrupted | Reconcile remote state, then require operator review |

Retries create a new attempt record referencing the original attempt. Preserve the original error, approvals, and logs. A retry of a modifying action against a protected target requires renewed explicit approval. Basic rechecks are non-modifying.

### 8.4 Cancellation

- Cancellation is a request, not an unqualified process kill.
- Stop dispatching queued targets immediately.
- For an active modifying operation, stop at the next known safe boundary where possible.
- If the process cannot be safely interrupted, display `Cancellation Pending` and allow it to complete its critical step.
- Distinguish `Cancelled Before Changes`, `Cancelled After Partial Changes`, and `Interrupted; State Unknown` in the result.

## 9. Data model

Use SQLite with foreign keys, migrations, WAL, and indexed timestamp fields. The following is the logical schema, not final SQL definitions.

| Table | Key fields | Purpose |
|---|---|---|
| `hosts` | id, name, address, os_family, disabled, protected, created_at | Inventory |
| `host_groups` | id, name, policy_id | Group definitions |
| `host_group_members` | host_id, group_id | Many-to-many grouping |
| `ssh_profiles` | id, user, port, key_reference, auth_method, sudo_policy | Reusable connection settings |
| `host_ssh_settings` | host_id, profile_id, overrides_json, fingerprint_ref | Host overrides and trust |
| `engine_contexts` | id, host_id, engine, linux_user, rootless, provider, access_state | Docker and Podman contexts |
| `compose_projects` | id, context_id, name, root_path, config_identity, verified_at | Deployment ownership |
| `workloads` | id, context_id, project_id, service, image_ref, deployed_digest, protected | Container inventory |
| `scans` | id, start_at, finish_at, scope, status | Scan execution |
| `findings` | id, scan_id, target_id, kind, data_json, freshness, severity | OS and container findings |
| `plans` | id, created_by, scan_id, policy_hash, snapshot_json, expiry_at | Immutable patch plan |
| `approvals` | id, plan_id, operator, approved_at, protection_ack | Explicit operator approval |
| `jobs` | id, plan_id, state, created_at, started_at, finished_at | Job identity |
| `job_targets` | id, job_id, host_id, workload_id, operation, state | Per-target execution |
| `attempts` | id, job_target_id, attempt_no, parent_attempt_id, status, rc | Retry lineage |
| `job_events` | id, attempt_id, seq, timestamp, task, level, sanitized_output | Structured execution log |
| `verification_results` | id, attempt_id, check_name, result, observed_at, detail | Post-patch checks |
| `settings` | key, validated_value | Controller defaults and UI preferences |

Use opaque IDs as stable internal keys. Never reuse a removed host's ID. Save an immutable sanitized plan snapshot and relevant approved image identities per job. Secrets must not be stored in these records.

### 9.1 Persistent paths

Proposed Linux defaults, subject to packaging review:

- Configuration: `/etc/opm/config.toml`
- SQLite data: `/var/lib/opm/state.db`
- Job artifacts: `/var/lib/opm/jobs/<job-id>/`
- SSH trust and approved identity references: `/var/lib/opm/ssh/`
- Unix socket: `/run/opm/controller.sock`
- Temporary credentials: controlled runtime-only location beneath `/run/opm/` with strict permissions and cleanup

## 10. Local controller protocol

The TUI talks to the local controller through a versioned, authenticated, structured Unix-socket protocol. No general remote shell command feature is exposed. Example operations:

| Category | Operations |
|---|---|
| Inventory | `hosts.list`, `hosts.add`, `hosts.update`, `hosts.disable`, `hosts.remove` |
| Access | `hosts.test_connection`, `hosts.verify_trust`, `profiles.list` |
| Discovery | `scans.start`, `scans.status`, `scans.findings`, `workloads.list` |
| Planning | `plans.create`, `plans.preflight`, `plans.get`, `plans.approve` |
| Execution | `jobs.start`, `jobs.status`, `jobs.events`, `jobs.request_cancel` |
| Recovery | `jobs.reconcile`, `jobs.recheck`, `jobs.retry_target` |
| Reporting | `history.query`, `history.get`, `history.export_sanitized` |

Every modifying request validates target state, operator authorization, current plan hash, and applicable locks. Provide an idempotency key for job creation so double-clicking or re-submitting a request cannot accidentally start duplicate patch runs.

## 11. Controller self-update model

Controller application releases are managed separately from managed-host patch jobs.

1. Check a trusted release source and verify package or artifact integrity.
2. Block new jobs and wait for active operations to complete or reach a safe state.
3. Use an independent installer or systemd-supervised updater, not the running controller replacing itself directly.
4. Back up the SQLite database and verify schema migration compatibility.
5. Apply release, restart controller, then check Unix-socket responsiveness and database compatibility.
6. Maintain a documented rollback path for the controller application itself.

Do not patch the controller Debian OS from OPM V1. Application update checks may be manual initially; automatic installation is not in V1.

## 12. Error logging and reporting

- Preserve structured Ansible events, sanitized stdout and stderr, exit status, host identity, task name, and timestamps.
- Docker and Podman events include provider, context user, service, image reference, image digest when known, and container state.
- Each run retains preflight findings and post-patch verification records, even after retry success.
- Show a concise summary first. Expand full technical output on request.
- Support copying a sanitized error and exporting a redacted report as Markdown or JSON.
- Include configurable retention and cleanup with a conservative initial default; never delete an active job's artifacts.
- Log the origin of a reported error. If no underlying error is available, say `No diagnostic output supplied`, rather than inventing an explanation.

## 13. Testing and acceptance criteria

### 13.1 Unit tests

- Inventory serialization and dynamic Ansible inventory JSON.
- Host/group validation and duplicate detection.
- SSH host-key mismatch handling.
- Plan hashing, expiration, and approval invalidation.
- Docker and Podman engine context classification.
- Registry digest comparison, including multi-platform images and unknown data.
- State transitions, retry limits, and error normalization.
- Sanitization and logging behavior.
- Terminal shortcut handling, mouse event handling, row selection, clickable widgets, scrolling and responsive layouts.

### 13.2 Integration tests

- Register a Debian VM using SSH key verification, then scan and patch it.
- Add an unreachable host; verify it is skipped and its error is actionable.
- Run an APT update with a package lock; verify no unsafe lock removal.
- Discover Docker Compose project and update one approved service without recreating an unrelated service.
- Discover rootless Podman workloads as their correct Linux user.
- Verify supported Podman Compose provider behavior; unsupported providers remain review-only.
- Confirm standalone containers, Quadlet workloads, and Unraid-managed containers cannot be updated by the V1 executor.
- Interrupt TUI SSH session during a job; job must continue and UI must reconnect.
- Restart controller while worker runs; daemon must reconstruct the active job.
- Reboot controller VM during a worker action; job must be reconciled and not silently replayed.
- Mark a Compose service protected; ensure approval is required.
- Force a container health check to fail; show the true failure and offer `Recheck` rather than automatic redeployment.
- Verify a running container with no health check is shown as `Running`, not `Healthy`.
- Attempt a stale or duplicate approval; reject unintended execution.
- Test all principal TUI operations using only the mouse, including server registration, update selection, plan review, failure inspection, retry confirmation, and log scrolling.
- Test keyboard-only equivalents and mouse behaviour over Windows Terminal through SSH.

### 13.3 Release acceptance checklist

V1 is complete only when all of the following pass:

- [ ] A host can be added, edited, grouped, tested, and removed entirely through the TUI.
- [ ] All core TUI workflows can be completed with a mouse in a compatible terminal.
- [ ] All core TUI workflows remain accessible by keyboard alone.
- [ ] No direct inventory YAML editing is required.
- [ ] Supported OS patches are reviewed and explicitly approved.
- [ ] Docker and Podman appear under one Containers filter.
- [ ] Scans distinguish available, current, unknown, stale, and review-only states.
- [ ] Verified Compose updates require explicit approval and pass scoped preflight checks.
- [ ] Protected workloads require an additional acknowledgement.
- [ ] No unsupported deployment is modified.
- [ ] Patch jobs continue when the TUI disconnects.
- [ ] A controller restart can reattach to active worker events.
- [ ] Failed and interrupted jobs retain errors and safe recovery choices.
- [ ] Cancellation does not blindly terminate critical package-manager actions.
- [ ] Health checks are limited to OS and container engine state.
- [ ] History is persistent and can export sanitized reports.
- [ ] All application code includes comprehensive comments and docstrings documenting intent and non-obvious behavior.
- [ ] No emojis or em dashes appear in project code comments, UI copy, or documentation.

## 14. Suggested implementation phases

| Phase | Deliverable | Exit condition |
|---|---|---|
| 1. Foundation | Project structure, configuration, SQLite schema, migrations, controller IPC, empty Textual shell | TUI connects to local controller; persistence works |
| 2. Inventory | Server wizard, SSH trust, connection profiles, dynamic Ansible inventory | Can manage test hosts without editing YAML |
| 3. Scanning | Debian/Ubuntu APT scan, Docker/Podman discovery and registry checks | Reliable and labeled scan results |
| 4. Planning | Selection, snapshot, review, preflight, protected acknowledgements | No mutation without a valid approved plan |
| 5. Execution | Systemd-managed workers, Ansible Runner, event streaming, host locks | Patch job survives terminal disconnection |
| 6. Containers | Verified Docker Compose and Podman Compose adapters | Scoped approved updates and basic verification |
| 7. Recovery | Error dialogs, retry/recheck, interruption reconciliation, history export | Failure and restart integration tests pass |
| 8. Hardening | Permissions, audit review, mouse and keyboard usability, tests, packaged installer | V1 acceptance checklist passes |

## 15. Source-code documentation standard

The user explicitly requests comprehensive comments. Implement to the following standard:

1. Public classes, modules, and nontrivial methods receive docstrings explaining purpose, invariants, inputs, outputs, and expected errors.
2. Complex state transitions include comments explaining **why** a transition is permitted or blocked.
3. Any potentially destructive operation documents required approval, locks, validation, rollback limitations, and its safe interruption boundary.
4. SSH, sudo, runtime credentials, temporary files, log redaction, and Unix-socket authorization contain clear security rationale comments.
5. Adapter interfaces explain engine differences and any compatibility checks instead of treating Docker and Podman as identical.
6. Tests include explanatory descriptions of the risk or behavior they protect.
7. Keep comments synchronized with implementation. Avoid redundant line-by-line commentary on obvious assignments.
8. Use Python typing, explicit error types, informative structured logging, and readable naming.
9. No emojis or em dashes in comments, docstrings, UI strings, or project documents.

## 16. Open engineering validations before coding

These are compatibility validations, not unresolved product design questions:

- Pin supported Debian, Python, Textual, Ansible Core, and Ansible Runner versions in the build environment.
- Test secure execution credentials with the chosen independent systemd worker model.
- Confirm image manifest comparison for specific registry providers, authentication, and multi-platform image variants.
- Confirm Compose project discovery from actual metadata and source files on supported Docker and Podman installations.
- Confirm rootless Podman context discovery and external Compose provider behavior on supported hosts.
- Validate package-manager simulation and safe APT upgrade policy against Debian and Ubuntu test VMs.
- Confirm that the controller installer can create restricted systemd units and filesystem paths without making the controller daemon root.

Do not claim an unsupported platform or provider is patchable until its compatibility tests pass.

## 17. Technical references

- Textual documentation: https://textual.textualize.io/guide/
- Ansible Runner Python interface: https://docs.ansible.com/projects/runner/en/latest/python_interface/
- Ansible dynamic inventory: https://docs.ansible.com/projects/ansible-core/devel/dev_guide/developing_inventory.html
- Ansible APT module: https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/apt_module.html
- Docker Compose up: https://docs.docker.com/reference/cli/docker/compose/up/
- Docker Registry API: https://docs.docker.com/reference/api/registry/latest/
- Docker image digest concepts: https://docs.docker.com/dhi/explore/security-concepts/digests/
- Podman Compose wrapper: https://docs.podman.io/en/latest/markdown/podman-compose.1.html
- Podman health checks: https://docs.podman.io/en/latest/markdown/podman-healthcheck.1.html
- systemd-run: https://www.freedesktop.org/software/systemd/man/devel/systemd-run.html

## 18. V2 roadmap: optional internal-only web interface

**Decision logged:** V1 ships as a mouse-first Textual TUI with keyboard fallback. The user is interested in an optional internal-only web UI for easier mobile use, but explicitly does not want the web complexity, JavaScript frontend, or username/password administration included in V1.

### 18.1 V2 scope

- Keep the existing Ansible controller, database, inventory, approval checks, patch engine, and job-history components unchanged.
- Explore a lightweight responsive browser client or Textual browser delivery after the V1 TUI is stable.
- Limit access strictly to internal/private paths, such as a trusted management LAN or WireGuard VPN. Do not add public internet exposure or router port forwarding.
- Evaluate practical mobile navigation for touch, small screens, confirmation dialogs, error details, and log scrolling before selecting a web implementation.
- Do not assume that a VPN alone always provides adequate user authorization. Specify explicit access controls, session handling, CSRF defenses where applicable, and audit logging before enabling any browser-originated modifying action.
- Web serving remains disabled by default and must be an opt-in feature.
- Avoid introducing a separate large JavaScript frontend unless the V2 usability requirements genuinely require it.

### 18.2 V2 entry criteria

- V1 acceptance tests, including mouse-driven TUI workflows, pass.
- Controller interfaces are transport-independent and distinguish read-only inspection from approved mutations.
- A separate threat model and mobile usability review approve the V2 interface before development.

**End of V1.8 specification.**

## V1 implementation addendum: dedicated SSH provisioning (v0.2.0)

The dedicated remote `opm` account and SSH-key wizard are required V1
functionality. An operator explicitly approves a one-time bootstrap through
an existing administrator account after out-of-band SSH fingerprint
verification. OPM installs a dedicated public key, creates a distinct Linux
user, configures ordinary sudo-group membership with a password, and confirms
key-based SSH plus sudo authentication. It never overwrites an existing
`opm` account, changes personal credentials or enables passwordless sudo
without an independent configuration change.

The interactive patch review requests sudo authorization once per run, with
an optional shared password and per-host overrides. Credentials remain
memory-resident and are sent only to an approved detached worker, then
consumed and discarded. A preflight refresh validates cached scan results
against the approved package candidates before any OS modifications.

This addendum describes V1 requirements and early v0.2.0 code. Live
end-to-end validation remains outstanding; it does not imply production
readiness.


## V1 dashboard and installer delta (v0.4.4)

- Dashboard Connection is an ICMP ping observation, not an SSH banner or
  successful login. Up means a reply; Down means no reply; Unknown means a
  controller-side probe failure. Firewalls can block echo requests.
- The existing two-minute, visible-page-only refresh and 110-second central
  cache remain. Full OS and container scans are separate operations.
- The Debian 13 installer uses quiet, stage-based terminal progress, never
  arbitrary download percentages. Detailed logs remain root-readable and
  failures reveal a short log excerpt.
