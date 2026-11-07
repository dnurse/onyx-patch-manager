# OPM v0.6.0 — GitHub milestone and usability refinements

- **Release policy:** Public GitHub milestones increment the middle version component (`0.6.0`, then `0.7.0`). Internal testing increments the third (`0.6.1` etc.). GM is the major release `1.0.0`. See `VERSIONING.md`.
- **Conditional SSH repair:** Show the administrator-only Repair SSH action on previously verified `opm` hosts only when the latest relevant recorded scan or SSH test indicates key-authentication failure. Healthy hosts, ICMP failures, network outages and unrelated sudo failures do not trigger it. Later successful SSH evidence clears the action. Test SSH can be used to establish a diagnosis. Backend authorization and recovery safeguards remain intact.
- **Settings:** Remove the duplicate top-level Change my password button. Retain Edit password under the account row and the separate password-change workflow.
- **Dashboard:** Default to numeric IP ordering (IPv4, IPv6, then DNS hosts); provide an IP sorting option. With configured group headings, numeric order applies within groups. Sort using server-derived ordinal positions so Viewer HTML never carries raw IP data in sort attributes.
- **GitHub documentation:** Consolidated README and release notes; add a full GitHub release description and versioning policy. Consolidated development highlights and release validation status are recorded in the [v0.6.0 GitHub release](https://github.com/dnurse/onyx-patch-manager/releases/tag/v0.6.0).
- **Release status:** Development/pre-GM. No new Compose update or automated password-fallback path. Live SSH recovery and responsive UI still need acceptance testing.

# OPM v0.5.24 — Settings repair, full-width forms, managed SSH account recovery, legacy UI cleanup

- **Settings HTTP 500:** Removed orphaned Django template tags and form markup left behind during the move to dedicated Create New Account pages. Account management remains administrator-only.
- **Full-width UI audit:** Repair SSH, account create/edit/security, scan authorization and other full-width form panels now use the available card width on desktop, while responsive/mobile and compact login controls remain appropriately sized.
- **Repair SSH when `opm` is missing:** Added an explicit "create account if missing" option and new 12+ character password confirmation. The trusted remote setup script checks account presence. If the account exists, it only restores the controller public key and preserves its password. If missing and approved, it creates `opm` with password-required sudo and installs the key. A leftover old `/home/opm` directory blocks automatic adoption for safety. Host-key approval, separate administrator credentials, host-operation locks, and post-repair SSH/sudo tests remain mandatory. History records whether an account was created. No unattended password fallback is introduced.
- **Retired legacy Textual UI:** Removed `src/opm/ui.py`, `opm tui` entry point, obsolete Textual tests, and the `textual` Python dependency. The actual Whiptail installer (`OPMTUI.sh`) is retained and required.
- **Validation:** Local regression suite and source/syntax checks; the new SSH recovery path and rendered Settings page require live Debian/Django validation before GM sign-off.

# OPM v0.5.23 — Account management, protected-host layout, administrator SSH repair

- **Protected Dashboard icon:** Replace, do not overlay, the server icon with a single white CSS padlock for protected hosts. Preserve other Dashboard styling.
- **Account management:** Move Accounts above Security in Settings. The **Create New Account** action opens a dedicated administrator-only creation page. Account role selection, password validation, audit events, and existing administrative restrictions remain in place.
- **Server registration:** Expand the three-step wizard to the available content width, with a compact inline **Protect server** control immediately to the left of Step 1's Next action. New registrations start unchecked; completing previously saved setup retains its stored selection. The legacy edit form is also full-width.
- **Manual SSH key recovery:** Administrator-only **Repair SSH access** on a previously verified `opm` host. Require a separate administrator SSH account, valid credentials, explicit ED25519 fingerprint approval and backend verification against the trusted SSH identity before using those credentials. The remote action appends only the current controller's public key and preserves the existing `opm` account/password/other authorized keys. It never automatically falls back to password SSH during normal scans. Missing `authorized_keys` can be securely created; mismatched ownership, symlinks, changed SSH host keys and active patch locks are rejected.
- **Separate sudo verification:** Optionally test the existing opm sudo password after recovering key login. Recovery is **Partial** if SSH works but sudo remains unverified (for example, when a manually recreated `opm` account lacks sudo membership). No sudo group membership or policy is silently changed. The administrator must inspect/repair sudo separately before patching.
- **History and security:** Record sanitized recovery events, approved/failed/partial outcomes, and non-secret `ssh_recovered`/`sudo_verified` results. Do not persist login or sudo passwords in SQLite, History, browser sessions, URL parameters, command arguments or logs. The operation is serialized with existing host activities and denied while a patch job holds the host.
- **Validation:** Local unit, integration-style mock, Python/Bash/JavaScript syntax, and archive checks; live remote SSH repair and rendered responsive pages require field testing before GM.

# OPM v0.5.22 — GM security/UI and scan diagnostics

- **Account management:** Account list and permissions note use consistent padding. Disable is a button with a separate inline confirmation form; the Create another account button moves to the right of Accounts heading. Account Management and Account Security sections are full-width, with readable form widths.
- **Viewer privacy:** Viewers cannot enter Settings (HTTP 403) or see the Settings navigation link. Literal network IP addresses are removed from Viewer dashboard, server inventory, server profile, History, job details, and background activity API responses. User password-change self-service remains available; it does not expose settings or infrastructure configuration.
- **Protected servers:** The Add Server wizard again shows the Protected server checkbox, unchecked for new hosts and retaining saved protection on resumed onboarding. The Dashboard uses a CSS-only lock on protected host icons. Existing host protection is unchanged.
- **Scan all diagnostics:** The persisted activity timeline now logs each failed server by name with a bounded, sanitized error and lists failed server names in the final summary. Successful scans are named in the log too. Older records are unchanged.
- **GM evidence:** Field tests reported successful role checks and Dry Run. Fresh Debian 13 installation, local complete uninstall, multi-host OS patching and remote OPM-account replacement were previously field-tested. The v0.5.22 UI/privacy/audit fixes require new live verification.

# OPM v0.5.21 — Patch review, enrollment audit clarity and native installer status

- Removed the redundant **Authorize sudo** actions from Patch Manager and the scan form; patch review continues to request a sudo password only if the current session has no valid cached authorization. Existing scan and patch endpoints remain unchanged.
- The Review patch plan surface fills the available desktop content width; narrow screens retain the mobile layout.
- A confirmed **Delete and recreate** of an existing remote `opm` account now writes an explicit, sanitized step to the enrollment event log before final SSH/sudo verification. Successful enrollment summaries identify replacement; failed verification still shows the account operation in History.
- Installer progress retains all 15 stages, checked completed steps, emphasized arrow/uppercase active stage, empty-square pending steps, and a green progress bar. Whiptail/newt does not guarantee independent per-row colour or bold styling.
- **Live GM testing:** Local complete uninstall and remote legacy-account replacement were confirmed by the user. These UI/audit changes and the remaining major release-validation scenarios still require deployment testing.

# OPM v0.5.20 — Enrollment recovery, unified History and maintenance polish

- Dashboard: show amber `Setup required` exclusively in the **Last scan** column for unverified servers. Server name and row background remain unchanged. The Servers page still identifies incomplete setup with an amber Finish setup action.
- Remote legacy-`opm` replacement: verify the passwd entry **after** `userdel -r` rather than conflating its exit status with account removal. Handle a nonfatal missing mail-spool warning when the account was actually removed, while refusing to force-delete accounts that remain busy. If the old account is gone but `/home/opm` remains, stop with a specific recovery diagnostic rather than silently inheriting old keys/files. A separate administrator SSH login and explicit destructive approval remain mandatory.
- History: rename the former Patch history page to **History**. Merge OS patch jobs, scans, onboarding, SSH tests, web-access operations and (for administrators) security audit entries. Show Patches and Activity checkboxes, newest/oldest sorting, expandable sanitized diagnostic messages and persistent step-by-step background activity events. Patch job details still show their Ansible event logs; no raw SSH stdout, passwords, private keys or submitted secrets are persisted. Existing historical rows retain their original details; records truncated by previous versions cannot be recovered. The new `activity_events` table is created automatically with the existing controller database initialization.
- Complete uninstall: exit the interactive maintenance utility on successful uninstall, but stay in the menu after cancellation or failure. Suppress the missing-mail-spool message after confirmed local account deletion; genuine failures remain visible.
- **GM checklist:** The user reported complete local uninstall and deletion of the `opm` account as successful in live testing. Treat that specific test as field-tested. Remote account replacement and v0.5.20 Web UI behavior are *not* field-tested yet.

**Validation:** Automated regression and simulated deletion/warning tests only. A new scan/provisioning attempt against the affected legacy server and a browser check of History remain required.

---

# OPM v0.5.19 — Enrollment recovery, sudo-only authorization, mobile alignment

- Confirmed destructive account replacement first disables linger and terminates only old `opm` logind user sessions before normal `userdel -r`; does not use forced deletion. Remaining processes still block replacement and require manual investigation.
- Failed enrollment remains registered as unverified for safe recovery, now marked with amber **Setup incomplete** status and **Finish setup** action on Dashboard, Servers and host profile. ICMP `Up` is independent of enrollment.
- Fixes right-alignment of the Dashboard Connection badge in mobile card layout.
- Adds **Authorize sudo only** option, validating sudo over SSH without APT refresh or scanning, caching validated credentials for at most 10 minutes in controller RAM scoped to the login session. Normal Patch Review accepts direct credentials too.
- Live integration tests with actual remote account processes remain required before GM sign-off.

# OPM v0.5.18: Complete uninstall recovery and visible progress checklist

- Fixed a destructive complete-uninstall bug: the old `userdel -r opm || rm -rf /home/opm` fallback could remove the home directory while leaving the local account registered. The new flow stops OPM units, disables lingering, terminates the dedicated systemd user session, and verifies account deletion before clearing any surviving home. Failure is explicitly reported, with the home preserved for inspection.
- A complete uninstall can now finish cleaning up a previous partial purge even when application files are already absent. Root-owned OPM maintenance helper files are also removed on successful complete uninstall; remote managed-server accounts are never touched.
- Fresh installation now repairs the specific older partial-uninstall state (existing `opm` account, missing `/home/opm`) by reconstructing its private service home. It refuses symlink or unexpected home-directory paths rather than modifying arbitrary directories.
- Fixed the Whiptail progress dialog's actual visible height. Newt sizes the text area from the *initial* message; starting with two lines made the later 15-step checklist invisible despite full updates being sent. The gauge is now opened with all steps present, and later updates preserve the same list with ✓ completed, ▶ active (uppercase), and □ upcoming markers.
- No changes to remote hosts, container scanning, patch execution, account migration, database schema, or Web UI. The unit/integration tests use mocks and do not perform destructive system operations. Live Debian 13 validation is still required.

# OPM v0.5.17: Server sorting, group organization, and Settings layout

- Dashboard separates scan status from **Scan time** into adjacent columns. Scan time is displayed in the configured OPM timezone, without stacking under the status badge.
- Dashboard's existing **View all servers** link becomes a sort dropdown that reorders the current table without navigation. Name ascending/descending, group ascending, update count descending, and latest scan are supported.
- The Servers inventory view receives the same sort options under **Sort servers**.
- Settings gains the administrator-only **Group servers in Dashboard and Servers tables** preference. The setting persists in the existing private web configuration, preserving inventory and all other preferences. Enabling it shows group headings from saved `group_name` values (or Ungrouped) and sorts within groups; disabling it sorts the complete list.
- The Settings page is consolidated into one large card with section dividers for Security, Regional preferences, Server organization, Web access, Testing & Validation, and Accounts.
- The sort controller uses plain DOM APIs and textContent for user-supplied group labels; no network requests, inventory mutation, or patch-order changes.
- No database or remote agent changes.

## GM validation

Automated tests and syntax checks are required before release. Browser layout/interaction and live installation on Debian 13 need field verification.

---

# OPM v0.5.16: Persistent installation progress checklist

- The native Whiptail progress dialog now displays **all 15 installer stages** from the beginning of the operation, not just the four most recently completed stages.
- Completed stages display `[x]`, the active stage is highlighted with `[>]` and uppercase text, and upcoming stages display `[ ]`. The progress bar and completion percentage remain at the bottom.
- Progress is reconstructed from the installation's completed-stage counter after the dialog reopens (for example, following an administrator setup prompt); the earlier checkmarks are retained.
- The progress window is taller on standard SSH terminals. Shorter terminals use a compact two-column checklist instead of silently removing completed steps. Terminal-native Whiptail does not support arbitrary per-row colours; emphasis is provided by the active marker and uppercase label.
- Stage display order and names come from a single manifest in `OPMInstall.sh`, checked against actual `start_step` calls in regression tests.
- The change affects only installer presentation. No patch execution, controller code, SSH keys, account handling, databases, or system service behavior changes.
- Automated tests cover all stages, active/completed/future status, shorter terminal rendering, and gauge reopens. Live SSH/Whiptail visual acceptance testing remains pending.

# OPM v0.5.15: Container card visibility and honest empty states

- In Server Details -> Container workloads and Patch Manager -> expanded host information, omit Docker/Podman sections only when the scan confirms they are not installed.
- Keep installed but unavailable engines, access errors, partial privileged/rootless scope warnings and their diagnostics visible. An unprivileged absence result with a privileged discovery error is *not* treated as a clean absence.
- When both engines are confirmed absent, show **Neither Docker nor Podman is installed.** When no scan results exist, show **No container scan results yet.** Incomplete inventory does not claim either engine is absent.
- Accept older ENOENT-based absence data as well as newer `installed: false` scan data.
- Presentation-only release: no change to remote discovery, SSH, sudo, database, accounts, or patching.
- Automated tests cover mixed and absent engines, legacy results, error visibility and both templates. Live Debian/Django browser verification remains a GM test.

# OPM v0.5.14: Rootless Podman scan working-directory repair

- Fixes authenticated rootless Podman discovery for service users such as `uosserver` when the OPM SSH user's home (`/home/opm`) has restrictive permissions. Although the scanner already switched Linux UID and set `HOME`, it inherited `/home/opm` as its process working directory, resulting in `cannot chdir to /home/opm: Permission denied` and `Error: setting up the process`.
- Runs the read-only `podman ps -aq` and `podman inspect` subprocesses with an explicit working directory of `/` **before** the user switch. Preserves the discovered account's real `HOME`, `USER`, `LOGNAME`, `XDG_RUNTIME_DIR`, and bus environment. No chmod, chown, persistent service-account change, Podman write, or privileged LXC change is required.
- Adds targeted regression coverage proving both rootless Podman subprocesses receive the safe cwd and the launched command remains read-only. Does not change container update status or implement container upgrades.
- A real sudo-authorized rescan on a multi-user Podman target is still required for field validation.

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
