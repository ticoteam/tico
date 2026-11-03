# The turn: bot code, as the unprivileged user. Prints "name=result" lines.
remote="$1"
say() { printf '%s=%s\n' "$1" "$2"; }
say uid "$(id -u)"
say caps "$(grep CapEff /proc/self/status | tr -d '\t ')"
cat /home/runner/runner.json >/dev/null 2>&1 && say read_registration yes || say read_registration no
ls /home/runner/state-* >/dev/null 2>&1 && say read_state yes || say read_state no
rm -f /home/runner/runner.json 2>/dev/null; [ -e /home/runner/runner.json ] && say delete_registration no || say delete_registration yes
env | grep -q REGISTRATION-SECRET && say registration_in_env yes || say registration_in_env no
say harness "$(codex login status 2>&1)"
mkdir -p /tmp/turn && cd /tmp/turn && git init -q -b main . && echo hi > f && git add f && git commit -qm "from the turn"
git remote add origin "$remote" && git push -q origin main 2>&1 && say push ok || say push failed
