"""What a runner's claim reads while it holds the single writer: its recheck of the one job it selected
(execution.candidate with `job_id`) reads that job's bot's open holds and its computer's grants, not every open hold
and every computer's grants (claim_loads.read `narrow`). Synthetic rows only."""

from backend import claim_loads, holds
from backend.store import H
from backend.tests.helpers.api import headers
from backend.tests.helpers.claim_statements import seed, traced

EVERY_HOLD = "SELECT * FROM holds INDEXED BY holds_bot WHERE released_at IS NULL"


def locked(statements):
    """The statements run between BEGIN IMMEDIATE and the end of that transaction."""
    out, inside = [], False
    for sql in statements:
        word = sql.strip().split(" ", 1)[0].upper()
        if sql.strip().upper().startswith("BEGIN IMMEDIATE"):
            inside = True
        elif word in ("COMMIT", "ROLLBACK") and not sql.strip().upper().startswith("ROLLBACK TO"):
            inside = False
        elif inside:
            out.append(sql.strip())
    return out


def test_the_claims_recheck_under_the_write_lock_reads_no_whole_hold_or_grant_table(api):
    store = api.app.state.store
    machine = seed(api, "narrow", 3)
    with store.transaction() as c:
        # Another computer's grant and other bots' holds: the whole tables the recheck used to read.
        c.execute(
            "INSERT INTO model_subscriptions(id,name,kind,sealed,status,added_by,created_at,updated_at) "
            "VALUES('plan','plan','claude_token','','working','human:ana',?,?)",
            (H.now(), H.now()),
        )
        c.execute(
            "INSERT INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated_at) "
            "VALUES('elsewhere','claude','plan','human:ana',?)",
            (H.now(),),
        )
        for n in range(5):
            holds.put(c, holds.CLAIM_ERROR, "job", f"other-{n}", actor=H.TICO_ACTOR, bot_id="finance")
    with traced(api) as seen:
        answer = api.post(
            "/api/v2/jobs/claim", json={"bot": None, "start_places": True}, headers=headers(machine["token"])
        ).json()
    assert answer.get("attempt"), answer
    inside = locked(seen)
    assert inside, "the claim wrote"
    assert EVERY_HOLD not in inside
    # The recheck read this computer's grants only. (The hand-out's own read of the plans it gives the turn,
    # model_subscriptions.for_claim, still reads every computer's, once.)
    assert any("g.runner_id IN" in s for s in inside)


def test_a_narrow_read_answers_its_keys_as_the_whole_read_does_and_reads_the_whole_for_any_other(api):
    store = api.app.state.store
    with store.transaction() as c:
        holds.put(c, holds.CLAIM_ERROR, "job", "mine", actor=H.TICO_ACTOR, bot_id="ops")
        holds.put(c, holds.CLAIM_ERROR, "job", "theirs", actor=H.TICO_ACTOR, bot_id="finance")
        holds.put(c, "usage_limit", "login", "a-login", actor=H.TICO_ACTOR)
        whole = claim_loads.open_holds(c)
        part = claim_loads.open_holds_of(c, ["ops"])
        assert part._whole is None
        for key in ((holds.CLAIM_ERROR, "job", "mine", "ops"), ("usage_limit", "login", "a-login", "")):
            assert part.get(key) == whole[key] and key in part
        assert part.get((holds.CLAIM_ERROR, "job", "absent", "ops")) is None
        assert part._whole is None  # its own keys never read every hold
        assert (
            part.get((holds.CLAIM_ERROR, "job", "theirs", "finance"))
            == whole[(holds.CLAIM_ERROR, "job", "theirs", "finance")]
        )
        assert dict(part) == whole
