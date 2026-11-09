## v0.7.0 — Nightly

- Add bulk CSV server inventory import and export, with preview and Checkmk column support.
- Include the internal updates since v0.6.0, covering Compose workflows, job recovery, and interface improvements.

## v0.6.21 — Server inventory CSV (internal)

- Add administrator CSV import with preview, Checkmk column support and duplicate detection.
- Export server inventory fields and keep imported profiles pending SSH setup.

## v0.6.20 — Breadcrumb links and signed build revisions (internal)

- Link the header breadcrumbs to parent pages and the Dashboard.
- Allow the existing signed deployment command to install newer build revisions without changing the application version.

## v0.6.19 — Sign-in title alignment (internal)

- Center the left-aligned shield on the title and display the running version in small text beside Manager.

## v0.6.18 — Sign-in header layout (internal)

- Align the Shield O mark beside the title and subtitle on desktop and phones.

## v0.6.17 — Unattended deployment test (internal)

- Bump the application and installer version to exercise signed deployment.
- Skip Debian package changes when upgrading an existing OPM installation.

## v0.6.16 — Shield O branding (internal)

- Apply the approved teal shield / white O mark as the navigation brand symbol and sign-in logo. Preserve the existing ONYX / PATCH MANAGER wordmark and mobile navigation.
- Ship an offline first-party SVG mark, PNG fallbacks at 16/32/48/64/180/192 pixels, multi-resolution `.ico`, and Apple Touch icon. All assets are derived from one vector source; no third-party image loading or network requirement.
- Use the same favicon on authenticated pages and login, with cache-safe static asset links compatible with Django's manifest static file pipeline.
- Preserve all v0.6.15 improvements: administrator-only Clear all history, non-destructive Clear alerts, bounded History scrolling, equal-height independent Dashboard panels.
- Release: internal v0.6.16; no changes to patch execution, activity processing, account permissions or stored records. Live browser appearance remains to be verified by the user.

## v0.6.15 — History and dashboard usability (internal)

- **Clear all history:** Replace the limited patch-history deletion button with an administrator-only, checkbox-confirmed deletion of patch jobs/task logs, background activities (including SSH repair), and Django audit records. Delete the related SQL records atomically in the shared SQLite database. Keep server inventory, host scans, Compose verifications, pending unused plans, accounts, sessions and login rate-limit protection. Refuse deletion during any active or locked operation. Clean up old known patch job artifact directories separately; never accept arbitrary paths.
- **Clear alerts:** Add an administrator-only button to the header Activity dropdown. It acknowledges past failures and warnings without deleting evidence, and returns to the page the administrator was viewing. Existing History maintenance acknowledgement uses the same semantics.
- **Bounded History:** Internal scrolling with a sticky table header; filters and History maintenance stay visible outside the scroll area.
- **Equal-sized Dashboard cards:** Server Overview and Recent Activity use the same responsive fixed-height container, each with its own scrollbar when content overflows. Server sorting and existing responsive layout remain intact.
- **Branding:** Favicon/sidebar replacement remains a design decision, not included in this release. Candidate directions: faceted onyx O, split-ring O, shield O.
- **Validation:** New in-memory SQLite regressions check atomic all-history deletion, active/locked work refusal, retention of inventory and scan records, warning acknowledgement, and template/layout contracts. No production history was deleted during testing.
- **Release:** Internal v0.6.15; next public milestone remains v0.7.0.

## v0.6.14 — Runner cancellation interruption handling (internal)

- Treat Ansible Runner `canceled`/`cancelled` results, including exit code 254, as interrupted patch execution instead of a normal failed operation. The affected target and overall job reconcile atomically; previously completed results remain intact.
- Keep ordinary Ansible errors in `Failed`; never auto-replay an interrupted APT operation. Existing systemd orphan reconciliation still handles abrupt worker termination.
- Add regression tests for canceled Runner results, noncancellation failures and preserved completed targets.

## v0.6.13 — Interrupted worker reconciliation (internal)

- Atomically reconcile interrupted detached OS patch workers: active/verifying targets become **Interrupted**, untouched queued targets **Not started**; preserve completed/failed per-target results and release locks.
- Record each affected target's last persisted step and a sanitized recovery event; show manual checks before retrying. Never automatically replay interrupted package operations.
- Periodically detect vanished workers while controller remains up; tolerate temporary systemd failures and allow queued-worker startup grace.
- Show systemd worker exit information when available; improve readable statuses across History, Dashboard and the job detail page.

## v0.6.12 — Verified-only APT partial updates (internal)

- Continue approved Debian/Ubuntu patches when a third-party repository's metadata fails, but only from freshly verified signed indexes in a private temporary APT directory. Do not trust the machine's old cached indexes, disable signature verification or alter repository files.
- Add explicit review checkbox and persisted controller approval for cached/partial scans; retain protected-host approval.
- Refuse unapproved versions, unexpected APT simulation actions, unsafe repo policy or missing signed indexes; fail closed when no eligible package remains.
- Mark verified-only jobs **Completed with warnings**, and persist upgraded, missing, held and unchanged packages in History. Keep sudo secrets out of argv and plan/history rows.
- Include regression tests and add live broken-repository acceptance cases to the GM checklist.

## v0.6.11 — Controller naming and GM checklist (internal)

- Label the sidebar controller status **OPM Controller** with matching accessibility state text.
- Publish a practical, saveable GM v1.0.0 checklist and record Podman-logo live failure as deferred work.
- No database, scanner, OS-patch or container-update behavior changes.

# Onyx Patch Manager changelog

## v0.6.10 — Podman logos and readable server display names (internal)

- Use the same application-artwork resolver for Docker and Podman containers,
  including scanned rootless Podman user contexts. Automatic selection prefers
  Docker images, then Podman images, then the Linux distribution logo. Dedupe
  identical repository images across engines; preserve existing logo IDs.
- Fix persistent selection of non-Docker-Hub (`image:`) logos in the controller
  store. Validate selections against current scan candidates; never allow a
  user-controlled logo URL or remote registry fetch.
- Permit single internal spaces in server *display names*. SSH usernames, host
  addresses, and credentials keep their existing strict validation. For spaced
  names only, generate a stable, immutable-ID-based Ansible inventory alias and
  validate runner callbacks/recaps against the alias, not the display name.
- No Podman image pull/recreation is enabled. Docker Compose explicit approvals
  and update preflight are unchanged. No SQL schema migration is required.


## v0.6.6 — Internal testing (Docker Compose source verification)

- Add administrator-only Verify Configuration action for discovered non-vendor Docker Compose projects. Remote source files and deployed container service hashes are checked read-only.
- Store time-limited, sanitized source-verification status and display it in server profiles; record operations in History.
- Keep registry checks, image pulls and container updates separate and disabled in this workflow; Podman and standalone Docker remain review-only.
- Add simulated remote validation and security regression tests.

## v0.6.5 — Internal hotfix (Django migration label)

- Fix the account-audit migration dependency and historical model lookup to consistently use the registered Django app label `opm_web` rather than `web`.
- Restore upgrades after a v0.6.4 `NodeNotFoundError` without resetting controller data or faking migrations.
- Add regression coverage for the migration dependency and audit-attribution backfill.

## v0.6.4 — Internal testing (account lifecycle and consistent forms)

- Administrators can Enable, Disable, or Delete another account from its dedicated Edit account page. Disabling can be reversed. Password/role editing remains unchanged.
- Replace typed confirmation phrases with checked acknowledgments and explicit actions for account deletion, server removal and job cancellation. Preserve self/last-administrator safeguards and authorization enforcement.
- Keep existing audit event attribution when an account is deleted, via the `actor_name` model field and migration.
- Remove the legacy inline disable panel and user database description from Settings.
- Correct the Individual server passwords disclosure width and shared form/card alignment across scan, patch review, account management and SSH recovery pages, including responsive breakpoints.
- This is an internal pre-GM build. Container updates remain read-only; live UI and Django migration tests still required.

## v0.6.3 — Internal testing (patch-review layout consistency)

- Corrected the full-width **Review patch plan** card: summary, host rows, warning panels, password input, and approval controls now share a consistent horizontal inset.
- Removed nested margin on the protected-server warning so it aligns precisely with the main warning and password input. Narrow/mobile viewports retain clean wrapping and readable controls.
- No change to scan freshness, credential caching, protected-host authorization, or patch execution. This remains a private internal build; v0.6.0 is the latest public GitHub milestone.

## v0.6.2 — Internal testing (one-flow patch review and scan refresh)

- Selecting **Review selected OS updates** no longer fails with a stale-scan instruction when a selected scan is more than one hour old, failed, or absent. OPM opens the inline **Refresh scans** authorization screen, preserving the selected servers and Dry Run/Apply mode.
- The operator can supply a shared sudo password with per-server overrides, or reuse credentials already authorized for this browser session. Passwords never enter Django sessions or persistent data.
- Refresh scans only the selected stale servers, not the entire inventory; a read-only activity progress screen advances automatically to immutable patch review after successful scan completion. Partial/failed scans show a safe retry and History diagnostic link, never start a patch automatically.
- Verified but unscanned servers can now be selected for a review, which will first refresh them. The existing controller and SQLite one-hour eligibility, pending-update, privilege, and protected-host checks remain authoritative.
- No Docker Compose update execution in this release; this is an internal test build based on the v0.6.0 GitHub milestone.


## v0.6.1 — Internal testing (Docker Compose discovery)

- Read-only Docker Compose project grouping, provenance flags and review-only status across the server and patch views.
- Scope future container updating to Docker Compose only; no container recreation is enabled.
- All other container engines and vendor-managed applications remain review-only.

## v0.6.0 — GitHub milestone

- Conditional **Repair SSH access** action on verified hosts with a recorded SSH key-authentication failure (not merely ICMP failure).
- Remove the duplicate top-level Change my password control from Settings.
- Default Dashboard to numeric IP sorting, retaining the Viewer IP-redaction boundary.
- Establish the milestone/internal/GM versioning policy and consolidate GitHub release documentation.

For the full feature-by-feature history since v0.5.6, see [CHANGELOG_SINCE_0.5.6.md](CHANGELOG_SINCE_0.5.6.md).

For the complete public release description and GM status, see [the v0.6.0 GitHub release](https://github.com/dnurse/onyx-patch-manager/releases/tag/v0.6.0).

Historical implementation status, migration guidance and earlier source review notes are preserved under [docs/archive/](docs/archive/).
