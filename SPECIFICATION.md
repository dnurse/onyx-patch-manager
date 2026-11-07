# Onyx Patch Manager — Current Architecture (v0.5.24)

OPM is an agentless Linux patch manager administered through a private Django Web UI.
It uses an unprivileged `opm` controller process, private Unix-socket RPC,
SQLite persistence, Ansible for approved OS patch execution, and systemd user
services for background work. Managed servers use key-based SSH and
password-required sudo authorization.

- Primary UI: Django templates, CSS and progressive JavaScript, on LAN/VPN.
- Installer/maintenance: Whiptail (`OPMTUI.sh`); this is separate from the
  retired Textual dashboard.
- Managed-server onboarding: SSH host fingerprint approval, dedicated account
  provisioning/adoption, and administrator-only explicit recovery.
- Docker and Podman: discovery and review; Compose updates are not yet enabled.
- Web security: server-enforced Administrator, Operator and Viewer permissions.
- Database, credentials, SSH keys and user accounts are preserved during normal upgrades.

See README.md, SECURITY.md, and RELEASE_NOTES.md for operational details.
The original Textual-first implementation has been retired.
