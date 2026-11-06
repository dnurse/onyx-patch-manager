# OPM v0.5.6 source review and release risk register

Review date: 2026-11-04. Input: v0.5.5 source archive. Output: v0.5.6.

## Scope and methods

Reviewed the Python controller, workers, remote discovery, onboarding, SQLite storage, Django views/settings/middleware, Whiptail installers and maintenance scripts, web templates, JavaScript assets, dependency declarations and test suite. Reviewed authorization checks, shell/SSH argument handling, credential exposure, patch safety, failure reporting, container isolation, and packaging. Automated unit tests and static syntax checks were performed. This is **not** a third-party security audit or a live deployment test.

## Issues resolved in v0.5.6

1. **Moderate: Podman discovery tied to a hardcoded service account.** Replaced `uosserver`-specific discovery with accounts selected by observable Podman state/configuration (NSS, running helpers, storage, runtime, sub-UID/GID mappings). Does not assume a login shell, numeric UID range, or specific username.
2. **Moderate: Incomplete engine inspection could be reported as healthy.** A successful `ps` with failed `inspect` now produces a partial-scan warning; inaccessible rootless contexts and exhausted time budget also produce warnings, displayed in host status.
3. **Low: Duplicate Podman entries from overlapping SSH-user scans.** Deduplicated by container ID while retaining each inspected user context.
4. **Moderate: Account name implicitly determined UniFi update policy.** Vendor classification now uses official UniFi OS container image metadata; the service account may also host ordinary containers, which retain their own read-only classifications.
5. **Operational: Unbounded rootless candidate inventory could tie up SSH.** Introduced a total discovery budget; leftover contexts are reported explicitly rather than skipped silently.

## Remaining risk / GM gates

- **High / feature incomplete:** Docker Compose and Podman Compose *update execution* remain out of scope for this change, and were an original V1 target. Existing container functionality is discovery/review only.
- **Moderate / environment-specific:** A rootless user's Podman runtime may require an active systemd user session or non-default XDG/storage configuration. OPM never enables lingering or modifies accounts. Affected user contexts will be reported as inaccessible. A custom runtime not represented by account storage, configuration, process, or sub-ID hints may not be discovered.
- **Moderate / time budget:** A host with many slow user stores can exceed the scan budget. OPM shows a partial warning and does not claim comprehensive coverage.
- **Moderate / planned:** Cross-user registry image comparisons remain `unknown` and no user-private registry credentials are accessed. This prevents false update claims. No automatic image pull/recreate is added.
- **Moderate / verification pending:** Django integration, Whiptail actual rendering, reverse-proxy ACL/security, fresh install, and role changes still require field tests. The test environment lacks Django, so its integration test is skipped.
- **Moderate / release testing:** Failure/retry after interrupted patching and interrupted LXC/user service lifecycles require live acceptance tests. Successful OS patching across enrolled hosts has already been confirmed by the operator.
- **Deployment safety:** LAN HTTP mode sends credentials unencrypted at the application layer. Restrict it to trusted private LAN or VPN; prefer HTTPS reverse proxy where available.
- **Maintenance / low:** The legacy Textual CLI remains in the codebase and should be reviewed for retirement when the Web UI/Whiptail flows are stable.

## Behavior and boundaries

- The discovery script only uses `podman ps -aq`, `podman inspect`, user-account and process metadata queries. It does not pull, stop, remove, or recreate workloads and does not create/change accounts or user lingering.
- Authorized sudo/root context is required for other users' stores; without it the scan remains limited to the SSH account and warns about coverage.
- `runuser` switches effective Linux user without executing arbitrary shell profiles; it receives a known PATH and user-specific HOME/runtime metadata.
- An absent user runtime may still prevent a Podman query; the user sees a scoped error instead of an empty list.
- Command-level Podman metadata inspection is intended to be observational; Podman's internal storage bookkeeping may differ across versions, so test on a disposable system before asserting zero filesystem writes.

## Acceptance test on UniFi OS LXC

1. Upgrade controller with v0.5.6, retaining accounts, keys and web settings.
2. In OPM, choose an authorized scan for the UniFi OS Server host (supply the dedicated sudo password if required).
3. Open Servers > host details. Under Podman, verify `uosserver` container appears with a `Podman user ... (UID ...)` scope and `vendor-managed` status.
4. Add a separate non-vendor rootless Podman workload under a different user in a disposable host and verify detection without editing OPM's source or configuration.
5. If the scan reports Partial scan, capture the per-user warning and investigate XDG runtime/service state without modifying host accounts automatically.
6. Confirm the scan did not restart, pull, or recreate containers; check any protected/Operator/Viewer access restrictions separately.
