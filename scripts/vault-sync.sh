#!/usr/bin/env bash
# Pull the company credentials the hub uses out of a 1Password vault into the
# env files under ~/tico-work/secrets/. Values are never printed. Idempotent: each variable
# is replaced in place or appended. Run it after adding or rotating an item in the vault.
#
# Usage: scripts/vault-sync.sh            # sync everything in the map
#        scripts/vault-sync.sh POSTHOG_API_KEY # one variable
#
# The map is a file, one line per variable (VAULT_SYNC_MAP, default secrets/vault-map.txt),
# `|`-separated:  ENV_VAR | op://<vault>/<item>/<field> | target env file (optional)
# The target defaults to _shared.env, which every bot run inherits. Name a bot's file (cro.env)
# to reach only that employee: the runner loads _shared.env, then <slug>.env, which wins.
#
# Headless: the 1Password service account scoped to the company's vault, OP_SERVICE_ACCOUNT_TOKEN
# in secrets/_shared.env (never in chat or git). The runner uses the same token to resolve
# `op://vault/item/field` values in secrets files at turn start and strips it from the bot's
# environment (runner_support/op.py). This script is the by-hand complement: plain values
# for connectors and checks run outside a bot turn.
set -euo pipefail
SECRETS="$(cd "$(dirname "$0")/../.." && pwd)/secrets"
MAP_FILE="${VAULT_SYNC_MAP:-$SECRETS/vault-map.txt}"
[ -f "$MAP_FILE" ] || { echo "No map at $MAP_FILE: one 'ENV_VAR | op://vault/item/field | target.env' line per credential"; exit 1; }
MAP="$(cat "$MAP_FILE")"
command -v op >/dev/null || { echo "1Password CLI (op) is not installed"; exit 1; }
if [ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ] && [ -f "$SECRETS/_shared.env" ]; then
  OP_SERVICE_ACCOUNT_TOKEN="$(grep '^OP_SERVICE_ACCOUNT_TOKEN=' "$SECRETS/_shared.env" | head -1 | cut -d= -f2-)"; export OP_SERVICE_ACCOUNT_TOKEN
fi
trim() { local s="$*"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }
only="${1:-}"; ok=0; missing=0
while IFS='|' read -r var ref target; do
  var=$(trim "$var"); ref=$(trim "$ref"); target=$(trim "${target:-}")
  [ -z "$var" ] && continue
  [ -n "$only" ] && [ "$var" != "$only" ] && continue
  envf="$SECRETS/${target:-_shared.env}"; touch "$envf"; chmod 600 "$envf"
  if val=$(op read "$ref" 2>/dev/null) && [ -n "$val" ]; then
    tmp=$(mktemp); grep -v "^${var}=" "$envf" > "$tmp" || true
    printf '%s=%s\n' "$var" "$val" >> "$tmp"; cat "$tmp" > "$envf"; rm -f "$tmp"
    echo "ok       $var -> $(basename "$envf")  (${#val} chars, from ${ref#op://})"; ok=$((ok+1))
  else
    echo "missing  $var -> $(basename "$envf")  (${ref#op://} is empty or absent)"; missing=$((missing+1))
  fi
done <<< "$MAP"
echo "$ok synced, $missing missing -> $SECRETS/"
[ "$missing" -eq 0 ]
