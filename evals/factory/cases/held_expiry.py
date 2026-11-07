# pyright: strict
"""A write a bot made that the org rules held, and nobody reviewed, is not left held: after HELD_HOURS (24 h) the hourly
approval sweep keeps it unsent and tells the bot it was not sent (backend/org_rule_check.expire_unreviewed, #2882,
merged). The scenario puts one such hold in the records at the start (the rule check itself needs a judge, which the
sim does not run) and runs past the 24 h: the hold must be decided and its author told."""

from evals.factory.scenarios import Scenario, standard, tickets

HOLD = "sim-held-write"
AUTHOR = "bot:builder-claude"  # actor ids: bot:<slug>; _tell_bot skips non-bot authors
GAP = "#2882"
HOURS = 26  # past the 24 h hold and one hourly sweep


def setup(run, w, team):
    tickets(run, w, 3)
    now = w.H.now()
    with w.store.transaction() as c:
        c.execute(
            "INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, verdict, why, nonce, state, "
            "reviewer, task_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                HOLD,
                "comment",
                '{"provenance": {}}',
                AUTHOR,
                "[]",
                "[]",
                "{}",
                "sim",
                "sim-nonce",
                "held",
                None,
                None,
                now,
            ),
        )


def held_expired(run):
    rows = run.w.sql("SELECT state, created_at, decided_at FROM org_rule_holds WHERE id=?", (HOLD,))
    if not rows:
        return False, "the held write is gone from the records"
    row = rows[0]
    if row["state"] == "held":
        return False, f"still held after {HOURS} h: nobody reviewed it and it was never decided"
    hours = (run.w.H.parse_ts(row["decided_at"]) - run.w.H.parse_ts(row["created_at"])).total_seconds() / 3600
    told = run.w.sql("SELECT count(*) n FROM notes WHERE to_actor=? AND body LIKE '%was not sent%'", (AUTHOR,))[0]["n"]
    ok = row["state"] != "held" and hours <= 25 and told > 0
    return ok, f"state {row['state']} {hours:.1f} h after it was held; its author told: {bool(told)}"


SCENARIOS = [
    Scenario(
        "held_expiry",
        "A held write nobody reviews is kept unsent after 24 h, and its author is told.",
        setup=setup,
        hours=HOURS,
        checks=standard(("a held write nobody decides is kept unsent within 24 h", GAP, held_expired)),
    ),
]
