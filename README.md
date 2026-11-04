# Onyx Patch Manager (OPM) v0.5.6

## New in v0.5.6

- Authorized scans discover **any detectable rootless Podman account**, not a hardcoded `uosserver` account. OPM considers NSS accounts of every UID, running Podman helper processes, storage/configuration, active runtime contexts and subordinate UID/GID mappings. The scanner never changes an account, starts a session, enables lingering, or pulls/recreates a container.
- Container results are grouped by host and show the owning Podman user. UniFi OS Server is classified by its official container image metadata, not by its Linux username, and remains vendor-managed/review-only.
- Rootless runtime/context errors and time-budget exhaustion are surfaced as **Partial scan**, not a false zero-container success. Duplicate SSH-user containers are suppressed.
- Cross-account registry version comparisons are intentionally **unknown** until a separate authenticated image-check implementation is approved. Docker/Podman Compose updates remain not implemented in this release.
- See `SOURCE_REVIEW_0.5.6.md` for the code review and remaining GM risks.

## Previous v0.5.5 account-role improvements

- Explicit Administrator, Operator and Viewer role dropdown during account creation.
- Edit control in Settings → Accounts to change another account's role or reset their password. The account's existing password remains unchanged if the reset fields are blank. A password reset invalidates existing sessions.
- Operators can scan and patch but cannot enroll, edit, remove, trust, bootstrap or test SSH for servers. All such actions require Administrator privileges on the backend.
- No user-account migration required; existing role flags stay intact. Review the roles of existing accounts after upgrading.

## Previous v0.5.4 installer improvements

- The first-install wizard and maintenance launcher now use native **Whiptail** dialogs with an OPM-inspired dark/slate/cyan 16-colour theme and ASCII wordmark.
- Installer flow: welcome, web access (LAN/VPN HTTP preselected), timezone, initial administrator/password, review, live progress and completion. Cancelling setup before confirmation does not begin OPM installation.
- Maintenance menu: Install/Upgrade, read-only Status, diagnose/review Repair, Uninstall, Web Access, Help and Exit. Existing security checks, backups and migrations remain unchanged.
- Whiptail is bootstrapped automatically on interactive first installation. Headless `--install`, `--repair`, `--status` and upgrades remain supported.
- Whiptail supports standard terminal palette colours, not pixel-perfect gradients/rounded corners from the concept mockup. Use an 80x24 or larger SSH/Proxmox terminal.

## Previous v0.5.3 network and settings features

- New installations **ask for Web Access mode**, defaulting to option 1, **LAN/VPN HTTP**, when Enter is pressed. No FQDN, certificate or reverse proxy is needed. The wizard discovers the LXC's private IPv4 address and lets the operator change it.
- Option 2 configures an **internal HTTPS reverse proxy**: hostname, source IP, CSRF origin and secure-cookie settings. Option 3 retains **localhost only** for tunnels.
- **Settings > Web Access** now lets an authenticated administrator request any of the three modes through a background controller activity. A root-owned helper validates settings, saves a backup, restarts only the web service and attempts rollback on failed local health checks.
- A noninteractive first installation automatically uses LAN HTTP only if a private IPv4 address is discovered; otherwise it safely retains localhost-only access.
- Existing upgrades preserve their chosen web-access settings. The original `sudo opm-installer --web-access` fallback is still available.
- LAN HTTP carries credentials without TLS: keep it on a trusted private network/VPN and never forward the OPM port to the internet.

## Existing v0.5.1 features

- Successful server enrollment schedules a separate initial scan.
- Authenticated scans inspect the SSH and root contexts plus dynamically discovered rootless Podman users. UniFi OS Server remains vendor-managed/review-only.
- Dry Run and Apply Updates remain separate approval modes with no-change testing support.


OPM is an internal-only, account-based, agentless server patch manager for Debian/Ubuntu hosts with Docker and Podman discovery.

**This release adds a separate Dry Run mode.** Set a default in **Settings > Testing & Validation**, then choose **Dry Run** or **Apply Updates** for each Patch Manager plan. Dry runs can target recently scanned, verified hosts with no pending updates; they use `apt-get -s upgrade` and an independent Ansible read-only validation playbook. A successful simulation is recorded as **Simulated**, never as a completed patch. Optional APT metadata refresh requires explicit approval.

The running controller's own Linux services can be diagnosed using `sudo opm-installer --status`. OPM does not self-patch the LXC in this release.

See `UPGRADE.md`, `RELEASE_NOTES.md`, and `SECURITY.md` for installation, changes and safety restrictions.

---

# Onyx Patch Manager (OPM) v0.4.13

**Release focus:** Correct native Ansible inventory generation and prevent
false-positive patch success when Ansible matches zero hosts.

See `UPGRADE.md` for the one-command Debian 13 LXC upgrade,
`RELEASE_NOTES.md` for the change and safety details, and `SECURITY.md` for
internal-only deployment recommendations.


This release fixes the missing `ansible-playbook` executable / PATH problem
observed during patch execution. It validates the Ansible Core CLI in the
installer, in the controller before accepting a patch, and in the detached
worker. Failed task events are expandable in the job detail page.

See [UPGRADE.md](UPGRADE.md) for the one-line Debian 13 LXC upgrade command,
[RELEASE_NOTES.md](RELEASE_NOTES.md) for details, and
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for test limitations.

# OPM v0.4.9: Installer, patch reliability and history controls

The installer now supports Install/Upgrade, Repair, Status, Uninstall and Help.
Run `sudo opm-installer` after installation, or launch `OPMInstall.sh` from a
new release to upgrade. There is a quiet noninteractive `--install` option.

The Patch Manager now starts with all hosts collapsed. Old failed-job and scan
alerts can be acknowledged separately from deleting patch history. Ansible
Runner no longer receives the unsupported `runner_mode` keyword.

See `UPGRADE.md` for the single command and `RELEASE_NOTES.md` for safeguards.

# OPM v0.4.6: Background activity

Every authenticated page now includes an **Activity** indicator. Long
operations such as scanning, SSH testing and account setup are submitted to
the central OPM controller and are tracked independently of the browser.
The operator can navigate to any page. The activity control shows progress,
completion, partial results, and failures. The full-screen loading overlay
has been removed.

A scan's progress measures hosts completed. It does not claim a percentage
of package installation or fake estimates. Controller restart interrupts
non-patch activities and never silently replays remote provisioning.

See `RELEASE_NOTES.md` for the detailed changes and `UPGRADE.md` for the
one-command installer invocation.

## v0.4.5 interaction feedback

Every server-side action now acknowledges the request immediately with a
visible dark-theme loading indicator. It identifies the current operation,
tracks elapsed waiting time and blocks duplicate submissions. Scans, SSH
checks, enrollment, patch-plan approval and cancellation have tailored text.
Server reachability indicators pulse during their two-minute ICMP refresh.

**Important:** An animated indicator means that the browser is waiting for a
server response. It does not imply that a remote operation succeeded and does
not fabricate a completion percentage or per-host progress. After the request
returns, the actual result and Django messages appear as before. Patch jobs
continue in the controller independent of the browser.

This focused release does not yet include the planned interactive maintenance
installer menu. `OPMInstall.sh` retains its existing one-command upgrade flow.

# OPM: Onyx Patch Manager v0.4.4

OPM is an internal-only, account-based homelab patch management application.
The primary interface is now a responsive Django Web UI. The original
Textual TUI remains available for maintenance, but the web interface is the
primary UI going forward.

## What's included

- Modern dark dashboard with a persistent desktop sidebar, responsive mobile
  navigation, compact status cards, server tables, scan timestamps and scan-
  time CPU/load, RAM, disk and uptime observations.
- Server registration, SSH host-key discovery and explicit verification,
  dedicated `opm` account provisioning and key-based connectivity testing.
- Debian and Ubuntu APT update discovery, review, explicit approval, sudo
  password prompts and detached systemd patch jobs.
- Docker and Podman inspection, image update information and basic container
  running/health states. Container **updates are review-only** in this build.
- Patch history, recent execution events, and safe cancellation requests.
- Django authentication, Argon2id passwords, persistent login throttling,
  per-user Operator/Viewer roles and a web audit trail.
- A single Debian 13 installation/upgrade script that retains the original
  controller and SQLite database and makes a consistent backup.

## Installing or upgrading on your Debian 13 Proxmox LXC

Transfer `onyx_patch_manager_0.4.4_source.zip` to the LXC, then run as root:

```bash
unzip onyx_patch_manager_0.4.4_source.zip -d /root/opm-release && cd /root/opm-release/onyx_patch_manager && bash OPMInstall.sh
```

If you are already inside the extracted project directory, one command is
sufficient:

```bash
sudo bash OPMInstall.sh
```

The script handles OS packages, the `opm` user (if needed), Python virtual
environment, dependencies, backup, Django migration, systemd services, and
interactive administrator creation. It preserves `~opm/.local/share/opm`.
Never run this upgrade while patch jobs are queued or running. Installer
execution will refuse if it finds such jobs.

### Access on first install

For security, the default listener is only `127.0.0.1:8765` in the LXC.
From your computer, create a tunnel using an account that can SSH into it:

```bash
ssh -L 8765:127.0.0.1:8765 YOUR_SSH_USER@LXC_IP
```

Then open `http://127.0.0.1:8765` in the computer's browser and sign in with
the administrator created by the installer. If you want private mobile
access through your existing LAN/WireGuard and reverse proxy, follow
[SECURITY.md](SECURITY.md) for a deliberately restricted HTTPS deployment.

### Services

The installer enables two **systemd user services** under the `opm` account:

- `opm-controller.service`: existing private Unix-socket patch controller
- `opm-web.service`: Gunicorn/Django web application

```bash
systemctl --user status opm-controller.service opm-web.service
```

If you connect to the LXC as root and use `su - opm`, set the `XDG_RUNTIME_DIR`
and `DBUS_SESSION_BUS_ADDRESS` variables from the earlier installation notes.

### Other commands

```bash
/home/opm/opm-venv/bin/opm ping
/home/opm/opm-venv/bin/opm tui
/home/opm/opm-venv/bin/opm web check
/home/opm/opm-venv/bin/opm web createsuperuser
```

Django commands require the `OPM_WEB_SECRET_KEY` environment value from the
owner-readable `~opm/.config/opm/web.env`. The installer supplies this during
setup; the systemd unit loads it automatically. For subsequent CLI admin
maintenance, read the file as the `opm` account in a trusted terminal, never
print or transmit the secret to others.

## Architecture

Browser -> Gunicorn/Django templates -> private Unix socket -> OPM controller
-> detached Ansible workers -> managed hosts over SSH.

Django adds authentication and audit tables to the existing `state.db` using
its migration system. OPM's inventory and jobs tables are not replaced.
There is no custom JavaScript or Node.js runtime; frontend updates use
normal HTML forms and server-rendered pages. Running job pages auto-refresh.

## Safety status

This is the **first web implementation milestone**. It is source-complete but
has not yet undergone live Django/browser verification on Debian 13 in this
build environment. Unit and source checks are included. Do not treat it as a
production-validated release. Test login, CSRF, onboarding and a noncritical
host before approving real OS updates.

See [SECURITY.md](SECURITY.md), [RELEASE_NOTES.md](RELEASE_NOTES.md), and
[UPGRADE.md](UPGRADE.md).


## Guided Add Server wizard (v0.4.3)

Choose **Servers > Add server**. The single wizard replaces the disconnected
host registration, fingerprint discovery, key creation, account creation, and
test controls:

1. **Administrator login:** Server name/IP, your temporary SSH username and
   password, plus an optional separate sudo password. Blank sudo password
   reuses the SSH password, or works with existing passwordless sudo.
2. **OPM account:** Choose and confirm the new dedicated remote `opm` sudo
   password. OPM creates its own SSH key automatically.
3. **Setup and test:** OPM discovers and displays the remote ED25519 SSH host
   fingerprint. Approve trusting this host key before submitting. OPM then
   provisions the account and verifies SSH key login and sudo.

**SSH identity warning:** A fingerprint retrieved over the network is not
independent proof of a server's identity. Compare with an independently known
fingerprint whenever possible. Trusting a new host key without doing so is
trust on first use and is vulnerable to an active network interception.
You never need to run SSH commands from your terminal to complete this wizard.

Passwords are submitted once at the final step and are not saved in SQLite,
web sessions, or audit logs. If setup fails, a partially created remote account
is never silently overwritten. The existing unverified server offers
**Finish setup**, including a safe key-login recovery path.

This wizard requires a small local JavaScript file for visual step changes and
public SSH fingerprint discovery. All remote actions remain server-side.

## Scanning Debian updates and Docker/Podman with sudo (v0.4.3)

The **Scan all** and **Scan server** buttons now open a Web UI authorization
page. No SSH terminal commands are needed for normal scans. When a host is
configured for password-required sudo, enter its dedicated remote `opm`
account sudo password. One shared password can be supplied for several hosts,
with per-host overrides when they differ.

OPM sends credentials to the private local controller only for that scan. It
will refresh APT package indexes, read package candidates, inspect ordinary
Docker and Podman contexts, and, where needed, use authorized sudo to inspect
root-managed engines. It does not install OS updates, pull images, restart
containers, or add `opm` to the Docker group.

On Debian/Ubuntu, an unsuccessful APT metadata refresh is presented as a
**partial scan**. Any cached update count is not proof that the host is
up-to-date. If sudo authentication fails, re-enter the remote `opm` password
and try again. The sudo password is not stored in SQLite or job history.

Podman can have multiple isolated user contexts. OPM reads the configured SSH
user's rootless Podman and the privileged/rootful Podman context when authorized.
Containers owned by other individual rootless Podman accounts remain outside
this scan's scope; they must be supported as separately verified accounts.

Patching is a separate operation. It requires selecting hosts, reviewing a
plan, entering the sudo password again for that patch run, and typing the
required approval phrase. Docker/Podman image *updates* remain review-only.

**Version scope:** Source tests exercise the command boundaries and data flow.
A live Debian SSH/sudo session, browser rendering, and Docker/Podman runtime
must be validated on a noncritical server before production use.


## v0.4.3: Server overview and dashboard connectivity

- After successful enrollment, the Host Overview card contains **Test SSH**.
  No separate SSH/account setup card remains on a configured server.
- Unverified hosts still have a guided **Finish setup** link. Existing SSH
  credentials and remote account permissions are not changed by this release.
- The dashboard probes with ICMP ping every two minutes while that
  page is open and visible. Probes do not require sudo, send credentials,
  refresh APT indexes, or scan containers. Probes are cached centrally for
  110 seconds so multiple tabs do not multiply their network traffic.
- The dashboard now has separate **Connection** and **Last scan** columns.
  Connection means an ICMP echo reply only; it does not guarantee the
  operating system or applications are healthy. **Last scan** retains the
  previously recorded patch scan result and timestamp.
- Card padding, inner empty states, container messages and controls use
  consistent spacing on desktop and narrow mobile screens.


## v0.4.4: ICMP connectivity and quiet installer

- The Dashboard Connection column displays **Up** when one ICMP echo request
  receives a reply, **Down** when it receives none, and **Unknown** when the
  controller cannot run the ping probe. No SSH or sudo credentials are needed.
- ICMP can be blocked by a firewall even if a server is up. No reply does not
  establish that a host has powered off. The **Last scan** column is unchanged.
- Ping runs on dashboard page load and every two minutes while it is visible.
  The controller caches results for 110 seconds across open browser sessions.
- `OPMInstall.sh` now shows the current stage and a 20-block completion bar
  instead of printing apt, pip and migration output to the terminal. It records
  detailed output in a root-only `/var/log/opm-installer/` log and shows an
  error excerpt if any stage fails. Percent is **completed stages**, not the
  download percentage within apt or pip.
- The installer includes Debian `iputils-ping` and continues to run the
  application as the dedicated non-root `opm` account. Both
  `OPMInstall.sh` and `OPMInstaller.sh` filenames work; the shorter file is
  the canonical installer and the longer one is a compatibility wrapper.
- ICMP permission may be restricted by an unprivileged LXC. In that case the
  dashboard reports Unknown. OPM does not enable privileged LXC or silently
  loosen networking capabilities. Diagnose ping permissions separately.


## v0.4.7 maintenance corrections

Dashboard ping operates under the unprivileged `opm` Linux service account.
If the Connection field shows Unknown, read its inline ICMP diagnostic; a
root shell's successful ping does not guarantee access for the service user.

A successful sudo-authorized scan retains temporary authorization in the
controller process for ten minutes for the same signed-in browser session.
This may be reused for an approved patch without retyping the password. It
is not stored in Django sessions, SQLite, or browser storage. The patch review
screen still requires clicking Confirm and patch, and protected hosts use a
separately labeled action. See RELEASE_NOTES.md for details.


## Changes in v0.4.13

See `RELEASE_NOTES.md` for automatic post-patch discovery, terminal menu behavior, and layout refinements.
