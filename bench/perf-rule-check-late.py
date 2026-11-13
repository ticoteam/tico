"""Measures the everyday writes a company rule check reads (perf P01): a task comment, a done note and an ask, each
answered before and after the check moved behind the write when every rule it meets only warns
(backend/org_rule_check.py `late`).

    python3 -m pytest bench/perf-rule-check-late.py -n 0 -s -q

One Warn rule for everyone and a fake judge that answers after JUDGE_MS (Jev's typical answer on production). Per kind
it times WRITES writes through the real routes with the test client and prints the median and p95 in milliseconds, and
how many judge calls they cost once the late checks ran. "before" turns the late path off (`deferred` always no), so
each write waits for its check as it did. A bot's ask stays checked before it is saved (a rule that only warns still
refuses a bot's ask it covers), so it is listed apart. Synthetic fixtures only: it shows the wait the check adds, not
production timings.
"""

import statistics
import time

import pytest

from backend import org_rule_check as K
from backend.tests.helpers.api import headers
from backend.tests.helpers.org_rule_check import COLOR, Judge, task_for_ops
from backend.tests.helpers.org_rules import org, rule

JUDGE_MS = 180
WRITES = 20


class SlowJudge(Judge):
    def __call__(self, state, questions, label=None):
        time.sleep(JUDGE_MS / 1000)
        return super().__call__(state, questions, label)


def timed(api, path, body, token):
    start = time.perf_counter()
    r = api.post("/api/v2/" + path, json=body, headers=headers(token))
    elapsed = (time.perf_counter() - start) * 1000
    assert r.status_code == 200, r.text
    return elapsed


def version(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT version FROM tasks WHERE id=?", (task_id,)).fetchone()[0]


def run(api, judge):
    """{kind: [ms per write]} for WRITES writes of each kind, and the judge calls they cost."""
    out = {"comment (bot)": [], "done (bot)": [], "ask (person)": [], "ask (bot)": []}
    task = task_for_ops(api)
    tasks = [task_for_ops(api, f"Write note {n}") for n in range(WRITES)]
    before = len(judge.calls)
    for n in range(WRITES):
        out["comment (bot)"].append(
            timed(api, f"tasks/{task['id']}/comments", {"text": f"Step {n} is done."}, "ops-test")
        )
        out["ask (person)"].append(
            timed(
                api,
                f"tasks/{task['id']}/asks",
                {"to": "ben", "line": f"Is step {n} right?", "version": version(api, task["id"])},
                "ana-test",
            )
        )
        out["ask (bot)"].append(
            timed(
                api,
                f"tasks/{task['id']}/asks",
                {"to": "ana", "line": f"Which day suits step {n}?", "version": version(api, task["id"])},
                "ops-test",
            )
        )
        tid = tasks[n]["id"]
        out["done (bot)"].append(
            timed(api, f"tasks/{tid}/done", {"version": version(api, tid), "note": "The note is in."}, "ops-test")
        )
    K.drain_late(api.app.state.store, api.app.state.judge)
    return out, len(judge.calls) - before


def p95(values):
    return sorted(values)[max(0, int(len(values) * 0.95 + 0.5) - 1)]


@pytest.mark.parametrize("mode", ["before", "after"])
def test_bench(api, mode, monkeypatch):
    org(api)
    judge = SlowJudge(api.app.state.store)
    api.app.state.judge = judge
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}, "if_broken": "warn"})
    if mode == "before":
        monkeypatch.setattr(K, "deferred", lambda *a, **k: False)
    out, calls = run(api, judge)
    print(f"\n{mode}: judge {JUDGE_MS} ms, {WRITES} writes each, {calls} judge calls for {WRITES * len(out)} writes")
    for kind, values in out.items():
        print(f"  {kind:14} median {statistics.median(values):7.1f} ms   p95 {p95(values):7.1f} ms")
