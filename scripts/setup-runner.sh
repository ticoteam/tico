#!/usr/bin/env bash
# One-time setup for a downloaded Tico enrollment file on a Mac.
# Usage: scripts/setup-runner.sh [--env <slug>] /path/to/tico-enrollment.json [projects-directory]
set -euo pipefail

TICO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_SLUG=""
POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_SLUG="${2:-}"; shift 2 ;;
    --env=*) ENV_SLUG="${1#*=}"; shift ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
if [ "${#POSITIONAL[@]}" -gt 0 ]; then set -- "${POSITIONAL[@]}"; else set --; fi

ENROLLMENT_INPUT="${1:-}"

if [ -z "$ENROLLMENT_INPUT" ] || [ ! -f "$ENROLLMENT_INPUT" ]; then
  echo "usage: scripts/setup-runner.sh [--env <slug>] /path/to/tico-enrollment.json [projects-directory]" >&2
  exit 2
fi
if [ "$(uname -s)" != "Darwin" ]; then
  echo "The automatic runner setup currently supports macOS only." >&2
  exit 2
fi

ENROLLMENT_DIR="$(cd "$(dirname "$ENROLLMENT_INPUT")" && pwd)"
ENROLLMENT_FILE="$ENROLLMENT_DIR/$(basename "$ENROLLMENT_INPUT")"
chmod 0600 "$ENROLLMENT_FILE"

# The newest supported Python on this Mac; the server images run 3.15, and 3.12 still works.
PYTHON_BIN="$(command -v python3.15 || command -v python3.14 || command -v python3.13 || command -v python3.12 || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.12 or newer (3.15 recommended) is required. Install it, then run this command again." >&2
  exit 1
fi

read_field() {
  "$PYTHON_BIN" -c 'import json,sys; value=json.load(open(sys.argv[1])).get(sys.argv[2], ""); print(value if isinstance(value, str) else "")' "$1" "$2"
}

RUNNER_URL="$(read_field "$ENROLLMENT_FILE" url)"
RUNNER_LABEL="$(read_field "$ENROLLMENT_FILE" label)"
if [ -z "$RUNNER_URL" ] || [ -z "$RUNNER_LABEL" ]; then
  echo "This enrollment file is missing its Tico URL or computer name. Download a new one from Settings." >&2
  exit 1
fi

# An environment keeps its registration, state, and workspace together; without one this is the
# original single-company layout.
CONFIG_FILE="${TICO_RUNNER_CONFIG:-$HOME/.config/tico/runner.json}"
ENVIRONMENT_ID=""
DEFAULT_PROJECTS="$(cd "$TICO_ROOT/.." && pwd)"
if [ -n "$ENV_SLUG" ]; then
  ENV_DIR="${TICO_ENVIRONMENTS_DIR:-$HOME/.config/tico/environments}/$ENV_SLUG"
  [ -f "$ENV_DIR/environment.json" ] \
    || { echo "no environment $ENV_SLUG at $ENV_DIR (scripts/tico env list)" >&2; exit 1; }
  CONFIG_FILE="$ENV_DIR/runner.json"
  ENVIRONMENT_ID="$(read_field "$ENV_DIR/environment.json" id)"
  DEFAULT_PROJECTS="$(read_field "$ENV_DIR/environment.json" workspace)"
fi
PROJECTS_DIR="${2:-$DEFAULT_PROJECTS}"

if [ -f "$CONFIG_FILE" ]; then
  echo "This Mac is already registered at $CONFIG_FILE. Revoke or move that registration deliberately before replacing it." >&2
  exit 1
fi

VENV="$TICO_ROOT/runtime/runner-venv"
if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q -r "$TICO_ROOT/backend/requirements.txt"
mkdir -p "$PROJECTS_DIR/secrets"
chmod 0700 "$PROJECTS_DIR" "$PROJECTS_DIR/secrets"
# `python -m` puts the working directory on the module path, so the runner runs from the checkout.
cd "$TICO_ROOT"
# The runner is the Rust binary: download this release's (or build it with cargo) before registering.
"$VENV/bin/python" -m runner.engine prepare
RUNNER_BIN="$("$VENV/bin/python" -m runner.engine path)"
export TICO_ROOT TICO_RUNNER_PYTHON="$VENV/bin/python"
"$RUNNER_BIN" --config "$CONFIG_FILE" enroll --url "$RUNNER_URL" \
  --code-file "$ENROLLMENT_FILE" --label "$RUNNER_LABEL" --projects "$PROJECTS_DIR" \
  ${ENVIRONMENT_ID:+--environment "$ENVIRONMENT_ID"}

if [ -n "$ENV_SLUG" ]; then
  TICO_RUNNER_PYTHON="$VENV/bin/python" "$TICO_ROOT/scripts/tico" -e "$ENV_SLUG" install bot
else
  TICO_RUNNER_PYTHON="$VENV/bin/python" TICO_RUNNER_CONFIG="$CONFIG_FILE" "$TICO_ROOT/scripts/tico" install bot
fi
if ! "$RUNNER_BIN" --config "$CONFIG_FILE" doctor; then
  echo >&2
  echo "This computer is registered, but one or more readiness checks need attention." >&2
  echo "Fix the reported repository, runtime, login, model, or configuration issue, then run:" >&2
  echo "  scripts/tico${ENV_SLUG:+ -e $ENV_SLUG} doctor" >&2
  exit 1
fi
"$RUNNER_BIN" --config "$CONFIG_FILE" status

echo
echo "This computer is registered and its runner starts automatically at login."
echo "Return to Tico Settings to assign bots. Readiness details appear after the next heartbeat."
