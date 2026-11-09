## v0.6.0 — GitHub release, conditional SSH repair, numeric IP sorting and Settings cleanup

- Show **Repair SSH access** only when a verified `opm` host has a recorded failed SSH key-authentication test/scan and no later successful authentication. Do not confuse offline ICMP, network failures or sudo errors with key damage; Test SSH remains the explicit diagnostic. Recovery is still administrator-only.
- Remove the redundant top-level Change my password button in Settings, keeping the account-row Edit password action.
- Dashboard defaults to **numeric IP** sorting and exposes IP sorting in the dropdown. Grouped layouts still sort within each group; Viewers never receive raw IPs in sorting attributes.
- Adopt a documented **GitHub milestone = MINOR increment**, **internal test build = PATCH increment**, **Gold Master = MAJOR increment** versioning policy. Add `VERSIONING.md`, refresh README and add `GITHUB_RELEASE_v0.6.0.md` with consolidated changes and live GM testing status.

## v0.5.24 — Settings, account recovery and retired UI cleanup

- Fixed Settings HTTP 500 from obsolete Django template markup.
- Full-width forms now match their cards, including Repair SSH.
- Administrator-approved Repair SSH can create a missing remote `opm` account with a new sudo password; existing accounts are preserved.
- Removed the obsolete Textual terminal dashboard, CLI entry and dependency; Whiptail installer stays.

# Onyx Patch Manager — Changes since v0.5.6

**Current GitHub milestone: v0.6.0**  
**Scope:** changes introduced in v0.5.7 through v0.6.0, inclusive.  
**Baseline:** v0.5.6's dynamic rootless Podman discovery, role-aware access controls and existing patching features are not relisted as new work.

## Release highlights

- **Guided Whiptail installer and maintenance polish:** a themed setup wizard, reliable terminal input, preview mode, upgrade/reinstall detection, explicit downgrade refusal, more accurate version reporting, and improved installation diagnostics.
- **Improved controller and network visibility:** the sidebar Local Controller indicator reflects real controller RPC connectivity across all authenticated pages, with a 20-second refresh while the page is visible; LAN install completion shows a usable private-address URL rather than `0.0.0.0`.
- **Migration-aware server enrollment:** after SSH host-key approval, OPM can detect an existing remote `opm` service account and request a choice between password-verified reuse or explicitly confirmed destructive deletion/recreation.
- **Safer maintenance choices:** standard uninstall preserves data, while a separately selected and confirmed complete local uninstall removes the local application, stored database, credentials, keys and service account (never remote managed-server accounts).
- **Web UI refinements:** Patch Manager's server-first layout and aligned bulk selector; centered sidebar account/status controls; text-only sidebar navigation; improved account role placement and installation empty states.
- **GM testing improvements:** additional regression coverage for installers, enrollment safeguards, controller heartbeat, navigation, role display and radio-button behavior.

## Version-by-version changes

### v0.5.23 — Account creation, protected-host presentation, administrator SSH key recovery
- Replace the Dashboard server glyph with a white CSS padlock when the host is protected; do not add a second overlay icon.
- Move Accounts to the top of Settings. **Create New Account** opens a dedicated administrator-only page; remove the old expand/collapse creation form.
- Widen server registration to fill the content column. Put an unchecked-by-default **Protect server** checkbox and description inline to the left of the Step 1 Next button.
- Add explicit administrator-only **Repair SSH access** to previously verified host profiles. Verify the ED25519 SSH host fingerprint, use separate administrator SSH/sudo credentials to restore only the current controller's public key, and test key access. Existing `opm` account, passwords, and other keys remain intact. Missing authorized_keys may be created safely. SSH recovery is separate from optional opm sudo validation, which can report partial without modifying privileges.
- Record sanitized completed/partial/failed SSH recovery in History and preserve host-level concurrency locks. Normal scans never use SSH password fallback.
- Automated tests cover safe workflow boundaries; live remote recovery and browser layout still require GM validation.

### v0.5.22 — Viewer privacy, account settings and scan failure traceability
- Enforce a Viewer 403 for Settings and remove its navigation entry. Redact literal IPv4/IPv6 addresses in Viewer pages, logs and activity JSON responses; preserve Viewer self-service password changes.
- Place Create another account at the right of the Accounts heading. Replace the Disable dropdown with a button that opens an explicit, typed confirmation form; normalize Operator account padding and make account management/security cards full-width.
- Restore the Protected server checkbox to the onboarding wizard, preserve existing protected settings when completing setup, and overlay a CSS lock on protected Dashboard server icons.
- Record named scan success, warning and failure events; partial Scan all summaries identify failed servers. Sanitized failure descriptions are persisted for future operations, not retroactively reconstructed.
- GM evidence: user role checks and Dry Run field-tested in addition to fresh installation, multi-server Linux patching, local uninstall and remote service-account replacement.

### v0.5.21 — Wider patch review, direct sudo prompt, and replacement audit
- Remove the separate Authorize sudo buttons; Review patch plan prompts for password only when not cached by the login-scoped 10-minute authorization.
- Expand patch review to the full desktop content width.
- Record successful legacy `opm` account replacement as a specific sanitized enrollment event in History (including when later verification fails).
- Retain native Whiptail progress distinction with supported glyphs and green progress bar, avoiding unsupported per-line colours.

### v0.5.20 — Unified History, safer legacy deletion and complete-uninstall UX

- Keep incomplete setup indicators on Dashboard exclusively in the Last scan column, without changing the server name or row color.
- Verify the account is actually present after remote `userdel -r`; a harmless missing-mail-spool warning no longer causes a false busy-account failure. Remaining accounts or unexpected home directories still stop provisioning safely.
- History now shows patch jobs, controller activity (scans, onboarding, SSH tests and settings-related background work) and administrator-visible web security audit events. Operators can filter Patches/Activity and sort chronologically. Expand a short error to see the saved sanitized event timeline. Old records cannot retroactively gain details that earlier releases discarded.
- A successful standard or complete local uninstall exits the maintenance UI. Missing mail-spool warnings are suppressed once account removal is independently verified; real errors remain visible.
- **Field validation:** The user confirmed complete local uninstall and local `opm` deletion succeeded. Remote account replacement remains an open GM verification item.

### v0.5.19 — Enrollment recovery, sudo-only authorization and mobile alignment

- Introduced sudo authorization without launching APT scans, improved remote account replacement preparation and amber incomplete-setup indicators, and aligned mobile Connection status.

### v0.5.18 — Complete uninstall recovery and real Whiptail checklist

- Prevent incomplete account deletion from being misreported as success: stop the dedicated systemd user manager, verify `userdel -r` removed the local `opm` account, and **do not forcibly delete `/home/opm` if the account still exists**.
- Allow complete uninstall to finish a previous partial purge, even if the application has already been removed. A successful complete purge also removes the installed root-owned maintenance helpers; remote managed-server accounts are never modified.
- Repair the specific earlier failed-uninstall state on installation: an existing local `opm` account with no home directory. Refuse symlink/unexpected-home paths instead of changing arbitrary files.
- Make the real Whiptail progress view persistent: newt sets gauge textbox height from its initial content, so the full fifteen-stage checklist now appears before progress begins. Render ✓ completed, ▶ ACTIVE and □ upcoming in every update, retaining the bar and percentage.
- Add non-destructive mock-account regression tests for both success and failure, plus a mock Whiptail test that inspects its **initial** gauge text.

### v0.5.17 — Server sorting, grouping, and consolidated Settings

- Add a separate Scan time column next to Last scan status on Dashboard.
- Replace Dashboard View all servers with an in-place sort menu; add sorting on Servers (name, group, pending updates and latest scan).
- Add persistent, administrator-only server grouping preference and display group headings when enabled.
- Consolidate Settings into one organized card with section dividers.

### v0.5.16 — Installer progress checklist (superseded by the v0.5.18 rendering correction)

- Introduced the complete fifteen-stage checklist and retained state across credential dialogs; initial implementation still had a newt text-height issue corrected in v0.5.18.

### v0.5.15 — Hide absent container engines (display only)

- Server Details and Patch Manager container cards hide engines only when a completed scan confirms Docker or Podman is absent, including historical ENOENT scan formats.
- Preserve visible errors for installed but inaccessible engines and for incomplete privileged account discovery; never mistake an access error for an absent installation.
- When both engines are absent, display **Neither Docker nor Podman is installed.** A missing scan still says **No container scan results yet.**
- No changes to container discovery, stored scan results, patching, host access, or the database.

### v0.5.14 — Fix rootless Podman inherited working directory

- Explicitly run read-only Podman inspection subprocesses from `/` rather than from the OPM SSH user’s private `/home/opm` directory, avoiding cross-user `cannot chdir` failures without changing home directory access permissions.
- Preserve each discovered Podman owner’s HOME, XDG runtime environment and authorization boundaries; add command and working-directory regression tests.

### v0.5.13 — Enrollment wording and radio-control fix

- Present existing-account enrollment as a **numbered two-option list**, with separate bullet explanations of credentials required, effects and consequences.
- Identify **Delete and recreate** as **(Destructive)** at the point of selection and retain the separate confirmation checkbox before account replacement.
- Fix the CSS selector that applied full-width textbox styling to radio inputs, causing elongated ovals; restore compact circular radios and a circular keyboard-focus outline.
- No change to remote account replacement logic, patch execution or database schema.

### v0.5.12 — Installer safety, networking, ICMP, accounts and navigation

- Fix the installed release version being overwritten by Debian's `/etc/os-release` (`v13 (trixie)`); report the actual OPM release version in messages and completion output.
- Identify whether an operation is a fresh **install**, **upgrade**, or **same-version reinstall**; block accidental downgrades when selected release media is older than the installed version.
- Display a browser-ready private IPv4 URL for direct LAN/VPN installations or the configured hostname for an internal HTTPS proxy, rather than telling users to visit `0.0.0.0`.
- Check ICMP socket access using the unprivileged local `opm` service account; where permitted, configure a narrowly scoped persistent `ping_group_range` allowance for its group, then retest. Do not grant broad capabilities or claim success if an LXC restricts ping.
- Remove navigation icons beside **Dashboard, Patch Manager, Servers, History and Settings** while keeping page labels visible on narrow screens and the mobile bottom bar.
- Position each user's role immediately beside their username in **Settings → Accounts**, with Edit/password actions on the right.

### v0.5.11 — Controller heartbeat and old-account adoption

- Back the **Local Controller** sidebar dot with a real Unix-socket controller `ping` on every authenticated page: green on a ready response, red when unreachable/unresponsive; refresh every 20 seconds while the tab is visible. This is separate from remote server ICMP reachability.
- In **Add Server**, check for a remote `opm` Linux account after approval of the server's SSH ED25519 host fingerprint; the probe is read-only and uses temporary administrator credentials.
- Offer **Reuse existing account** using the current `opm` sudo password, or **Delete and recreate** with administrator access, a new password and explicit destructive confirmation.
- On reuse, verify sudo credentials before appending the new controller public SSH key; preserve existing account, home and authorized keys. Wrong passwords do not authorize key changes.
- On replacement, validate explicit approval and account/home constraints; invoke ordinary `userdel -r` (no force), then create the managed account again. Active processes can block deletion.
- Retain SSH fingerprint revalidation, SSH/sudo tests and the initial read-only scan after successful onboarding; avoid persistent storage of onboarding passwords.
- Include the prior v0.5.10 hotfix changes: correct first-install wizard state detection, complete local uninstall option, sidebar identity centering and bulk checkbox alignment.

### v0.5.10 — Installer recovery and account UI consistency

- Retry initial administrator password creation when Django reports a password-policy rejection, showing the actual validation errors without rerunning package installation.
- Route the extracted installer to the maintenance menu when a complete existing installation is present, unless an explicit install/upgrade action is requested.
- Read the selected release version from `pyproject.toml` and display the installed application's version in the Web UI instead of using a hardcoded version string.
- Improve Settings account spacing, the Web Access dropdown chevron, sidebar user/Sign out controls and first-server empty states in Servers and Patch Manager.
- Maintain accurate Linux-manager wording while stating that automated OS patches currently support APT-based Debian/Ubuntu systems.
- **Post-v0.5.10 hotfix folded into v0.5.11:** a partial `web.env` no longer by itself skips the first-install wizard; offer a complete local uninstall and align the sidebar and Patch Manager controls.

### v0.5.9 — Whiptail wizard reliability

- Repair native Whiptail input capture in command substitutions by keeping the UI attached to the terminal while returning selected values through file descriptor 3; fix disappearing radio lists and setup failures immediately after the System Requirements screen.
- Correct terminal-dimension parsing with the installer's strict shell IFS; reduce extra welcome-screen spacing on standard terminal sizes.
- Refine slate/navy/teal native terminal colors, selected buttons and progress gauge; support terminal recovery after cancellation/errors.
- Improve pre-install wizard-stage logs without logging credentials; maintain cancellation before application mutation.
- Add pseudo-terminal regression coverage for Whiptail rendering/capture behavior.

### v0.5.8 — Installer entry point, preview and presentation

- Make the extracted `OPMInstall.sh` launch the guided installation or upgrade directly, with `--menu` or persistent `opm-installer` reserved for maintenance, and `--install` for explicit install/upgrade calls.
- Correct native Whiptail color names and selected-control contrast.
- Use the approved six-line ASCII wordmark on wide terminals, a compact alternative on narrow terminals, and improved centered header/welcome layout.
- Add `OPMInstall.sh --preview-ui` for viewing real Whiptail welcome/network screens without changing OPM application state (may install the Whiptail UI dependency).
- Preserve selected web-access defaults and exit to the shell rather than reopening the menu after successful installation.

### v0.5.7 — Installer and Patch Manager layout

- Refine the Whiptail installer theme, fixed-width title, review and checklist-like installation progress.
- Default first-time web access to LAN/VPN HTTP while preserving the selected mode on return and for maintenance reconfiguration; the installed maintenance utility reads only required fields from `web.env`, not by executing it.
- Return to the invoking shell after successful installation.
- Reorder **Patch Manager** to show server accordions first, then **Select all eligible servers**, then **Execution mode** and review controls; preserve backend eligibility checks.

## Compatibility and operational notes

- Upgrades normally retain server inventory, local login accounts, database, SSH identities, web configuration and patch history. **Complete local uninstall is intentionally destructive** and must never be used for an upgrade.
- Remote account **reuse** preserves the former controller's authorized key. Retire/revoke that key separately once migration is complete.
- Remote **delete/recreate** permanently removes that managed server's `/home/opm` and SSH keys. Verify the account is dedicated to OPM and have administrator access and backups before using it.
- OPM is intended for trusted LAN/VPN or restricted internal reverse-proxy access. Direct LAN HTTP does **not** encrypt credentials in transit.
- Automated OS patch execution remains APT-based; Docker and Podman/Compose updates are not enabled. Rootless multi-user container discovery is informational.
- Local automated tests and syntax checks do **not** replace fresh-install, live browser, authenticated-account adoption, destructive replacement, failure recovery or real multi-user Podman field testing.

## GitHub publishing checklist

- Commit source, docs, and tests, **not** the controller's runtime directories under `/home/opm/.local/share/opm`, `/home/opm/.config/opm`, `/home/opm/.ssh`, or any live `state.db`, passwords, signing keys, logs or backup archives.
- Read the `SECURITY.md` and test the upgrade on your dedicated GM environment before announcing a stable/GM release.
- If creating a GitHub release, label v0.6.0 a **pre-GM development release** until the remaining live acceptance checks pass.
