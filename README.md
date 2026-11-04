# Onyx Patch Manager

Onyx Patch Manager (OPM) is a self-hosted patch management platform for homelabs. It gives operators one place to inventory Debian and Ubuntu servers, review available updates, and manage approved patch jobs. OPM uses Ansible over SSH, explicit change approval, and job history to make routine maintenance easier to follow.

## Features

- Central inventory for managed servers, scan status, and patch activity.
- APT update discovery with a reviewable plan before changes are applied.
- Separate **Dry Run** and **Apply Updates** workflows.
- Background patch jobs that continue independently of the browser, with recorded results and history.
- Administrator, Operator, and Viewer roles for day-to-day access control.
- Read-only Docker and Podman inventory alongside server information.

OPM does not automatically reboot servers or schedule unattended patching. Container discovery is informational: OPM does not pull images, recreate containers, or apply Docker Compose or Podman Compose updates.

## How to use OPM

1. Install OPM on a dedicated Debian 13 VM or LXC.
2. Sign in to the web interface and add a server using its SSH details. Review the server's SSH host fingerprint before trusting it.
3. Scan enrolled servers to see available APT updates and container inventory.
4. Choose **Dry Run** to simulate a plan, or review and explicitly approve **Apply Updates**.
5. Follow the job activity and inspect its result in History.

Managed servers should be Debian or Ubuntu systems reachable from the OPM controller over SSH. Enrollment and scans may require sudo authorization on the managed server.

## Installation

Download and extract a source archive from [GitHub Releases](https://github.com/dnurse/onyx-patch-manager/releases). On the Debian 13 controller, run the installer from the extracted project directory:

```bash
sudo bash OPMInstaller.sh
```

Choose **Install / Upgrade** and follow the prompts to configure web access, timezone, and the initial administrator account. The installer sets up the OPM service account, Python environment, database, web application, and systemd services.

For existing installations and maintenance options, see [UPGRADE.md](UPGRADE.md).

## Access and security

OPM manages privileged infrastructure. Keep the web interface on a trusted private LAN or VPN; do not expose port 8765 directly to the public internet. Direct LAN/VPN HTTP is not encrypted. For private HTTPS reverse-proxy deployment, firewall restrictions, and account-role details, see [SECURITY.md](SECURITY.md).

Review patch plans before approval and keep controller backups. Start with systems whose maintenance impact you understand.
