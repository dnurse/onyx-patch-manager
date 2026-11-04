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
# A TTY launch opens the complete maintenance menu, as requested.
# Automation explicitly supplies --install and retains the one-line upgrade
# route. The menu is independent from the privileged installation stages.
if [[ "${1:-}" != "--install" ]]; then
  exec bash "$SRC_DIR/OPMMaintenance.sh" --source "$SRC_DIR" "$@"
fi
shift
[[ $# == 0 ]] || { echo 'Unexpected installer argument.' >&2; exit 2; }
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WEB_BIND="127.0.0.1:8765"
VERSION="0.5.6"

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
    opm_ui_gauge_update "$percent" "$CURRENT_STEP"
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
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]] || {
  printf 'This installer supports Debian 13 only.\n' >&2; exit 1;
}
# Whiptail is bootstrapped before setup, not during a patching stage.
if opm_ui_available; then
  opm_ui_bootstrap || { echo 'Cannot start the terminal installer.' >&2; exit 1; }
fi
install -d -m 0700 "$LOG_DIR"
( umask 077; : > "$LOG_FILE" )
chmod 0600 "$LOG_FILE"
printf 'OPM v%s installer | detailed log: %s\n' "$VERSION" "$LOG_FILE"

# First-time interactive configuration is reviewed before system mutation.
# Existing installs skip this wizard and retain all network/account settings.
if [[ "$OPM_UI_ACTIVE" == 1 && ! -e "$ENV_FILE" ]]; then
  INSTALL_TZ_GUESS=$(cat /etc/timezone 2>/dev/null || echo 'Etc/UTC')
  [[ -f "/usr/share/zoneinfo/${INSTALL_TZ_GUESS}" ]] || INSTALL_TZ_GUESS='Etc/UTC'
  if ! opm_ui_first_install "$SRC_DIR" "$INSTALL_TZ_GUESS"; then
    printf 'Installation cancelled. No OPM packages, services or users were changed.\n'
    exit 3
  fi
fi
if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
  opm_ui_gauge_start
  trap 'opm_ui_gauge_stop' EXIT
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
  ca-certificates iputils-ping
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

# A root shell being able to ping is not sufficient: the controller runs
# under the dedicated unprivileged `opm` account. Check the actual service
# identity, and warn without modifying LXC kernel capabilities or sysctls.
# Some Proxmox unprivileged LXCs disallow unprivileged ICMP echo sockets.
PING_WARNING=0
if ! as_opm ping -n -c 1 -W 1 127.0.0.1 >>"$LOG_FILE" 2>&1; then
  PING_WARNING=1
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
    # Credentials go exclusively through stdin to an unprivileged Python
    # process. Never place them on an argv command line or in a log file.
    if ! printf '%s\n%s\n' "$OPM_UI_ADMIN" "$OPM_UI_ADMIN_PASSWORD" | \
      as_opm "$VENV/bin/python" -c '
import os, sys, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "opm.web.settings")
django.setup()
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
name = sys.stdin.readline().rstrip("\n")
password = sys.stdin.readline().rstrip("\n")
user_cls = get_user_model()
user = user_cls(username=name)
validate_password(password, user=user)
user_cls.objects.create_superuser(username=name, password=password)
' >>"$LOG_FILE" 2>&1; then
      unset OPM_UI_ADMIN_PASSWORD
      fail 'Could not create administrator; password may not meet Django policy'
    fi
    unset OPM_UI_ADMIN_PASSWORD
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

CURRENT_STEP="OPM v${VERSION} installation complete"
render_progress
if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
  opm_ui_gauge_update 100 'OPM installation complete'
  opm_ui_gauge_stop
fi
WEB_BIND_NOW=$(sed -n 's/^OPM_WEB_BIND=//p' "$ENV_FILE" | head -1)
WEB_MODE_NOW=$(sed -n 's/^OPM_WEB_ACCESS_MODE=//p' "$ENV_FILE" | head -1)
printf '\nWeb listener: %s (mode: %s)\n' "${WEB_BIND_NOW:-127.0.0.1:8765}" "${WEB_MODE_NOW:-legacy}"
if [[ "$WEB_MODE_NOW" == proxy ]]; then
  WEB_HOST_NOW=$(sed -n 's/^OPM_WEB_ALLOWED_HOSTS=//p' "$ENV_FILE" | cut -d, -f1 | head -1)
  printf 'Open through private HTTPS reverse proxy: https://%s\n' "$WEB_HOST_NOW"
  printf 'Restrict TCP 8765 to the reverse proxy source IP and protect the HTTPS host with LAN/VPN ACL.\n'
elif [[ "$WEB_MODE_NOW" == lan ]]; then
  printf 'Direct LAN/VPN access uses unencrypted HTTP. Configure a certificate and reverse proxy before routine use.\n'
else
  printf 'To configure mobile/LAN HTTPS access: sudo opm-installer --web-access\n'
fi
printf 'Installer log: %s\n' "$LOG_FILE"
if [[ $PING_WARNING == 1 ]]; then
  printf '\nNotice: ICMP ping is unavailable to the opm service account.\n'
  printf 'Dashboard Connection will show Unknown with the ping error details.\n'
  printf 'See RELEASE_NOTES.md for Debian 13 LXC ping permission diagnostics.\n'
fi

if [[ "$OPM_UI_ACTIVE" == 1 ]]; then
  opm_ui_message ' Installation complete ' "Onyx Patch Manager v${VERSION} installed successfully.\n\nWeb listener: ${WEB_BIND_NOW:-127.0.0.1:8765}\nAccess mode: ${WEB_MODE_NOW:-localhost}\n\nNext: open the Web UI and sign in as your administrator.\n\nLog: ${LOG_FILE}" || true
fi
