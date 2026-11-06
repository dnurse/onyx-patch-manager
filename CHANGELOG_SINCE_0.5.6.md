# Onyx Patch Manager — Changes since v0.5.6

**Current source release: v0.5.13**  
**Scope:** changes introduced in v0.5.7 through v0.5.13, inclusive.  
**Baseline:** v0.5.6's dynamic rootless Podman discovery, role-aware access controls and existing patching features are not relisted as new work.

## Release highlights

- **Guided Whiptail installer and maintenance polish:** a themed setup wizard, reliable terminal input, preview mode, upgrade/reinstall detection, explicit downgrade refusal, more accurate version reporting, and improved installation diagnostics.
- **Improved controller and network visibility:** the sidebar Local Controller indicator reflects real controller RPC connectivity across all authenticated pages, with a 20-second refresh while the page is visible; LAN install completion shows a usable private-address URL rather than `0.0.0.0`.
- **Migration-aware server enrollment:** after SSH host-key approval, OPM can detect an existing remote `opm` service account and request a choice between password-verified reuse or explicitly confirmed destructive deletion/recreation.
- **Safer maintenance choices:** standard uninstall preserves data, while a separately selected and confirmed complete local uninstall removes the local application, stored database, credentials, keys and service account (never remote managed-server accounts).
- **Web UI refinements:** Patch Manager's server-first layout and aligned bulk selector; centered sidebar account/status controls; text-only sidebar navigation; improved account role placement and installation empty states.
- **GM testing improvements:** additional regression coverage for installers, enrollment safeguards, controller heartbeat, navigation, role display and radio-button behavior.

## Version-by-version changes

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
- v0.5.13 is published as a full GitHub release. Full-release status describes the GitHub release channel and is not, by itself, a claim that every GM acceptance check has passed.
