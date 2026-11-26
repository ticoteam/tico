"""P01's ❓ row (spec "Bots' questions checked after save", criterion 1): how long a bot's question to a person waits
when the built-in plain-words rule ("Asks to a person", Warn) is in force and the judge takes 180 ms.

    .venv/bin/python -m pytest bench/perf-questions-late.py -n 0 -s -q

Synthetic fixtures only (Acme, backend/tests/helpers/org_rules.org). The fake judge answers "not broken" after
JUDGE_MS, standing in for the production rule-check call, as in the P01 bench (specs/tools/p01-rule-check-bench.py in
the Performance Owner's repository). The late checks' drain thread runs as in production, beside the requests.
Prints the median and p95 of N ❓s and the judge calls made in all."""

import statistics
import time

from backend import org_rule_check
from backend import org_rules as OR
from backend.tests.helpers.api import headers
from backend.tests.helpers.org_rule_check import Judge, task_for_ops
from backend.tests.helpers.org_rules import org

N, JUDGE_MS = 20, 180


class SlowJudge(Judge):
    def __call__(self, state, questions, label=None):
        time.sleep(JUDGE_MS / 1000)
        return super().__call__(state, questions, label)


def p95(ms):
    ms = sorted(ms)
    return ms[max(0, int(len(ms) * 0.95) - 1)]


def test_bench(api):
    org(api)
    store = api.app.state.store
    with store.read() as c:
        assert c.execute("SELECT if_broken FROM org_rules WHERE text=?", (OR.ASKS_TO_A_PERSON,)).fetchone()[0] == "warn"
    judge = api.app.state.judge = SlowJudge(store)
    task = task_for_ops(api)
    ms = []
    for n in range(N):
        line = f"Which of the two launch note drafts goes out in week {n + 2}?"
        t = time.perf_counter()
        r = api.post(
            f"/api/v2/tasks/{task['id']}/questions", json={"to": "ana", "text": line}, headers=headers("ops-test")
        )
        ms.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200, r.text
    org_rule_check.drain_late(store, judge)
    print(
        f"P01 question  one warn rule (built-in)  median {statistics.median(ms):6.1f} ms  p95 {p95(ms):6.1f} ms"
        f"  ({N} ❓s, judge {JUDGE_MS} ms, {len(judge.calls)} judge calls in all)"
    )
