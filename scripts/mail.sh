#!/usr/bin/env bash
# The company's mail tool. Every employee with gmail access calls this and nothing else.
#
#   scripts/mail.sh doctor
#   scripts/mail.sh inbox --as influencer --new
#   scripts/mail.sh rules run --as ana --mailbox ana@acme.example --dry-run
#
# First use builds a venv at <projects>/runtime/mail/venv from connectors/mail/requirements.txt;
# after that it is just an exec. Rebuild it any time by deleting that directory.
# A Linux runner has no such folder layout: a turn gets TICO_PROJECTS_DIR (the bot repos and secrets, in the
# runner's volume and writable by the bot user), so the venv lands under it; TICO_MAIL_VENV names another.
# `scripts/mail.sh --setup` only builds or updates the venv, so a caller can do it with a long timeout.
set -euo pipefail
HUB="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${TICO_MAIL_VENV:-${TICO_PROJECTS_DIR:-$(dirname "$HUB")}/runtime/mail/venv}"
REQ="$HUB/connectors/mail/requirements.txt"

# A venv belongs to the Python that built it: after a new runner image (or a Python upgrade on a Mac) its
# bin/python still runs, but the packages sit under the old lib/pythonX.Y, so build it again.
PY="python$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ -x "$VENV/bin/python" ] && [ ! -d "$VENV/lib/$PY" ]; then
  echo "mail: the venv at $VENV was built by another Python, rebuilding it for $PY" >&2
  rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  echo "mail: building the venv at $VENV (first use, about a minute)" >&2
  mkdir -p "$(dirname "$VENV")"
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip >&2
  "$VENV/bin/python" -m pip install --quiet -r "$REQ" >&2
  cp "$REQ" "$VENV/.requirements.txt"
elif ! cmp -s "$REQ" "$VENV/.requirements.txt" 2>/dev/null; then
  echo "mail: requirements changed, updating the venv" >&2
  "$VENV/bin/python" -m pip install --quiet -r "$REQ" >&2
  cp "$REQ" "$VENV/.requirements.txt"
fi

[ "${1:-}" = "--setup" ] && exit 0
cd "$HUB"
exec "$VENV/bin/python" -m connectors.mail "$@"
