# Onyx Patch Manager

Onyx Patch Manager (OPM) is a self-hosted, agentless patch manager for homelabs. It uses Ansible over SSH to scan Debian and Ubuntu servers, prepare update plans, and run operator-approved APT patch jobs from one web interface.

OPM is currently **pre-GM and under active testing**. The v0.5.6 prerelease is available from [GitHub Releases](https://github.com/dnurse/onyx-patch-manager/releases). Do not treat it as production-validated software.

## What OPM does

- Keeps a central inventory of SSH-managed servers and their scan status.
- Finds available Debian/Ubuntu APT updates and presents a plan before changes are made.
- Separates **Dry Run** simulations from **Apply Updates** jobs; patching requires an explicit review and approval.
- Runs accepted patch jobs independently of the browser session and records job results and history.
- Provides three roles: Administrators manage users and servers, Operators scan and patch, and Viewers are read-only.
- Inspects Docker and Podman containers as read-only inventory. Container image or Compose updates are **not implemented**.

OPM does not install an agent on managed servers. It does not automatically reboot servers or schedule unattended patching.

## How it works

1. Install OPM on a dedicated Debian 13 VM or LXC.
2. Sign in to the web interface and add a server using its SSH details. Review the SSH host fingerprint before trusting it.
3. Scan enrolled servers to view APT updates and read-only container inventory.
4. Choose **Dry Run** to simulate a patch plan, or review and explicitly approve **Apply Updates**.
5. Check the job result and history in OPM.

Managed servers should be Debian or Ubuntu hosts reachable from the OPM controller over SSH. Enrollment and scans may require sudo authorization on the remote host.

## Install

Clone the current pre-GM branch and start the installer on the Debian 13 controller:

```bash
git clone --branch nightly https://github.com/dnurse/onyx-patch-manager.git
cd onyx-patch-manager
sudo bash OPMInstaller.sh
```

Choose **Install / Upgrade** in the maintenance menu and follow the setup prompts. The installer configures the OPM service account, Python environment, database, web application, and systemd services.

For existing installations and detailed options, see [UPGRADE.md](UPGRADE.md). The installer includes unattended options, but review the upgrade safeguards before using them on a live controller.

## Access and security

OPM manages privileged infrastructure and should only be reachable from a trusted private LAN or VPN. **Do not expose the web interface or port 8765 directly to the public internet.** Direct LAN/VPN HTTP is not encrypted. For private HTTPS reverse-proxy deployment, firewall restrictions, and account-role details, follow [SECURITY.md](SECURITY.md).

Use a noncritical host for early testing. Review each patch plan before approval, keep controller backups, and do not assume a successful local test suite proves safe behavior in your environment.

## Current testing limits

The v0.5.6 source includes dynamic discovery of rootless Podman accounts, but that path has not yet been validated on the target UniFi OS Server environment. OPM reports incomplete container discovery as a partial scan where detected. Container discovery is observational; OPM does not pull images or update/recreate containers.

See [SOURCE_REVIEW_0.5.6.md](SOURCE_REVIEW_0.5.6.md) for the review scope and outstanding validation gates. Version-specific changes are documented in [GitHub Releases](https://github.com/dnurse/onyx-patch-manager/releases), not in this overview.
