# Onyx Patch Manager (OPM) — v0.7.0 Nightly

**v0.7.0 nightly** is a pre-Gold-Master development build from the nightly branch.

**New in this nightly:** Bulk CSV import and export for server inventory.

**v0.6.20:** Adds clickable breadcrumbs and signed same-version deployment revisions on the test server.

**v0.6.19:** Centers the sign-in shield on the title and displays the running version beside it.

**v0.6.18:** Aligns the sign-in logo beside the title and subtitle on desktop and phones.

**v0.6.17:** Internal unattended deployment test. Existing installations keep their OS packages.

**v0.6.16:** Applies the approved Shield O vector identity to the sidebar and login screen,
with locally bundled SVG, multi-resolution favicon and Apple Touch icon. Retains all
v0.6.15 History, Clear alerts, and Dashboard scrolling improvements.

OPM is a private, agentless Linux patch manager for a Debian 13 controller with
a web dashboard, approved APT patching, SSH key onboarding, and Docker Compose
verification and explicitly approved image updates. v0.6.14 closes an
additional interruption path: Ansible Runner cancellation (often exit 254)
now reconciles as Interrupted rather than Failed. Detached worker recovery
retains no replay, consistent per-target status, periodic reconciliation,
and last-known-stage diagnostics.
v0.6.15 makes all History records scroll within their card; Dashboard server
and recent activity panels have equal, bounded heights. Administrators can
now clear ALL historical patch, activity, SSH repair and audit records through
a checkbox-confirmed action, blocked during active work. This does not remove
hosts, scans, login accounts or validated Compose source state. Clearing alerts
is separate and retains the underlying history; the header Activity dropdown
now exposes the non-destructive Clear alerts action.
The v0.6.12 verified-only APT fallback remains available. Podman application
logos remain an open live issue and are deferred.

## Capabilities

- Discover servers and inspect available APT updates on supported Debian/Ubuntu hosts.
- Import and export server inventory CSV from **Servers**. Administrators can upload or paste OPM or Checkmk-style CSV, review duplicates and validation errors, then add up to 250 profiles per batch. Imported servers appear as **Setup incomplete** until their SSH identity is verified through **Finish setup**.
- Execute explicitly approved OS patches, optionally continue a safe subset after a repository failure, or run non-mutating Ansible/Apt Dry Run validation. Automatic reboots are disabled. An out-of-date scan triggers an inline sudo authorization and selective scan refresh, then automatically returns to patch review.
- Discover Docker and Podman workloads (including rootless contexts). Docker application logos are shown when artwork is available; Podman logos remain unverified in the live environment and are deferred. Verified Docker Compose services support explicitly approved pull-and-apply updates; Podman and standalone Docker remain review-only.
- Guided remote SSH onboarding, existing-`opm` account adoption/replacement, and administrator-approved recovery of lost SSH keys.
- Protect sensitive hosts with a separate patch approval; inspect persisted jobs, activity history, scan results and SSH diagnostics.
- Administrator, Operator and Viewer access controls; Viewer IP redaction and no Settings access.
- Modern responsive web UI with grouping/sorting, controller heartbeat and cached ICMP reachability. Dashboard defaults to numeric IP order; group sorting preserves configured headings.
- Native Whiptail install/upgrade/repair/uninstall utility (not the retired Textual management UI).

### Interrupted patch worker recovery (v0.6.14 internal)

When an OS patch worker stops, OPM checks its systemd unit on controller
startup and periodically while running. If the worker is verifiably no
longer active, OPM marks the patch **Interrupted** and atomically updates
all unfinished target states. A target that was running or verifying becomes
**Interrupted** with its last recorded operation; a target not reached by the
worker becomes **Not started**. Previously completed or failed targets
remain unchanged. OPM never automatically re-executes the interrupted patch.
The job details show manual APT/dpkg checks before a new operator-approved run.
Systemd exit metadata appears only if systemd still retains it.
If Ansible Runner returns a canceled status before verifying a patch, the
worker also records an Interrupted result with the same manual-recovery
requirements rather than incorrectly treating it as a normal task failure.

### Partial APT repository failures (v0.6.12 internal)

When a signed repository refresh fails (for example, a vendor signing-key
error), the Review patch plan page warns and requires an acknowledgement
checkbox. OPM first attempts its usual strict full refresh. Only if that fails,
it creates a fresh, isolated APT index directory on the target and downloads
metadata there without changing system sources or trust settings. It requires
signed metadata and checks the exact package/version set against the approved
scan. If no safe approved package set can be proven, it stops before installing.

For a safe subset, OPM runs a fixed `apt-get upgrade` through Ansible against
**only the new verified indexes** with no removals/new packages. It queries
installed package versions afterward and records unavailable, held and
unchanged packages in History; the result is **Completed with warnings**.
No repository configuration, signing key or security policy is modified.
A stopped worker can leave a temporary `/var/tmp/opm-apt-*` metadata directory;
OPM removes it after a normal run, but an interrupted worker needs review.

Initial live testing exercised a signed-repository fallback. Additional failure modes and interruption recovery still need testing.

## Install on Debian 13

Extract the GitHub source ZIP and from the directory containing `OPMInstall.sh` run:

```bash
sudo bash OPMInstall.sh
```

New installations open a guided Whiptail wizard. Existing installations open the maintenance menu. For an explicit upgrade from an extracted newer source:

```bash
sudo bash OPMInstall.sh --install
```

Do not select Complete Uninstall to perform an upgrade. It intentionally removes the local OPM account and controller state. The normal upgrade path is designed to preserve server inventory, SQLite history, accounts, SSH identities and web settings.

After installation, access OPM through its configured private LAN/VPN address or internal HTTPS reverse proxy. Do **not** expose the plain HTTP listener directly to the Internet.

## Release notes and documentation

- [Working Gold Master v1.0.0 acceptance checklist](GM_READINESS_CHECKLIST.md)
- [GitHub release notes for v0.6.0](https://github.com/dnurse/onyx-patch-manager/releases/tag/v0.6.0)
- [Docker Compose implementation and acceptance roadmap](DOCKER_COMPOSE_ROADMAP.md)
- [Versioning policy](VERSIONING.md) — GitHub minor bumps, internal patch bumps, GM major bump
- [Detailed development history since v0.5.6](CHANGELOG_SINCE_0.5.6.md)
- [All release notes](RELEASE_NOTES.md)
- [Security and deployment](SECURITY.md)
- [Upgrade guide](UPGRADE.md)
- [Architecture and scope](SPECIFICATION.md)

## Development and tests

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Tests include simulated SSH/account operations and source-level release regressions. Some Django integration tests require a full configured Django environment and may be skipped in a minimal development container. A passing test suite does not prove live controller upgrades, remote account recovery, proxy security or Compose operation.

## Road to GM

See [GM_READINESS_CHECKLIST.md](GM_READINESS_CHECKLIST.md) for completed field tests, live acceptance gates, prioritized next steps and deferred items. Known issue: Podman logos have not worked in the live environment and are explicitly deferred; no Podman update functionality is included. Actual changed-image Docker Compose updating, recovery and security acceptance remain required before GM.

## Publishing

The nightly branch tracks reviewed development builds. Stable milestones use GitHub Releases. See [Versioning](VERSIONING.md) for the numbering policy.

### Docker Compose image updates (v0.6.7 internal)

In Servers → Host Overview → Docker Compose projects, run **Verify Configuration**,
then choose a service under **Pull image & apply update**. Administrator approval
uses a checkbox, with separate approval for protected hosts. Credentials are
held transiently by the controller, never stored. Docker Compose pulls the
selected service image from its configured registry (Hub/GHCR/private), and
only recreates the service if the image digest changed. New service health is
checked, but application integrity and database migrations are not certified.

Operations require real Docker Compose V2, a matching original configuration,
a running image-based service, and a recent source verification. Failure or
interruption is **not** automatically retried or rolled back. A remote service
may have changed before a connection interruption; inspect it before retrying.
Standalone Docker, Podman, vendor-managed, digest-pinned and build-only services
are excluded. No auto-update schedules are enabled.

## v0.6.9 — Generic container application logos (internal)

- Fix the silent Debian fallback when Docker Hub does not publish artwork:
  exact-repository Docker Hub logos are checked first, then the **same generic
  application-icon resolver** for every recognized Docker image, without a
  hardcoded per-image/logo mapping.
- Detect app icon candidates from the sanitized image basename across Docker Hub,
  GHCR, LinuxServer and other registries. Match only existing raster artwork at
  the fixed public Dashboard Icons source; never fetch arbitrary registry or
  user-supplied icon URLs, namespace avatars, SVG or executable content.
- Keep all discovered, non-vendor Docker images selectable, including private
  registry names. Where no matching artwork is published or internet access is
  blocked, explicitly say that in Edit Server and show the distro fallback.
- Invalidate old negative logo cache entries and disable browser-side stale
  caching; show a live preview and explanation in Edit Server.
- No changes to Compose update execution, authorization, or remote scanning.
  Docker image references are used for artwork lookup only; no image pulls.

## Screenshots


These representative interface screenshots are privacy-filtered: infrastructure IP addresses, account usernames, exact timestamps, and captured version badges are removed. Dashboard server labels are intentionally retained; names are not treated as credentials.

| Dashboard | Patch Manager |
|---|---|
| ![OPM dashboard overview](docs/screenshots/dashboard.png) | ![OPM patch manager server and update view](docs/screenshots/patch-manager.png) |

| Patch review | Job history |
|---|---|
| ![OPM patch plan review](docs/screenshots/patch-review.png) | ![OPM patch job history](docs/screenshots/patch-history.png) |

### Installer welcome

![OPM installer welcome screen](docs/screenshots/installer-welcome.png)
