#!/usr/bin/env bash
# OPM maintenance front-end. This script is installed as /usr/local/sbin/opm-installer
# with root ownership. It never obtains commands from the browser or other users.
#
# There are two separate trust domains:
#   * system administration here runs as root and manages systemd/install files;
#   * OPM's controller and web server always run as the unprivileged opm user.
#
# Repair is a diagnose-review-apply workflow. There is deliberately no generic
# chmod/chown -R, database reset, password reset, SSH key overwrite, or automatic
# deletion of remote automation accounts.
set -Eeuo pipefail
IFS=$'\n\t'
APP_USER=opm
APP_HOME=/home/opm
APP_DIR="$APP_HOME/onyx_patch_manager"
APP_DB="$APP_HOME/.local/share/opm/state.db"
VENV="$APP_HOME/opm-venv"
SERVICE_DIR="$APP_HOME/.config/systemd/user"
TRUSTED_UNITS=/usr/local/share/opm-installer/service-units
WEB_ACCESS_TOOL=/usr/local/share/opm-installer/OPMWebAccess.py
TRUSTED_TUI=/usr/local/share/opm-installer/OPMTUI.sh
LOG_DIR=/var/log/opm-installer
SOURCE_DIR=""
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ $EUID == 0 ]] || { echo 'Run the OPM installer with sudo or as root.' >&2; exit 1; }

# A persistent installer can operate without extracted release files. When
# invoked from an archive, --source is supplied and becomes the default for
# Install/Upgrade, but it is never used as a repair source without review.
# A trusted root-owned TUI copy is preferred for persistent maintenance.
# Fresh install media supplies --source and can bootstrap the same library.
load_tui() {
  local ui_path=''
  if [[ -f "$TRUSTED_TUI" && ! -L "$TRUSTED_TUI" ]]; then
    ui_path="$TRUSTED_TUI"
  elif [[ -f "$SOURCE_DIR/OPMTUI.sh" ]]; then
    ui_path="$SOURCE_DIR/OPMTUI.sh"
  elif [[ -f "$SCRIPT_DIR/OPMTUI.sh" ]]; then
    ui_path="$SCRIPT_DIR/OPMTUI.sh"
  fi
  [[ -n "$ui_path" ]] || { echo 'Whiptail interface missing from OPM installer.' >&2; return 1; }
  # shellcheck disable=SC1090
  source "$ui_path"
}

parse_arguments() {
  ACTION=menu
  YES=0
  PURGE=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source) SOURCE_DIR="${2:?Missing source directory}"; shift 2 ;;
      --menu) ACTION=menu; shift ;;
      --install|--upgrade) ACTION=install; shift ;;
      --repair) ACTION=repair; shift ;;
      --status) ACTION=status; shift ;;
      --uninstall) ACTION=uninstall; shift ;;
      --network|--web-access) ACTION=web_access; shift ;;
      --help|-h) ACTION=help; shift ;;
      --yes) YES=1; shift ;;
      --purge-data) PURGE=1; shift ;;
      *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
  done
}

help_text() {
  cat <<'HELP'
Onyx Patch Manager Installation and Maintenance Utility

  sudo opm-installer             Interactive maintenance menu
  sudo opm-installer --status    Read-only service/database diagnostics
  sudo opm-installer --repair    Diagnose, preview and confirm safe repairs
  sudo opm-installer --uninstall Remove application; preserve data by default
  sudo opm-installer --uninstall --purge-data
                                 Remove application, database, keys and local opm account
  sudo opm-installer --web-access Configure LAN/VPN or private HTTPS reverse proxy
  sudo opm-installer --install --source /root/opm-v054/onyx_patch_manager
                                 Install/upgrade from extracted trusted release

Run OPMInstall.sh from an extracted release for the installation wizard.
Use OPMInstall.sh --menu for the maintenance menu; use --install for
explicit scripted upgrades.
Detailed logs: /var/log/opm-installer/

Repair never replaces passwords, SSH keys, database contents or credentials.
Uninstall never removes the remote opm accounts from managed servers.
HELP
}

as_opm() {
  local uid
  uid=$(id -u "$APP_USER")
  runuser -u "$APP_USER" -- env "XDG_RUNTIME_DIR=/run/user/$uid" \
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus" "$@"
}

# Read-only job checks run before ALL disruptive operations. Treat a damaged or
# inaccessible database as unsafe and refuse modifications until reviewed.
no_active_jobs() {
  [[ -f "$APP_DB" ]] || return 0
  python3 - "$APP_DB" <<'PY'
import sqlite3, sys
try:
    db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True, timeout=8)
    tables = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    active = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] if 'jobs' in tables else 0
    if 'activities' in tables:
        active += db.execute("SELECT COUNT(*) FROM activities WHERE state IN ('queued','running')").fetchone()[0]
    db.close()
except (sqlite3.Error, OSError) as e:
    print('Cannot safely inspect job state: ' + str(e), file=sys.stderr)
    sys.exit(2)
if active:
    print(f'Refusing modification: {active} operations still active.', file=sys.stderr)
    sys.exit(1)
PY
}

# Diagnostics are read-only: do not start systemd or change database settings
# merely to find out what is wrong. Repair suggestions are a strict allowlist.
# Tokens are kept in REPAIRS rather than turning arbitrary UI strings into shell.
declare -a REPAIRS=()
check_system() {
  REPAIRS=()
  echo 'OPM system diagnostics'
  echo '----------------------'
  if ! id "$APP_USER" >/dev/null 2>&1; then
    echo 'Service user: MISSING. Reinstall the application before repairing.'
    return 0
  fi
  if [[ -x "$VENV/bin/opm" ]]; then
    local version
    version=$(as_opm "$VENV/bin/python" -c 'import opm; print(opm.__version__)' 2>/dev/null || true)
    echo "Application executable: installed (${version:-version unknown})"
  else
    echo 'Application executable: MISSING (reinstall from release)'
  fi
  # Both Ansible Runner and the ansible-playbook CLI must be available.
  # A working Python import alone does not prove a patch can be executed.
  if [[ -x "$VENV/bin/ansible-playbook" && -x "$VENV/bin/ansible-inventory" ]] &&
       as_opm "$VENV/bin/ansible-playbook" --version >/dev/null 2>&1 &&
       as_opm "$VENV/bin/ansible-inventory" --version >/dev/null 2>&1 &&
       as_opm "$VENV/bin/python" -c 'import ansible_runner; import ansible' >/dev/null 2>&1; then
    echo 'Ansible Core executable: ready'
  elif [[ -x "$VENV/bin/pip" && -f "$APP_DIR/pyproject.toml" ]]; then
    echo 'Ansible Core executable: missing or failing; dependency repair available'
    REPAIRS+=("install:ansible-core")
  else
    echo 'Ansible Core executable: unavailable; full installation required'
  fi
  if [[ "$(loginctl show-user "$APP_USER" -p Linger --value 2>/dev/null || true)" != yes ]]; then
    echo 'Persistent user services: lingering disabled, repair available'
    REPAIRS+=("enable:linger")
  else
    echo 'Persistent user services: enabled'
  fi
  for service in opm-controller.service opm-web.service; do
    if [[ -f "$SERVICE_DIR/$service" ]]; then
      if [[ -f "$TRUSTED_UNITS/$service" ]] && ! cmp -s "$SERVICE_DIR/$service" "$TRUSTED_UNITS/$service"; then
        echo "$service configuration: differs from installed known-good copy, repair available"
        REPAIRS+=("restore:$service")
      else
        echo "$service configuration: present"
      fi
    elif [[ -f "$TRUSTED_UNITS/$service" ]]; then
      echo "$service configuration: missing, repair available"
      REPAIRS+=("restore:$service")
    else
      echo "$service configuration: missing, no trusted copy (reinstall)"
    fi
    if as_opm systemctl --user is-active --quiet "$service" 2>/dev/null; then
      echo "$service: active"
    else
      echo "$service: inactive, repair available"
      if [[ -f "$SERVICE_DIR/$service" || -f "$TRUSTED_UNITS/$service" ]]; then
        REPAIRS+=("start:$service")
      fi
    fi
    if ! as_opm systemctl --user is-enabled --quiet "$service" 2>/dev/null; then
      echo "$service: disabled, repair available"
      if [[ -f "$SERVICE_DIR/$service" || -f "$TRUSTED_UNITS/$service" ]]; then
        REPAIRS+=("enable:$service")
      fi
    fi
  done
  if [[ -f "$APP_DB" ]]; then
    if python3 - "$APP_DB" <<'PY'
import sqlite3, sys
try:
    db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)
    result = db.execute('PRAGMA quick_check').fetchone()[0]
    db.close()
except Exception:
    sys.exit(1)
sys.exit(0 if result == 'ok' else 1)
PY
    then echo 'SQLite integrity: OK'; else echo 'SQLite integrity: FAILED, manual recovery required'; fi
  else
    echo 'SQLite inventory: missing, repair will not create or reset it'
  fi
  if [[ -f "$APP_HOME/.config/opm/web.env" ]]; then
    echo 'Web settings: present'
  else
    echo 'Web settings: missing, repair will not recreate signing secrets'
  fi
  if [[ -x "$VENV/bin/opm" ]] && as_opm "$VENV/bin/opm" ping >/dev/null 2>&1; then
    echo 'Controller RPC: ready'
  else
    echo 'Controller RPC: unavailable'
  fi
}

confirm_yesno() {
  local message="$1" entry
  [[ -t 0 ]] || { echo "Interactive confirmation required: $message" >&2; return 1; }
  if [[ "${OPM_UI_ACTIVE:-0}" == 1 ]]; then
    opm_ui_confirm ' Confirm action ' "$message" Yes No
    return $?
  fi
  printf '%s (Y/n) ' "$message"
  IFS= read -r entry
  entry=${entry:-Y}
  case "${entry^^}" in
    Y|YES) return 0 ;;
    N|NO) return 1 ;;
    *) return 1 ;;
  esac
}

confirm() {
  local message="$1" word="$2" entry
  [[ -t 0 ]] || { echo "Interactive confirmation required: $message" >&2; return 1; }
  if [[ "${OPM_UI_ACTIVE:-0}" == 1 ]]; then
    entry=$(opm_ui_input ' Confirm administrative action ' "$message\n\nType exactly: $word" '') || return 1
    [[ "$entry" == "$word" ]]
    return $?
  fi
  printf '%s\nType %s to continue: ' "$message" "$word"
  IFS= read -r entry
  [[ "$entry" == "$word" ]]
}

# Safe backup covers only files we will actually replace, and never includes
# private-key or password material in console output.
repair_system() {
  check_system
  if [[ ${#REPAIRS[@]} == 0 ]]; then echo 'No supported automatic repairs identified.'; return 0; fi
  echo
  echo 'Proposed repairs:'
  printf '  %s\n' "${REPAIRS[@]}"
  echo 'Only service unit files, enablement and startup will be modified.'
  if [[ $YES != 1 ]]; then
    confirm 'Approve these repairs?' REPAIR || { echo 'Repair cancelled; no changes made.'; return 0; }
  fi
  no_active_jobs || return 1
  local timestamp backup log service fix
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  install -d -m 0700 "$LOG_DIR"
  backup="$LOG_DIR/repair-backup-$timestamp"
  install -d -m 0700 "$backup"
  log="$LOG_DIR/repair-$timestamp.log"
  (umask 077; : > "$log")
  # Start the account's user manager only after approval. Diagnosing did not
  # start it, and changing lingering affects only local OPM service lifetime.
  for fix in "${REPAIRS[@]}"; do
    if [[ "$fix" == 'enable:linger' ]]; then
      loginctl enable-linger "$APP_USER" >>"$log" 2>&1
      systemctl start "user@$(id -u "$APP_USER").service" >>"$log" 2>&1
    fi
  done
  # Dependency repair runs pip as the unprivileged service owner, never
  # root. This avoids granting package setup scripts elevated privileges.
  # The operator has already reviewed and approved this exact repair.
  for fix in "${REPAIRS[@]}"; do
    if [[ "$fix" == 'install:ansible-core' ]]; then
      as_opm "$VENV/bin/pip" install -e "$APP_DIR" >>"$log" 2>&1 || {
        echo "Ansible dependency repair failed. See $log" >&2; return 1; }
      as_opm "$VENV/bin/ansible-playbook" --version >>"$log" 2>&1 || {
        echo "Ansible executable is still unavailable. See $log" >&2; return 1; }
      as_opm "$VENV/bin/ansible-inventory" --version >>"$log" 2>&1 || {
        echo "Ansible inventory parser is still unavailable. See $log" >&2; return 1; }
    fi
  done
  install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$SERVICE_DIR"
  for fix in "${REPAIRS[@]}"; do
    service="${fix#*:}"
    case "$fix" in
      restore:opm-controller.service|restore:opm-web.service)
        [[ -f "$TRUSTED_UNITS/$service" ]] || { echo 'Trusted unit missing, refusing repair.'; return 1; }
        if [[ -e "$SERVICE_DIR/$service" ]]; then
          cp -p -- "$SERVICE_DIR/$service" "$backup/$service"
        fi
        install -o "$APP_USER" -g "$APP_USER" -m 0644 "$TRUSTED_UNITS/$service" "$SERVICE_DIR/$service" >>"$log" 2>&1
        ;;
      start:opm-controller.service|start:opm-web.service|enable:opm-controller.service|enable:opm-web.service|enable:linger|install:ansible-core) : ;;
      *) echo "Unsupported repair action: $fix" >&2; return 1 ;;
    esac
  done
  as_opm systemctl --user daemon-reload >>"$log" 2>&1 || { echo "systemd reload failed. See $log"; return 1; }
  # Controller must start before the web service. Do not restart an already
  # active service unless necessary to apply a restored unit definition.
  for service in opm-controller.service opm-web.service; do
    for fix in "${REPAIRS[@]}"; do
      case "$fix" in
        enable:"$service") as_opm systemctl --user enable "$service" >>"$log" 2>&1 || true ;;
        start:"$service")
          as_opm systemctl --user enable --now "$service" >>"$log" 2>&1 || {
            echo "Could not start $service. See $log" >&2; return 1; }
          ;;
        restore:"$service")
          # Reloaded unit files do not affect an already running process.
          # Restart only the specific service whose unit was changed.
          as_opm systemctl --user enable "$service" >>"$log" 2>&1
          as_opm systemctl --user restart "$service" >>"$log" 2>&1 || {
            echo "Could not restart $service. See $log" >&2; return 1; }
          ;;
      esac
    done
  done
  echo 'Repair actions applied; verifying services and database:'
  check_system
  echo "Repair log: $log"
}

uninstall_system() {
  # A previous failed purge can leave an opm passwd entry but no application
  # directory. Such a state must still offer Complete Uninstall to finish it.
  if [[ ! -d "$APP_DIR" && ! -f "$SERVICE_DIR/opm-web.service" ]] &&
     { [[ $PURGE != 1 ]] || { ! id "$APP_USER" >/dev/null 2>&1 &&
       [[ ! -e "$APP_HOME" && ! -L "$APP_HOME" ]]; }; }; then
    echo 'OPM is not installed.'; return 0
  fi
  echo 'Uninstall stops and removes local OPM services, application files and virtualenv.'
  echo 'Remote managed-server opm accounts will NOT be modified.'
  if [[ $PURGE == 1 ]]; then
    echo 'WARNING: Complete uninstall selected. This deletes inventory, login accounts, SSH keys, history, and the local opm Linux account.'
    confirm 'Permanently delete OPM data, local keys, and the opm account?' 'DELETE OPM DATA' || return 1
    # Never terminate the very SSH/login session that invoked this utility.
    if [[ "${SUDO_USER:-}" == "$APP_USER" ]]; then
      echo 'Complete uninstall must be run from root or a different administrator, not from the opm login.' >&2
      return 1
    fi
  else
    echo 'Inventory, user accounts, SSH keys, configuration, and log files will be PRESERVED.'
  fi
  confirm 'Remove installed OPM application?' UNINSTALL || { echo 'Uninstall cancelled.'; return 0; }
  no_active_jobs || return 1
  if [[ -L "$APP_HOME" || ( -e "$APP_HOME" && ! -d "$APP_HOME" ) ]]; then
    echo 'Refusing to uninstall: /home/opm is a symlink or not a directory.' >&2
    return 1
  fi
  local uid='' account_home='' userdel_rc=0 userdel_output=''
  if id "$APP_USER" >/dev/null 2>&1; then
    account_home=$(getent passwd "$APP_USER" | cut -d: -f6)
    if [[ "$account_home" != "$APP_HOME" ]]; then
      echo 'Refusing to uninstall: opm account home does not match /home/opm.' >&2
      return 1
    fi
    uid=$(id -u "$APP_USER")
    for service in opm-web.service opm-controller.service; do
      as_opm systemctl --user disable --now "$service" >/dev/null 2>&1 || true
      rm -f -- "$SERVICE_DIR/$service"
    done
    as_opm systemctl --user daemon-reload >/dev/null 2>&1 || true
  fi
  if [[ $PURGE == 1 ]]; then
    if [[ -n "$uid" ]]; then
      # Stop the systemd user manager as well as OPM units; otherwise userdel
      # often refuses because the old UID still owns running user processes.
      loginctl disable-linger "$APP_USER" >/dev/null 2>&1 || true
      loginctl terminate-user "$APP_USER" >/dev/null 2>&1 || true
      systemctl stop "user@${uid}.service" >/dev/null 2>&1 || true
      # A missing /var/mail/opm spool may produce a nonfatal userdel
      # diagnostic even if removal succeeds. Capture its stderr, then verify
      # the account state. Never print a benign mail-spool warning on exit.
      userdel_output=$(userdel -r "$APP_USER" 2>&1) || userdel_rc=$?
      if id "$APP_USER" >/dev/null 2>&1; then
        [[ -z "$userdel_output" ]] || printf '%s\n' "$userdel_output" >&2
        echo "Complete uninstall INCOMPLETE: userdel -r opm failed (exit ${userdel_rc})." >&2
        echo 'The opm account still exists. Its home directory has NOT been forcibly removed.' >&2
        echo 'Check for remaining opm processes with: ps -u opm -f' >&2
        return 1
      fi
      # userdel can report a cleanup warning even if account removal succeeds.
      # In that case only, remove the known leftover service-account home.
      if (( userdel_rc != 0 )); then
        echo 'The opm account was removed; nonfatal account cleanup warnings were handled.'
      fi
    fi
    rm -rf -- "$APP_HOME" || { echo 'Failed to remove remaining /home/opm.' >&2; return 1; }
    # Remove only fixed root-owned OPM helpers, never the operator's source ZIP.
    rm -f -- /usr/local/sbin/opm-installer
    rm -rf -- /usr/local/share/opm-installer
    echo 'Complete uninstall finished. The local opm account, home, data and keys have been removed.'
    echo 'Remote managed-server opm accounts were not changed.'
    OPM_UNINSTALL_COMPLETED=1
  else
    rm -rf -- "$APP_DIR" "$VENV"
    echo 'OPM application removed. The local opm Linux account remains for safe ownership and recovery.'
    echo 'OPM private data was preserved.'
    OPM_UNINSTALL_COMPLETED=1
  fi
}

install_release() {
  local source="$SOURCE_DIR" archive extracted
  if [[ -z "$source" ]]; then
    [[ -t 0 ]] || { echo 'Use --source /path/to/release to install noninteractively.' >&2; return 2; }
    if [[ "${OPM_UI_ACTIVE:-0}" == 1 ]]; then
      source=$(opm_ui_input ' Select OPM release ' 'Path to an OPM release ZIP or extracted release directory:' '/root/') || return 3
    else
      echo 'Path to a downloaded OPM release ZIP (or extracted release directory):'
      IFS= read -r source
    fi
  fi
  [[ -n "$source" ]] || { echo 'No release selected.'; return 1; }
  if [[ "$source" == *.zip ]]; then
    extracted=$(mktemp -d /root/opm-release.XXXXXX)
    # Ownership and mode of staging are root-controlled. Never execute a file
    # directly out of a writable shared download folder.
    chmod 0700 "$extracted"
    unzip -q "$source" -d "$extracted" || { rm -rf -- "$extracted"; return 1; }
    archive=$(find "$extracted" -type f -name OPMInstall.sh | head -1)
    [[ -n "$archive" ]] || { rm -rf -- "$extracted"; echo 'Missing installer in archive.'; return 1; }
    source=$(dirname "$archive")
  fi
  [[ -f "$source/OPMInstall.sh" && -f "$source/pyproject.toml" ]] || { echo 'Not an OPM release directory.'; return 1; }
  # Report the version of the selected release, never Debian VERSION_ID.
  local release_version
  release_version=$(python3 - "$source/pyproject.toml" <<'PYVERSION'
import sys, tomllib
with open(sys.argv[1], 'rb') as f:
    print(tomllib.load(f)['project']['version'])
PYVERSION
) || { echo 'Cannot read OPM release version.' >&2; return 1; }
  echo "Selected OPM release: v${release_version} ($source)"
  local installed_version='' action=install question
  if [[ -f "$APP_DIR/pyproject.toml" ]]; then
    installed_version=$(python3 - "$APP_DIR/pyproject.toml" <<'PYINSTALLED'
import re, sys, tomllib
with open(sys.argv[1], 'rb') as f:
    version = tomllib.load(f)['project']['version']
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
    raise SystemExit('Invalid installed OPM version')
print(version)
PYINSTALLED
) || { echo 'Cannot verify the installed OPM version. No files changed.' >&2; return 2; }
    action=$(python3 - "$installed_version" "$release_version" <<'PYCOMPARE'
import sys
old, new = (tuple(map(int, value.split('.'))) for value in sys.argv[1:])
print('downgrade' if new < old else 'upgrade' if new > old else 'reinstall')
PYCOMPARE
) || return 2
  fi
  case "$action" in
    downgrade)
      echo "Refusing downgrade from OPM v${installed_version} to v${release_version}. Select a newer release archive; nothing changed." >&2
      [[ -z "${extracted:-}" ]] || rm -rf -- "$extracted"
      return 2 ;;
    upgrade) question="Upgrade OPM from v${installed_version} to v${release_version}? Inventory, accounts and SSH keys will be preserved." ;;
    reinstall) question="Reinstall OPM v${release_version}? Existing inventory, accounts and SSH keys will be preserved." ;;
    *) question="Install OPM v${release_version}? This will create a new installation." ;;
  esac
  echo "$question"
  if [[ $YES != 1 ]]; then
    confirm_yesno "$question" || { echo 'No changes made.'; [[ -z "${extracted:-}" ]] || rm -rf -- "$extracted"; return 3; }
  fi
  bash "$source/OPMInstall.sh" --install
  local rc=$?
  if [[ -n "${extracted:-}" ]]; then rm -rf -- "$extracted"; fi
  return $rc
}

# Whiptail handles the terminal screen and restores it after each dialog.
# The installer never overwrites the shell scrollback or requires a custom ANSI UI.
configure_web_access() {
  [[ -f "$WEB_ACCESS_TOOL" && ! -L "$WEB_ACCESS_TOOL" ]] || {
    echo 'Trusted web configurator missing. Upgrade OPM first.' >&2; return 1;
  }
  [[ -f "$APP_HOME/.config/opm/web.env" ]] || {
    echo 'OPM web configuration missing. Install OPM first.' >&2; return 1;
  }
  if [[ "${OPM_UI_ACTIVE:-0}" == 1 ]]; then
    OPM_UI_SOURCE="$(dirname "$WEB_ACCESS_TOOL")"
    OPM_UI_MODE='lan'
    OPM_UI_HOST=''; OPM_UI_PROXY_IP=''
    # Preselect the configured mode and restore its address values.
    # A missing/invalid mode falls back to LAN for compatibility.
    opm_ui_access_from_env "$APP_HOME/.config/opm/web.env" || true
    opm_ui_select_access || return 0
    local summary="Mode: ${OPM_UI_MODE}"
    case "$OPM_UI_MODE" in
      lan) summary+="\nBrowser: http://${OPM_UI_HOST}:8765" ;;
      proxy) summary+="\nBrowser: https://${OPM_UI_HOST}\nProxy source: ${OPM_UI_PROXY_IP}" ;;
      localhost) summary+='\nBrowser: http://127.0.0.1:8765' ;;
    esac
    opm_ui_confirm ' Apply web access settings ' "${summary}\n\nChanging this setting restarts only the OPM web service.\nThe current browser session may disconnect." Apply Back || return 0
    python3 "$WEB_ACCESS_TOOL" --configure --mode "$OPM_UI_MODE" \
      --hostname "$OPM_UI_HOST" --proxy-ip "$OPM_UI_PROXY_IP"
  else
    python3 "$WEB_ACCESS_TOOL" --configure
  fi
}

# Capture CLI diagnostic output, then show it in a scrollable Whiptail
# textbox. Maintenance routines themselves remain unchanged and authoritative.
choose_uninstall_mode() {
  if [[ "${OPM_UI_ACTIVE:-0}" != 1 ]]; then
    uninstall_system
    return $?
  fi
  local choice
  choice=$(opm_ui_capture --title ' OPM Uninstall Options '     --ok-button Continue --cancel-button Back     --menu 'Choose how much of the local OPM installation to remove. Remote managed-server opm accounts are never modified.'     18 "$OPM_UI_WIDTH" 3     1 'Standard uninstall (keep database, config, SSH keys, local opm account)'     2 'Complete uninstall (remove database, keys, history, and local opm account)'     3 'Back') || return 0
  case "$choice" in
    1) PURGE=0; uninstall_system ;;
    2) PURGE=1; uninstall_system ;;
    *) return 0 ;;
  esac
}

opm_ui_run_and_show() {
  local heading="$1"; shift
  local output rc=0
  output=$(mktemp /tmp/opm-maintenance.XXXXXXXX) || return 1
  chmod 0600 "$output"
  # Keep stderr on the terminal: newt uses the TTY while its choices are
  # collected. A redirection of stderr would render a blank dialogue.
  "$@" >"$output" || rc=$?
  if [[ "${OPM_UI_ACTIVE:-0}" == 1 ]]; then
    opm_ui_box --title "$heading" --textbox "$output" 21 "$OPM_UI_WIDTH" || true
  else
    cat "$output"
  fi
  rm -f -- "$output"
  return "$rc"
}

menu() {
  [[ -t 0 && -t 1 ]] || {
    echo 'Interactive terminal required. Use --install, --repair or --status.' >&2
    return 2
  }
  opm_ui_bootstrap || return 1
  trap 'opm_ui_terminal_cleanup' EXIT
  local choice rc
  while :; do
    choice=$(opm_ui_capture --title ' OPM Installer & Maintenance ' \
      --ok-button Select --cancel-button Exit \
      --menu 'Select an action. New installs use the guided setup wizard.\nRepairs are diagnosed and reviewed before changes.' \
      19 "$OPM_UI_WIDTH" 7 \
      1 'Upgrade / Install OPM' \
      2 'System Status' \
      3 'Repair Installation' \
      4 'Configure Web Access' \
      5 'Uninstall OPM' \
      6 'Help' \
      7 'Exit') || return 0
    case "$choice" in
      1)
        if install_release; then
          # A completed installation ends the utility; do not redisplay menu.
          return 0
        else
          rc=$?
          [[ $rc == 3 ]] || opm_ui_message ' Installation incomplete ' "Installation stopped (exit $rc). Check the installer log for details."
        fi
        ;;
      2) opm_ui_run_and_show ' OPM System Status ' check_system || true ;;
      3) opm_ui_run_and_show ' Repair diagnosis and result ' repair_system || true ;;
      4) opm_ui_run_and_show ' Web access result ' configure_web_access || true ;;
      5)
        OPM_UNINSTALL_COMPLETED=0
        opm_ui_run_and_show ' Uninstall result ' choose_uninstall_mode || true
        # A completed uninstall closes the utility, not the maintenance menu.
        [[ "${OPM_UNINSTALL_COMPLETED:-0}" == 1 ]] && return 0
        ;; 
      6) opm_ui_run_and_show ' OPM Installer Help ' help_text || true ;;
      7) return 0 ;;
    esac
  done
}

parse_arguments "$@"
if [[ "$ACTION" == menu || "$ACTION" == web_access ]]; then
  load_tui
  # The selected release archive is authoritative for the version shown by
  # the menu even if the installed root-owned TUI predates this release.
  if [[ -n "$SOURCE_DIR" && -r "$SOURCE_DIR/pyproject.toml" ]]; then
    release_menu_version=$(python3 - "$SOURCE_DIR/pyproject.toml" <<'PYVERSION'
import sys, tomllib
with open(sys.argv[1], 'rb') as f:
    print(tomllib.load(f)['project']['version'])
PYVERSION
) || exit 1
    OPM_UI_VERSION="$release_menu_version"
    OPM_UI_BACKTITLE=" ONYX PATCH MANAGER  |  ${OPM_UI_VERSION}  |  INSTALLATION & MAINTENANCE "
  fi
  if [[ "$ACTION" == web_access ]] && opm_ui_available; then
    opm_ui_bootstrap || exit 1
    trap 'opm_ui_terminal_cleanup' EXIT
  fi
fi
case "$ACTION" in
  help) help_text ;;
  status) check_system ;;
  repair) repair_system ;;
  uninstall) uninstall_system ;;
  web_access) configure_web_access ;;
  install) install_release ;;
  menu) menu ;;
esac
