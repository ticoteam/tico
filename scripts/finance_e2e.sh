#!/usr/bin/env bash
# Finance end to end on this computer: a fake Mercury (scripts/fake_mercury.py, 127.0.0.1:18790) and the Tico demo
# (Acme, 127.0.0.1:18766) that reads it through TICO_MERCURY_BASE. Nothing leaves the computer, and the demo's files
# are a new temporary folder that goes when the demo stops. Ctrl+C stops both, by the PIDs this script started.
#
# Sign-in: the demo has no account and no password. Its first page visit signs you in as Ana, the owner
# (ana@acme.example), with the local owner session it writes when it starts: open http://localhost:18766.
# View as a teammate: the owner reads Finance as anyone. In the app, the Finance "View as" control does it (owner only,
# read only); in an address, add ?view_as=human:<id> to a #/finance page, for example #/finance/cards?view_as=human:<id>.
# Each Finance read takes view_as= too. The demo's people are the ones in backend/demo_content.py.
# Connect Mercury in Finance > Sources with any token: the fake accepts any Bearer token.
#
# Usage: scripts/finance_e2e.sh            (PYTHON=/path/to/python picks the interpreter; Python 3.15 as the Dockerfile)
#        scripts/finance_e2e.sh & then, in another shell, PYTHON=... uv run ... scripts/finance_e2e_smoke.py
set -u
cd "$(dirname "$0")/.." || exit 1
PY="${PYTHON:-python3}"
# Other ports for a second stack on the same computer: FINANCE_FAKE_PORT=18852 FINANCE_DEMO_PORT=18851.
FAKE_PORT="${FINANCE_FAKE_PORT:-18790}"
DEMO_PORT="${FINANCE_DEMO_PORT:-18766}"

"$PY" scripts/fake_mercury.py --port "$FAKE_PORT" &
FAKE_PID=$!
TICO_MERCURY_BASE="http://127.0.0.1:${FAKE_PORT}/api/v1" "$PY" -m backend.demo --port "$DEMO_PORT" &
DEMO_PID=$!

cleanup() {
  kill "$DEMO_PID" "$FAKE_PID" 2>/dev/null
  wait "$DEMO_PID" "$FAKE_PID" 2>/dev/null
}
trap 'cleanup; exit 130' INT TERM
trap cleanup EXIT

echo "fake Mercury: pid $FAKE_PID, http://127.0.0.1:$FAKE_PORT/api/v1"
echo "Tico demo:    pid $DEMO_PID, http://localhost:$DEMO_PORT (waiting for it to answer ...)"
for _ in $(seq 1 120); do
  if curl -s -o /dev/null "http://127.0.0.1:$DEMO_PORT/"; then
    echo "ready: open http://localhost:$DEMO_PORT (Ctrl+C stops both)"
    break
  fi
  sleep 1
done
wait "$DEMO_PID"
