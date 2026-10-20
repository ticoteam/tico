#!/bin/bash
# The full default Python suite on this computer, in one of the box's heavy-job slots (scripts/heavy.sh), at low
# priority. It may wait for a slot, so a bot starts it detached and reads the log later.
#
#   scripts/full-suite.sh [pytest args...]        (Python: TICO_PYTHON, else .venv/bin/python)
set -u
cd "$(dirname "$0")/.." || exit 2
exec scripts/heavy.sh "${TICO_PYTHON:-.venv/bin/python}" -m pytest -q "$@"
