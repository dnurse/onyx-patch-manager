# OPM v0.5.10 implementation status

The first-install Whiptail transition from System Requirements to Web Access
now retains a terminal drawing stream and captures selected values on FD 3.
Input and password dialogs and maintenance menus use the same adapter.
The palette and active-button contrast are adjusted, terminal cleanup is
registered, terminal dimensions are parsed correctly, and the wizard logs
which screen ended without logging secrets. No backend patching changes.

Validation: 178 local automated tests passed, 1 Django integration test skipped.
The real Debian/Whiptail and upgrade path still require user field validation.

# OPM v0.5.8 implementation status

See the release notes for the native Whiptail UI update and Patch Manager control ordering. The exact six-row ASCII banner is now used on terminals at least 106 columns wide; narrow consoles use a compact variant. Welcome prose is centred while the task block remains aligned. Launch routing, focused-button contrast and saved web access-mode preselection have regression coverage. 174 tests passed; one Django integration test skipped. No patching logic was altered. Full live Whiptail visual and installation verification remain outstanding.

# OPM v0.5.6 implementation status

Dynamic Podman user-account discovery is implemented in the existing authorized SSH scan. No list of account usernames is maintained. UniFi OS is recognized by its container image, with vendor-managed updates excluded. Inaccessible stores and time-budget exhaustion are explicitly reported; host views show Partial scan when container coverage is incomplete.

Local results: 160 passing automated tests, 1 skipped Django integration test. Python/remote script compilation, Bash syntax, JavaScript syntax, archive verification and static source review performed. Not field-validated against the user's UniFi OS Server LXC; final confirmation needs an authorized rescan.

Docker Compose / Podman Compose update execution and guided retry/recheck remain unfinished V1 work. No new container mutation paths have been added.

# OPM v0.5.5 implementation status

Implemented account role selection and administrator-only account editing (role and password reset), and restricted all server inventory/SSH management endpoints to Administrators. Existing Operator accounts are automatically restricted because authorization uses their current flags. Operators still scan/patch; Viewer is read-only.

Validation pending on a live Debian LXC: Django authentication integration, password reset/session behavior, template rendering and responsive layout. No schema migration needed.

# OPM v0.5.4 implementation status

- Native Whiptail UI integrated into setup and maintenance; release tests validate source and execution contracts.
- Full live newt/Whiptail rendering, Proxmox console behaviour, package/service installation, and interactive recovery **require testing on a Debian 13 LXC/VM**; not validated by local automated tests.
- Runtime policies, data location, SSH keys, web settings on upgrade and other features remain as in v0.5.3.

# OPM v0.5.4 implementation status

Implemented: first-install explicit access choice with LAN/VPN HTTP as default (press Enter); automatic private-IP detection with override; noninteractive LAN fallback; Settings admin-only mode selection and controller-backed asynchronous application; private config backups and rollback on local health-check failure.

Not validated live: Debian 13 first install, source-IP behavior behind the user's NPM, firewall ACL effectiveness, and mobile browser reconnection after changing modes. Unit/source tests do not substitute for that validation.

# OPM v0.5.3 Implementation Status

Source changes for distinct Dry Run plans, UI preferences, Ansible check-mode validation, separate Simulated status and zero-update hosts are implemented.

Validation: 100 local automated tests pass. Python compilation, Bash and JS parsing and archive integrity are checked during packaging.

Not yet validated: real Django server startup on Debian 13, remote SSH and Ansible dry run on a managed host, real sudo handoff on that host, and mobile layout on the user's running installation. Do not treat local tests as proof of production behavior.

The separate controller self-health utility remains accessible as `sudo opm-installer --status`. A Web UI Controller Health card and self-patching are not part of v0.5.3.

## v0.5.3 incremental scope

- Initial scan scheduled automatically for successfully enrolled/verified hosts. No scan on partial or failed enrollment.
- Authorized Podman discovery includes the `uosserver` rootless service context for official UniFi OS Server. This is discovery only, not vendor workload updating.
- Results show each Podman context and report errors where service-user discovery is unavailable.
- Local test suite: 108 passing. Live UniFi LXC runtime test outstanding.
