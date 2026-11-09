"""Helpers that other tests share, moved out of test_judge_spend.py so they import no test file."""

from backend import hubdb as H
from clients import judge as J


DOWN = J.JudgeError("unavailable", "TypeSafe did not answer: TimeoutError", retryable=True)


def spend(api, usd, ts=None, actor="bot:ops"):
    """A past decision call that cost `usd`, this month unless `ts` says otherwise."""
    with api.app.state.store.transaction() as c:
        H.event(c, actor, "judge.spend", "mail-triage@1", {"cost_usd": usd})
        if ts:
            c.execute(
                "UPDATE events SET created_at=? WHERE id=(SELECT id FROM events WHERE action='judge.spend' "
                "ORDER BY rowid DESC LIMIT 1)",
                (ts,),
            )
