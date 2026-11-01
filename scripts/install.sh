#!/bin/sh
# Installs Tico on this Linux server: Docker if missing, the compose bundle of one exact release, then the
# `tico setup` wizard. Safe to run again: it upgrades or repairs and never rewrites an existing .env.
#
#   curl -fsSL https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install.sh -o install.sh && sh install.sh
#   sh install.sh --recommended [--owner-email you@example.com]   # the recommended setup (below)
#   sh install.sh --local [--owner-email you@example.com] [--team-name Acme]   # quick start: no domain, no sign-in setup
#   sh install.sh [--version vX.Y.Z] [--dir /opt/tico] [--yes] [--tunnel] [--docker-only] [-- wizard flags]
#   sh install.sh --runner --url https://tico.example.com --code <code> --label "Build box" [--version vX.Y.Z]
#   sh install.sh --runner --name mail --url http://server:8765 --code <code> --server-network tico_default
#
# On a Mac, --local and --runner work the same and install into ~/tico and ~/tico-runner (Docker Desktop must be
# running; nothing needs sudo). The team install is Linux only.
#
# --local runs Tico on this computer only (http://127.0.0.1:8765, the owner signs in with a token): no domain, no DNS, no
# OIDC client. The domain and sign-in are added later in .env (docs/install.md, "Add a domain and sign-in later").
#
# --recommended is the quick start with the recommended choices made: the sample company, models on your
# Claude and ChatGPT subscriptions plus Tico Gateway, Tico Support on, and a computer on this server that runs the bots
# (a runner on the server's Docker network). What is left for the person is signing in to Claude and ChatGPT from
# Settings > Computers. The Gateway invite comes from the environment (TICO_GATEWAY_INVITE, set by whoever runs the
# install) and is never printed; without one the Gateway is simply not set up. On a terminal with no other choice made,
# the install asks which setup to use and offers this one first.
#
# --runner sets up a computer that runs bots for a Tico server instead: Docker, that release's runner.compose.yaml (the
# runner and its updater sidecar, both pinned to the release) in /opt/tico-runner, a .env with the join settings, and
# `docker compose up -d`. The sidecar is what keeps the runner on the server's release.
# --name adds another computer on the same host (a message bot needs its own): it installs into /opt/tico-runner-<slug>
# with its own compose project, container, home volume and updater, and leaves the default runner alone.
#
# Everything after `--` goes to `tico setup` (python3 -m setup), so automation can answer its questions with flags
# and environment variables (secrets only ever by environment). Exit codes: 0 done, 2 usage, 3 this computer does not
# qualify, 4 download or checksum failed, 5 Docker or Python setup failed, 6 the wizard or the health check failed.
# When it fails after the release is unpacked and there is a terminal, it asks whether to send the error to Tico
# support (redacted; TICO_SUPPORT=off or the wizard's --no-support turns that off); the exit code stays the same.
set -eu

# The release workflow replaces the placeholder with the tag; on main it stays and the newest release is looked up.
TICO_VERSION_BAKED='@TICO_VERSION@'
REPO=ticoteam/tico
# TICO_INSTALL_* variables below exist so tests can run this script against local files and fake hosts.
RELEASES=${TICO_INSTALL_RELEASES_URL:-https://github.com/$REPO/releases}
LATEST_API=${TICO_INSTALL_LATEST_URL:-https://api.github.com/repos/$REPO/releases/latest}
OS_RELEASE=${TICO_INSTALL_OS_RELEASE:-/etc/os-release}
MEMINFO=${TICO_INSTALL_MEMINFO:-/proc/meminfo}
HEALTH_URL=${TICO_INSTALL_HEALTH_URL:-http://127.0.0.1:8765/healthz}
HEALTH_TRIES=${TICO_INSTALL_HEALTH_TRIES:-60}

MIN_MEM_KB=900000   # a 1 GB computer reports a little under 1,000,000 kB
MIN_DISK_KB=1000000
VERSION_RE='^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$'
# backend/company_file/starter.py ENV_PACKED: a packed company file this long or shorter goes in .env.
ENV_PACKED_MAX=7168

# A Mac (Docker Desktop) keeps its files in the home folder: /opt needs sudo there, and nothing else does.
MAC=
[ "$(uname -s)" != Darwin ] || MAC=1
BASE=/opt
[ -z "$MAC" ] || BASE=${HOME:-/tmp}
DIR=${TICO_INSTALL_DIR:-$BASE/tico}
VERSION=
FORCED=
YES=
TUNNEL=
LOCAL=
RECOMMENDED=
SUPPORT=
COMPANY=
COMPANY_FILE=
OWNER_EMAIL=
OWNER_NAME=
PORT=
DOCKER_ONLY=
RUNNER=
RUNNER_URL=
RUNNER_CODE=
RUNNER_LABEL=
RUNNER_NAME=
SERVER_NETWORK=
SCREEN=
DIR_GIVEN=${TICO_INSTALL_DIR:+1}
BUNDLED=
SUPPORT_ASKED=

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  code=$1; shift
  printf 'error: %s\n' "$*" >&2
  case $code in 3|4|5|6) offer_support "$code" "$*" ;; esac
  exit "$code"
}

# After a failure, the setup this run unpacked offers to send the error to Tico support (setup/support.py): only on a
# terminal, at most once a run (a wizard that asked leaves the marker), and the exit code stays the failure's. Before
# the bundle is unpacked there is no redaction to apply, so nothing is offered.
offer_support() {
  [ -n "$BUNDLED" ] && [ -f "$DIR/setup/support.py" ] && [ -f "$DIR/backend/redaction.py" ] || return 0
  [ -z "$SUPPORT_ASKED" ] || [ ! -e "$SUPPORT_ASKED" ] || return 0
  ( : </dev/tty ) 2>/dev/null || return 0
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null || return 0
  ( cd "$DIR" && TICO_SETUP_HOME="$DIR/.setup" python3 -m setup support --step "$1" --error "$2" </dev/tty ) || true
}

usage() {
  cat <<'EOF'
Usage: install.sh [options] [-- tico-setup flags]

  --version vX.Y.Z   install this release (default: the release this script came from)
  --dir PATH         install directory (default /opt/tico; ~/tico on a Mac)
  --yes, -y          do not ask for confirmation
  --recommended      the recommended setup on this computer: the quick start plus the sample company, your
                     Claude and ChatGPT subscriptions plus Tico Gateway, Tico Support on and a computer on this server
                     (the Gateway invite comes from TICO_GATEWAY_INVITE in the environment, never a flag)
  --local            quick start on this computer only: no domain and no sign-in setup (add them later in .env)
  --support on|off   with --local or --recommended: Tico Support, where the ? chat sends tickets to Tico, tied to
                     this install. off writes TICO_SUPPORT=off, which removes it (PRIVACY.md, Support tickets)
  --owner-email E    with --local: the owner's email (asked when omitted)
  --owner-name NAME  with --local: prefill your name in Finish setup
  --team-name NAME   with --local: prefill the team name (default: My Team); --company is an alias
  --company-file PATH  the company file (tico.company.yaml) to start with: with --local its team is loaded on first start,
                     with --recommended it replaces the sample company; a team install passes it to the setup wizard
  --port PORT        server host port (default: 8765; kept on upgrades unless given)
  --tunnel           Cloudflare Tunnel: no public ports needed, so ports 80 and 443 are not checked
  --docker-only      only make sure Docker and Compose are installed (used for runner boxes)
  --runner           set up a computer that runs bots for a Tico server (Docker Compose, with the updater sidecar)
  --url URL          with --runner: the Tico server; with --local: its loopback browser address
  --code CODE        with --runner: the one-time code from Settings > Computers > Add computer (15 minutes, single use)
  --label NAME       with --runner: the computer's name in Tico (default: this host's name)
  --name NAME        with --runner: add another computer on this host (for a message bot). It gets its own directory
                     (/opt/tico-runner-<name>), containers, volume and updater; the label defaults to NAME
  --server-network N with --runner on the server's own computer: join its Docker network N (usually tico_default) and
                     use --url http://server:8765, so the runner never goes through Cloudflare Access
  --screen           with --runner: offer this computer's screen to the web app (TICO_SCREEN=1 in .env; the
                     server's Screen setting still decides who may open it). A new runner offers it by default
  --no-screen        with --runner: do not offer the screen (TICO_SCREEN=0 in .env)
  --help, -h         this text

Run it again any time: with an existing .env it upgrades to --version or repairs and restarts, and leaves
your settings alone. Flags after -- go to the wizard (python3 -m setup --help).
EOF
}

as_root() {
  if [ "$(id -u)" = 0 ] || [ -n "$MAC" ]; then "$@"; else sudo "$@"; fi
}

# Whether the install was started with any argument at all: the setup question is only for a bare `sh install.sh`.
ARGC=$#
while [ $# -gt 0 ]; do
  case $1 in
    --version) [ $# -ge 2 ] || { usage >&2; die 2 "--version needs a value"; }; VERSION=$2; FORCED=1; shift 2 ;;
    --version=*) VERSION=${1#--version=}; FORCED=1; shift ;;
    --dir) [ $# -ge 2 ] || { usage >&2; die 2 "--dir needs a value"; }; DIR=$2; DIR_GIVEN=1; shift 2 ;;
    --dir=*) DIR=${1#--dir=}; DIR_GIVEN=1; shift ;;
    --runner) RUNNER=1; shift ;;
    --url) [ $# -ge 2 ] || { usage >&2; die 2 "--url needs a value"; }; RUNNER_URL=$2; shift 2 ;;
    --url=*) RUNNER_URL=${1#--url=}; shift ;;
    --code) [ $# -ge 2 ] || { usage >&2; die 2 "--code needs a value"; }; RUNNER_CODE=$2; shift 2 ;;
    --code=*) RUNNER_CODE=${1#--code=}; shift ;;
    --label) [ $# -ge 2 ] || { usage >&2; die 2 "--label needs a value"; }; RUNNER_LABEL=$2; shift 2 ;;
    --label=*) RUNNER_LABEL=${1#--label=}; shift ;;
    --name) [ $# -ge 2 ] || { usage >&2; die 2 "--name needs a value"; }; RUNNER_NAME=$2; shift 2 ;;
    --name=*) RUNNER_NAME=${1#--name=}; shift ;;
    --server-network) [ $# -ge 2 ] || { usage >&2; die 2 "--server-network needs a value"; }; SERVER_NETWORK=$2; shift 2 ;;
    --server-network=*) SERVER_NETWORK=${1#--server-network=}; shift ;;
    --screen) SCREEN=1; shift ;;
    --no-screen) SCREEN=0; shift ;;
    --yes|-y) YES=1; shift ;;
    --tunnel) TUNNEL=1; shift ;;
    --local) LOCAL=1; shift ;;
    --recommended) RECOMMENDED=1; LOCAL=1; shift ;;
    --support) [ $# -ge 2 ] || { usage >&2; die 2 "--support needs on or off"; }; SUPPORT=$2; shift 2 ;;
    --support=*) SUPPORT=${1#--support=}; shift ;;
    --owner-email) [ $# -ge 2 ] || { usage >&2; die 2 "--owner-email needs a value"; }; OWNER_EMAIL=$2; shift 2 ;;
    --owner-email=*) OWNER_EMAIL=${1#--owner-email=}; shift ;;
    --team-name|--company) [ $# -ge 2 ] || { usage >&2; die 2 "$1 needs a value"; }; COMPANY=$2; shift 2 ;;
    --team-name=*|--company=*) COMPANY=${1#*=}; shift ;;
    --company-file) [ -n "${2:-}" ] || { usage >&2; die 2 "--company-file needs a path"; }; COMPANY_FILE=$2; shift 2 ;;
    --company-file=*) [ -n "${1#--company-file=}" ] || { usage >&2; die 2 "--company-file needs a path"; }; COMPANY_FILE=${1#--company-file=}; shift ;;
    --owner-name) [ $# -ge 2 ] || { usage >&2; die 2 "--owner-name needs a value"; }; OWNER_NAME=$2; shift 2 ;;
    --owner-name=*) OWNER_NAME=${1#--owner-name=}; shift ;;
    --port) [ $# -ge 2 ] || { usage >&2; die 2 "--port needs a value"; }; PORT=$2; shift 2 ;;
    --port=*) PORT=${1#--port=}; shift ;;
    --docker-only) DOCKER_ONLY=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --) shift; break ;;
    *) usage >&2; die 2 "unknown option: $1" ;;
  esac
done
# The wizard's --no-support also keeps this script from offering.
case " $* " in *" --no-support "*) TICO_SUPPORT=off; export TICO_SUPPORT ;; esac

# Nothing chosen on a terminal (no arguments at all, nothing installed yet): ask, with the recommended setup first.
# Without a terminal nothing is asked and the install does what it always did.
if [ "$ARGC" -eq 0 ] && [ -z "$LOCAL$RUNNER$DOCKER_ONLY$TUNNEL$YES" ] && [ ! -f "$DIR/.env" ] && ( : </dev/tty ) 2>/dev/null; then
  say "How should Tico be set up?"
  say "  1) Recommended: Tico on this computer with a sample company, your Claude and ChatGPT subscriptions plus"
  say "     Tico Gateway, and Tico Support on. No domain or sign-in setup; you sign in to Claude and ChatGPT afterwards."
  say "  2) Quick start: Tico on this computer only; you choose the team and the models yourself."
  say "  3) Team install: your own domain and sign-in (Google, Microsoft or Cloudflare Access)."
  printf 'Choose [1]: '
  read -r choice </dev/tty || choice=
  case ${choice:-1} in
    1) RECOMMENDED=1; LOCAL=1 ;;
    2) LOCAL=1 ;;
    3) ;;
    *) die 2 "Choose 1, 2 or 3." ;;
  esac
fi
case $SUPPORT in
  ''|on|off) ;;
  *) die 2 "--support is on or off." ;;
esac
[ -z "$SUPPORT" ] || [ -n "$LOCAL" ] || die 2 "--support goes with --local or --recommended (the team install's wizard takes --no-support)."
# Given on the command line, the switch is also saved on an existing install; the preset's default is for new ones.
SUPPORT_GIVEN=$SUPPORT
[ -n "$SUPPORT" ] || [ -z "$RECOMMENDED" ] || SUPPORT=on
# Support off also keeps this script and the wizard from offering to send an install error.
[ "$SUPPORT" != off ] || { TICO_SUPPORT=off; export TICO_SUPPORT; }
# The Gateway invite is the operator's, so it is read from the environment and never shown: not here, not in an error.
GATEWAY_INVITE=
if [ -n "$RECOMMENDED" ]; then
  GATEWAY_INVITE=${TICO_GATEWAY_INVITE:-}
  # The whole value is checked, not line by line: it goes into .env as one line, so a newline must never pass.
  if [ -n "$GATEWAY_INVITE" ]; then
    case $GATEWAY_INVITE in
      *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-]*) invite_ok= ;;
      *) invite_ok=1; [ ${#GATEWAY_INVITE} -ge 8 ] && [ ${#GATEWAY_INVITE} -le 200 ] || invite_ok= ;;
    esac
    [ -n "$invite_ok" ] \
      || die 2 "TICO_GATEWAY_INVITE is not an invite code (8 to 200 letters, digits, - and _). It is not shown here."
  fi
fi

# The default runner is `tico-runner` in /opt/tico-runner. --name adds `tico-runner-<slug>` in /opt/tico-runner-<slug>.
RUNNER_SLUG=
RUNNER_PROJECT=tico-runner
[ -z "$RUNNER_NAME" ] || [ -n "$RUNNER" ] || die 2 "--name goes with --runner."
[ -z "$SCREEN" ] || [ -n "$RUNNER" ] || die 2 "--screen and --no-screen go with --runner."
if [ -n "$RUNNER_NAME" ]; then
  RUNNER_SLUG=$(printf '%s' "$RUNNER_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9][^a-z0-9]*/-/g; s/^-//; s/-$//' | cut -c1-32 | sed 's/-$//')
  printf '%s' "$RUNNER_SLUG" | grep -Eq '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$' || die 2 "--name needs letters or digits, such as --name mail."
  RUNNER_PROJECT=tico-runner-$RUNNER_SLUG
fi
[ -n "$RUNNER" ] && [ -z "$DIR_GIVEN" ] && DIR=$BASE/$RUNNER_PROJECT
if [ -n "$RUNNER" ]; then
  # These reach a file and a command line, so only plain values are taken.
  [ -z "$TUNNEL$DOCKER_ONLY" ] || die 2 "--runner cannot be combined with --tunnel or --docker-only."
  # An existing .env already holds the join settings, so the flags are only needed to change them.
  if [ -n "$RUNNER_URL" ] || [ ! -f "$DIR/.env" ]; then
    printf '%s' "$RUNNER_URL" | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?$' || { usage >&2; die 2 "--runner needs --url https://your-tico-server (no path)."; }
  fi
  if [ -n "$RUNNER_CODE" ] || [ ! -f "$DIR/.env" ]; then
    printf '%s' "$RUNNER_CODE" | grep -Eq '^[A-Za-z0-9_-]+$' || { usage >&2; die 2 "--runner needs --code, the one-time code from Settings > Computers > Add computer."; }
  fi
  [ -n "$RUNNER_LABEL" ] || [ -f "$DIR/.env" ] || RUNNER_LABEL=${RUNNER_NAME:-$(hostname 2>/dev/null || echo runner)}
  [ -z "$RUNNER_LABEL" ] || printf '%s' "$RUNNER_LABEL" | grep -Eq '^[^"$`\\]{1,80}$' || die 2 "--label is 1 to 80 characters without quotes, dollar signs, backticks or backslashes."
fi
[ -z "$LOCAL" ] || [ -z "$TUNNEL$RUNNER$DOCKER_ONLY" ] || die 2 "--local cannot be combined with --tunnel, --runner or --docker-only."
[ -n "$LOCAL" ] || [ -z "$OWNER_EMAIL$OWNER_NAME$COMPANY" ] || die 2 "--owner-email, --owner-name and --team-name go with --local."
if [ -n "$PORT" ]; then
  case "$PORT" in *[!0-9]*|'') die 2 "--port needs a number from 1 to 65535." ;; esac
  if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then die 2 "--port needs a number from 1 to 65535."; fi
  [ -z "$RUNNER$DOCKER_ONLY" ] || die 2 "--port goes with a server install."
  export TICO_PORT="$PORT"
fi
[ -n "$RUNNER$LOCAL" ] || [ -z "$RUNNER_URL" ] || die 2 "--url goes with --local or --runner."
if [ -n "$OWNER_NAME" ]; then
  printf '%s' "$OWNER_NAME" | grep -Eq '^[^"$`\\]{1,100}$' || die 2 "--owner-name is 1 to 100 characters without quotes, dollar signs, backticks or backslashes."
fi
if [ -n "$LOCAL" ] && [ -n "$RUNNER_URL" ]; then
  printf '%s' "$RUNNER_URL" | grep -Eq '^http://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]+)?$' || die 2 "--local --url needs a loopback address such as http://localhost:8765."
fi
# The file is read by this user, before any sudo, so a path it cannot read stops here, before anything is installed.
# The shell does not expand ~ after --company-file=, so it is expanded here. The install changes folder, so the path
# is made absolute now.
case $COMPANY_FILE in
  \~) COMPANY_FILE=${HOME:-} ;;
  \~/*) COMPANY_FILE=${HOME:-}/${COMPANY_FILE#'~/'} ;;
  ''|/*) ;;
  *) COMPANY_FILE=$(pwd)/$COMPANY_FILE ;;
esac
if [ -n "$COMPANY_FILE" ]; then
  if [ ! -f "$COMPANY_FILE" ] || [ ! -r "$COMPANY_FILE" ]; then die 2 "Cannot read --company-file; use a readable UTF-8 company YAML file"; fi
  [ -z "$RUNNER$DOCKER_ONLY" ] || die 2 "--company-file goes with --local, --recommended or a team install."
  # A team install hands the file to the setup wizard, as `-- --company-file` always did.
  if [ -z "$LOCAL" ]; then set -- --company-file "$COMPANY_FILE" "$@"; fi
fi
printf '%s' "$DIR" | grep -Eq '^/[A-Za-z0-9._/-]+$' || die 2 "--dir must be an absolute path made of letters, digits and . _ - /"

# ---------------------------------------------------------------------------------------------- preflight

preflight() {
  step "Checking this computer"
  if [ -n "$MAC" ]; then
    [ -n "$LOCAL$RUNNER" ] || die 3 "The team install is for Linux servers. On this Mac, run it again with --local (Tico on this Mac only), or with --runner to join a server's computer."
  else
    [ "$(uname -s)" = Linux ] || die 3 "Tico installs on Linux servers only (this is $(uname -s))."
  fi
  case $(uname -m) in
    x86_64|amd64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) die 3 "Unsupported CPU $(uname -m): the images are built for x86_64 and arm64." ;;
  esac
  if [ "$(id -u)" != 0 ] && [ -z "$MAC" ]; then
    command -v sudo >/dev/null 2>&1 || die 3 "Run this as root, or install sudo: it needs to install Docker and write $DIR."
    if ! sudo -n true 2>/dev/null; then
      say "This needs administrator rights; sudo may ask for your password."
      # A piped script has no stdin, so ask on the terminal.
      # shellcheck disable=SC2024  # the terminal is read by this user's shell on purpose
      if ( : </dev/tty ) 2>/dev/null; then sudo -v </dev/tty || die 3 "sudo did not accept the password."
      else die 3 "sudo needs a password and there is no terminal. Run as root or with passwordless sudo."; fi
    fi
  fi
  if [ -n "$MAC" ]; then
    mem_kb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 ))
    [ "$mem_kb" -ge "$MIN_MEM_KB" ] || mem_kb=$MIN_MEM_KB   # Docker Desktop sets its own limit; do not guess
  else
    mem_kb=$(awk '/^MemTotal:/ {print $2}' "$MEMINFO" 2>/dev/null || true)
    [ -n "${mem_kb:-}" ] || die 3 "Could not read the memory size from $MEMINFO."
    [ "$mem_kb" -ge "$MIN_MEM_KB" ] || die 3 "Needs at least 1 GB of memory (this computer has $((mem_kb / 1024)) MB); 2 GB is comfortable."
  fi
  probe=$DIR
  while [ ! -d "$probe" ]; do probe=$(dirname "$probe"); done
  disk_kb=$(df -Pk "$probe" | awk 'NR==2 {print $4}')
  [ "${disk_kb:-0}" -ge "$MIN_DISK_KB" ] || die 3 "Needs at least 1 GB of free disk under $probe (found $((${disk_kb:-0} / 1024)) MB); the images alone are about 0.6 GB."
  if [ -z "$TUNNEL" ] && [ -z "$LOCAL" ] && [ -z "$DOCKER_ONLY" ] && [ -z "$RUNNER" ] && [ ! -f "$DIR/.env" ]; then
    for port in 80 443; do
      if port_busy "$port"; then
        die 3 "Port $port is already in use, and Tico's HTTPS front door needs it. Stop what listens there, or use --tunnel (Cloudflare Tunnel needs no open ports)."
      fi
    done
  fi
  say "ok: $([ -n "$MAC" ] && echo macOS || echo Linux) $arch, $((mem_kb / 1024)) MB memory, $((disk_kb / 1024)) MB free disk"
}

port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk -v p=":$1" '$1 == "LISTEN" && $4 ~ (p "$") {f = 1} END {exit !f}'
  else
    warn "ss is not installed, so port $1 was not checked."
    return 1
  fi
}

# ---------------------------------------------------------------------------------------------- packages

pkg_install() {
  if command -v apt-get >/dev/null 2>&1; then
    as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
  elif command -v dnf >/dev/null 2>&1; then
    as_root dnf install -y -q "$@"
  elif command -v yum >/dev/null 2>&1; then
    as_root yum install -y -q "$@"
  else
    return 1
  fi
}

need_tool() {
  command -v "$1" >/dev/null 2>&1 && return 0
  say "Installing $1 ..."
  pkg_install "$2" >/dev/null || die 5 "Could not install $2. Install it and run this again."
}

# shellcheck disable=SC1090
os_field() { ( . "$OS_RELEASE" 2>/dev/null; eval "printf '%s' \"\${$1:-}\"" ); }

compose_ok() {
  command -v docker >/dev/null 2>&1 || return 1
  v=$(as_root docker compose version --short 2>/dev/null) || return 1
  v=${v#v}
  [ "$(printf '%s\n%s\n' 2.20.0 "$v" | sort -V | head -n 1)" = 2.20.0 ]
}

# Docker's documented method for Debian and Ubuntu is its own apt repository; the get.docker.com convenience
# script is their fallback for other distributions.
install_docker() {
  step "Installing Docker"
  id=$(os_field ID)
  like=$(os_field ID_LIKE)
  case " $id $like " in
    *" ubuntu "*) family=ubuntu; codename=$(os_field UBUNTU_CODENAME) ;;
    *" debian "*) family=debian; codename=$(os_field VERSION_CODENAME) ;;
    *) family= ;;
  esac
  [ "$id" = ubuntu ] && family=ubuntu
  [ "$id" = debian ] && family=debian
  [ -n "$family" ] && [ -z "$codename" ] && codename=$(os_field VERSION_CODENAME)
  if [ -n "$family" ] && [ -n "$codename" ] && command -v apt-get >/dev/null 2>&1; then
    need_tool curl curl
    pkg_install ca-certificates >/dev/null || die 5 "Could not install ca-certificates."
    as_root install -m 0755 -d /etc/apt/keyrings
    as_root curl -fsSL "https://download.docker.com/linux/$family/gpg" -o /etc/apt/keyrings/docker.asc \
      || die 5 "Could not download Docker's signing key."
    as_root chmod a+r /etc/apt/keyrings/docker.asc
    printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
      "$family" "$codename" | as_root tee /etc/apt/sources.list.d/docker.sources >/dev/null
    pkg_install docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null \
      || die 5 "Installing Docker from Docker's apt repository failed. See https://docs.docker.com/engine/install/ and run this again."
  else
    warn "No Docker package repository is set up for this distribution ($id); using Docker's convenience script."
    need_tool curl curl
    tmp_docker=$(mktemp)
    curl -fsSL https://get.docker.com -o "$tmp_docker" || { rm -f "$tmp_docker"; die 5 "Could not download get.docker.com."; }
    as_root sh "$tmp_docker" >/dev/null || { rm -f "$tmp_docker"; die 5 "Docker's install script failed. See https://docs.docker.com/engine/install/ and run this again."; }
    rm -f "$tmp_docker"
  fi
}

ensure_docker() {
  step "Docker"
  if [ -n "$MAC" ]; then
    if ! { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }; then
      die 5 "Docker is not running. Install Docker Desktop (https://www.docker.com/products/docker-desktop/), open it, wait until it says it is running, and run this again."
    fi
  elif ! compose_ok; then install_docker; fi
  if ! as_root docker info >/dev/null 2>&1; then
    if command -v systemctl >/dev/null 2>&1; then as_root systemctl enable --now docker >/dev/null 2>&1 || true; fi
    as_root docker info >/dev/null 2>&1 || die 5 "The Docker daemon is not running. Start it (systemctl start docker) and run this again."
  else
    if command -v systemctl >/dev/null 2>&1; then as_root systemctl enable docker >/dev/null 2>&1 || true; fi
  fi
  compose_ok || die 5 "Docker is installed but the Compose plugin (v2.20 or newer) is not. Install docker-compose-plugin and run this again."
  say "ok: Docker $(as_root docker version --format '{{.Server.Version}}' 2>/dev/null || echo installed), Compose $(as_root docker compose version --short)"
}

ensure_python() {
  py_ok() { command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; }
  py_ok && return 0
  step "Python 3"
  pkg_install python3 >/dev/null || true
  py_ok || die 5 "The setup wizard needs Python 3.10 or newer. Install python3 and run this again."
}

# ---------------------------------------------------------------------------------------------- the release

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

resolve_version() {
  if [ -z "$VERSION" ]; then
    case $TICO_VERSION_BAKED in
      @*) # Not built by a release: main's copy of this script.
        need_tool curl curl
        VERSION=$(curl -fsSL --retry 3 "$LATEST_API" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1) || true
        [ -n "$VERSION" ] || die 4 "Could not look up the latest release. Pass --version vX.Y.Z."
        warn "This copy of install.sh is not pinned to a release; using the latest, $VERSION." ;;
      *) VERSION=$TICO_VERSION_BAKED ;;
    esac
  fi
  case $VERSION in v*) ;; *) VERSION=v$VERSION ;; esac
  printf '%s' "$VERSION" | grep -Eq "$VERSION_RE" || die 2 "Version must look like v1.2.3, not '$VERSION'."
}

installed_version() { sed -n 's/^TICO_TAG=//p' "$DIR/.env" 2>/dev/null | head -n 1; }

fetch_bundle() {
  step "Downloading Tico $VERSION"
  need_tool curl curl
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  SUPPORT_ASKED=$work/support-asked
  TICO_SUPPORT_ASKED=$SUPPORT_ASKED
  export TICO_SUPPORT_ASKED
  trap 'exit 130' INT TERM
  bundle=tico-bundle-$VERSION.tar.gz
  base=$RELEASES/download/$VERSION
  curl -fsSL --retry 3 -o "$work/SHA256SUMS" "$base/SHA256SUMS" || die 4 "Could not download $base/SHA256SUMS. Is $VERSION a published release?"
  curl -fsSL --retry 3 -o "$work/$bundle" "$base/$bundle" || die 4 "Could not download $base/$bundle."
  want=$(awk -v f="$bundle" '{n = $2; sub(/^\*/, "", n)} n == f {print $1; exit}' "$work/SHA256SUMS")
  [ -n "$want" ] || die 4 "SHA256SUMS does not list $bundle; refusing to use it."
  got=$(sha256_of "$work/$bundle")
  [ "$got" = "$want" ] || die 4 "Checksum mismatch for $bundle (expected $want, got $got). Nothing was installed."
  # A bundle is ours, but never let an archive write outside the install directory.
  if tar -tzf "$work/$bundle" | grep -Eq '(^|/)\.\.(/|$)|^/'; then die 4 "The bundle has unsafe paths; refusing to unpack it."; fi
  say "ok: $bundle matches SHA256SUMS"

  as_root install -d -m 0755 "$DIR"
  if [ -n "$RUNNER" ]; then
    # A runner needs the compose file and nothing else; the server's compose.yaml stays out of this directory.
    tar -xzf "$work/$bundle" -C "$work" docker/runner.compose.yaml || die 4 "$bundle has no docker/runner.compose.yaml."
    # Older releases have no memory rule: such a runner gets no default cap, as it always had.
    tar -xzf "$work/$bundle" -C "$work" docker/runner-memory.sh 2>/dev/null || true
    as_root install -m 0644 "$work/docker/runner.compose.yaml" "$DIR/runner.compose.yaml"
    printf '%s\n' "$VERSION" | as_root tee "$DIR/.bundle-version" >/dev/null
    say "ok: runner.compose.yaml for $VERSION is in $DIR"
    return 0
  fi
  [ -f "$DIR/compose.yaml" ] && as_root cp "$DIR/compose.yaml" "$DIR/compose.yaml.previous"
  as_root rm -rf "$DIR/setup"
  as_root tar -xzf "$work/$bundle" -C "$DIR" --no-same-owner
  printf '%s\n' "$VERSION" | as_root tee "$DIR/.bundle-version" >/dev/null
  BUNDLED=1
  say "ok: unpacked into $DIR"
}

pin_tag() {
  # Only the TICO_TAG line changes; every other setting in .env is kept as it is.
  as_root sh -c "umask 077; { grep -v '^TICO_TAG=' '$DIR/.env' || true; printf 'TICO_TAG=%s\n' '$VERSION'; } > '$DIR/.env.new' && chmod 600 '$DIR/.env.new' && mv '$DIR/.env.new' '$DIR/.env'"
}

start_stack() {
  step "Starting Tico"
  installed_port=$(as_root sed -n 's/^TICO_PORT=//p' "$DIR/.env" | head -n 1 | tr -d '\"\047')
  HEALTH_URL=${TICO_INSTALL_HEALTH_URL:-http://127.0.0.1:${installed_port:-8765}/healthz}
  ( cd "$DIR" && as_root docker compose pull --quiet && as_root docker compose up -d ) || die 6 "docker compose failed in $DIR. Run 'docker compose logs' there."
  i=0
  while [ "$i" -lt "$HEALTH_TRIES" ]; do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then say "ok: the server answers on $HEALTH_URL"; return 0; fi
    i=$((i + 1)); sleep 5
  done
  die 6 "The server did not become healthy. Look at: cd $DIR && docker compose logs server"
}

run_wizard() {
  step "Setting up your team"
  ensure_python
  extra=
  [ -n "$TUNNEL" ] && extra="--front-door cloudflared"
  [ -n "$YES" ] && extra="$extra --yes"
  state_dir=$DIR/.setup
  as_root install -d -m 0700 "$state_dir"
  # A wizard that cannot ask (no terminal) needs its answers as flags; say so instead of failing halfway.
  if ( : </dev/tty ) 2>/dev/null; then tty_in=/dev/tty; else tty_in=; fi
  if [ -z "$tty_in" ] && ! printf '%s ' "$@" | grep -q -- '--non-interactive'; then
    set -- --non-interactive "$@"
    warn "No terminal to ask questions on; the wizard runs with flags only (see 'python3 -m setup --help')."
  fi
  # $extra is a fixed list of flags built above, never user text, so splitting it is intended.
  # shellcheck disable=SC2086
  set -- --target local --tico-version "$VERSION" $extra "$@"
  cd "$DIR"
  if [ "$(id -u)" = 0 ]; then
    if [ -n "$tty_in" ]; then TICO_INSTALL_DIR=$DIR TICO_SETUP_HOME=$state_dir python3 -m setup "$@" <"$tty_in"
    else TICO_INSTALL_DIR=$DIR TICO_SETUP_HOME=$state_dir python3 -m setup "$@" </dev/null; fi
  else
    keep=
    for v in TICO_OIDC_CLIENT_SECRET CLOUDFLARE_API_TOKEN CLOUDFLARE_TUNNEL_TOKEN OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY \
             TICO_INBOUND_KEY TICO_INBOUND_URL \
             LITESTREAM_ACCESS_KEY_ID LITESTREAM_SECRET_ACCESS_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN \
             TICO_SUPPORT TICO_SUPPORT_ASKED TICO_HQ_URL; do
      eval "[ -n \"\${$v:-}\" ]" && keep="$keep,$v"
    done
    # sudo drops the environment, so the secrets the caller set are kept by name; their values never reach a command line.
    # shellcheck disable=SC2024  # same: the terminal is opened by this shell, not by root
    if [ -n "$tty_in" ]; then
      sudo -H "--preserve-env=TICO_INSTALL_DIR,TICO_SETUP_HOME$keep" env TICO_INSTALL_DIR="$DIR" TICO_SETUP_HOME="$state_dir" python3 -m setup "$@" <"$tty_in"
    else
      sudo -H "--preserve-env=TICO_INSTALL_DIR,TICO_SETUP_HOME$keep" env TICO_INSTALL_DIR="$DIR" TICO_SETUP_HOME="$state_dir" python3 -m setup "$@" </dev/null
    fi
  fi
}

# A company file becomes the first start's team. The server image packs it with the code that reads it on start
# (backend/company_file/starter.py), so this computer needs no Python. A packed plan that fits goes in .env as
# TICO_COMPANY_FILE_B64; a bigger one is a private file the server container reads, named by TICO_COMPANY_FILE, as
# setup does for a server (setup/settings.py). It runs before .env is installed, so a file that does not pack leaves
# nothing behind to skip the team on a re-run.
# PACKER uses only what every 0.4 image has (starter.pack, MAX_BYTES, MAX_PACKED), so --version can pick an older one.
# Exit 2 with the reason on stderr for a file Tico cannot import.
PACKER='import sys
from backend.company_file import draft, starter
raw = sys.stdin.buffer.read(starter.MAX_BYTES + 1)
try:
    if len(raw) > starter.MAX_BYTES:
        raise draft.DraftError("the company file is too large")
    print(starter.pack(raw.decode("utf-8"), starter.MAX_PACKED))
except UnicodeError:
    print("Cannot read --company-file; use a readable UTF-8 company YAML file", file=sys.stderr)
    sys.exit(2)
except draft.DraftError as exc:
    print(exc, file=sys.stderr)
    sys.exit(2)
'
pack_company_file() {
  env_out=$1
  step "Packing $COMPANY_FILE"
  ( cd "$DIR" && as_root env TICO_TAG="$VERSION" docker compose pull --quiet server ) \
    || die 6 "docker compose could not pull the Tico image in $DIR. Run this again."
  # The packer exits 2 with the reason for a file Tico cannot import; any other failure is docker compose's.
  packed=$( cd "$DIR" && as_root env TICO_TAG="$VERSION" TICO_PACKER="$PACKER" docker compose run --rm -T --no-deps \
    -e TICO_PACKER server python -c 'import os; exec(os.environ["TICO_PACKER"])' <"$COMPANY_FILE" ) || {
    rc=$?
    rm -f "$env_out"
    [ "$rc" = 2 ] && die 2 "The company file is not one Tico can import (the reason is above). Nothing was installed."
    die 6 "docker compose could not pack the company file in $DIR (the error is above). Nothing was installed."
  }
  case $packed in
    ''|*[!A-Za-z0-9+/=]*) rm -f "$env_out"; die 5 "The server image did not give a packed company file. Nothing was installed." ;;
  esac
  if [ "${#packed}" -le "$ENV_PACKED_MAX" ]; then
    printf 'TICO_COMPANY_FILE_B64=%s\n' "$packed" >> "$env_out"
  else
    # The folder compose.yaml mounts read-only into the server (setup/contract.py), read by the container's user 10001.
    plan_dir=$DIR/company-file
    as_root install -d -m 0755 "$plan_dir"
    printf '%s\n' "$packed" | as_root tee "$plan_dir/plan.b64" >/dev/null
    if [ -n "$MAC" ]; then as_root chmod 0644 "$plan_dir/plan.b64"
    else as_root chown 10001:10001 "$plan_dir/plan.b64" && as_root chmod 0600 "$plan_dir/plan.b64"; fi
    printf 'TICO_COMPANY_FILE_DIR=%s\nTICO_COMPANY_FILE=/run/tico/company-file/plan.b64\n' "$plan_dir" >> "$env_out"
  fi
}

# The quick start: a server on this computer only. The server sees no domain and no TICO_AUTH_PROXY, so it serves
# 127.0.0.1 and the owner signs in with a token from the data volume.
run_local() {
  step "Setting up Tico on this computer"
  if [ -z "$OWNER_EMAIL" ]; then
    if ( : </dev/tty ) 2>/dev/null; then printf 'Your email address (you are the owner): '; read -r OWNER_EMAIL </dev/tty
    else die 2 "--local needs --owner-email (there is no terminal to ask on)."; fi
  fi
  printf '%s' "$OWNER_EMAIL" | grep -Eq '^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$' || die 2 "--owner-email must be a plain email address."
  if [ -z "$COMPANY" ]; then
    # With a company file the team is the file's, so the sample company's name would be wrong.
    if [ -n "$RECOMMENDED" ] && [ -z "$COMPANY_FILE" ]; then COMPANY="Sample Co"; else COMPANY="My Team"; fi
  fi
  printf '%s' "$COMPANY" | grep -Eq '^[^"$`\\]{1,80}$' || die 2 "--team-name is 1 to 80 characters without quotes, dollar signs, backticks or backslashes."
  env_tmp=$(mktemp)
  ( umask 077
    { printf '# Tico on this computer only. To add a domain and sign-in later, see docs/install.md.\n'
      printf 'TICO_TEAM_NAME="%s"\nTICO_COMPANY_NAME="%s"\nTICO_OWNER_EMAIL=%s\n' "$COMPANY" "$COMPANY" "$OWNER_EMAIL"
      [ -z "$OWNER_NAME" ] || printf 'TICO_OWNER_NAME="%s"\n' "$OWNER_NAME"
      printf 'TICO_PORT=%s\n' "${PORT:-8765}"
      [ -z "$RUNNER_URL" ] || printf 'TICO_PUBLIC_URL=%s\n' "$RUNNER_URL"
      printf 'COMPOSE_PROFILES=updater\nTICO_UPDATER_URL=http://updater:8080\nTICO_TAG=%s\n' "$VERSION"
      [ -z "$SUPPORT" ] || printf 'TICO_SUPPORT=%s\n' "$SUPPORT"
      if [ -n "$RECOMMENDED" ]; then
        # The recommended choices, read by the server on its first start (sample company, models preset, Gateway enrollment).
        # A company file is the team instead of the sample company, so the sample is not switched on.
        printf 'TICO_INSTALL_PRESET=recommended\n'
        [ -n "$COMPANY_FILE" ] || printf 'TICO_SAMPLE_COMPANY=on\n'
        printf 'TICO_MODELS_PRESET=subscriptions-gateway\n'
        [ -z "$GATEWAY_INVITE" ] || printf 'TICO_GATEWAY_INVITE=%s\n' "$GATEWAY_INVITE"
      fi
    } > "$env_tmp" )
  [ -z "$COMPANY_FILE" ] || pack_company_file "$env_tmp"
  as_root install -m 0600 "$env_tmp" "$DIR/.env"
  rm -f "$env_tmp"
  start_stack
  token=$(cd "$DIR" && as_root docker compose exec -T server cat /data/local-owner.token 2>/dev/null) || token=
  [ -z "$RECOMMENDED" ] || add_server_computer
  say ""
  say "Tico is running on this computer only."
  if [ -n "$token" ]; then say "Keep this reusable owner sign-in link private. Sign in as $OWNER_EMAIL: ${RUNNER_URL:-http://127.0.0.1:${PORT:-8765}}/api/v2/local-signin?token=$token"
  else say "Sign in as $OWNER_EMAIL with the token from: cd $DIR && docker compose exec server cat /data/local-owner.token"; fi
  say "Recover the sign-in token: cd $DIR && docker compose exec server cat /data/local-owner.token"
  [ -z "$COMPANY_FILE" ] || say "If the team does not load, Health in the app says why."
  if [ -n "$RECOMMENDED" ]; then
    if [ -n "$COMPANY_FILE" ]; then say "Your team from $COMPANY_FILE is loaded, and Tico Support is ${SUPPORT}."
    else say "The sample company is loaded and Tico Support is ${SUPPORT}."; fi
    if [ -n "$GATEWAY_INVITE" ]; then say "Tico Gateway: it connects on the first start, from the invite this install was given."
    else say "Tico Gateway: not set up (this install was given no invite). Bots use your Claude and ChatGPT subscriptions."; fi
    say "One step is left: in the app, Settings > Computers > Server computer > Sign in, to Claude and to ChatGPT."
  else
    if [ -n "$COMPANY_FILE" ]; then
      say "Your team from $COMPANY_FILE is loaded."
      say "In the app, use Add computer to join this computer."
    else
      say "In the app, follow Finish setup: name the team, pick its groups, and use Add computer to join this computer."
    fi
    say "Bots need an AI provider to run: Settings > Computers > Models, any time."
  fi
  say "Add a domain and sign-in for your humans later: docs/install.md, \"Add a domain and sign-in later\"."
}

# A fresh value for each write to the Tico API: the server keeps its answer under the key, so a retry reuses the value
# and a new request gets another.
idempotency_key() {
  if [ -r /proc/sys/kernel/random/uuid ]; then cat /proc/sys/kernel/random/uuid
  else printf 'tico-install-%s-%s-%s\n' "$(date +%s)" "$$" "$(od -An -N8 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')"; fi
}

# The recommended setup's computer: a runner on the server's own Docker network (docs/install.md, "A computer on the
# server"), joined with a one-time code the owner token asks the server for. The token goes to curl on stdin, never on a
# command line. A failure here leaves the server running and says how to add the computer by hand.
add_server_computer() {
  step "Adding a computer on this server"
  later="Add it later: Settings > Computers > Add computer, then the --runner command it shows with --server-network tico_default."
  if [ -z "$token" ]; then warn "No owner token to ask for a computer code. $later"; return 0; fi
  # No -f: an error answer's body holds the short reason the warning repeats. The server refuses a write without an Idempotency-Key.
  reply=$(printf 'header = "Authorization: Bearer %s"\n' "$token" \
    | curl -sS --config - -X POST -H 'Content-Type: application/json' -H "Idempotency-Key: $(idempotency_key)" -d '{}' \
      "http://127.0.0.1:${PORT:-8765}/api/v2/enrollments" 2>/dev/null) || reply=
  # An error answer has a "code" of its own ({"error": {"code": ..., "detail": ...}}), which is not a computer code.
  case $reply in *'"error"'*) code= ;; *) code=$(printf '%s' "$reply" | sed -n 's/.*"code"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p') ;; esac
  if [ -z "$code" ]; then
    detail=$(printf '%s' "$reply" | sed -n 's/.*"detail"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | tr -d '\r\n' | cut -c1-160)
    warn "The server did not give a computer code${detail:+ ($detail)}. $later"; return 0
  fi
  rm -rf "$work"
  # A subshell: the runner install sets its own directory and options, and its failure must not stop this one.
  ( server_computer_runner "$code" ) || warn "The server computer did not start. $later"
}

# The runner install's own settings for the server's computer; add_server_computer runs it in a subshell, so they stay there.
server_computer_runner() {
  RUNNER=1; LOCAL=''; RUNNER_URL=http://server:8765; RUNNER_CODE=$1; RUNNER_LABEL="Server computer"
  SERVER_NETWORK=tico_default; DIR=$DIR-runner
  run_runner
}

# A runner started with a bare `docker run -v tico-runner:/home/runner` keeps its login and repositories in that
# volume. The compose file can use the same volume, so switching to it keeps the enrolled runner instead of making a second.
runner_env() {
  volume_line=
  if [ -n "$RUNNER_SLUG" ]; then
    # Its own home volume, named like the compose default for its project, so it never shares the first runner's.
    volume_line=TICO_RUNNER_HOME_VOLUME=${RUNNER_PROJECT}_runner-home
  elif as_root docker volume inspect tico-runner >/dev/null 2>&1; then
    volume_line=TICO_RUNNER_HOME_VOLUME=tico-runner
    say "Found the tico-runner volume of an earlier docker run: the runner keeps its login, its repositories and its enrollment."
  fi
  env_tmp=$work_env
  ( umask 077
    { printf 'TICO_URL=%s\n' "$RUNNER_URL"
      printf 'TICO_CODE=%s\n' "$RUNNER_CODE"
      printf 'TICO_RUNNER_LABEL="%s"\n' "$RUNNER_LABEL"
      printf 'TICO_TAG=%s\nTICO_UPDATER_TAG=%s\n' "$VERSION" "$VERSION"
      # A new runner offers its screen unless --no-screen; the server's Screen setting decides who may open it.
      printf 'TICO_SCREEN=%s\n' "${SCREEN:-1}"
      [ -z "$volume_line" ] || printf '%s\n' "$volume_line"
    } > "$env_tmp" )
  as_root install -m 0600 "$env_tmp" "$DIR/.env"
  rm -f "$env_tmp"
}

# The join flags given on this run replace their keys in an existing .env; every other line stays.
update_runner_env() {
  env_tmp=$(mktemp)
  ( umask 077
    as_root cat "$DIR/.env" | awk -v url="$RUNNER_URL" -v code="$RUNNER_CODE" -v label="$RUNNER_LABEL" '
      /^TICO_URL=/ && url != "" { print "TICO_URL=" url; url = ""; next }
      /^TICO_CODE=/ && code != "" { print "TICO_CODE=" code; code = ""; next }
      /^TICO_RUNNER_LABEL=/ && label != "" { print "TICO_RUNNER_LABEL=\"" label "\""; label = ""; next }
      { print }
      END {
        if (url != "") print "TICO_URL=" url
        if (code != "") print "TICO_CODE=" code
        if (label != "") print "TICO_RUNNER_LABEL=\"" label "\""
      }' > "$env_tmp" )
  as_root install -m 0600 "$env_tmp" "$DIR/.env"
  rm -f "$env_tmp"
}

# A named runner is the stock compose file plus this override, which the updater reads next to it (docker/updater.py
# adds runner.override.yaml to every compose call, and no update replaces it): its own project name (so its containers,
# volumes and network are its own), its own container name, and an alias for its updater. The alias matters on the
# server's network, where the server's own updater is also called `updater`. A second run without --server-network
# keeps the network the first run set.
write_named_override() {
  net=$SERVER_NETWORK
  if [ -z "$net" ] && [ -f "$DIR/runner.override.yaml" ]; then
    net=$(as_root sed -n 's/^    name: //p' "$DIR/runner.override.yaml" | head -n 1)
  fi
  {
    printf 'name: %s\nservices:\n  runner:\n    container_name: %s\n    environment:\n      TICO_UPDATER_URL: http://%s-updater:8080\n' \
      "$RUNNER_PROJECT" "$RUNNER_PROJECT" "$RUNNER_PROJECT"
    [ -z "$net" ] || printf '    networks: [default, server]\n'
    printf '  updater:\n    networks:\n      default:\n        aliases: [%s-updater]\n' "$RUNNER_PROJECT"
    [ -z "$net" ] || printf 'networks:\n  server:\n    external: true\n    name: %s\n' "$net"
  } | as_root tee "$DIR/runner.override.yaml" >/dev/null
}

run_runner() {
  fetch_bundle
  step "Setting up the runner"
  if [ -f "$DIR/.env" ]; then
    changed=
    [ -z "$RUNNER_URL" ] || changed="$changed TICO_URL"
    [ -z "$RUNNER_CODE" ] || changed="$changed TICO_CODE"
    [ -z "$RUNNER_LABEL" ] || changed="$changed TICO_RUNNER_LABEL"
    if [ -n "$changed" ]; then
      say "Updating${changed} in $DIR/.env from the options you gave; its other settings are kept."
      update_runner_env
    else
      say "Keeping the settings in $DIR/.env."
    fi
    if [ -n "$FORCED" ]; then
      as_root sh -c "umask 077; { grep -v -e '^TICO_TAG=' -e '^TICO_UPDATER_TAG=' '$DIR/.env' || true; printf 'TICO_TAG=%s\nTICO_UPDATER_TAG=%s\n' '$VERSION' '$VERSION'; } > '$DIR/.env.new' && mv '$DIR/.env.new' '$DIR/.env'"
    fi
  else
    work_env=$(mktemp)
    runner_env
  fi
  if [ -n "$SCREEN" ]; then
    # The runner image has the display and VNC programs; the runner starts them while TICO_SCREEN is 1.
    as_root sh -c "umask 077; { grep -v '^TICO_SCREEN=' '$DIR/.env' || true; echo TICO_SCREEN=$SCREEN; } > '$DIR/.env.new' && mv '$DIR/.env.new' '$DIR/.env'"
  fi
  if as_root grep -qx 'TICO_SCREEN=1' "$DIR/.env"; then
    say "Screen on: TICO_SCREEN=1 in $DIR/.env (set it to 0, or run again with --no-screen, to turn it off)."
  fi
  # The bare container has the name compose wants; the volume it used stays.
  if [ -z "$RUNNER_SLUG" ] && as_root docker container inspect tico-runner >/dev/null 2>&1 \
     && [ -z "$(as_root docker container inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' tico-runner 2>/dev/null)" ]; then
    say "Replacing the container from an earlier docker run (its volume is kept)."
    as_root docker rm -f tico-runner >/dev/null
  fi
  if [ -n "$SERVER_NETWORK" ]; then
    printf '%s' "$SERVER_NETWORK" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$' || die 2 "--server-network is a Docker network name."
    as_root docker network inspect "$SERVER_NETWORK" >/dev/null 2>&1 || die 2 "No Docker network $SERVER_NETWORK on this computer (the server's is usually tico_default)."
  fi
  if [ -n "$RUNNER_SLUG" ]; then
    write_named_override
  elif [ -n "$SERVER_NETWORK" ]; then
    # Not part of the bundle, so an update keeps it; the updater adds it to its compose calls.
    printf 'services:\n  runner:\n    networks: [default, server]\nnetworks:\n  server:\n    external: true\n    name: %s\n' "$SERVER_NETWORK" \
      | as_root tee "$DIR/runner.override.yaml" >/dev/null
  fi
  # The default memory cap, unless .env has TICO_RUNNER_MEMORY (empty counts as set, and 0 means no limit). It goes in
  # the environment of compose, which outranks .env, so it is never written to .env: a later, bigger computer gets a new default.
  memory_env=
  if ! as_root grep -Eq '^[[:space:]]*(export[[:space:]]+)?TICO_RUNNER_MEMORY[[:space:]]*[=:]' "$DIR/.env" && [ -f "$work/docker/runner-memory.sh" ]; then
    cap=$(sh "$work/docker/runner-memory.sh" "$MEMINFO" 2>/dev/null) || cap=
    if [ -n "$cap" ]; then
      memory_env="TICO_RUNNER_MEMORY=$cap"
      say "Memory cap: $cap, a default for this computer (set TICO_RUNNER_MEMORY in $DIR/.env to change it; 0 means no limit)."
    fi
  fi
  files="-f runner.compose.yaml"
  [ -f "$DIR/runner.override.yaml" ] && files="$files -f runner.override.yaml"
  # shellcheck disable=SC2086  # $files is two or four words on purpose
  ( cd "$DIR" && as_root env $memory_env docker compose $files pull --quiet && as_root env $memory_env docker compose $files up -d ) \
    || die 6 "docker compose failed in $DIR. Run 'docker compose $files logs' there."
  say ""
  say "The runner is starting; it shows online in Settings > Computers in a minute."
  [ -z "$RUNNER_SLUG" ] || say "Its files are in $DIR and its container is $RUNNER_PROJECT."
  say "It follows the server's release through its updater; sign the bots in to a model from Settings > Computers."
}

# ---------------------------------------------------------------------------------------------- main

preflight
if [ -n "$RUNNER" ]; then resolve_version; ensure_docker; run_runner; exit 0; fi
if [ -n "$DOCKER_ONLY" ]; then ensure_docker; say "Docker is ready."; exit 0; fi
# A bad version should fail before anything is installed.
resolve_version
ensure_docker
say "Installing Tico $VERSION into $DIR"
say "Tico counts active installs anonymously (a random ID, version, and three yes/no activity flags). Turn off: Settings > General or TICO_TELEMETRY=off."
say "Details: PRIVACY.md in the Tico repository."

if [ -f "$DIR/.env" ]; then
  # The team is loaded on a server's first start only: an install that has its .env keeps the team it has.
  [ -z "$COMPANY_FILE" ] || warn "--company-file is used when Tico is first installed; $DIR/.env exists, so the file was not used."
  have=$(installed_version)
  if [ -n "$have" ] && [ "$have" != "$VERSION" ] && [ -z "$FORCED" ] \
     && [ "$(printf '%s\n%s\n' "$have" "$VERSION" | sort -V | tail -n 1)" = "$have" ]; then
    # The one-click updater moves TICO_TAG ahead of a pinned installer; an old installer must not undo that.
    say "Installed $have is newer than this installer ($VERSION): repairing it as it is. Pass --version to change it."
    VERSION=$have
  else
    fetch_bundle
    if [ "$have" != "$VERSION" ]; then
      say "Changing the installed release from ${have:-unknown} to $VERSION; your .env is kept."
      pin_tag
    else
      say "Already at $VERSION: repairing and restarting; your .env is kept."
    fi
  fi
  # --port and --support given again are saved; every other line of .env stays as it is.
  if [ -n "$PORT$SUPPORT_GIVEN" ]; then
    drop=; [ -z "$PORT" ] || drop=PORT; [ -z "$SUPPORT_GIVEN" ] || drop=${drop:+$drop|}SUPPORT
    env_tmp=$(mktemp)
    ( umask 077
      { as_root cat "$DIR/.env" | sed -E "/^TICO_($drop)=/d"
        [ -z "$PORT" ] || printf 'TICO_PORT=%s\n' "$PORT"
        [ -z "$SUPPORT_GIVEN" ] || printf 'TICO_SUPPORT=%s\n' "$SUPPORT_GIVEN"; } > "$env_tmp" )
    as_root install -m 0600 "$env_tmp" "$DIR/.env"
    rm -f "$env_tmp"
    [ -z "$SUPPORT_GIVEN" ] || say "Tico Support is now $SUPPORT_GIVEN (saved in $DIR/.env)."
  fi
  start_stack
  say ""
  say "Tico $VERSION is running from $DIR."
  exit 0
fi

fetch_bundle
if [ -n "$LOCAL" ]; then run_local; exit 0; fi
run_wizard "$@" || die 6 "The setup wizard did not finish. Run this again: it resumes where it stopped, and finished steps are skipped."
say ""
say "Next: add the computers that run your bots (Settings > Computers > Add computer in the app)."
say "Later checks: cd $DIR && sudo ./scripts/tico-setup doctor --domain <your domain>"
