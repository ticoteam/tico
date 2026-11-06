#!/usr/bin/env bash
# Diff coverage for this branch, the check the merge queue runs before a change lands: the Python statements the branch
# adds to backend/ and clients/ must run under the tests its change selects (scripts/diff_coverage.py). It runs those
# tests the way the queue does, in one process, so run it on a RunsOn VM against a pushed commit:
#   python3 scripts/remote.py --setup python --cpus 16 -- bash scripts/diff_coverage.sh
# BASE names the branch the change lands on (default origin/main). Runner conformance tests are not counted here.
set -euo pipefail
cd "$(dirname "$0")/.."
export TICO_PYTEST_WORKERS="${TICO_PYTEST_WORKERS:-$(nproc)}"
base=$(git merge-base "${BASE:-origin/main}" HEAD)
mkdir -p ci-results
# The queue's own event for a change against its base: no pull request checks (the changelog guard, the merge parent).
python3 scripts/pr_checks.py plan --event merge_group --base "$base" --head HEAD --output ci-results/plan.json > /dev/null
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
