#!/usr/bin/env bash
# Sets up the UniFi monitoring stack: asks a few questions, writes every .env file and the Dozzle login,
# then starts the containers.
#
#   ./install.sh             set up (or change settings) and start the stack
#   ./install.sh --no-start  write the settings but don't start anything
#   ./install.sh --zones     only refresh the firewall zone names from UniFi (after a UniFi upgrade or a new zone)
#
# Safe to run again: it reads your current .env files and offers their values as the defaults, so pressing
# Enter keeps what you have. Settings it doesn't ask about (and any edits you made by hand) are left alone.

set -euo pipefail

cd "$(dirname "$0")"

START=1
ZONES_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-start) START=0 ;;
    --zones) ZONES_ONLY=1 ;;
    -h | --help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

SERVICES="unpoller grafana prometheus loki reporter dozzle"
DOZZLE_IMAGE=$(grep -o 'amir20/dozzle:[^ ]*' compose.yaml | head -n 1)
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

# ---------------------------------------------------------------- firewall zone names

ZONE_FILE=prometheus/config/rules/zone-names.yml

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
      json=$(curl -sk --max-time 20 -H "X-API-KEY: $key" "$base/v2/api/site/$site/firewall/zone" || true)
      case "$json" in '[{'*'"_id":"'*) break ;; esac
    fi
    if [ -n "$user" ]; then
      jar=$(mktemp)
      login="$url/api/auth/login"
      [ "$base" = "$url" ] && login="$url/api/login"
      # the password goes in on stdin so it never shows in the process list
      printf '{"username":"%s","password":"%s"}' "$user" "$pass" \
        | curl -sk --max-time 20 -o /dev/null -c "$jar" -H 'Content-Type: application/json' -d @- "$login" || true
      json=$(curl -sk --max-time 20 -b "$jar" "$base/v2/api/site/$site/firewall/zone" || true)
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
    [ -n "$id" ] && [ -n "$name" ] || continue
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
  if write_zone_names "$(get_env "$U" UP_UNIFI_CONTROLLER_0_URL)" "$(get_env "$U" UP_UNIFI_CONTROLLER_0_SITE)" \
    "$(get_env "$U" UP_UNIFI_CONTROLLER_0_API_KEY)" "$(get_env "$U" UP_UNIFI_CONTROLLER_0_USER)" \
    "$(get_env "$U" UP_UNIFI_CONTROLLER_0_PASS)"; then
    if [ "$(id -u)" = 0 ] && [ -n "${SUDO_UID:-}" ]; then chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$ZONE_FILE"; fi
    note "  wrote $ZONE_FILE ($ZONE_COUNT zones)"
    if docker compose kill -s HUP prometheus > /dev/null 2>&1; then
      note "Prometheus reloaded it. The Firewall dashboard shows the names within a minute."
    else
      note "Prometheus isn't running; it reads the file when it starts."
    fi
    exit 0
  fi
  die "Couldn't read the firewall zones from UniFi. Check the controller address and login in $U (zones need UniFi Network 9 or newer with zone-based firewall)."
fi

# ---------------------------------------------------------------- checks

step "Checking this machine"

command -v docker > /dev/null || die "Docker isn't installed. Install Docker Engine first: https://docs.docker.com/engine/install/"
docker compose version > /dev/null 2>&1 || die "The Docker Compose plugin is missing ('docker compose'). Install it, then run this again."
docker info > /dev/null 2>&1 || die "Docker is installed but this user can't use it. Start Docker, or add yourself to the docker group (sudo usermod -aG docker \$USER, then log out and in), or run: sudo ./install.sh"
[ -n "$DOZZLE_IMAGE" ] || die "Couldn't find the Dozzle image in compose.yaml."

# containers run as the user who owns this folder (not root when run with sudo)
RUN_UID=${SUDO_UID:-$(id -u)}
RUN_GID=${SUDO_GID:-$(id -g)}
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

DOZZLE_USER="" DOZZLE_PASS=""
if [ -s dozzle/data/users.yml ] && yes_no "Keep the existing Dozzle login?" y; then
  :
else
  ask_required DOZZLE_USER "Dozzle username" "${GRAFANA_USER:-admin}"
  ask_secret DOZZLE_PASS "Dozzle password" generate
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
note "The Grafana address people open from their own computers. Report PDFs link to it."
ask_required GRAFANA_URL "Grafana address" "${cur:-http://$HOST_IP:3000}"
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
if [ -n "$UNIFI_KEY" ]; then set_env "$U" UP_UNIFI_CONTROLLER_0_API_KEY "$UNIFI_KEY"; else unset_env "$U" UP_UNIFI_CONTROLLER_0_API_KEY; fi
if [ -n "$UNIFI_USER" ]; then
  set_env "$U" UP_UNIFI_CONTROLLER_0_USER "$UNIFI_USER"
  set_env "$U" UP_UNIFI_CONTROLLER_0_PASS "$UNIFI_PASS"
else
  unset_env "$U" UP_UNIFI_CONTROLLER_0_USER
  unset_env "$U" UP_UNIFI_CONTROLLER_0_PASS
fi
for k in SAVE_ALARMS SAVE_EVENTS SAVE_IDS; do set_env "$U" "UP_UNIFI_CONTROLLER_0_$k" "$legacy"; done

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
  note "Creating the Dozzle login (downloads $DOZZLE_IMAGE the first time)..."
  tmp=$(mktemp)
  if docker run --rm "$DOZZLE_IMAGE" generate "$DOZZLE_USER" --password "$DOZZLE_PASS" > "$tmp"; then
    cat "$tmp" > dozzle/data/users.yml
    rm -f "$tmp"
    WRITTEN+=(dozzle/data/users.yml)
  else
    rm -f "$tmp"
    die "Couldn't create the Dozzle login. Your other settings are saved; run this again to retry."
  fi
fi

if write_zone_names "$UNIFI_URL" "$UNIFI_SITE" "$UNIFI_KEY" "$UNIFI_USER" "$UNIFI_PASS"; then
  WRITTEN+=("$ZONE_FILE")
else
  note "Firewall zone names weren't read from UniFi (no zone-based firewall, or it couldn't be reached). Run ./install.sh --zones to try again."
fi

# run with sudo: hand the files and data folders back to the user who owns the checkout
if [ "$(id -u)" = 0 ] && [ -n "${SUDO_UID:-}" ]; then
  chown "$RUN_UID:$RUN_GID" "${WRITTEN[@]}" grafana/data prometheus/data loki/data reporter/out dozzle/data
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

  if [ "$RESET_GRAFANA" = 1 ]; then
    note "Setting the new Grafana password..."
    ok=0
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if docker compose exec -T grafana grafana cli --homepath /usr/share/grafana admin reset-admin-password "$GRAFANA_PASS" > /dev/null 2>&1; then
        ok=1
        break
      fi
      sleep 3
    done
    [ "$ok" = 1 ] || warn "Couldn't reset the Grafana password. Try: docker compose exec grafana grafana cli admin reset-admin-password '<new password>'"
  fi
fi

# ---------------------------------------------------------------- summary

step "${G}Done${N}"
cat << EOF

  Grafana          http://$HOST_IP:3000
  Reports portal   http://$HOST_IP:8088   (sign in with a Grafana account)
  Dozzle           http://$HOST_IP:8080
EOF
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
note "To change any of these settings later, run ./install.sh again."
