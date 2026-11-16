#!/usr/bin/env bash
# Sets up the UniFi monitoring stack: asks a few questions, writes every .env file and the Dozzle login,
# then starts the containers.
#
#   ./install.sh             set up (or change settings) and start the stack
#   ./install.sh --no-start  write the settings but don't start anything
#   ./install.sh --zones     only refresh the firewall zone names from UniFi (after a UniFi upgrade or a new zone)
#   ./install.sh --cert      only save the UniFi console's certificate again (after it makes a new one)
#
# Safe to run again: it reads your current .env files and offers their values as the defaults, so pressing
# Enter keeps what you have. Settings it doesn't ask about (and any edits you made by hand) are left alone.

set -euo pipefail

cd "$(dirname "$0")"

START=1
ZONES_ONLY=0
CERT_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-start) START=0 ;;
    --zones) ZONES_ONLY=1 ;;
    --cert) CERT_ONLY=1 ;;
    -h | --help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

SERVICES="unpoller grafana prometheus loki reporter dozzle syslog"
PY_IMAGE=python:3.12-slim # only for hashing the Dozzle password (bcrypt via Python's crypt module)
WRITTEN=()

# ---------------------------------------------------------------- output

if [ -t 1 ]; then
  B=$'\e[1m' D=$'\e[2m' R=$'\e[31m' Y=$'\e[33m' G=$'\e[32m' N=$'\e[0m'
else
  B='' D='' R='' Y='' G='' N=''
fi
step() { printf '\n%s%s%s\n' "$B" "$1" "$N"; }
note() { printf '%s%s%s\n' "$D" "$1" "$N"; }
warn() { printf '%sWarning:%s %s\n' "$Y" "$N" "$1" >&2; }
die()  { printf '%sError:%s %s\n' "$R" "$N" "$1" >&2; exit 1; }

# ---------------------------------------------------------------- prompts

# ask VAR "Question" "default"  (Enter takes the default)
ask() {
  local reply
  if [ -n "$3" ]; then
    read -r -p "$2 [$3]: " reply || die "Input ended before setup finished."
    reply=${reply:-$3}
  else
    read -r -p "$2: " reply || die "Input ended before setup finished."
  fi
  printf -v "$1" '%s' "$reply"
}

# ask_required VAR "Question" "default"
ask_required() {
  while :; do
    ask "$1" "$2" "$3"
    [ -n "${!1}" ] && return
    echo "  This one is needed."
  done
}

# yes_no "Question" y|n  -> exit status 0 for yes
yes_no() {
  local reply hint="y/N"
  [ "$2" = y ] && hint="Y/n"
  while :; do
    read -r -p "$1 [$hint]: " reply || die "Input ended before setup finished."
    case "${reply:-$2}" in
      [Yy] | [Yy][Ee][Ss]) return 0 ;;
      [Nn] | [Nn][Oo]) return 1 ;;
    esac
  done
}

random_password() {
  head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c 1-20
}

# ask_secret VAR "Question" mode
#   mode keep:     Enter keeps the current value (VAR already holds it)
#   mode generate: Enter makes a random password
#   mode optional: Enter leaves it empty
ask_secret() {
  local first second hint
  case "$3" in
    keep) hint="Enter keeps the current one" ;;
    generate) hint="Enter makes a random one" ;;
    optional) hint="Enter to skip" ;;
  esac
  while :; do
    read -r -s -p "$2 ($hint): " first || die "Input ended before setup finished."
    echo
    if [ -z "$first" ]; then
      case "$3" in
        generate) printf -v "$1" '%s' "$(random_password)"; GENERATED="$GENERATED $1 "; return ;;
        optional) printf -v "$1" '%s' ""; return ;;
        keep) return ;;
      esac
    fi
    if [[ "$first" == *"'"* ]]; then
      echo "  Please leave out single quotes (')."
      continue
    fi
    read -r -s -p "  Type it again: " second || die "Input ended before setup finished."
    echo
    if [ "$first" = "$second" ]; then
      printf -v "$1" '%s' "$first"
      return
    fi
    echo "  They didn't match. Try again."
  done
}

# ---------------------------------------------------------------- .env files

# start a service's .env from its .env.example the first time
ensure_env() {
  local f="$1/.env"
  if [ ! -f "$f" ]; then
    cp "$1/.env.example" "$f"
  fi
  chmod 600 "$f"
}

# get_env FILE KEY -> the value, without quotes or a trailing comment; empty if missing or commented out
get_env() {
  local line
  [ -f "$1" ] || return 0
  line=$(grep -m 1 "^$2=" "$1" || true)
  line=${line#*=}
  case "$line" in
    \'*) line=${line#\'}; line=${line%%\'*} ;;
    \"*) line=${line#\"}; line=${line%%\"*} ;;
    *) line=${line%%#*}; line="${line%"${line##*[![:space:]]}"}" ;;
  esac
  printf '%s' "$line"
}

# quote a value for a Compose env file: plain if safe, otherwise single quotes (taken literally)
env_quote() {
  if [[ "$1" =~ ^[A-Za-z0-9_./:@,+=-]*$ ]]; then
    printf '%s' "$1"
  else
    printf "'%s'" "$1"
  fi
}

# rewrite_line FILE KEY NEWLINE: replace the first "KEY=..." or "# KEY=..." line, or append one
rewrite_line() {
  local tmp
  tmp=$(mktemp)
  K="$2" L="$3" awk '
    BEGIN { k = ENVIRON["K"]; l = ENVIRON["L"] }
    !done && (index($0, k "=") == 1 || index($0, "# " k "=") == 1) { print l; done = 1; next }
    { print }
    END { if (!done) print l }
  ' "$1" > "$tmp"
  cat "$tmp" > "$1"   # keeps the file's permissions
  rm -f "$tmp"
}

set_env()   { rewrite_line "$1" "$2" "$2=$(env_quote "$3")"; }
unset_env() { rewrite_line "$1" "$2" "# $2="; }

# containers keep running as the user their data belongs to; a new install uses whoever runs this (not root with sudo)
RUN_UID=$(get_env .env UID) RUN_GID=$(get_env .env GID)
if [ -z "$RUN_UID" ] && [ -f grafana/data/grafana.db ]; then # installs from before .env had UID
  RUN_UID=$(stat -c %u grafana/data/grafana.db) RUN_GID=$(stat -c %g grafana/data/grafana.db)
fi
RUN_UID=${RUN_UID:-${SUDO_UID:-$(id -u)}}
RUN_GID=${RUN_GID:-${SUDO_GID:-$(id -g)}}

# ---------------------------------------------------------------- firewall zone names

ZONE_FILE=prometheus/config/rules/zone-names.yml

# ---------------------------------------------------------------- UniFi console certificate

# UniFi consoles use a self-signed certificate that a normal check rejects, so UnPoller and this script
# accept exactly the one saved here instead. Anyone posing as the console on the network is turned away.
CTRL_CERT=unpoller/certs/controller.pem
CTRL_CERT_IN_CONTAINER=/etc/unpoller/certs/controller.pem
CURL_PIN=()

# fetch_cert URL FILE -> the certificate the server at URL presents, saved to FILE; 1 if it couldn't
fetch_cert() {
  local hostport=${1#https://}
  hostport=${hostport%%/*}
  case "$hostport" in *:*) ;; *) hostport="$hostport:443" ;; esac
  openssl s_client -connect "$hostport" -servername "${hostport%:*}" < /dev/null 2> /dev/null \
    | openssl x509 -outform PEM > "$2" 2> /dev/null && [ -s "$2" ]
}
fingerprint() { openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2; }

# use_pin: curl calls to the controller accept only the saved certificate
use_pin() {
  local pin
  CURL_PIN=()
  [ -s "$CTRL_CERT" ] || return 0
  pin=$(openssl x509 -in "$CTRL_CERT" -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64)
  CURL_PIN=(--pinnedpubkey "sha256//$pin")
}

# save_controller_cert URL -> 0 when the certificate is saved (or was already), 1 when it isn't
save_controller_cert() {
  local tmp fp
  case "$1" in https://*) ;; *) note "The controller address isn't https://, so there's no certificate to check."; return 1 ;; esac
  command -v openssl > /dev/null || { warn "openssl isn't installed, so the UniFi certificate isn't checked. Install it and run ./install.sh --cert"; return 1; }
  tmp=$(mktemp)
  if ! fetch_cert "$1" "$tmp"; then
    rm -f "$tmp"
    warn "Couldn't read the certificate from $1. Run ./install.sh --cert when it can be reached."
    return 1
  fi
  fp=$(fingerprint "$tmp")
  if [ -s "$CTRL_CERT" ] && [ "$(fingerprint "$CTRL_CERT")" = "$fp" ]; then
    rm -f "$tmp"
    note "The UniFi certificate matches the one saved before."
    return 0
  fi
  if [ -s "$CTRL_CERT" ]; then warn "The UniFi console now shows a different certificate than the one saved before."; fi
  note "The UniFi console's certificate fingerprint (SHA-256) is:"
  echo "  $fp"
  note "Check it matches before trusting it: open $1 in a browser and view the certificate,"
  note "or on the console over SSH: openssl x509 -noout -fingerprint -sha256 -in /data/unifi-core/config/unifi-core.crt"
  if yes_no "Does it match? Trust this certificate?" y; then
    cat "$tmp" > "$CTRL_CERT"
    rm -f "$tmp"
    WRITTEN+=("$CTRL_CERT")
    return 0
  fi
  rm -f "$tmp"
  warn "Not saved. Run ./install.sh --cert once you've checked it."
  return 1
}

# write_zone_names URL SITE KEY USER PASS -> writes ZONE_FILE from the controller's zones; 1 if it couldn't.
# UnPoller's policies name their zones only by ID and its zone metric has only the name, so Prometheus
# publishes the pairs from this file. UniFi gives zones new IDs on some upgrades, hence --zones.
write_zone_names() {
  local url="$1" site="$2" key="$3" user="$4" pass="$5" json="" jar base login obj id name n=0 tmp
  command -v curl > /dev/null || return 1
  pass=${pass//\\/\\\\}
  pass=${pass//\"/\\\"}
  for base in "$url/proxy/network" "$url"; do # UniFi OS consoles, then self-hosted controllers
    if [ -n "$key" ]; then
      json=$(curl -sk ${CURL_PIN[@]+"${CURL_PIN[@]}"} --max-time 20 -H "X-API-KEY: $key" "$base/v2/api/site/$site/firewall/zone" || true)
      case "$json" in '[{'*'"_id":"'*) break ;; esac
    fi
    if [ -n "$user" ]; then
      jar=$(mktemp)
      login="$url/api/auth/login"
      [ "$base" = "$url" ] && login="$url/api/login"
      # the password goes in on stdin so it never shows in the process list
      printf '{"username":"%s","password":"%s"}' "$user" "$pass" \
        | curl -sk ${CURL_PIN[@]+"${CURL_PIN[@]}"} --max-time 20 -o /dev/null -c "$jar" -H 'Content-Type: application/json' -d @- "$login" || true
      json=$(curl -sk ${CURL_PIN[@]+"${CURL_PIN[@]}"} --max-time 20 -b "$jar" "$base/v2/api/site/$site/firewall/zone" || true)
      rm -f "$jar"
      case "$json" in '[{'*'"_id":"'*) break ;; esac
    fi
    json=""
  done
  [ -n "$json" ] || return 1

  tmp=$(mktemp)
  {
    echo "# Firewall zone names, written by ./install.sh from the UniFi controller. Run ./install.sh --zones to"
    echo "# refresh it after a UniFi upgrade or when you add a zone; edits made here are overwritten."
    echo "groups:"
    echo "  - name: unifi-zone-names"
    echo "    rules:"
  } > "$tmp"
  json=${json//\},\{/$'\n'}
  while IFS= read -r obj; do
    id=$(printf '%s' "$obj" | sed -n 's/.*"_id":"\([^"]*\)".*/\1/p')
    name=$(printf '%s' "$obj" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | tr -d '\134') # no backslashes in the YAML
    if [ -z "$id" ] || [ -z "$name" ]; then continue; fi
    printf '      - record: unifi_firewall_zone_name\n        expr: vector(1)\n        labels: {zone_id: "%s", zone_name: "%s"}\n' "$id" "$name" >> "$tmp"
    n=$((n + 1))
  done << EOF
$json
EOF
  if [ "$n" = 0 ]; then
    rm -f "$tmp"
    return 1
  fi
  cat "$tmp" > "$ZONE_FILE"
  rm -f "$tmp"
  ZONE_COUNT=$n
}

if [ "$ZONES_ONLY" = 1 ]; then
  U=unpoller/.env
  [ -f "$U" ] || die "No settings yet. Run ./install.sh first."
  step "Firewall zone names"
  use_pin
  if write_zone_names "$(get_env "$U" UP_UNIFI_CONTROLLER_0_URL)" "$(get_env "$U" UP_UNIFI_CONTROLLER_0_SITE)" \
    "$(get_env "$U" UP_UNIFI_CONTROLLER_0_API_KEY)" "$(get_env "$U" UP_UNIFI_CONTROLLER_0_USER)" \
    "$(get_env "$U" UP_UNIFI_CONTROLLER_0_PASS)"; then
    if [ "$(id -u)" = 0 ]; then chown "$RUN_UID:$RUN_GID" "$ZONE_FILE"; fi
    note "  wrote $ZONE_FILE ($ZONE_COUNT zones)"
    # Signal from inside the container: `docker compose kill -s HUP` makes Docker count it as stopped by hand,
    # so it stays down after the next reboot.
    if docker compose exec -T prometheus kill -HUP 1 > /dev/null 2>&1; then
      note "Prometheus reloaded it. The Firewall dashboard shows the names within a minute."
    else
      note "Prometheus isn't running; it reads the file when it starts."
    fi
    exit 0
  fi
  die "Couldn't read the firewall zones from UniFi. Check the controller address and login in $U (zones need UniFi Network 9 or newer with zone-based firewall)."
fi

# pin_unpoller ENV_FILE: point UnPoller at the saved certificate (or drop the setting when there isn't one)
pin_unpoller() {
  # a list in UnPoller's settings: one numbered variable per file
  if [ -s "$CTRL_CERT" ]; then
    set_env "$1" UP_UNIFI_CONTROLLER_0_SSL_CERT_PATH_0 "$CTRL_CERT_IN_CONTAINER"
  else
    unset_env "$1" UP_UNIFI_CONTROLLER_0_SSL_CERT_PATH_0
  fi
}

if [ "$CERT_ONLY" = 1 ]; then
  U=unpoller/.env
  [ -f "$U" ] || die "No settings yet. Run ./install.sh first."
  step "UniFi console certificate"
  save_controller_cert "$(get_env "$U" UP_UNIFI_CONTROLLER_0_URL)" || exit 1
  pin_unpoller "$U"
  if [ "$(id -u)" = 0 ]; then chown "$RUN_UID:$RUN_GID" "$CTRL_CERT"; fi
  if docker compose up -d --force-recreate unpoller > /dev/null 2>&1; then
    note "UnPoller restarted with it."
  else
    note "UnPoller isn't running; it uses the certificate when it starts."
  fi
  exit 0
fi

# ---------------------------------------------------------------- checks

step "Checking this machine"

command -v docker > /dev/null || die "Docker isn't installed. Install Docker Engine first: https://docs.docker.com/engine/install/"
docker compose version > /dev/null 2>&1 || die "The Docker Compose plugin is missing ('docker compose'). Install it, then run this again."
command -v openssl > /dev/null || die "openssl isn't installed. It makes the HTTPS certificate and checks the UniFi one: sudo apt install openssl"
docker info > /dev/null 2>&1 || die "Docker is installed but this user can't use it. Start Docker, or add yourself to the docker group (sudo usermod -aG docker \$USER, then log out and in), or run: sudo ./install.sh"

note "Docker is ready. Containers will run as user $RUN_UID, group $RUN_GID."

first_run=1
for s in $SERVICES; do [ -f "$s/.env" ] && first_run=0; done
if [ "$first_run" = 0 ]; then
  note "Found existing settings. Press Enter at any question to keep the current value."
fi

for s in $SERVICES; do ensure_env "$s"; done
GENERATED=" "

# ---------------------------------------------------------------- time zone

step "Time zone"

detect_tz() {
  local tz=""
  tz=$(timedatectl show -p Timezone --value 2> /dev/null || true)
  [ -z "$tz" ] && [ -f /etc/timezone ] && tz=$(head -n 1 /etc/timezone)
  [ -z "$tz" ] && [ -L /etc/localtime ] && tz=$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')
  printf '%s' "${tz:-UTC}"
}

current_tz=$(get_env unpoller/.env TZ)
if [ "$first_run" = 1 ] || [ -z "$current_tz" ]; then current_tz=$(detect_tz); fi
note "Used for dashboards, logs and when reports are sent, e.g. Europe/London or America/New_York."
while :; do
  ask_required TZ_VALUE "Time zone" "$current_tz"
  if [ ! -d /usr/share/zoneinfo ] || [ -f "/usr/share/zoneinfo/$TZ_VALUE" ]; then break; fi
  echo "  '$TZ_VALUE' isn't a known time zone. Use a name like Europe/London."
done

# ---------------------------------------------------------------- UniFi

step "UniFi controller"
note "UnPoller reads metrics and the System Log from your UniFi console."

U=unpoller/.env
cur_url=$(get_env "$U" UP_UNIFI_CONTROLLER_0_URL)
[ "$first_run" = 1 ] && cur_url=""
while :; do
  ask_required UNIFI_URL "Controller address, e.g. https://192.168.1.1" "$cur_url"
  case "$UNIFI_URL" in
    http://* | https://*) break ;;
    *) UNIFI_URL="https://$UNIFI_URL"; echo "  Using $UNIFI_URL"; break ;;
  esac
done
UNIFI_URL=${UNIFI_URL%/}

if command -v curl > /dev/null; then
  if curl -sk -o /dev/null --max-time 5 "$UNIFI_URL"; then
    note "Reached $UNIFI_URL."
  else
    warn "Couldn't reach $UNIFI_URL from this machine. Check the address; UnPoller needs to reach it from here."
  fi
fi

ask_required UNIFI_SITE "Site name (usually 'default')" "$(get_env "$U" UP_UNIFI_CONTROLLER_0_SITE)"

note ""
note "An API key is needed for the firewall zone panels. Create one in UniFi Network under"
note "Settings > Control Plane > Integrations. With a key, a username and password are optional."
UNIFI_KEY=$(get_env "$U" UP_UNIFI_CONTROLLER_0_API_KEY)
if [ -n "$UNIFI_KEY" ]; then
  ask_secret UNIFI_KEY "API key" keep
else
  ask_secret UNIFI_KEY "API key" optional
fi

note ""
note "A local UniFi account with read-only access is recommended for UnPoller."
cur_user=$(get_env "$U" UP_UNIFI_CONTROLLER_0_USER)
UNIFI_PASS=$(get_env "$U" UP_UNIFI_CONTROLLER_0_PASS)
if [ "$first_run" = 1 ]; then cur_user="" UNIFI_PASS=""; fi
if [ -n "$UNIFI_KEY" ]; then
  ask UNIFI_USER "Username (Enter to skip)" "$cur_user"
else
  ask_required UNIFI_USER "Username" "${cur_user:-unpoller}"
fi
if [ -n "$UNIFI_USER" ]; then
  if [ -n "$UNIFI_PASS" ]; then
    ask_secret UNIFI_PASS "Password for $UNIFI_USER" keep
  else
    while [ -z "$UNIFI_PASS" ]; do ask_secret UNIFI_PASS "Password for $UNIFI_USER" optional; done
  fi
fi

note ""
save_controller_cert "$UNIFI_URL" || true
use_pin

if [ "$(get_env "$U" UP_UNIFI_CONTROLLER_0_SAVE_EVENTS)" = true ]; then new_net=n; else new_net=y; fi
if yes_no "Is UniFi Network version 10 or newer?" "$new_net"; then legacy=false; else legacy=true; fi

# ---------------------------------------------------------------- Grafana

step "Grafana"

G_ENV=grafana/.env
GRAFANA_USER=$(get_env "$G_ENV" GF_SECURITY_ADMIN_USER)
GRAFANA_PASS=""
RESET_GRAFANA=0
if [ -f grafana/data/grafana.db ]; then
  note "Grafana is already set up, so its admin account lives in Grafana now, not in the .env file."
  if yes_no "Reset the password for the Grafana user '$GRAFANA_USER'?" n; then
    ask_secret GRAFANA_PASS "New Grafana password" generate
    RESET_GRAFANA=1
  fi
else
  ask_required GRAFANA_USER "Admin username" "${GRAFANA_USER:-admin}"
  cur=$(get_env "$G_ENV" GF_SECURITY_ADMIN_PASSWORD)
  if [ "$first_run" = 0 ] && [ -n "$cur" ] && [ "$cur" != admin123 ]; then
    GRAFANA_PASS=$cur
    ask_secret GRAFANA_PASS "Admin password" keep
  else
    ask_secret GRAFANA_PASS "Admin password" generate
  fi
fi

# ---------------------------------------------------------------- Dozzle

step "Dozzle (container log viewer)"

DOZZLE_USER="" DOZZLE_PASS="" DOZZLE=0
note "Dozzle shows every container's logs in the browser. It needs full access to Docker, which means"
note "control of this server, so it's off unless you want it."
if [ -s dozzle/data/users.yml ]; then dz=y; else dz=n; fi
if yes_no "Run Dozzle?" "$dz"; then
  DOZZLE=1
  if [ -s dozzle/data/users.yml ] && yes_no "Keep the existing Dozzle login?" y; then
    :
  else
    ask_required DOZZLE_USER "Dozzle username" "${GRAFANA_USER:-admin}"
    ask_secret DOZZLE_PASS "Dozzle password" generate
  fi
fi

# ---------------------------------------------------------------- firewall log over syslog

step "UniFi firewall log (syslog)"

SYSLOG=0 SYSLOG_FROM=""
note "UniFi Network 11 no longer puts blocks of internet traffic in its System Log. Its firewall log, sent over"
note "syslog, still has them (with ports). A small receiver takes it on UDP port 5514 and keeps only what the"
note "UniFi console sends. UniFi has to be told to send it; the summary at the end says where."
case ",$(get_env .env COMPOSE_PROFILES)," in *,syslog,*) sl=y ;; *) sl=n ;; esac
if yes_no "Receive UniFi's firewall log over syslog?" "$sl"; then
  SYSLOG=1
  ctrl=${UNIFI_URL#*://}
  ctrl=${ctrl%%/*}
  case "$ctrl" in \[*) ctrl=${ctrl#[}; ctrl=${ctrl%%]*} ;; *:*) ctrl=${ctrl%:*} ;; esac
  cur=$(get_env syslog/.env SYSLOG_FROM)
  cur=${cur//\\/}
  cur=${cur//|/ }
  [ "$cur" = "192.168.1.1" ] && cur="" # the example value
  note "The console's address as this server sees it, usually the one in the controller address. If your console"
  note "sends from another of its addresses, add it (separate several with spaces)."
  while :; do
    ask_required SYSLOG_IPS "UniFi console address(es)" "${cur:-$ctrl}"
    bad=""
    for ip in $SYSLOG_IPS; do
      [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ || "$ip" =~ ^[0-9A-Fa-f:]+$ ]] || bad="$bad $ip"
    done
    [ -z "$bad" ] && break
    warn "Not an IP address:$bad. Use the console's IP, not a name."
  done
  for ip in $SYSLOG_IPS; do SYSLOG_FROM="${SYSLOG_FROM:+$SYSLOG_FROM|}${ip//./\\.}"; done
fi

# ---------------------------------------------------------------- Reporter

step "Reports"

R_ENV=reporter/.env
host_ip() {
  local ip=""
  ip=$(ip -4 route get 1.1.1.1 2> /dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}' || true)
  [ -z "$ip" ] && ip=$(hostname -I 2> /dev/null | awk '{print $1}' || true)
  printf '%s' "${ip:-localhost}"
}
HOST_IP=$(host_ip)

cur=$(get_env "$R_ENV" GRAFANA_PUBLIC_URL)
case "$cur" in http://*) cur="https://${cur#http://}" ;; esac # Grafana is served over HTTPS now
note "The Grafana address people open from their own computers. Report PDFs link to it."
ask_required GRAFANA_URL "Grafana address" "${cur:-https://$HOST_IP:3000}"
GRAFANA_URL=${GRAFANA_URL%/}
ask REPORT_NAME "Name on the reports, e.g. your company (Enter for 'Reports')" "$(get_env "$R_ENV" REPORT_NAME)"

SMTP=0
note "Email can also be set up later in the reports portal, under Settings."
if yes_no "Set up email for reports now?" n; then
  SMTP=1
  ask_required SMTP_HOST "SMTP server" "$(get_env "$R_ENV" SMTP_HOST)"
  ask_required SMTP_PORT "SMTP port" "$(get_env "$R_ENV" SMTP_PORT)"
  ask SMTP_USER "SMTP username (Enter if none)" "$(get_env "$R_ENV" SMTP_USER)"
  SMTP_PASSWORD=$(get_env "$R_ENV" SMTP_PASSWORD)
  if [ -n "$SMTP_USER" ]; then
    if [ -n "$SMTP_PASSWORD" ]; then
      ask_secret SMTP_PASSWORD "SMTP password" keep
    else
      ask_secret SMTP_PASSWORD "SMTP password" optional
    fi
  fi
  ask_required SMTP_FROM "Send from (email address)" "$(get_env "$R_ENV" SMTP_FROM)"
  ask_required REPORT_TO "Send to (comma-separated addresses)" "$(get_env "$R_ENV" REPORT_TO)"
fi

# ---------------------------------------------------------------- write

step "Saving settings"

# Compose reads only this file (next to compose.yaml) for ${UID} and ${GID}; the user: lines in compose.yaml use them.
[ -f .env ] || : > .env
chmod 600 .env
set_env .env UID "$RUN_UID"
set_env .env GID "$RUN_GID"
PROFILES=""
[ "$DOZZLE" = 1 ] && PROFILES="dozzle"
[ "$SYSLOG" = 1 ] && PROFILES="${PROFILES:+$PROFILES,}syslog"
if [ -n "$PROFILES" ]; then set_env .env COMPOSE_PROFILES "$PROFILES"; else unset_env .env COMPOSE_PROFILES; fi
WRITTEN+=(.env)

for s in $SERVICES; do
  set_env "$s/.env" TZ "$TZ_VALUE"
  WRITTEN+=("$s/.env")
done
for s in grafana prometheus loki; do
  set_env "$s/.env" UID "$RUN_UID"
  set_env "$s/.env" GID "$RUN_GID"
done

set_env "$U" UP_UNIFI_CONTROLLER_0_URL "$UNIFI_URL"
set_env "$U" UP_UNIFI_CONTROLLER_0_SITE "$UNIFI_SITE"
if [ "$SYSLOG" = 1 ]; then
  set_env syslog/.env SYSLOG_FROM "$SYSLOG_FROM"
  set_env syslog/.env SYSLOG_SOURCE "$UNIFI_URL"
fi
if [ -n "$UNIFI_KEY" ]; then set_env "$U" UP_UNIFI_CONTROLLER_0_API_KEY "$UNIFI_KEY"; else unset_env "$U" UP_UNIFI_CONTROLLER_0_API_KEY; fi
if [ -n "$UNIFI_USER" ]; then
  set_env "$U" UP_UNIFI_CONTROLLER_0_USER "$UNIFI_USER"
  set_env "$U" UP_UNIFI_CONTROLLER_0_PASS "$UNIFI_PASS"
else
  unset_env "$U" UP_UNIFI_CONTROLLER_0_USER
  unset_env "$U" UP_UNIFI_CONTROLLER_0_PASS
fi
for k in SAVE_ALARMS SAVE_EVENTS SAVE_IDS; do set_env "$U" "UP_UNIFI_CONTROLLER_0_$k" "$legacy"; done
pin_unpoller "$U"

if [ "$RESET_GRAFANA" = 0 ] && [ ! -f grafana/data/grafana.db ]; then
  set_env "$G_ENV" GF_SECURITY_ADMIN_USER "$GRAFANA_USER"
  set_env "$G_ENV" GF_SECURITY_ADMIN_PASSWORD "$GRAFANA_PASS"
fi

set_env "$R_ENV" GRAFANA_PUBLIC_URL "$GRAFANA_URL"
set_env "$R_ENV" REPORT_NAME "$REPORT_NAME"
if [ "$SMTP" = 1 ]; then
  set_env "$R_ENV" SMTP_HOST "$SMTP_HOST"
  set_env "$R_ENV" SMTP_PORT "$SMTP_PORT"
  set_env "$R_ENV" SMTP_USER "$SMTP_USER"
  set_env "$R_ENV" SMTP_PASSWORD "${SMTP_PASSWORD:-}"
  set_env "$R_ENV" SMTP_FROM "$SMTP_FROM"
  set_env "$R_ENV" REPORT_TO "$REPORT_TO"
fi

if [ -n "$DOZZLE_USER" ]; then
  note "Creating the Dozzle login..."
  # the bcrypt hash is made from the password on stdin, so it never shows in the process list
  if hash=$(printf '%s' "$DOZZLE_PASS" | docker run --rm -i "$PY_IMAGE" python -W ignore -c \
    'import crypt, sys; print(crypt.crypt(sys.stdin.read(), crypt.mksalt(crypt.METHOD_BLOWFISH)).replace("$2b$", "$2a$", 1))') \
    && [ -n "$hash" ]; then
    printf 'users:\n  %s:\n    name: %s\n    password: %s\n' "$DOZZLE_USER" "$DOZZLE_USER" "$hash" > dozzle/data/users.yml
    WRITTEN+=(dozzle/data/users.yml)
  else
    die "Couldn't create the Dozzle login. Your other settings are saved; run this again to retry."
  fi
fi

if [ ! -s nginx/certs/server.crt ] || [ ! -s nginx/certs/server.key ]; then
  note "Making a self-signed HTTPS certificate (replace nginx/certs/server.* with your own any time)..."
  san="DNS:$(hostname),DNS:localhost,IP:127.0.0.1"
  [ "$HOST_IP" = localhost ] || san="IP:$HOST_IP,$san"
  (umask 077 && openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes -subj "/CN=$(hostname)" \
    -addext "subjectAltName=$san" -keyout nginx/certs/server.key -out nginx/certs/server.crt 2> /dev/null) \
    || die "Couldn't make the HTTPS certificate with openssl."
  WRITTEN+=(nginx/certs/server.crt nginx/certs/server.key)
fi

if write_zone_names "$UNIFI_URL" "$UNIFI_SITE" "$UNIFI_KEY" "$UNIFI_USER" "$UNIFI_PASS"; then
  WRITTEN+=("$ZONE_FILE")
else
  note "Firewall zone names weren't read from UniFi (no zone-based firewall, or it couldn't be reached). Run ./install.sh --zones to try again."
fi

# run with sudo: hand the files and data folders back to the user who owns the checkout
if [ "$(id -u)" = 0 ] && [ -n "${SUDO_UID:-}" ]; then
  chown "$RUN_UID:$RUN_GID" "${WRITTEN[@]}" grafana/data prometheus/data loki/data reporter/out dozzle/data nginx/certs unpoller/certs
fi

for f in "${WRITTEN[@]}"; do note "  wrote $f"; done
for d in grafana/data prometheus/data loki/data reporter/out; do
  if [ "$(stat -c %u "$d" 2> /dev/null || echo "$RUN_UID")" != "$RUN_UID" ]; then
    warn "$d isn't owned by user $RUN_UID, so its container may not be able to write to it. Fix: sudo chown -R $RUN_UID:$RUN_GID $d"
  fi
done

# ---------------------------------------------------------------- start

if [ "$START" = 1 ]; then
  step "Starting the stack"
  note "The first start downloads the images and builds the reporter, which takes a few minutes."
  docker compose up -d --build
  if [ "$DOZZLE" = 0 ]; then docker compose --profile dozzle rm -sf dozzle > /dev/null 2>&1 || true; fi
  if [ "$SYSLOG" = 0 ]; then docker compose --profile syslog rm -sf syslog > /dev/null 2>&1 || true; fi

  if [ "$RESET_GRAFANA" = 1 ]; then
    note "Setting the new Grafana password..."
    ok=0
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if printf '%s\n' "$GRAFANA_PASS" | docker compose exec -T grafana grafana cli --homepath /usr/share/grafana \
        admin reset-admin-password --password-from-stdin > /dev/null 2>&1; then
        ok=1
        break
      fi
      sleep 3
    done
    [ "$ok" = 1 ] || warn "Couldn't reset the Grafana password. Try: docker compose exec grafana grafana cli admin reset-admin-password --password-from-stdin, then type it"
  fi
fi

# ---------------------------------------------------------------- summary

step "${G}Done${N}"
cat << EOF

  Grafana          https://$HOST_IP:3000
  Reports portal   https://$HOST_IP:8088   (sign in with a Grafana account)
EOF
if [ "$DOZZLE" = 1 ]; then echo "  Dozzle           https://$HOST_IP:8080"; fi
echo
if [ "$SYSLOG" = 1 ]; then
  note "Firewall log: in UniFi, open Settings > CyberSecure > Traffic Logging, set Activity Logging (Syslog) to"
  note "SIEM Server with server $HOST_IP and port 5514, tick only Gateway and Firewall Default Policy, and apply."
  note "Then turn on Syslog Logging on each firewall policy whose blocks you want to see. docs/deployment.md has more."
fi
note "The HTTPS certificate is self-signed, so browsers warn the first time. Accept it, or put your own in nginx/certs."
note "Prometheus has no login, so it only listens on this server: http://localhost:9090 here, or"
note "ssh -L 9090:localhost:9090 ${SUDO_USER:-$(id -un)}@$HOST_IP from another machine. UnPoller isn't published at all."

case "$GENERATED" in
  *" GRAFANA_PASS "* | *" DOZZLE_PASS "*)
    echo
    echo "  Generated passwords (shown once; keep them somewhere safe):"
    case "$GENERATED" in *" GRAFANA_PASS "*) echo "    Grafana   $GRAFANA_USER / $GRAFANA_PASS" ;; esac
    case "$GENERATED" in *" DOZZLE_PASS "*) echo "    Dozzle    $DOZZLE_USER / $DOZZLE_PASS" ;; esac
    ;;
esac

echo
if [ "$START" = 0 ]; then
  note "Settings are saved. Start the stack with: docker compose up -d --build"
  [ "$RESET_GRAFANA" = 0 ] || warn "The Grafana password wasn't reset because nothing was started. Run ./install.sh again without --no-start."
fi
note "After a UniFi upgrade or a new firewall zone, refresh the zone names: ./install.sh --zones"
note "If UniFi stops collecting after the console gets a new certificate: ./install.sh --cert"
note "To change any of these settings later, run ./install.sh again."
