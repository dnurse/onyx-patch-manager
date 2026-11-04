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
