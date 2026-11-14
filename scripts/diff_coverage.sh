#!/usr/bin/env bash
# Diff coverage for this branch, the gate the PR checks (on a ready pull request) and the merge queue run: the Python
# statements the branch adds to backend/ and clients/ must run under the tests its change selects
# (scripts/diff_coverage.py). It runs those tests the way the queue does, in one process, so it is as heavy as the
# change's whole Python selection; the ready pull request's checks run the same gate.
# BASE names the branch the change lands on (default origin/main). Runner conformance tests are not counted here.
set -euo pipefail
cd "$(dirname "$0")/.."
export TICO_PYTEST_WORKERS="${TICO_PYTEST_WORKERS:-$(nproc)}"
base=$(git merge-base "${BASE:-origin/main}" HEAD)
mkdir -p ci-results
# Results from an earlier run would count toward this one's gate.
rm -f ci-results/coverage-*.json
# The queue's own event for a change against its base: no pull request checks (the changelog guard, the merge parent).
TICO_PLAN_LOCAL=1 python3 scripts/pr_checks.py plan --event merge_group --base "$base" --head HEAD --output ci-results/plan.json > /dev/null
status=0
while read -r suite; do
  python3 scripts/pr_checks.py run "$suite" --plan ci-results/plan.json || status=1
done < <(python3 -c '
import json, sys
plan = json.load(open(sys.argv[1]))
for job in plan["matrix"]["include"]:
    if job["suite"].startswith(("python-", "contracts")):
        print(job["suite"])
' ci-results/plan.json)
python3 scripts/diff_coverage.py gate --plan ci-results/plan.json --results ci-results || status=1
exit "$status"
