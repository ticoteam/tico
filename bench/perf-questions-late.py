"""P01's ❓ row (spec "Bots' questions checked after save" v3, criterion 1): how long a bot's question to a person waits
when the built-in plain-words rule ("Asks to a person", Warn) is in force and the judge takes 180 ms, against the same
route with no rule at all, in the same session.

    .venv/bin/python -m pytest bench/perf-questions-late.py -n 0 -s -q

Criterion 1 (v3): with the rule, the median is at most 10 ms and the p95 at most 15 ms above the no-rule run's.

Synthetic fixtures only (Acme, backend/tests/helpers/org_rules.org). The fake judge answers "not broken" after
JUDGE_MS, standing in for the production rule-check call, as in the P01 bench (specs/tools/p01-rule-check-bench.py in
the Performance Owner's repository). The late checks' drain thread runs as in production, beside the requests. Runs
alternate no rule / rule ROUNDS times, each on a task of its own, so both rows share the session, the warm-up and the computer's load. Prints
each round and the pooled median and p95 of each row, the gap between them, and whether criterion 1 is met."""

import statistics
import time

from backend import org_rule_check
from backend import org_rules as OR
from backend.tests.helpers.api import headers
from backend.tests.helpers.org_rule_check import Judge, task_for_ops
from backend.tests.helpers.org_rules import org

N, JUDGE_MS, ROUNDS = 20, 180, 10
MEDIAN_GAP_MS, P95_GAP_MS = 10, 15


class SlowJudge(Judge):
    def __call__(self, state, questions, label=None):
        time.sleep(JUDGE_MS / 1000)
        return super().__call__(state, questions, label)


def p95(ms):
    ms = sorted(ms)
    return ms[max(0, int(len(ms) * 0.95) - 1)]


def builtin(store, status):
    with store.transaction() as c:
        c.execute("UPDATE org_rules SET status=? WHERE text=?", (status, OR.ASKS_TO_A_PERSON))


def asks(api, task, n0):
    ms = []
    for n in range(n0, n0 + N):
        line = f"Which of the two launch note drafts goes out in week {n + 2}?"
        t = time.perf_counter()
        r = api.post(
            f"/api/v2/tasks/{task['id']}/questions", json={"to": "ana", "text": line}, headers=headers("ops-test")
        )
        ms.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200, r.text
    return ms


def line(name, ms):
    return f"{name:<26} median {statistics.median(ms):6.1f} ms  p95 {p95(ms):6.1f} ms"


def test_bench(api, monkeypatch):
    org(api)
    queued = []
    queue = org_rule_check._queue_late
    monkeypatch.setattr(org_rule_check, "_queue_late", lambda c, verdict: queued.append(1) or queue(c, verdict))
    monkeypatch.setattr(org_rule_check.B, "daily_cap", lambda settings, who: 1_000_000)  # every ❓ is judged
    store = api.app.state.store
    with store.read() as c:
        assert c.execute("SELECT if_broken FROM org_rules WHERE text=?", (OR.ASKS_TO_A_PERSON,)).fetchone()[0] == "warn"
    judge = api.app.state.judge = SlowJudge(store)
    asks(api, task_for_ops(api, "Warm up"), 10_000)  # warm-up, not counted
    org_rule_check.drain_late(store, judge)
    none, ruled, n0 = [], [], 0
    for k in range(ROUNDS):
        org_rule_check.drain_late(store, judge)  # the last run's checks end under the rule they were queued for
        builtin(store, "retired")
        before = len(queued)
        got = asks(api, task_for_ops(api, f"Launch note {n0}"), n0)
        assert len(queued) == before, "no check queued with no rule in force"
        none += got
        print(line(f"round {k + 1}  no rule", got))
        builtin(store, "active")
        before = len(queued)
        got = asks(api, task_for_ops(api, f"Launch note {n0 + N}"), n0 + N)
        assert len(queued) == before + N, "each ❓ under the rule queues its check"
        ruled += got
        print(line(f"round {k + 1}  warn rule", got))
        n0 += 2 * N
    org_rule_check.drain_late(store, judge)
    gap_median = statistics.median(ruled) - statistics.median(none)
    gap_p95 = p95(ruled) - p95(none)
    met = gap_median <= MEDIAN_GAP_MS and gap_p95 <= P95_GAP_MS
    print(line(f"all  no rule ({len(none)})", none))
    print(line(f"all  warn rule ({len(ruled)})", ruled))
    print(
        f"gap  median {gap_median:+.1f} ms (<= {MEDIAN_GAP_MS})  p95 {gap_p95:+.1f} ms (<= {P95_GAP_MS})"
        f"  criterion 1 {'met' if met else 'MISSED'}  ({len(judge.calls)} judge calls in all, judge {JUDGE_MS} ms)"
    )
