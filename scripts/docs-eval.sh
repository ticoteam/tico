#!/usr/bin/env bash
# The Librarian eval (docs/librarian.md): loads docs-eval/fixture/ into a live Tico with `tico doc write`, asks each
# question in docs-eval/questions.yaml through POST /api/v2/docs/ask, waits for the answers and scores citations
# and "Not in the docs." on demand only, never in CI, and never against a company's real Tico.
#   TICO_URL=https://tico.example.com TICO_TOKEN=<personal API token> scripts/docs-eval.sh [--only ID] [--keep] [--wait S]
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." && pwd)"
PYTHON="${TICO_PYTHON:-python3}"
exec "$PYTHON" "$ROOT/docs-eval/run.py" "$@"
