#!/usr/bin/env bash
# The runner's contract with the real `claude` and `codex` CLIs (runner-rs/src/cli_contract.rs, docs/harnesses.md).
# Runs whichever of them are on PATH, in an empty home, up to authentication and never through it.
#
#   scripts/cli_contract.sh            # the CLIs on PATH
#   scripts/cli_contract.sh --latest   # first install the newest of each into a throwaway folder, as a cloud
#                                      # computer's runner does (`[update] policy = "latest"`), and check those
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${1:-}" = --latest ]; then
  tools="$(mktemp -d)"
  trap 'rm -r "$tools"' EXIT
  npm install --silent --no-audit --no-fund --prefix "$tools" @anthropic-ai/claude-code@latest @openai/codex@latest
  export PATH="$tools/node_modules/.bin:$PATH"
fi
for cli in claude codex; do
  command -v "$cli" >/dev/null || { echo "cli contract: no \`$cli\` on PATH (try --latest)" >&2; exit 2; }
done
# One test at a time: each starts a real CLI, and two of them wait on the provider's answer.
cargo test --locked --manifest-path "$root/runner-rs/Cargo.toml" cli_contract:: -- --ignored --test-threads 1 --nocapture
