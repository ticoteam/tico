#!/bin/sh
# Receipt for Dev 679 at the committed head, then one removal control. Runs inside one heavy slot.
cd /home/runner/workspace/tasks/ba072e2d/ticoteam__tico || exit 2
PY=/home/runner/workspace/tasks/338fcce5/ticoteam__tico/.venv/bin/python
[ -x "$PY" ] || { echo "NO PYTHON"; exit 2; }
git diff --quiet HEAD -- backend || { echo "DIRTY TREE"; exit 2; }
echo "HEAD $(git rev-parse HEAD)"
$PY -m pytest -n0 -q backend/tests/test_ceo_bot.py backend/tests/test_openapi_v2.py 2>&1 | tail -3
echo "RECEIPT-EXIT $?"
# Control: drop the personal-token check. The token test must fail on an assertion.
sed -i 's/ or getattr(who, "via_token", False):$/:/' backend/ceo_bot.py
grep -q 'via_token' backend/ceo_bot.py && { echo "CONTROL EDIT DID NOT APPLY"; git checkout -- backend; exit 2; }
$PY -m pytest -n0 -q backend/tests/test_ceo_bot.py -k "own_yes_or_no" 2>&1 | grep -E "AssertionError|assert |passed|failed|Error" | tail -4
git checkout -- backend/ceo_bot.py
git diff --quiet HEAD -- backend && echo "RESTORED"
