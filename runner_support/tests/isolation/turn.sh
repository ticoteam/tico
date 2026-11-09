#!/bin/sh
# The turn: bot code, as the unprivileged user. Prints "name=result" lines.
remote="$1"
say() { printf '%s=%s\n' "$1" "$2"; }
say uid "$(id -u)"
say caps "$(grep CapEff /proc/self/status | tr -d '\t ')"
if cat /home/runner/runner.json >/dev/null 2>&1; then say read_registration yes; else say read_registration no; fi
if ls /home/runner/state-* >/dev/null 2>&1; then say read_state yes; else say read_state no; fi
rm -f /home/runner/runner.json 2>/dev/null; if [ -e /home/runner/runner.json ]; then say delete_registration no; else say delete_registration yes; fi
if env | grep -q REGISTRATION-SECRET; then say registration_in_env yes; else say registration_in_env no; fi
say harness "$(codex login status 2>&1)"
mkdir -p /tmp/turn && cd /tmp/turn && git init -q -b main . && echo hi > f && git add f && git commit -qm "from the turn"
if git remote add origin "$remote" && git push -q origin main 2>&1; then say push ok; else say push failed; fi
