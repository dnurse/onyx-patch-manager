# Onyx Patch Manager (OPM) v0.6.0

**OPM v0.6.0 is a full GitHub release and a pre-Gold-Master development milestone.**

A private, agentless Linux patch-management application for a Debian 13 controller, with a web dashboard, explicit approval for APT patching, and SSH-key-managed remote hosts.

## Capabilities

- Discover servers and inspect available APT updates on supported Debian/Ubuntu hosts.
- Execute explicitly approved OS patches or run non-mutating Ansible/Apt Dry Run validation. Automatic reboots are disabled.
- Detect Docker and Podman workloads (including multi-user rootless contexts) for **review only**; Compose update execution is not yet implemented.
- Guided remote SSH onboarding, existing-`opm` account adoption/replacement, and administrator-approved recovery of lost SSH keys.
- Protect sensitive hosts with a separate patch approval; inspect persisted jobs, activity history, scan results and SSH diagnostics.
- Administrator, Operator and Viewer access controls; Viewer IP redaction and no Settings access.
- Modern responsive web UI with grouping/sorting, controller heartbeat and cached ICMP reachability. Dashboard defaults to numeric IP order; group sorting preserves configured headings.
- Native Whiptail install/upgrade/repair/uninstall utility (not the retired Textual management UI).

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

- [v0.6.0 GitHub release](https://github.com/dnurse/onyx-patch-manager/releases/tag/v0.6.0) — canonical release summary and validation status
- [Versioning policy](VERSIONING.md) — GitHub minor bumps, internal patch bumps, GM major bump
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

Field-tested so far: fresh Debian 13 installation, successful multi-host OS patching, Dry Run, user role checks, complete local uninstall, and remote account deletion/recreation. Remaining validation includes SSH repair, interrupted jobs, security edge cases, multi-user Podman, upgrade/repair, and mobile UI. Docker/Podman Compose updating remains a development item. These gaps must be resolved or explicitly deferred before GM sign-off.

## Publishing

This source archive excludes live controller files, credentials, logs, caches, and databases. Publish its **contents** at the repository root and attach the source ZIP to the GitHub release. The GitHub release description is the canonical version-specific change summary; no separate release-description Markdown file is needed. The version scheme is maintained in `VERSIONING.md`.

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
