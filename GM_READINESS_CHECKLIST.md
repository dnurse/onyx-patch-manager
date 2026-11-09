# Onyx Patch Manager — Gold Master (v1.0.0) Acceptance Checklist

**Working checklist** · Updated **November 8, 2026** · Source baseline: **v0.7.0 nightly**  
**Next public milestone:** v0.7.0 · **Gold Master target:** v1.0.0, only after acceptance gates pass.

> How to use: Check off a task only after the **live** test passes. For incomplete tasks, keep `[ ]` and note the date, host/test environment and sanitized History activity ID or installer log. A passing unit test is not a substitute for live acceptance. Never include passwords, keys, `.env`, raw Compose configuration, or production database backups in public evidence.

## Already field-tested — do not repeat without a regression reason

- [x] Fresh **Debian 13 controller** installation through OPM installer.
- [x] **Multi-host Linux APT patch execution** completed successfully on enrolled servers.
- [x] **OS Dry Run** exercised successfully.
- [x] **Administrator / Operator / Viewer** role behavior exercised.
- [x] **Complete local uninstall**, including OPM Linux service-account removal, tested.
- [x] **Remote `opm` account removal and recreation** tested.
- [x] A test Docker Compose service **no-change update path** observed: registry image matched, no container restart. This is *not* evidence that a changed image updates successfully.

## P0 — Must pass before GM

### 1. In-place install, upgrade and repair

- [ ] **In-place upgrade:** upgrade a populated v0.6.x controller without removing/re-enrolling hosts. Confirm accounts/roles, server names, SSH keys, protected flags, history, settings and web access survive; check migrations and services start. (Re-test especially because an earlier account-audit migration caused an install failure.)
- [ ] **Installer Repair:** deliberately break a *disposable* installation (for example a service unit or permissions), run the Whiptail repair path, review proposed fixes, confirm it repairs without erasing credentials or state, and verify both services afterward.
- [ ] **Failed upgrade recovery:** simulate an installer failure on a disposable controller, validate backup/restore or documented recovery procedure; ensure the previous usable state can be recovered. Never do this destructively on production.
- [ ] **System Status and version:** verify the version and service health reported by installer, OPM Controller sidebar indicator, and UI all agree.

### 2. Remote access and privilege safety

- [ ] **SSH Repair:** on a disposable enrolled host, invalidate the managed SSH key and use OPM's conditional **Repair SSH access** flow. Confirm normal connectivity is restored and History captures the outcome.
- [ ] **Missing remote service account:** remove the remote `opm` account on a disposable server, then test the explicitly approved recovery/recreate flow (not routine onboarding, which is already tested).
- [ ] **Sudo authentication:** test wrong password, missing sudo rights, passwordless sudo and a valid password; errors should identify the affected host and never disclose passwords.
- [ ] **10-minute sudo cache:** confirm authorized reuse within the same browser login, expiry after ten minutes, and no reuse across different login sessions or accounts.
- [ ] **Stale scan refresh:** choose several hosts with expired scans, continue from Patch Manager, verify only necessary hosts scan and the selected hosts plus Apply/Dry Run mode are preserved.
- [ ] **Protected host approval:** confirm protected OS patching and protected Compose updates require their own checkbox approval and backend authorization; normal hosts must not get spurious prompts. *Do not use typed confirmations.*

### Interrupted OS patch worker recovery (v0.6.14 — live retest required)

- [x] Initial interruption on a disposable host: OPM detected a stopped worker and did not automatically restart its patch; previous version incorrectly left its target labeled Running.
- [ ] **v0.6.14 retest:** On a disposable host, stop a worker during Ansible execution and verify job **Interrupted**, current target **Interrupted**, untouched future targets **Not started**, and completed targets unchanged. Previous live test reported **Failed** / `Ansible returned canceled, exit code 254`; regression fixed in v0.6.14 but live validation remains open.
- [ ] **Detection without restarting the controller:** Interrupt a worker, keep the controller running, and confirm reconciliation appears within the periodic check interval.
- [ ] **Manual recovery:** Confirm the job details show the last recorded step, do not silently re-run APT, and give useful `dpkg --audit` / `apt-get check` instructions.
- [ ] **Package transaction interruption:** Deliberately interrupt APT only on a disposable VM with a snapshot. Verify OPM never claims success or launches a second transaction without explicit approval and remote inspection.

### 3. APT partial-repository updates (v0.6.12 — live acceptance required)

- [ ] **Broken third-party repository:** on a disposable Debian/Ubuntu host with a deliberately invalid third-party signing key, scan and confirm OPM shows the warning, accepts a required checkbox, and installs only approved versions available from independently refreshed, signed indexes. Do not weaken APT trust or edit repository definitions.
- [ ] **Package-level reporting:** verify History shows correct post-job package results: installed, unavailable from verified indexes, held by APT, and any failed/unchanged selections. A successful partial job must be marked **Completed with warnings**, not clean success.
- [ ] **All sources unusable:** deliberately cause all configured repositories to fail verification; ensure zero packages are installed and a clear failure is recorded.
- [ ] **Candidate drift and dependency protection:** change a package candidate version or introduce an unapproved dependency after scan. Ensure OPM fails closed rather than silently expanding the patch plan.
- [ ] **Partial-update cleanup/recovery:** interrupt a disposable worker; verify no automatic replay, record post-interruption installed versions, and check whether any temporary `/var/tmp/opm-apt-*` metadata directory needs administrative cleanup.

### 4. Failed/interrupted job handling — v0.7.0 focus

- [ ] **Remote disconnection:** interrupt the network/SSH link during a safe test job; OPM must indicate whether the remote change is known, uncertain or completed, and avoid automatically duplicating work.
- [ ] **Controller/web restart:** restart each service while a noncritical job is underway; verify persisted History, job state and appropriate recovery/reconciliation after restart. Never report an interrupted job as successful without confirmation.
- [ ] **Failed-job Retry/Recheck:** provide a guided diagnostic/recheck path and test it; do not silently rerun OS patches or Compose pull/recreation after an ambiguous failure.
- [ ] **Service/host reboot after a patch:** test graceful job status when the managed host restarts or becomes temporarily unavailable. Confirm actual post-patch SSH/container checks distinguish outage from success.
- [ ] **Concurrent operations:** ensure incompatible scans, SSH repair, and patch/Compose operations cannot overlap on the same managed host unsafely; the UI should show the active operation.

### 5. Docker Compose — only supported container update engine

- [ ] **Source verification:** on a real Docker Compose V2 host, verify project and service configuration with current files; prove a changed/missing original file, mismatched hash or non-Compose deployment is rejected.
- [ ] **Actual changed-image update:** on a disposable, backed-up Compose service, verify source, explicitly approve a pull, confirm only the selected service is recreated with the expected digest, and confirm running/health state in History.
- [ ] **No-change update regression:** repeat the already observed no-op path on the current release; confirm no recreation or restart when the image is unchanged.
- [ ] **Failure paths:** test registry denial/unreachable, missing image, unhealthy replacement, interrupted pull/recreation and a changed Compose file after verification. Show safe, actionable errors and never claim automatic rollback of volumes/databases.
- [ ] **Protected Compose workflow:** verify the additional checkbox, authorization and confirmation before a service can be recreated; confirm a Viewer/Operator cannot bypass administrator-only constraints.
- [ ] **Scope exclusions:** verify OPM refuses updates for standalone Docker, Podman, vendor-managed workloads (including UniFi OS), digest-pinned/build-only projects and projects with uncertain provenance.

### 6. Web security, roles, privacy and deployment

- [ ] **Role enforcement at the backend:** repeat critical administrator/operator/viewer actions and forged POSTs, including account deletion/enable, host management, source verification, Compose updating and protected approvals. Viewer can neither mutate nor read restricted network details.
- [ ] **Account lifecycle:** enable a disabled account; disable it; delete a disposable account using checkbox confirmation; confirm audit attribution is retained and self/last-admin safeguards hold.
- [ ] **Browser/session security:** confirm password change invalidates old sessions, CSRF rejection, logout, privilege changes and sudo-cache isolation. Inspect generated History and logs for leaked credentials.
- [ ] **Private reverse proxy:** verify HTTPS, secure cookies, exact trusted proxy origin/IP, firewall to the OPM listener, and LAN/VPN-only access. Test from cellular *without VPN* to confirm the public internet cannot reach OPM, even if a public NPM instance forwards 80/443.
- [ ] **Threat/supply-chain review:** inspect dependencies, SSH host key checks, least-privilege boundaries, image-artwork fetching/cache, secret/file permissions and installer root-owned scripts. Review migration/backups for sensitive content.

### 6. Real user experience and release qualification

- [ ] **Responsive UI:** test Dashboard, Patch Manager, Account Management, SSH Recovery and Host Overview on desktop and iPhone Safari; verify aligned cards/fields, no sideways scroll, no broken controls and consistent logos.
- [ ] **Patch Manager review flow:** validate the inline stale-scan → password → scan → review → explicit approval path without returning to another page or losing server selection.
- [ ] **Activity and History:** verify success/failure results, scans, Compose verification and image actions use sanitized logs, accurate timestamps, and do not disappear after a controller restart.
- [ ] **Final regression run:** complete automated suite, Django migration checks, syntax tests and ZIP integrity; run smoke tests on the exact release candidate after clean install and in-place upgrade.
- [ ] **GM release audit:** update documentation/screenshots/security advisories, remove unsupported claims, sanitize package (no DB/keys/logs), freeze scope and tag **v1.0.0** only after all required P0 items pass or are formally scoped out with documented risk.

## Recommended next five live tests (in order)

1. **Upgrade/repair integrity** on a populated disposable controller (protect the production data first).
2. **SSH Repair plus sudo-cache expiry** on a disposable managed server.
3. **Protected-host approvals and role/CSRF checks** from different user accounts.
4. **One genuine Docker Compose changed-image update** on a noncritical service, with backup and health check.
5. **Interruption/restart reconciliation** for a test OS patch or Compose activity.

## P1 — Valuable after P0, before or after GM as scope permits

- [ ] Validate more Ubuntu/Debian managed-host combinations; record what versions were tested, not just supported in theory.
- [ ] Improve diagnostics and user-friendly status for uncommon Podman/rootless permissions failures (inventory only).
- [ ] Review accessibility (keyboard focus, color contrast, screen readers, error descriptions).
- [ ] Complete more real-world host combinations: nested containers, limited sudo, no Docker, Docker without Compose and mixed services.

## Deferred / future work — NOT claimed as GM functionality

- [ ] **Podman application logos are NOT working in the live environment** as of Nov 7, 2026. Investigate discovery → logo candidate → image endpoint → cache → browser selection end-to-end. Keep as a future item; do not claim operational support in GM release notes until retested.
- [ ] Podman / Podman Compose updates: **out of scope** for the Docker Compose-only updater. No automated Podman pulls or recreation.
- [ ] Standalone Docker updates and vendor-managed container updates: remain **out of scope**.
- [ ] Unattended/scheduled updates: remain **out of scope**; explicit operator approval is mandatory.

## Test evidence log

| Date (UTC) | OPM version | Host/test type (no secrets) | Checklist item | Result (pass/fail) | Sanitized evidence / issue |
|---|---|---|---|---|---|
| | | | | | |
| | | | | | |
| | | | | | |

**GM decision:** `[ ] GO` / `[ ] NO-GO`  
**Release candidate:** __________  **Reviewer:** __________  **Sign-off date:** __________

**Decision rule:** All P0 checks must be completed on the intended release candidate or be explicitly removed from v1.0.0 scope with an accepted risk and updated documentation. A known broken feature cannot be represented as working simply because its unit tests pass.

### History maintenance and dashboard usability (v0.6.15, live acceptance open)

- [ ] Verify **Clear alerts** in the header dismisses only attention badges and preserves History (Administrator only).
- [ ] Verify **Clear all history** removes completed patch, SSH repair, failed activity, scan-activity and audit records after checkbox acknowledgement, preserving hosts, scan findings, accounts, SSH credentials and Compose source verification. Test only after saving any needed evidence.
- [ ] Verify deleting History while a patch worker or background scan is active is blocked with a clear message.
- [ ] Verify History and Dashboard cards scroll internally on desktop and mobile without losing the sort menu or causing horizontal overflow.
- [ ] Decide on a 16px-readable favicon and matching sidebar O mark. No branding replacement implemented yet.
