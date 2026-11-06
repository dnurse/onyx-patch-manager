# Current OPM v0.5.10 install and upgrade

On a **new** Debian 13 controller LXC/VM, extract the release ZIP and run `sudo bash OPMInstall.sh` to begin the Whiptail setup wizard. The default web access mode is LAN/VPN HTTP; existing mode selections are retained when edited later.

On an **already installed** OPM controller, the same flagless command opens the maintenance menu. Select **Upgrade / Install OPM** to install the OPM version from the extracted source. The menu also offers **System Status**, **Repair Installation**, **Configure Web Access**, and **Uninstall OPM**. The upgrade confirmation shows the **OPM release version**, not the Debian version.

To run an explicit scripted upgrade rather than the maintenance menu, use `sudo bash OPMInstall.sh --install`. Existing SQLite state, user accounts, SSH keys, web-access configuration and patch history are intended to be preserved.

If the administrator password violates Django's password policy during a new installation, the installer shows those validation errors and returns to credential entry without repeating the package installation.

**Important:** The controller installer itself currently targets Debian 13. OPM can inventory Linux hosts, but automated OS patch execution is currently implemented for APT-based Debian/Ubuntu targets. Full testing on your live installation is still required.

---

## Historical upgrade instructions (prior releases)

# OPM v0.5.8 upgrade

From the extracted release directory, run `sudo bash OPMInstall.sh`
to launch the guided installation/upgrade wizard directly. Do not add
`--install` for interactive use; it is still accepted for scripted callers.
To open the maintenance menu use `sudo opm-installer` after installation,
or `sudo bash OPMInstall.sh --menu` from extracted source. A successful installation will show its completion dialog and exit to the shell, not reopen the menu. Existing web access settings and accounts remain unchanged unless explicitly reconfigured. To change access later, run `sudo opm-installer --web-access`.

# Install or upgrade OPM v0.5.6

Copy `onyx_patch_manager_0.5.6_source.zip` to `/home/opm` on a Debian 13 LXC or VM, then run as root:

```bash
unzip -oq /home/opm/onyx_patch_manager_0.5.6_source.zip -d /root/opm-v056 && bash /root/opm-v056/onyx_patch_manager/OPMInstall.sh
```

Choose **1. Install / Upgrade**. After upgrading, run an authorized scan on your UniFi OS LXC and inspect the rootless Podman owner in the host detail view. If a user runtime is inaccessible, the scan will warn rather than show a false zero. Existing accounts keep their roles; this release makes host enrollment, edit, SSH trust/bootstrap/test and removal administrator-only. Operators continue to scan, dry run and patch.

Go to **Settings → Accounts** to choose a role during creation or select **Edit** beside another account to change its role/reset its password. Your own password is changed via **Change my password**. A blank edit password means no password change.

## Whiptail installation and maintenance

Run `bash OPMInstall.sh` to start the Whiptail maintenance menu. Choose **Install / Upgrade**. On a new Debian 13 host the installer displays the ASCII-art welcome screen, validates inputs for Web Access, timezone and administrator, shows a review screen and only then begins installation. During package/configuration work it shows the current stage in a Whiptail gauge. The meter is based on stages completed and is **not** the percentage of package download.

The default appearance uses dark and cyan OPM-style colours via `NEWT_COLORS`. The real terminal theme can vary with console/terminal settings. If `whiptail` is missing, the launcher installs the Debian package before opening its menu. Direct headless `OPMInstall.sh --install` still works without a TTY.

## Fresh installation: choose access mode

The Whiptail wizard explicitly asks for LAN/VPN HTTP (selected by default), internal HTTPS reverse proxy, or localhost only. A separate timezone and administrator step follows.

Press Enter for LAN/VPN HTTP. Confirm the detected LXC private IPv4 or enter a different one. Use `http://<LXC-IP>:8765` from a trusted LAN or VPN. HTTP is **not encrypted**, so do not expose the service on a public network or router port forward.

Use option 2 when you already have an internal HTTPS reverse proxy. Enter its external HTTPS hostname and the **proxy source IP seen by the LXC** (not the LXC IP). Forward HTTP to `<LXC-IP>:8765` and configure a valid cert, LAN/VPN-only ACL, and firewall rules. Verify the HTTPS hostname cannot be reached from the internet when the VPN is off.

Option 3 preserves the localhost-only SSH tunnel setup.

## Change networking later

Administrators can use **OPM Settings > Web Access**, select a mode, enter its IP/hostname and acknowledge the potential reconnect. The controller applies it asynchronously and records the result under Activity. Navigate to the new address if the original session is disconnected.

If browser access is lost, the CLI fallback remains:

```bash
sudo opm-installer --web-access
```

Existing upgrades preserve accounts, database, SSH keys, and web settings until changed explicitly. The controller continues operating during web-service reconfiguration.
