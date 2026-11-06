# OPM v0.5.13: Existing-account choice clarity and radio-control sizing

- In the Add Server wizard, when a remote `opm` account already exists, the choice now appears as a numbered list of **Reuse existing opm account** and **Delete and recreate the opm account (Destructive)**.
- Each option explains its requirements and consequences: reuse needs the current sudo password and preserves the account while authorizing the new controller SSH key; replacement needs administrator access and deletes the existing `opm` home directory and SSH keys before creating a new account.
- Fixed a CSS selector that applied full-width text-field dimensions and padding to radio inputs, producing elongated oval controls. Native circular radios now have a circular keyboard-focus outline and the expected compact alignment.
- UI-only release: remote account operations, verification safeguards, patch workflows, and data formats are unchanged.
- Includes regression checks for option wording, destructive disclosure, radio selectors, source version, Bash syntax and Python tests. Browser rendering and live migration remain GM acceptance tests.

# OPM v0.5.12: Installer release identification, upgrade safety, LAN URL and ICMP permissions

- Navigation sidebar icons removed for Dashboard, Patch Manager, Servers, History, and Settings. Page names remain visible on narrower screens and in the mobile bottom navigation.
- Settings → Accounts now shows each user's role immediately to the right of their username. Edit / Edit password actions remain aligned on the far right, with wrapping on narrow screens.
- Fixes Debian `/etc/os-release` clobbering the installer `VERSION` shell variable; the OPM release is independently named and the OS check is isolated in a subshell. The completion screen correctly reports v0.5.12 (not Debian v13).
- Detects installed OPM version before mutation. Menu prompts distinguish install, upgrade, and reinstall. An older selected source is rejected as a downgrade, including direct `OPMInstall.sh --install` calls.
- The completion screen displays a browser-ready private IP URL for LAN mode and an HTTPS hostname for proxy mode, rather than presenting the `0.0.0.0` bind address as a destination. When no local private IPv4 is detectable, reports the limitation and a diagnostic command instead of guessing.
- Checks ping as the unprivileged `opm` service account. If its GID is excluded by `net.ipv4.ping_group_range`, attempts a narrowly scoped, persisted `/etc/sysctl.d/90-opm-ping.conf` adjustment. Does not add `CAP_NET_RAW`, enable privileged LXC, or bypass a kernel restriction; reports unavailable ICMP explicitly if the host/LXC denies permission.
- New installer regression tests cover version handling, downgrade policy, LAN URLs, and restricted ping-group ranges. These are local/source tests; field validation in a Debian 13 LXC is still required.

# OPM v0.5.11: Existing account adoption and persistent controller heartbeat

- The sidebar Local Controller indicator now checks the real local controller Unix-socket `ping` on every authenticated page. Green means the controller responded `ready`; red means offline/unresponsive. A small authenticated, read-only heartbeat refreshes every 20 seconds while the tab is visible. This is NOT a per-server ICMP indicator.
- Add Server detects whether a managed Linux host already has an `opm` account, but ONLY after explicit SSH fingerprint approval. Its SSH probe uses temporary administrator credentials and validates the host's actual ED25519 key against the approved fingerprint without trusting it permanently or saving submitted passwords.
- If the account exists, operators must select **Reuse existing opm account** (enter existing sudo password) or **Delete and recreate** (enter a new password, confirm it, and acknowledge destructive deletion). If the account does not exist, create new account is selected automatically.
- Reuse checks password-required sudo BEFORE appending the new controller public key; existing keys and the Linux account are preserved. A wrong password results in no key mutation.
- Delete/recreate requires administrator SSH and sudo authorization. The remote script independently verifies that the old account exists, the dedicated home is `/home/opm`, and deletion was explicitly approved. `userdel -r` is used without force; it refuses an account with active processes. Recreating the account requires a new password of at least 12 characters.
- Enrollment still rechecks host identity, verifies SSH and sudo, and starts the existing initial APT/Podman/Docker scan only after successful verification. Credentials remain transient in controller memory; no new persistent database fields or migrations are introduced.
- Includes the earlier v0.5.10 hotfixes: first-install welcome detection, selectable complete local uninstall, sidebar account alignment, and Patch Manager bulk-checkbox alignment.

**Validation status:** Automated Python tests, JS syntax checks, installer shell syntax, remote-script compilation and archive checks are local checks only. Live migration/adoption, actual remote account deletion, browser rendering and fresh Debian 13 installation remain separate GM acceptance tests. Do not perform account replacement on a production server before verifying you have current backups and a working administrator login.

# OPM v0.5.10: Installer recovery and Web UI polish

- New install: Django password-policy rejection appears in Whiptail and prompts the administrator to retry, without rerunning package installation.
- Relaunching OPMInstall.sh on a fully installed host opens the maintenance menu; explicit --install still starts an upgrade.
- Installer/maintenance displays the selected application release from pyproject.toml, never Debian VERSION_ID.
- The Web UI version is provided from opm.__version__, rather than hardcoded HTML.
- Accounts have consistent panel padding; Web Access select chevron has extra right inset.
- Sidebar user initial removed, Sign out is a button, and the 'Internal management' label is gone.
- Servers and Patch Manager have the same initial empty state; Linux-oriented product wording retains the accurate APT-only automated patch support boundary.

# OPM v0.5.9: Whiptail first-install wizard recovery and palette

This is an installer-only bug-fix release based on v0.5.8. It does not change
patching, host discovery, remote SSH operations, database schema, user roles,
or the Web UI Patch Manager controls.

## Fixed

- Corrected Whiptail form capture: newt draws its screen to stdout, so using
  `--output-fd 1` inside `$(...)` hid the first radiolist and could exit setup
  immediately after the System Requirements screen. The installer now keeps
  stdout attached to the controlling terminal and returns values on FD 3.
  This fix also covers LAN/IP and proxy input, administrator password screens,
  and the maintenance action picker.
- Fixed terminal width parsing with the installer's strict IFS configuration.
- Reduced excess vertical whitespace on the welcome screen to fit ordinary
  24-row consoles. The full requested six-line ASCII logo remains unchanged
  on sufficiently wide terminals; narrow consoles retain the compact logo.
- Updated NEWT_COLORS for a less saturated OPM slate/navy/teal approximation:
  slate borders, cyan/teal titles, white-on-green selected controls and a
  native green installation gauge. Exact RGB, gradients and custom buttons
  are not possible with Whiptail/newt.
- Added best-effort terminal recovery (echo/cursor/alternate-screen) on exit.
- Pre-install logs now show the wizard start and the screen at which it ended,
  including the exit status. Credentials are never logged. Cancelling before
  the final review does not create an OPM account or modify application data.

## Unchanged behavior

- Fresh installs default to LAN/VPN HTTP; existing web-access choices are
  preselected in maintenance Web Access configuration.
- Successful installs and upgrades exit to the invoking shell, not the menu.
- Patch Manager shows the server list first, then Select all eligible servers,
  then the Execution mode card.

## Validation

- 178 local automated tests passed, 1 Django integration test skipped.
- Includes a pseudo-terminal test for the Whiptail output-capture bug.
- `bash -n` checks pass for all installer Bash entrypoints; source compiles.
- Real Whiptail visual rendering and a fresh Debian 13 install must still be
  validated on the user's LXC. Whiptail is unavailable in the build environment.

## Upgrade / preview

Unzip the release into a clean directory and run:

    sudo bash /root/opm-v059/onyx_patch_manager/OPMInstall.sh --preview-ui

Preview runs the real Welcome and Web Access dialogs but does not modify OPM
application data or services. It may bootstrap the `whiptail` package if absent.

Then upgrade with:

    sudo bash /root/opm-v059/onyx_patch_manager/OPMInstall.sh

Previous notes follow.

# OPM v0.5.8: Installer entry point and Whiptail theme corrections

This is a bug-fix release built from v0.5.7. It does not change managed host
patch behaviour or the Patch Manager layout.

- `bash OPMInstall.sh` now opens the real guided install/upgrade flow.
  `--install` remains supported for explicit unattended calls. Use
  `bash OPMInstall.sh --menu` or the installed `sudo opm-installer` command for
  maintenance. An interactive direct upgrade requires confirmation.
- Whiptail's palette now uses supported newt colour names (`brightgreen`,
  `brightcyan`). The v0.5.7 palette used unsupported `lightgreen` and
  `lightcyan`, which resulted in fallback red titles and an invisible selection.
- Focused buttons have a green background with black foreground, and native
  Whiptail full-button outlines. List selection and progress bars use the
  same green accent. The background remains dark and the borders cyan.
- Replaced the distorted ASCII title with the exact user-approved, six-line
  `Onyx Patch Manager` ASCII wordmark. The full 94-character-wide art is
  displayed as one centred block in terminals at least 106 columns wide.
  A compact six-line wordmark is used on narrower consoles because Whiptail
  cannot fit the full art into 80 columns without wrapping or distortion.
- The welcome heading, introduction and final prompt are centred. The four
  setup tasks stay left-aligned inside a single centred block. Dialog width
  adapts to terminal width, and the existing high-contrast selected button
  and green native progress gauge remain in place.
- Added `sudo bash OPMInstall.sh --preview-ui` to show the real welcome and
  Web Access screens without modifying the OPM installation. If Whiptail is
  missing, the preview may install that terminal UI dependency.
- The installer continues to exit to the terminal after a successful install
  or upgrade, not return to the maintenance menu.
- Existing Web Access preferences continue to preselect the saved mode;
  fresh installations default to LAN/VPN HTTP.
- Patch Manager's server-first layout from v0.5.7 remains unchanged.

## Limitations

Whiptail/newt only supports standard terminal colours, not RGB, gradients,
rounded borders, graphics or independently coloured individual lines of text.
The exact shades depend on the connected terminal's palette. This source was
verified with test doubles and syntax checks (174 tests passed, one skipped);
a live visual test must still be performed on Debian with real Whiptail.
The build environment could not download the Whiptail dependency.

# OPM v0.5.7: Whiptail Installer and Patch Manager Layout

- Native Whiptail installer layout refined to the approved dark OPM theme: black/navy-equivalent background, slate/grey text, mint/teal-equivalent highlights, fixed-width ASCII-art title, labelled installation review and step-driven progress gauge.
- The real Whiptail binary supports 16 terminal colours, not RGB, gradients, corner radii or independently styled button groups. Exact colours depend on the terminal palette; no generated image is used as part of the installer.
- Success now returns to the shell after the installer's final completion dialog. The maintenance menu does **not** reopen automatically. Failed or cancelled attempts retain the existing error/return path.
- Fresh installation preselects LAN/VPN HTTP; re-entering the dialog retains the selected mode. The maintenance CLI reads the saved mode from `web.env` without sourcing the signing secret, and preselects that mode while reusing the saved hostname and proxy IP where applicable.
- Patch Manager: server accordions precede **Select all eligible servers**, followed by **Execution mode** (Dry Run / Apply Updates), then the review button. Eligibility and validation remain enforced by the existing JavaScript and backend.
- No change to SSH onboarding, patch execution, controller state, Podman discovery or database schema.
- Verification: source-level regression suite and fake-Whiptail interaction tests. Live newt colour/spacing and upgrade behaviour still require testing in a Debian 13 LXC or VM.

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
