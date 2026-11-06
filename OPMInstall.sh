#!/usr/bin/env bash
# Onyx Patch Manager: quiet, one-command installer and in-place upgrader.
#
# Operational safeguards:
#  - Installation uses root only for OS packages, file ownership and services.
#  - Both OPM processes continue to run as the unprivileged `opm` account.
#  - Preserve SSH identities, Django signing keys and the existing SQLite DB.
#  - Refuse upgrades when a patch job is active or its state is uncertain.
#  - Choose local, LAN, or explicitly configured internal HTTPS-proxy access.
#  - Show only the current step and a progress bar on an interactive terminal.
#  - Capture detailed non-secret command output in a root-only log. On failure,
#    display a short diagnostic tail and the log path instead of flooding stdout.
#  - Progress is the share of completed stages, NOT guessed apt/pip download
#    progress. Long-running stages can hold at the same value until finished.
set -Eeuo pipefail
IFS=$'\n\t'

APP_USER=opm
APP_HOME=/home/opm
APP_DIR="$APP_HOME/onyx_patch_manager"
VENV="$APP_HOME/opm-venv"
DATA_DIR="$APP_HOME/.local/share/opm"
CONF_DIR="$APP_HOME/.config/opm"
ENV_FILE="$CONF_DIR/web.env"
SERVICE_DIR="$APP_HOME/.config/systemd/user"
SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# All terminal presentation is delegated to native Whiptail/newt.
# shellcheck disable=SC1091
source "$SRC_DIR/OPMTUI.sh"
# Launch the guided installer when invoked without arguments, which is what
# users expect from OPMInstall.sh. The dedicated persistent 'opm-installer'
# command continues to open the maintenance menu. Explicit --menu is also
# available from extracted release media. Keep --install for automation.
OPM_INSTALL_EXPLICIT=0
OPM_INSTALL_PREVIEW=0
case "${1:-}" in
  "") ;;
  --install) OPM_INSTALL_EXPLICIT=1; shift ;;
  --preview-ui) OPM_INSTALL_PREVIEW=1; shift ;;
  --menu|--repair|--status|--uninstall|--web-access|--network|--help|-h)
    exec bash "$SRC_DIR/OPMMaintenance.sh" --source "$SRC_DIR" "$@" ;;
  *) echo "Unrecognized installer option: $1" >&2; exit 2 ;;
esac
[[ $# == 0 ]] || { echo 'Unexpected installer argument.' >&2; exit 2; }
# Treat OPM as an existing installation only when both its application tree and
# persisted web configuration are present. A stale web.env from a failed or
# partial attempt must NOT suppress the first-install wizard.
HAS_EXISTING_INSTALL=0
if [[ -f "$ENV_FILE" && -d "$APP_DIR" ]]; then
  HAS_EXISTING_INSTALL=1
fi
# After a successful first install, a flagless launch opens maintenance rather
# than prompting a potentially accidental upgrade. --install remains explicit.
if [[ "$OPM_INSTALL_EXPLICIT" == 0 && "$OPM_INSTALL_PREVIEW" == 0 &&
      "$HAS_EXISTING_INSTALL" == 1 ]]; then
  exec bash "$SRC_DIR/OPMMaintenance.sh" --source "$SRC_DIR" --menu
fi
# Preview mode exercises the REAL whiptail screens without installing OPM.
# It may bootstrap the small whiptail package if this host lacks it. It never
# modifies OPM's database, configuration, accounts, services or patch state.
if [[ "$OPM_INSTALL_PREVIEW" == 1 ]]; then
  OPM_UI_SOURCE="$SRC_DIR"
  OPM_UI_MODE='lan'; OPM_UI_HOST=''; OPM_UI_PROXY_IP=''
  opm_ui_bootstrap || { echo 'Interactive Whiptail terminal required for preview.' >&2; exit 1; }
  trap 'opm_ui_terminal_cleanup' EXIT
  opm_ui_welcome || exit 0
  opm_ui_access_from_env /home/opm/.config/opm/web.env || true
  opm_ui_select_access || exit 0
  opm_ui_message ' Installer preview complete ' \
    'This was a visual preview of the real OPM installer. No application settings or services were changed.' || true
  exit 0
fi
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WEB_BIND="127.0.0.1:8765"
# Use a dedicated name: /etc/os-release defines VERSION="13 (trixie)".
OPM_RELEASE_VERSION=$(python3 - "$SRC_DIR/pyproject.toml" <<'PYVERSION'
import re, sys, tomllib
with open(sys.argv[1], 'rb') as source:
    version = tomllib.load(source)['project']['version']
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
    raise SystemExit('Invalid OPM release version')
print(version)
PYVERSION
)

# Reject accidental downgrades even when the operator directly runs
# OPMInstall.sh --install, bypassing the maintenance menu.
INSTALL_ACTION=install
if [[ -f "$APP_DIR/pyproject.toml" ]]; then
  INSTALLED_RELEASE_VERSION=$(python3 - "$APP_DIR/pyproject.toml" <<'PYINSTALLED'
import sys, tomllib, re
with open(sys.argv[1], 'rb') as source:
    version = tomllib.load(source)['project']['version']
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
    raise SystemExit('Invalid installed OPM release version')
print(version)
PYINSTALLED
) || { echo 'Could not verify installed OPM release. No files changed.' >&2; exit 1; }
  VERSION_ORDER=$(python3 - "$INSTALLED_RELEASE_VERSION" "$OPM_RELEASE_VERSION" <<'PYCOMPARE'
import sys
old, new = (tuple(map(int, x.split('.'))) for x in sys.argv[1:])
print('downgrade' if new < old else 'upgrade' if new > old else 'reinstall')
PYCOMPARE
)
  if [[ "$VERSION_ORDER" == downgrade ]]; then
    printf 'Refusing OPM downgrade from v%s to v%s. Select newer release media; no installation changes were made.\n' \
      "$INSTALLED_RELEASE_VERSION" "$OPM_RELEASE_VERSION" >&2
    exit 2
  fi
  INSTALL_ACTION="$VERSION_ORDER"
fi
case "$INSTALL_ACTION" in
  upgrade) INSTALL_ACTION_TITLE='Upgrade'; INSTALL_ACTION_RESULT='upgraded' ;;
  reinstall) INSTALL_ACTION_TITLE='Reinstall'; INSTALL_ACTION_RESULT='reinstalled' ;;
  *) INSTALL_ACTION_TITLE='Install'; INSTALL_ACTION_RESULT='installed' ;;
esac
case "$INSTALL_ACTION" in
  upgrade) OPM_UI_ACTION_LABEL='UPGRADING ONYX PATCH MANAGER' ;;
  reinstall) OPM_UI_ACTION_LABEL='REINSTALLING ONYX PATCH MANAGER' ;;
  *) OPM_UI_ACTION_LABEL='INSTALLING ONYX PATCH MANAGER' ;;
esac

# Keep all logs on the administrator-controlled filesystem with restrictive
# permissions. Never print the SSH, sudo or Django signing secrets in logs.
LOG_DIR=/var/log/opm-installer
LOG_FILE="$LOG_DIR/install-$TIMESTAMP.log"
TOTAL_STEPS=15
COMPLETED=0
CURRENT_STEP=""
PREVIOUS_RENDER=0
FANCY=0
if [[ -t 1 && "${TERM:-dumb}" != dumb ]]; then FANCY=1; fi

render_progress() {
  local percent blocks filled empty
  percent=$((COMPLETED * 100 / TOTAL_STEPS))
  if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
    opm_ui_gauge_update "$percent" "$CURRENT_STEP" "$COMPLETED"
    return 0
  fi
  blocks=$((percent * 20 / 100))
  printf -v filled '%*s' "$blocks" ''
  printf -v empty '%*s' "$((20-blocks))" ''
  if [[ $FANCY == 1 ]]; then
    filled=${filled// /█}
    empty=${empty// /░}
    # Redraw precisely two lines rather than appending all command output.
    if [[ $PREVIOUS_RENDER == 1 ]]; then printf '\033[2A\r'; fi
    printf '\033[2K%s\n' "$CURRENT_STEP"
    printf '\033[2K%s%s %3d%%\n' "$filled" "$empty" "$percent"
    PREVIOUS_RENDER=1
  else
    # Piped/noninteractive runs get clean single-line stage messages.
    printf '[%3d%%] %s\n' "$percent" "$CURRENT_STEP"
  fi
}

start_step() { CURRENT_STEP="$1"; render_progress; }
end_step() { COMPLETED=$((COMPLETED+1)); render_progress; }

fail() {
  if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
    opm_ui_gauge_stop
    opm_ui_message ' Installation failed ' "The installer stopped at:\n\n${CURRENT_STEP}\n\nSee ${LOG_FILE} for diagnostic output." || true
  fi
  # Avoid accidental exposure of a secret in standard output. The installer
  # never enables shell xtrace, and its logs are readable only by root.
  if [[ $FANCY == 1 ]]; then printf '\n'; fi
  printf 'OPM installation failed: %s\n' "$*" >&2
  if [[ -f "$LOG_FILE" ]]; then
    printf 'Recent installer output:\n' >&2
    tail -n 18 "$LOG_FILE" >&2 || true
    printf 'Full installation log: %s\n' "$LOG_FILE" >&2
  fi
  exit 1
}

# All command output is appended to the private log. Command exit codes still
# control the installer, so a failed apt/pip/migration never looks successful.
run_logged() {
  if "$@" >>"$LOG_FILE" 2>&1; then return 0; fi
  fail "${CURRENT_STEP}"
}

if [[ "${1:-}" == '--help' ]]; then
  cat <<'HELP'
OPMInstall.sh installs or upgrades Onyx Patch Manager on Debian 13.
Run from the extracted source directory as root:
  bash OPMInstall.sh
The terminal shows completed installation stages; detailed output is stored
in /var/log/opm-installer/ and displayed if an operation fails.
On first install, choose local, LAN/VPN, or internal HTTPS-proxy access.
On an existing installation, use: sudo opm-installer --web-access
HELP
  exit 0
fi
[[ $EUID == 0 ]] || { printf 'Run this installer as root.\n' >&2; exit 1; }
[[ -f "$SRC_DIR/pyproject.toml" && -f "$SRC_DIR/src/opm/web/wsgi.py" ]] || {
  printf 'Run OPMInstall.sh from the extracted OPM release directory.\n' >&2; exit 1;
}
[[ -r /etc/os-release ]] || { printf 'Cannot identify the operating system.\n' >&2; exit 1; }
# Source OS metadata inside a subshell to prevent VERSION, NAME and other
# generic os-release variables from clobbering OPM release metadata.
if ! ( # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]]
); then
  printf 'This installer supports Debian 13 only.\n' >&2; exit 1
fi
# Whiptail is bootstrapped before setup, not during a patching stage.
if opm_ui_available; then
  opm_ui_bootstrap || { echo 'Cannot start the terminal installer.' >&2; exit 1; }
  trap 'opm_ui_terminal_cleanup' EXIT
fi
install -d -m 0700 "$LOG_DIR"
( umask 077; : > "$LOG_FILE" )
chmod 0600 "$LOG_FILE"
printf 'OPM v%s %s | detailed log: %s\n' "$OPM_RELEASE_VERSION" "${INSTALL_ACTION_TITLE,,}" "$LOG_FILE"
printf 'OPM %s started (release %s); configuring before modification.\n' "${INSTALL_ACTION_TITLE,,}" "$OPM_RELEASE_VERSION" >>"$LOG_FILE"

# First-time interactive configuration is reviewed before system mutation.
# Existing installs skip this wizard and retain all network/account settings.
# Match the same install-state check used by the launcher so a leftover web.env
# alone cannot skip the welcome screen and jump straight into installation.
if [[ "$OPM_UI_ACTIVE" == 1 && "$HAS_EXISTING_INSTALL" == 0 ]]; then
  INSTALL_TZ_GUESS=$(cat /etc/timezone 2>/dev/null || echo 'Etc/UTC')
  [[ -f "/usr/share/zoneinfo/${INSTALL_TZ_GUESS}" ]] || INSTALL_TZ_GUESS='Etc/UTC'
  if opm_ui_first_install "$SRC_DIR" "$INSTALL_TZ_GUESS"; then
    printf 'Pre-installation settings approved.\n' >>"$LOG_FILE"
  else
    wizard_rc=$?
    # Do not confuse an unexpected dialog error with deliberate cancellation.
    printf 'Setup ended at %s (dialog status %s).\n' "${OPM_UI_WIZARD_STEP:-Unknown}" "$wizard_rc" >>"$LOG_FILE"
    printf 'Installation stopped at %s (dialog status %s). No OPM packages, services or users were changed.\n' \
      "${OPM_UI_WIZARD_STEP:-Unknown}" "$wizard_rc"
    exit 3
  fi
fi
if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
  opm_ui_gauge_start
  trap 'opm_ui_terminal_cleanup' EXIT
fi

start_step 'Checking existing patch jobs'
if [[ -f "$DATA_DIR/state.db" ]]; then
  # SQLite backup/check use its normal locking and WAL semantics. If a query
  # fails unexpectedly, do not touch source code or running workers.
  if python3 - "$DATA_DIR/state.db" >>"$LOG_FILE" 2>&1 <<'PY'
import sqlite3, sys
try:
    db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True, timeout=8)
    count = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
    # Earlier releases do not have the background-activities table. If it
    # exists, do not interrupt running account provisioning or scans during
    # an upgrade, even though they are not detached systemd patch jobs.
    has_activities = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='activities'").fetchone()
    if has_activities:
        count += db.execute("SELECT COUNT(*) FROM activities WHERE state IN ('queued','running')").fetchone()[0]
    db.close()
except sqlite3.OperationalError as exc:
    if 'no such table: jobs' in str(exc):
        count = 0
    else:
        sys.exit(2)
sys.exit(1 if count else 0)
PY
  then :; else fail 'Active or queued patch jobs or background operations exist, or job state is unreadable. Finish jobs before upgrading.'; fi
fi
end_step

export DEBIAN_FRONTEND=noninteractive

detect_timezone() {
  local guessed="America/Toronto"
  if [[ -r /etc/timezone ]]; then
    guessed=$(tr -d '[:space:]' < /etc/timezone)
  elif command -v timedatectl >/dev/null 2>&1; then
    guessed=$(timedatectl show -p Timezone --value 2>/dev/null || printf 'America/Toronto')
  fi
  [[ -n "$guessed" ]] || guessed="America/Toronto"
  printf '%s' "$guessed"
}

start_step 'Refreshing Debian package indexes'
run_logged apt-get -qq update
end_step

start_step 'Installing Python and required system packages'
# iputils-ping enables unprivileged ICMP echo checks in supported LXC configs.
# We intentionally do NOT require privileged LXC or add CAP_NET_RAW.
run_logged apt-get -qq install -y python3 python3-venv python3-pip python3-dev \
  build-essential libffi-dev libssl-dev openssh-client sudo sqlite3 \
  ca-certificates iputils-ping iproute2
end_step

start_step 'Preparing the dedicated OPM service account'
if ! id "$APP_USER" >/dev/null 2>&1; then
  run_logged useradd --create-home --shell /bin/bash "$APP_USER"
fi
[[ "$(getent passwd "$APP_USER" | cut -d: -f6)" == "$APP_HOME" ]] || \
  fail 'The existing opm user has an unexpected home directory.'
run_logged loginctl enable-linger "$APP_USER"
UID_OPM="$(id -u "$APP_USER")"
run_logged systemctl start "user@${UID_OPM}.service"

as_opm() {
  # Run all application-level operations without root privileges. The user
  # systemd bus exists even if root entered the LXC without a login session.
  ( cd "$APP_HOME" && runuser -u "$APP_USER" -- env \
      "XDG_RUNTIME_DIR=/run/user/$UID_OPM" \
      "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_OPM/bus" \
      "$@" )
}
end_step

# Debian 13 uses net.ipv4.ping_group_range rather than CAP_NET_RAW for
# unprivileged ICMP. Grant the service GID access only if it is excluded.
# Respect read-only Proxmox/LXC restrictions: never change container privileges.
PING_WARNING=0
if ! as_opm ping -n -c 1 -W 1 127.0.0.1 >>"$LOG_FILE" 2>&1; then
  python3 "$SRC_DIR/OPMPingAccess.py" --user "$APP_USER" >>"$LOG_FILE" 2>&1 || true
  if ! as_opm ping -n -c 1 -W 1 127.0.0.1 >>"$LOG_FILE" 2>&1; then
    PING_WARNING=1
  fi
fi

start_step 'Preparing private web configuration'
run_logged install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$CONF_DIR" "$SERVICE_DIR" "$DATA_DIR"
if [[ ! -e "$ENV_FILE" ]]; then
  APP_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(64))')"
  INSTALL_TZ="$(detect_timezone)"
  if [[ "$OPM_UI_ACTIVE" == 1 ]]; then INSTALL_TZ="$OPM_UI_TIMEZONE"; fi
  if [[ "$OPM_UI_ACTIVE" != 1 && -t 0 && -t 1 ]]; then
    printf '
Select the web UI timezone [%s]: ' "$INSTALL_TZ"
    IFS= read -r picked_tz
    if [[ -n "${picked_tz// }" ]]; then INSTALL_TZ="$picked_tz"; fi
  fi
  ( umask 077; cat > "$ENV_FILE" <<VARS
OPM_WEB_SECRET_KEY=$APP_SECRET
OPM_WEB_ALLOWED_HOSTS=127.0.0.1,localhost
OPM_WEB_BIND=$WEB_BIND
OPM_WEB_SECURE_COOKIES=0
OPM_WEB_TIME_ZONE=$INSTALL_TZ
OPM_WEB_TRUST_PROXY=0
VARS
  )
  run_logged chown "$APP_USER:$APP_USER" "$ENV_FILE"
  run_logged chmod 0600 "$ENV_FILE"
  # First-install only. Upgrades preserve existing proxy/host/cookie settings.
  # Prompts are user-visible; secrets are not echoed or written to logs.
  if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
    python3 "$SRC_DIR/OPMWebAccess.py" --initial --mode "$OPM_UI_MODE" \
      --hostname "$OPM_UI_HOST" --proxy-ip "$OPM_UI_PROXY_IP" >>"$LOG_FILE" 2>&1 || {
        rm -f -- "$ENV_FILE"; fail 'Chosen web access mode could not be applied';
      }
  elif ! python3 "$SRC_DIR/OPMWebAccess.py" --initial; then
    # This file was created by this branch just above; remove it so a retry
    # of fresh installation presents the access-mode question again.
    rm -f -- "$ENV_FILE"
    fail 'Web access selection was not completed; rerun the installer to choose a mode'
  fi
  # Interactive prompts may have scrolled the two-line progress renderer.
  # Start a fresh progress line instead of moving over the wizard text.
  PREVIOUS_RENDER=0
fi
run_logged as_opm systemctl --user show-environment
end_step

start_step 'Stopping OPM services safely'
# Do not replace source or apply migrations while Django/controller is active.
for unit in opm-web.service opm-controller.service; do
  if as_opm systemctl --user cat "$unit" >/dev/null 2>&1; then
    run_logged as_opm systemctl --user stop "$unit"
  fi
done
if as_opm systemctl --user is-active --quiet opm-controller.service; then
  fail 'Controller remains active. Refusing to replace source.'
fi
end_step

start_step 'Backing up inventory, accounts and patch history'
if [[ -f "$DATA_DIR/state.db" ]]; then
  run_logged install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$DATA_DIR/backups"
  # SQLite's online backup API preserves consistency with WAL files.
  if as_opm python3 - "$DATA_DIR/state.db" "$DATA_DIR/backups/state-$TIMESTAMP.db" >>"$LOG_FILE" 2>&1 <<'PY'
import sqlite3, sys
source = sqlite3.connect(sys.argv[1]); dest = sqlite3.connect(sys.argv[2])
try:
    source.backup(dest)
finally:
    source.close(); dest.close()
PY
  then :; else fail 'SQLite backup failed'; fi
  run_logged chmod 0600 "$DATA_DIR/backups/state-$TIMESTAMP.db"
fi
end_step

start_step 'Installing OPM application files'
STAGED="${APP_HOME}/.opm-stage-$TIMESTAMP"
run_logged install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$STAGED"
run_logged cp -a "$SRC_DIR"/. "$STAGED"/
run_logged chown -R "$APP_USER:$APP_USER" "$STAGED"
if [[ -d "$APP_DIR" && "$(realpath "$APP_DIR")" != "$(realpath "$STAGED")" ]]; then
  run_logged mv "$APP_DIR" "${APP_HOME}/onyx_patch_manager_backup_$TIMESTAMP"
fi
run_logged mv "$STAGED" "$APP_DIR"
end_step

start_step 'Preparing Python virtual environment'
if [[ ! -f "$VENV/bin/python" ]]; then
  run_logged as_opm python3 -m venv "$VENV"
fi
end_step

start_step 'Installing OPM Python dependencies'
run_logged as_opm "$VENV/bin/pip" install -e "$APP_DIR"
end_step

start_step 'Verifying Ansible Core executable'
# Installing ansible-runner alone is not sufficient. The systemd worker
# launches ansible-playbook from this virtual environment, so verify both
# the entry point, inventory parser and Python API before allowing success.
if [[ ! -x "$VENV/bin/ansible-playbook" ]]; then
  fail 'ansible-playbook missing from the OPM Python environment'
fi
run_logged as_opm "$VENV/bin/ansible-playbook" --version
if [[ ! -x "$VENV/bin/ansible-inventory" ]]; then
  fail 'ansible-inventory is missing from the OPM Python environment'
fi
run_logged as_opm "$VENV/bin/ansible-inventory" --version
run_logged as_opm "$VENV/bin/python" -c 'import ansible_runner; import ansible'
end_step

start_step 'Applying database migrations and static files'
# Management commands need the existing Django signing key from web.env.
# This file is writable by the unprivileged service owner, so NEVER `source`
# it in a root shell: sourcing it would execute arbitrary shell commands as
# root. Parse the EnvironmentFile as key/value data without eval or expansion.
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  [[ "$line" == *=* ]] || fail 'Invalid web environment entry'
  key=${line%%=*}
  value=${line#*=}
  [[ "$key" =~ ^OPM_WEB_[A-Z0-9_]+$ ]] || fail 'Invalid web environment key'
  # systemd allows quoted values; the installer-generated secret uses no
  # quotes, but accept simple paired quotes from manual configuration.
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value=${value:1:${#value}-2}
  fi
  export "$key=$value"
done < "$ENV_FILE"
run_logged as_opm "$VENV/bin/opm" web migrate --noinput
run_logged as_opm "$VENV/bin/opm" web collectstatic --noinput --clear
end_step

start_step 'Configuring controller and web services'
run_logged install -o "$APP_USER" -g "$APP_USER" -m 0644 \
  "$APP_DIR/systemd/opm-controller.service" "$SERVICE_DIR/opm-controller.service"
run_logged install -o "$APP_USER" -g "$APP_USER" -m 0644 \
  "$APP_DIR/systemd/opm-web.service" "$SERVICE_DIR/opm-web.service"
run_logged as_opm systemctl --user daemon-reload
run_logged as_opm systemctl --user enable --now opm-controller.service
run_logged as_opm systemctl --user enable --now opm-web.service
# Preserve a root-owned copy of the maintenance tool and unit definitions.
# This allows `sudo opm-installer` to diagnose and repair missing service
# units even if the application source directory was damaged or removed.
run_logged install -d -o root -g root -m 0755 /usr/local/share/opm-installer/service-units
for unit in opm-controller.service opm-web.service; do
  run_logged install -o root -g root -m 0644     "$SRC_DIR/systemd/$unit" "/usr/local/share/opm-installer/service-units/$unit"
done
run_logged install -o root -g root -m 0755   "$SRC_DIR/OPMMaintenance.sh" /usr/local/sbin/opm-installer
# Keep the network configurator outside the opm-owned application tree.
# This avoids executing a service-owned script with root permissions later.
run_logged install -o root -g root -m 0644 "$SRC_DIR/OPMWebAccess.py" /usr/local/share/opm-installer/OPMWebAccess.py
run_logged install -o root -g root -m 0644 "$SRC_DIR/OPMTUI.sh" /usr/local/share/opm-installer/OPMTUI.sh
end_step

start_step 'Checking administrator account'
if HAS_ADMIN="$(as_opm "$VENV/bin/python" -c 'import os;os.environ.setdefault("DJANGO_SETTINGS_MODULE","opm.web.settings");import django;django.setup();from django.contrib.auth import get_user_model;print(int(get_user_model().objects.filter(is_superuser=True).exists()))' 2>>"$LOG_FILE")"; then
  :
else
  fail 'Administrator check failed'
fi
if [[ "$HAS_ADMIN" != 1 ]]; then
  # Resume an interrupted installation without falling back to a raw CLI
  # prompt. New installations already collected this in the review wizard.
  if [[ "$OPM_UI_ACTIVE" == 1 && -z "${OPM_UI_ADMIN:-}" ]]; then
    opm_ui_gauge_stop
    while :; do
      if opm_ui_get_administrator; then
        break
      else
        rc=$?
        [[ $rc == 2 ]] && continue
        fail 'Administrator setup was cancelled; create a user before using the Web UI'
      fi
    done
    opm_ui_gauge_start
  fi
  if [[ "$OPM_UI_ACTIVE" == 1 && -n "${OPM_UI_ADMIN:-}" ]]; then
    # Password policy is validated by the same Django validators used by the
    # Web UI. Reject weak passwords WITHIN this step instead of aborting an
    # otherwise successful installation after packages and services exist.
    # Only policy messages may be written to the private error file; submitted
    # credentials always travel through stdin, never argv or log files.
    while :; do
      admin_errors=$(mktemp /tmp/opm-admin-validation.XXXXXXXX) || fail 'Cannot create secure password-validation output'
      chmod 0600 "$admin_errors"
      if printf '%s\n%s\n' "$OPM_UI_ADMIN" "$OPM_UI_ADMIN_PASSWORD" | \
        as_opm "$VENV/bin/python" -c '
import os, sys, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "opm.web.settings")
django.setup()
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError
name = sys.stdin.readline().rstrip("\n")
password = sys.stdin.readline().rstrip("\n")
user_cls = get_user_model()
user = user_cls(username=name)
try:
    validate_password(password, user=user)
    user_cls.objects.create_superuser(username=name, password=password)
except ValidationError as exc:
    print("\n".join(exc.messages))
    sys.exit(2)
except IntegrityError:
    print("That administrator username is already in use.")
    sys.exit(2)
' >"$admin_errors" 2>>"$LOG_FILE"; then
        rm -f -- "$admin_errors"
        unset OPM_UI_ADMIN_PASSWORD
        break
      else
        admin_rc=$?
      fi
      if [[ "$admin_rc" != 2 ]]; then
        rm -f -- "$admin_errors"
        unset OPM_UI_ADMIN_PASSWORD
        fail 'Administrator creation failed unexpectedly; inspect the detailed log'
      fi
      # Django's exact policy errors are safe to show. Never log or print the
      # candidate password. A failed attempt does not touch existing users.
      admin_error_message=$(cat -- "$admin_errors")
      rm -f -- "$admin_errors"
      [[ -n "$admin_error_message" ]] || admin_error_message='Choose a stronger password and try again.'
      printf 'Administrator validation rejected credentials: %s\n' "$admin_error_message" >>"$LOG_FILE"
      opm_ui_gauge_stop
      opm_ui_message ' Administrator account | Retry ' \
        "${admin_error_message}\n\nNothing needs to be reinstalled. Enter new administrator credentials to continue." || true
      while :; do
        if opm_ui_get_administrator; then
          break
        else
          admin_prompt_rc=$?
          [[ "$admin_prompt_rc" == 2 ]] && continue
          # Cancel is intentional; preserve the installed components and log.
          fail 'Administrator setup cancelled by operator; installation can be resumed'
        fi
      done
      opm_ui_gauge_start
    done
  elif [[ -t 0 && -t 1 ]]; then
    # Terminal fallback for an interrupted or resumed installation.
    opm_ui_gauge_stop
    printf '\nInitial administrator setup (password is hidden):\n'
    if ! as_opm "$VENV/bin/opm" web createsuperuser; then
      fail 'Administrator setup was not completed'
    fi
    if [[ "$OPM_UI_ACTIVE" == 1 ]]; then opm_ui_gauge_start; fi
    PREVIOUS_RENDER=0
  else
    # Noninteractive install is permitted, but no default credential is made.
    printf '\nNo administrator exists. Create one interactively using:\n'
    printf '  sudo -u opm %s/bin/opm web createsuperuser\n' "$VENV"
  fi
fi
end_step

start_step 'Verifying OPM controller and web service'
run_logged as_opm "$VENV/bin/opm" ping
run_logged as_opm systemctl --user is-active --quiet opm-web.service
end_step

CURRENT_STEP="OPM v${OPM_RELEASE_VERSION} ${INSTALL_ACTION_RESULT} successfully"
render_progress
if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
  opm_ui_gauge_update 100 "OPM ${INSTALL_ACTION_RESULT} successfully" "$COMPLETED"
  opm_ui_gauge_stop
fi
WEB_BIND_NOW=$(sed -n 's/^OPM_WEB_BIND=//p' "$ENV_FILE" | head -1)
WEB_MODE_NOW=$(sed -n 's/^OPM_WEB_ACCESS_MODE=//p' "$ENV_FILE" | head -1)
WEB_HOST_NOW=$(sed -n 's/^OPM_WEB_ALLOWED_HOSTS=//p' "$ENV_FILE" | head -1)
WEB_ACCESS_URL=$(python3 "$SRC_DIR/OPMAccessURL.py" "${WEB_MODE_NOW:-legacy}" \
  "${WEB_BIND_NOW:-127.0.0.1:8765}" "$WEB_HOST_NOW")
printf '\nOnyx Patch Manager v%s %s successfully.\n' "$OPM_RELEASE_VERSION" "$INSTALL_ACTION_RESULT"
printf 'Open OPM: %s\n' "$WEB_ACCESS_URL"
printf 'Access mode: %s\n' "${WEB_MODE_NOW:-legacy}"
if [[ "$WEB_MODE_NOW" == proxy ]]; then
  printf 'Restrict TCP 8765 to the reverse proxy source IP and protect the HTTPS host with LAN/VPN ACL.\n'
elif [[ "$WEB_MODE_NOW" == lan ]]; then
  printf 'Direct LAN/VPN access uses unencrypted HTTP. Configure a certificate and reverse proxy before routine use.\n'
else
  printf 'To configure LAN/VPN HTTPS access: sudo opm-installer --web-access\n'
fi
printf 'Installer log: %s\n' "$LOG_FILE"
if [[ $PING_WARNING == 1 ]]; then
  printf '\nNotice: ICMP ping is unavailable to the opm service account.\n'
  printf 'Dashboard Connection will show Unknown with the ping error details.\n'
  printf 'Inspect the installer log for kernel/LXC ping permission diagnostics.\n'
fi

if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
  opm_ui_message " ${INSTALL_ACTION_TITLE} complete " "Onyx Patch Manager v${OPM_RELEASE_VERSION} ${INSTALL_ACTION_RESULT} successfully.\n\nOpen OPM: ${WEB_ACCESS_URL}\nAccess mode: ${WEB_MODE_NOW:-localhost}\n\nNext: open the Web UI and sign in as your administrator.\n\nLog: ${LOG_FILE}" || true
fi
