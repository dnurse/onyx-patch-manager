# OPM v0.5.6: dynamic rootless Podman discovery and source review

- Replaced the hardcoded UniFi OS `uosserver` probe with discovery of Linux accounts showing Podman state or configuration, including non-login service accounts and low-UID accounts.
- Inspects each discovered account in its own clean, restricted `runuser` environment using read-only `podman ps -aq` and `podman inspect` commands, respecting a bounded remote scan time budget.
- Retains rootful Docker/Podman and SSH-user inspection. Container IDs are deduplicated across overlapping contexts.
- Detects UniFi OS vendor ownership from image metadata rather than account name; its update lifecycle remains external to OPM.
- Failed or incomplete cross-user probes and elapsed scan budgets are reported as partial discovery warnings in host views. Failed container inspection is no longer suppressed after a successful list command.
- Cross-user image-update status is `unknown` until secure registry credentials and comparison handling are designed. No pulling, installation, restarting, account edits, or Compose updates are enabled.
- 160 local unit/contract tests pass; one Django integration test remains skipped due to missing Django in the build environment. Live UniFi OS and multi-account Podman testing is outstanding.
- Includes a separate source review document identifying release blockers and operational limitations.

# OPM v0.5.5: account roles and server administration permissions

- Settings → Accounts: role dropdown when creating a user (Administrator, Operator, Viewer).
- New Edit action beside each other account; administrator can change the role or reset its password without deleting and recreating the account. Blank password leaves the password unchanged. The current admin changes their own password using the existing current-password-protected form.
- Role changes preserve existing accounts and use Django's existing `is_superuser` and `is_staff` flags. No database migration required.
- Only Administrators can enroll, modify, trust, bootstrap, test SSH on, or remove hosts. This is enforced in the web endpoint, not only by hiding controls. Operators retain scanning, dry runs and patch execution. Viewers remain read-only.
- Prevent last-active-administrator demotion/deactivation, keep CSRF and audit records, do not store plaintext reset passwords, and retain existing accounts across upgrades.
- No changes to installed system services, controller database schema, remote SSH credentials, or patch operation behavior.

Live Django integration, authentication/session invalidation behavior, and browser layout require validation on a Debian LXC.

# OPM v0.5.4 Release Notes

## Native Whiptail installation

The previous text/ANSI installer UI has been replaced with a native Whiptail installation wizard and maintenance menu using OPM-inspired black/slate/cyan colours via `NEWT_COLORS`. New installations collect web access, timezone and administrator credentials in the terminal before any OPM install stages begin. Package operations and systemd services still run only in the original core installer; the terminal layer never bypasses the existing active-job, backup, repair, or controller safeguards. Administrator passwords are sent via stdin only to an unprivileged process and never through CLI argv or installation logs.

The installer bootstraps Whiptail on interactive Debian hosts if missing. An unattended install does not require it. Existing installations retain current settings, and the maintenance utility retains its CLI options.

## v0.5.3 Release Notes

## Guided network access

On a **fresh install**, an explicit access-mode prompt is displayed before web services are started:

1. **LAN/VPN HTTP (default):** Press Enter to use the LXC private IPv4 address on port 8765. No domain, reverse proxy, or certificate required.
2. **Internal HTTPS reverse proxy:** Enter the internal hostname and exact private proxy source IP; configure TLS/ACL separately in the proxy.
3. **Localhost only:** Bind to 127.0.0.1:8765 for SSH tunneling.

The installer detects a private IPv4 address and lets the operator override it. If no private IP is available during unattended installation, OPM remains on localhost rather than binding unsafely. Existing installations preserve their current networking and signing keys through upgrades.

## Web UI configuration

**Settings > Web Access** now provides administrator-only access-mode selection. The change is recorded as a background activity. The OPM controller uses the root-owned installer helper, executed as the unprivileged `opm` Linux user, to atomically replace only the allowed web settings and restart **only** the web service. The previous environment file is backed up privately, and a failed local web health check triggers a rollback attempt. Running controller jobs and host data are not changed.

Browser access may be interrupted during mode switching. Navigate to the selected URL after the activity completes. A successful localhost health check cannot establish that an external reverse proxy, certificate or firewall ACL is correctly configured; check those separately before relying on it.

## Security

LAN HTTP is unencrypted. Use only over a private, trusted LAN or encrypted VPN; do not expose TCP 8765 to the public internet. For internal HTTPS, restrict upstream access to the exact proxy source IP and independently verify NPM's ACL and certificate. No change enables public registration.

## Validation

Local automated tests cover menu defaults, noninteractive fallback, mode configuration, controller activity plumbing, secret preservation and proxy restrictions. Real LXC, NPM and iPhone access remain to be validated.
