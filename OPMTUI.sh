#!/usr/bin/env bash
# Shared, root-owned-on-install Whiptail presentation layer for OPM.
# Sourced by OPMInstall.sh and OPMMaintenance.sh. No executable install logic
# lives here: it only gathers validated answers and renders native newt dialogs.
# NEWT_COLORS uses 16 terminal colour names; navy/slate/teal are approximations.

OPM_UI_VERSION="0.5.5"
OPM_UI_BACKTITLE=" ONYX PATCH MANAGER  |  ${OPM_UI_VERSION}  |  INSTALLATION & MAINTENANCE "
OPM_UI_WIDTH=76
OPM_UI_ACTIVE=0
OPM_UI_GAUGE_PID=''
OPM_UI_GAUGE_DIR=''
OPM_UI_GAUGE_FD=''

opm_ui_theme() {
  export NEWT_COLORS='root=lightgray,black:border=cyan,black:window=white,black:shadow=black,black:title=lightcyan,black:button=lightgray,blue:actbutton=black,cyan:compactbutton=white,black:checkbox=white,black:actcheckbox=black,cyan:entry=white,blue:disentry=gray,black:label=lightcyan,black:listbox=white,black:actlistbox=black,cyan:sellistbox=white,blue:actsellistbox=black,cyan:textbox=white,black:acttextbox=black,cyan:emptyscale=,blue:fullscale=,cyan:helpline=lightgray,black:roottext=lightgray,black'
}

opm_ui_available() { [[ -t 0 && -t 1 && "${TERM:-dumb}" != dumb ]]; }

# Only the interactive entry points bootstrap whiptail. Noninteractive installs
# never install a UI dependency or require a terminal.
opm_ui_bootstrap() {
  opm_ui_available || return 1
  if ! command -v whiptail >/dev/null 2>&1; then
    [[ $EUID == 0 ]] || { printf 'Whiptail requires root to install.\n' >&2; return 1; }
    printf 'Preparing OPM terminal installer (installing whiptail)...\n'
    DEBIAN_FRONTEND=noninteractive apt-get -qq update &&
      DEBIAN_FRONTEND=noninteractive apt-get -qq install -y whiptail || {
        printf 'Could not install whiptail. Check APT connectivity and retry.\n' >&2
        return 1
      }
  fi
  opm_ui_theme
  OPM_UI_ACTIVE=1
}

opm_ui_box() { whiptail --backtitle "$OPM_UI_BACKTITLE" "$@"; }

opm_ui_message() {
  local title="$1" message="$2"
  message=${message//\\n/$'\n'}
  opm_ui_box --title "$title" --msgbox "$message" 15 "$OPM_UI_WIDTH"
}

opm_ui_confirm() {
  local title="$1" message="$2" yes="${3:-Continue}" no="${4:-Back}"
  message=${message//\\n/$'\n'}
  opm_ui_box --title "$title" --yes-button "$yes" --no-button "$no" \
    --yesno "$message" 17 "$OPM_UI_WIDTH"
}

opm_ui_input() {
  local title="$1" question="$2" initial="${3:-}"
  question=${question//\\n/$'\n'}
  opm_ui_box --title "$title" --ok-button Continue --cancel-button Back \
    --output-fd 1 --inputbox "$question" 13 "$OPM_UI_WIDTH" "$initial"
}

opm_ui_welcome() {
  local art
  art=$(cat <<'BANNER'
  ___  _  _ __   __ __  __
 / _ \| \| |\ \ / / \ \/ /
| (_) | .` | \ V /   >  <
 \___/|_|\_|  |_|   /_/\_\
  ___  _ _____ ___ _  _   __  __   _   _  _   _   ___ ___ ___
 | _ \/_\_   _/ __| || | |  \/  | /_\ | \| | /_\ / __| __| _ \
 |  _/ _ \| || (__| __ | | |\/| |/ _ \| .` |/ _ \ (_ | _||   /
 |_|/_/ \_\_| \___|_||_| |_|  |_/_/ \_\_|\_/_/ \_\___|___|_|_\
BANNER
)
  local description="${art}\n\nAgentless patch management for Debian servers.\n\nThis setup checks the host, configures web access,\ncreates an administrator and installs OPM.\n\nUse Tab / arrow keys / Enter to navigate."
  description=${description//\\n/$'\n'}
  opm_ui_box --title ' Install Onyx Patch Manager ' \
    --yes-button 'Begin Setup' --no-button 'Exit' \
    --yesno "$description" 22 "$OPM_UI_WIDTH"
}

opm_ui_system_check() {
  local disk_kb status
  local dependency
  for dependency in apt-get python3 systemctl; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
      opm_ui_message ' Missing system requirement ' "The required program $dependency was not found. Install it before continuing."
      return 1
    fi
  done
  status='Operating system: Debian 13\nAPT package manager: available\nPython 3: available\nsystemd: available' 
  disk_kb=$(df -Pk / | awk 'NR==2 {print $4}')
  if [[ -z "$disk_kb" || ! "$disk_kb" =~ ^[0-9]+$ || "$disk_kb" -lt 262144 ]]; then
    opm_ui_message ' Insufficient disk space ' 'OPM requires at least 256 MiB of free space for installation. More space is recommended for backups and Python dependencies.'
    return 1
  fi
  opm_ui_confirm ' System requirements ' "${status}\nFree disk space: $((disk_kb / 1024)) MiB\n\nChecks passed. Continue to web configuration?" Continue Exit
}

opm_ui_select_access() {
  local selected access_prompt
  access_prompt=$'Select how OPM will be accessed.\n\nLAN/VPN HTTP is recommended for simple private deployments.\nHTTP is unencrypted: never expose port 8765 publicly.'
  selected=$(opm_ui_box --title ' Step 1 of 4 : Web access ' \
    --ok-button Continue --cancel-button Back --output-fd 1 \
    --radiolist "$access_prompt" \
    19 "$OPM_UI_WIDTH" 3 \
    lan 'LAN / VPN HTTP (Default, no domain required)' ON \
    proxy 'Internal HTTPS Reverse Proxy (certificate required)' OFF \
    localhost 'Localhost only (SSH tunnel)' OFF) || return 1
  OPM_UI_MODE="$selected"
  OPM_UI_HOST=''
  OPM_UI_PROXY_IP=''
  local answer detected
  case "$selected" in
    lan)
      detected=$(python3 - "$OPM_UI_SOURCE" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from OPMWebAccess import detect_lan_ip
print(detect_lan_ip())
PY
) || return 1
      while :; do
        answer=$(opm_ui_input ' LAN/VPN HTTP : Host address ' \
          'Confirm the private IPv4 address of this OPM LXC/VM:\n\nThis will be used for http://IP:8765' "$detected") || return 1
        if python3 - "$OPM_UI_SOURCE" "$answer" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from OPMWebAccess import settings_for
settings_for('lan', sys.argv[2])
PY
        then OPM_UI_HOST="$answer"; break; fi
        opm_ui_message ' Invalid LAN address ' 'Enter a private non-loopback IPv4 address belonging to this host.'
      done
      ;;
    proxy)
      while :; do
        OPM_UI_HOST=$(opm_ui_input ' HTTPS proxy : Hostname ' \
          'Enter your private HTTPS hostname, without https:// or port.\n\nExample: opm.example.internal' '') || return 1
        OPM_UI_PROXY_IP=$(opm_ui_input ' HTTPS proxy : Trusted source ' \
          'Enter the private source IPv4 address of your reverse proxy.\n\nOnly this proxy will be trusted to forward HTTPS headers.' '') || return 1
        if python3 - "$OPM_UI_SOURCE" "$OPM_UI_HOST" "$OPM_UI_PROXY_IP" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from OPMWebAccess import settings_for
settings_for('proxy', sys.argv[2], sys.argv[3])
PY
        then break; fi
        opm_ui_message ' Invalid reverse proxy settings ' 'Enter a valid internal hostname and the private IPv4 address of the reverse proxy.'
      done
      ;;
    localhost) ;;
    *) return 1 ;;
  esac
  return 0
}

opm_ui_get_timezone() {
  local zone answer
  zone="${1:-Etc/UTC}"
  while :; do
    answer=$(opm_ui_input ' Step 2 of 4 : Timezone ' \
      'Select the timezone used by the OPM dashboard.\n\nUse an IANA timezone such as America/Toronto or Etc/UTC.' "$zone") || return 1
    if [[ "$answer" != /* && "$answer" != *..* && -f "/usr/share/zoneinfo/$answer" ]]; then
      OPM_UI_TIMEZONE="$answer"; return 0
    fi
    opm_ui_message ' Invalid timezone ' 'Select an existing IANA timezone (e.g. America/Toronto).'
  done
}

opm_ui_get_administrator() {
  local account password confirm_password
  account=$(opm_ui_input ' Step 3 of 4 : Administrator ' \
    'Choose the first OPM web administrator username.\n\nThe local Linux service account will always be named opm.' 'admin') || return 1
  if [[ ! "$account" =~ ^[A-Za-z][A-Za-z0-9_.@+-]{2,63}$ ]]; then
    opm_ui_message ' Invalid username ' 'Use 3-64 characters, starting with a letter; letters, digits, . _ @ + - are allowed.'
    return 2
  fi
  password=$(opm_ui_box --title ' Step 3 of 4 : Administrator password ' \
    --ok-button Continue --cancel-button Back --output-fd 1 \
    --passwordbox $'Create a strong administrator password (minimum 12 characters).\n\nThe password is never saved to installer logs.' \
    13 "$OPM_UI_WIDTH") || return 1
  if [[ ${#password} -lt 12 ]]; then
    opm_ui_message ' Password too short ' 'Choose a password with at least 12 characters.'
    return 2
  fi
  confirm_password=$(opm_ui_box --title ' Step 3 of 4 : Confirm password ' \
    --ok-button Continue --cancel-button Back --output-fd 1 \
    --passwordbox 'Re-enter the administrator password:' 12 "$OPM_UI_WIDTH") || return 1
  if [[ "$password" != "$confirm_password" ]]; then
    opm_ui_message ' Password mismatch ' 'The two passwords did not match. Please try again.'
    return 2
  fi
  OPM_UI_ADMIN="$account"
  OPM_UI_ADMIN_PASSWORD="$password"
  unset confirm_password password
  return 0
}

opm_ui_review_install() {
  local access
  case "$OPM_UI_MODE" in
    lan) access="LAN/VPN HTTP - http://${OPM_UI_HOST}:8765" ;;
    proxy) access="HTTPS reverse proxy - https://${OPM_UI_HOST}" ;;
    localhost) access='Localhost - http://127.0.0.1:8765' ;;
  esac
  local summary="OPM is ready to install with these settings:\n\n  Operating system: Debian 13\n  Application user: opm (created automatically)\n  Application path: /home/opm/onyx_patch_manager\n  Web access: $access\n  Timezone: $OPM_UI_TIMEZONE\n  Administrator: $OPM_UI_ADMIN\n\nThe installer will install dependencies, create systemd\nuser services and initialize its SQLite database.\n\nProceed with installation?"
  summary=${summary//\\n/$'\n'}
  opm_ui_box --title ' Step 4 of 4 : Review installation ' \
    --yes-button Install --no-button Back --yesno "$summary" 20 "$OPM_UI_WIDTH"
}

# First-run wizard finishes before any package changes or account creation.
# Only the confirmed configuration is applied later by the core installer.
opm_ui_first_install() {
  OPM_UI_SOURCE="$1"
  local guessed="$2" rc
  OPM_UI_MODE='lan'; OPM_UI_HOST=''; OPM_UI_PROXY_IP=''
  OPM_UI_TIMEZONE="$guessed"; OPM_UI_ADMIN=''; OPM_UI_ADMIN_PASSWORD=''
  opm_ui_welcome || return 1
  opm_ui_system_check || return 1
  while :; do
    opm_ui_select_access || return 1
    opm_ui_get_timezone "$OPM_UI_TIMEZONE" || continue
    while :; do
      if opm_ui_get_administrator; then break; else
        rc=$?; [[ $rc == 2 ]] && continue
        # Back from the administrator returns to the web-access step.
        break
      fi
    done
    [[ -n "$OPM_UI_ADMIN" ]] || continue
    if opm_ui_review_install; then return 0; fi
    # Back from review allows mode/timezone/administrator revision.
    OPM_UI_ADMIN=''
    unset OPM_UI_ADMIN_PASSWORD
  done
}

opm_ui_gauge_start() {
  [[ "$OPM_UI_ACTIVE" == 1 ]] || return 0
  OPM_UI_GAUGE_DIR=$(mktemp -d /tmp/opm-gauge.XXXXXXXX) || return 1
  chmod 0700 "$OPM_UI_GAUGE_DIR"
  mkfifo -m 0600 "$OPM_UI_GAUGE_DIR/events"
  whiptail --backtitle "$OPM_UI_BACKTITLE" --title ' Installing Onyx Patch Manager ' \
    --gauge 'Preparing installation...' 11 "$OPM_UI_WIDTH" 0 \
    < "$OPM_UI_GAUGE_DIR/events" &
  OPM_UI_GAUGE_PID=$!
  exec {OPM_UI_GAUGE_FD}>"$OPM_UI_GAUGE_DIR/events"
}

opm_ui_gauge_update() {
  [[ -n "$OPM_UI_GAUGE_FD" ]] || return 0
  local percent="$1" message="$2"
  printf 'XXX\n%s\n%s\nXXX\n' "$percent" "$message" >&"$OPM_UI_GAUGE_FD" || true
}

opm_ui_gauge_stop() {
  [[ -n "$OPM_UI_GAUGE_FD" ]] || return 0
  exec {OPM_UI_GAUGE_FD}>&-
  if [[ -n "$OPM_UI_GAUGE_PID" ]]; then
    wait "$OPM_UI_GAUGE_PID" 2>/dev/null || true
  fi
  rm -rf -- "$OPM_UI_GAUGE_DIR"
  OPM_UI_GAUGE_FD=''; OPM_UI_GAUGE_DIR=''; OPM_UI_GAUGE_PID=''
}

opm_ui_show_text() {
  local title="$1" content="$2" tmp
  tmp=$(mktemp /tmp/opm-tui-output.XXXXXXXX) || return 1
  chmod 0600 "$tmp"
  printf '%s\n' "$content" > "$tmp"
  opm_ui_box --title "$title" --textbox "$tmp" 20 "$OPM_UI_WIDTH" || true
  rm -f -- "$tmp"
}
