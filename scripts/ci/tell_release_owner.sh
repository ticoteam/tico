#!/usr/bin/env bash
# Hand something on the release path to its owner, the Release Owner bot on Tico Team: a merge queue group that failed,
# a test that passed only on its retry, or a release validation of main that went red. The bot decides what it is and
# fixes it the same day: the test at its cause, or deletes it (docs/releasing.md#who-fixes-a-red-or-flaky-test).
#
#   scripts/ci/tell_release_owner.sh KIND [DETAIL]
#
# KIND is queue-failed, queue-flaky or validation-red; DETAIL is a short line (the flaky tests, say). It posts a small
# JSON body to TICO_RELEASE_OWNER_HOOK, an incoming webhook on Tico Team, signed with TICO_RELEASE_OWNER_HOOK_SECRET
# (HMAC-SHA256 hex in X-Hub-Signature-256). Without both (a fork, a copy of the repository) it does nothing. It never
# fails its job: a hand-off that cannot be sent is a warning.
set -uo pipefail

kind="${1:?usage: tell_release_owner.sh KIND [DETAIL]}"
detail="${2:-}"
if [ -z "${TICO_RELEASE_OWNER_HOOK:-}" ] || [ -z "${TICO_RELEASE_OWNER_HOOK_SECRET:-}" ]; then
  echo "No Release Owner hook is set up; nothing sent."
  exit 0
fi
run="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
body="$(jq -cn --arg kind "$kind" --arg run "$run" --arg sha "${GITHUB_SHA:-}" --arg ref "${GITHUB_REF:-}" \
  --arg event "${GITHUB_EVENT_NAME:-}" --arg detail "${detail:0:1500}" \
  '{kind: $kind, run: $run, sha: $sha, ref: $ref, event: $event, detail: $detail}')"
signature="$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$TICO_RELEASE_OWNER_HOOK_SECRET" -r | cut -d' ' -f1)"
# One hand-off per run attempt and kind: a retried delivery is the same call to Tico, which drops the repeat.
if ! curl -fsS --max-time 20 --retry 3 --retry-all-errors -o /dev/null \
  -H 'Content-Type: application/json' \
  -H "X-Hub-Signature-256: sha256=$signature" \
  -H "Idempotency-Key: ${GITHUB_RUN_ID:-0}-${GITHUB_RUN_ATTEMPT:-1}-$kind" \
  --data "$body" "$TICO_RELEASE_OWNER_HOOK"; then
  echo "::warning title=Release Owner not told::The $kind hand-off to Tico Team could not be sent; see $run"
fi
exit 0
