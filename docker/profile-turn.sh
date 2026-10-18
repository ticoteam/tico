# shellcheck shell=sh
# A login shell in a bot's turn (Codex runs `bash -lc`) keeps the turn's PATH. /etc/profile resets PATH, which
# would hide the turn's `gh` wrapper (runner/git_credentials.py) and leave `gh` on a token minted at turn start.
if [ -n "${TICO_TURN_PATH:-}" ]; then
  PATH="$TICO_TURN_PATH"
  export PATH
fi
