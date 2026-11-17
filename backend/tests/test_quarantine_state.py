"""A bot-wide quarantine is a hold (backend/holds.py) and bots.state together: the hold keeps the reason, the cooldown
and the history, and bots.state stays `quarantined` while it is open, the switch every reader of a bot checks (the
runner's watchers, credentials, routing, the producers). Its release sets the bot active again, never an archived one."""

from backend import hubdb as H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner, setup_attempt  # noqa: F401  (the api fixture)

HELD = "SELECT * FROM holds WHERE kind='quarantine' AND holdable_type='bot' AND holdable_id='ops' ORDER BY created_at"


def _cool(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE holds SET until_at=? WHERE kind='quarantine'", (H.shift(H.now(), seconds=-100),))
        return H.lift_cooled_quarantines(c)


def _listed(api, computer):
    return {row["bot"]: row for row in get(api, "runners/assignments", computer["token"])}["ops"]


def test_a_quarantined_bot_reads_quarantined_to_its_computer_until_its_cooldown_lifts(api):
    # Before: the hold alone stopped the claim, and the computer's list still said active, so its watchers ran.
    computer = runner(api)
    assign(api, computer, "ops")
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "ops", "10 repeated refusals today")
    assert _listed(api, computer)["state"] == "quarantined"

    assert _cool(api) == ["ops"]
    assert _listed(api, computer)["state"] == "active"
    with api.app.state.store.read() as c:
        [hold] = c.execute(HELD).fetchall()
    assert (hold["released_how"], hold["released_by_id"]) == ("expired", H.KEEPER)


def test_a_cooldown_never_brings_back_an_archived_bot(api):
    # Before: the sweep lifted every expired hold through status_set, which set an archived bot active again.
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "ops", "10 repeated refusals today")
        c.execute("UPDATE bots SET state='archived' WHERE id='ops'")
    assert _cool(api) == []
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "archived"
        [hold] = c.execute(HELD).fetchall()
    assert hold["released_how"] == "expired", "its hold ends all the same"


def test_quarantined_through_the_status_api_is_a_hold_a_person_clears(api):
    # Before: the status API answered quarantined, but nothing held the bot and quarantine/clear said it was not.
    post(api, "bots/ops/status", {"state": "quarantined", "focus": "Checking a leak"})
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "quarantined"
        [hold] = c.execute(HELD).fetchall()
        assert hold["until_at"] is None and hold["created_by_id"] == "human:ana"
    with api.app.state.store.transaction() as c:  # as quarantine/clear does, for the person who reviewed it
        H.status_set(c, "human:ana", "ops", state="active", reason="reviewed")
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"
        [hold] = c.execute(HELD).fetchall()
    assert (hold["released_how"], hold["released_by_id"]) == ("person", "human:ana")


def test_a_cooldown_lifts_the_quarantine_alone_and_a_paused_bot_stays_paused(api):
    # Codex on #3989: the cooldown went through status_set(state='active'), which also resumed the bot's own pause,
    # so a bot paused and then quarantined came back active and claimed work nobody had resumed it for.
    from backend import company_pause, holds

    computer = runner(api)
    assign(api, computer, "ops")
    ready(api, computer, ["ops"])
    with api.app.state.store.transaction() as c:
        company_pause.pause_bot(c, "human:ana", "ops")
        H.quarantine(c, "ops", "10 repeated refusals today")
        assert H.bot(c, "ops")["state"] == "quarantined"
    post(api, "chat/ops", {"text": "Please summarize the current work."})

    assert _cool(api) == ["ops"]
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "paused"
        assert holds.get(c, holds.PAUSED, "bot", "ops")  # still open: nobody resumed it
        assert not holds.get(c, holds.QUARANTINE, "bot", "ops")
        assert H.status(c, "ops")["state"] == "paused"
    assert _listed(api, computer)["state"] == "paused"
    assert claim(api, computer) is None

    with api.app.state.store.transaction() as c:  # a person's resume lets it work again
        H.status_set(c, "human:ana", "ops", state="active", reason="Resumed")
    assert claim(api, computer)


def test_a_person_clearing_a_quarantine_leaves_the_bots_own_pause(api):
    from backend import company_pause

    setup_attempt(api)  # a bot with its repository built, as a person's clear needs
    with api.app.state.store.transaction() as c:
        company_pause.pause_bot(c, "human:ana", "ops")
        H.quarantine(c, "ops", "Wrote outside its task")  # an escape: no cooldown, a person clears it
    post(api, "bots/ops/quarantine/clear", {"note": "Reviewed"})
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "paused"
