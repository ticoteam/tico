"""BotOps never switches a login to another account or signs one out: both stop the bots on that login. Not as itself,
and not with the rights of the person it works for, however it borrows them (the default for a person's turn, the
On-Behalf-Of header, the query, a task it follows through on, a request another bot carried). An ordinary sign-in it
still starts for that person, and a person still switches and signs out from the app."""
import re

import pytest

from backend.tests.test_api import api, assign, claim, get, post, ready  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixture)
from backend.tests.test_model_logout import online, pending, report
from backend.store import H, Problem

ALIASES = ("runners", "computers")
PROFILE = "work"                    # a named subscription left from before one login per computer: signed out only
SWITCHES = (True, 1, "true", "1", "on", "yes")    # what the typed `switch: bool` reads as a switch
NOTHING = {"logins": [], "cleared": [], "invalidated": False, "events": []}


def effects(api, rid):
    """What a queued request leaves behind: the row the runner runs, the signed-out mark, the invalidated reading, the
    events."""
    with api.app.state.store.read() as c:
        return {"logins": [tuple(r) for r in c.execute("SELECT kind, requested_by FROM model_logins WHERE runner_id=? "
                                                        "ORDER BY created_at", (rid,))],
                "cleared": [tuple(r) for r in c.execute("SELECT runtime, profile FROM model_login_cleared "
                                                         "WHERE runner_id=?", (rid,))],
                "invalidated": c.execute("SELECT 1 FROM registry_metadata WHERE key=?",
                                         (f"subscription-reading-after:{rid}:{PROFILE}",)).fetchone() is not None,
                "events": [r[0] for r in c.execute("SELECT action FROM events WHERE target=? AND action LIKE "
                                                    "'runner.login.%' ORDER BY rowid", (rid,))]}


def reset(api, rid):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM model_logins WHERE runner_id=?", (rid,))
        c.execute("DELETE FROM model_login_cleared WHERE runner_id=?", (rid,))
        c.execute("DELETE FROM registry_metadata WHERE key LIKE ?", (f"subscription-reading-after:{rid}:%",))
        c.execute("DELETE FROM events WHERE target=? AND action LIKE 'runner.login.%'", (rid,))


def ask(api, attempt, alias, rid, route, body, how):
    """BotOps' call, borrowing the person's rights `how`: "auto" (no reference: the default for a person's turn),
    a header reference, or ("query", reference)."""
    if isinstance(how, tuple):
        return act(api, attempt, "POST", f"{alias}/{rid}/{route}?on_behalf_of={how[1]}", body, ref=None)
    return act(api, attempt, "POST", f"{alias}/{rid}/{route}", body, ref=None if how == "auto" else how)


def signs_in_for_her(api, attempt, rid, how, alias):
    """The control: in this same context an ordinary sign-in is BotOps' to start, as her, so a refusal next to it is the
    switch or sign-out rule and not a stale or wrong reference."""
    for body in ({"runtime": "codex"}, {"runtime": "codex", "switch": False}):
        done = ask(api, attempt, alias, rid, "logins", body, how)
        assert done.status_code == 200, (alias, how, body, done.text)
        assert done.json()["kind"] == "sign_in"
        assert effects(api, rid)["logins"] == [("sign_in", "human:ana")]
        with api.app.state.store.read() as c:
            event = c.execute("SELECT actor, detail FROM events WHERE target=? AND action='runner.login.start'",
                              (rid,)).fetchone()
        assert event["actor"] == "human:ana" and '"via": "botops"' in event["detail"]
        reset(api, rid)


def refused(api, attempt, rid, how, alias):
    for value in SWITCHES:
        r = ask(api, attempt, alias, rid, "logins", {"runtime": "codex", "switch": value}, how)
        assert r.status_code == 403 and "BotOps cannot switch accounts" in r.json()["error"]["detail"], \
            (alias, how, value, r.text)
        assert effects(api, rid) == NOTHING, (alias, how, value)
    for route_body in ({"runtime": "codex"}, {"runtime": "codex", "profile": PROFILE}):
        r = ask(api, attempt, alias, rid, "logouts", route_body, how)
        assert r.status_code == 403 and "BotOps cannot sign a login out" in r.json()["error"]["detail"], \
            (alias, how, route_body, r.text)
        assert effects(api, rid) == NOTHING, (alias, how, route_body)


def test_botops_never_switches_or_signs_out_for_the_person_it_works_for(api, botops):
    r = online(api)
    rid = r["runner_id"]
    ana = turn(api, botops, person="ana-test", text="Switch the codex login on the Test Mac to my other account")
    for alias in ALIASES:
        for how in ("auto", "turn", "default", ana["message"]["id"], ("query", "turn"), ("query", ana["message"]["id"])):
            signs_in_for_her(api, ana, rid, how, alias)
            refused(api, ana, rid, how, alias)
        # Not a bool at all: refused by the contract, before anything runs.
        for value in ("maybe", 2, [True]):
            bad = ask(api, ana, alias, rid, "logins", {"runtime": "codex", "switch": value}, "auto")
            assert bad.status_code == 422, (alias, value, bad.text)
        # `on_behalf_of` in the body is not part of these requests: still refused as an unknown field.
        for route, body in (("logins", {"runtime": "codex", "switch": True}), ("logins", {"runtime": "codex"}),
                            ("logouts", {"runtime": "codex"})):
            bad = act(api, ana, "POST", f"{alias}/{rid}/{route}", {**body, "on_behalf_of": "turn"}, ref=None)
            assert bad.status_code == 422, (alias, route, bad.text)
        assert effects(api, rid) == NOTHING

    # Following through on her open task in a later run: the same.
    task = act(api, ana, "POST", "tasks", {"owner": "botops", "title": "Switch the codex login", "body": "As asked.",
                                          "request_id": ana["message"]["id"]})
    assert task.status_code == 200, task.text
    task_id = (task.json().get("task") or task.json())["id"]
    finish(api, botops, ana)
    finish(api, botops, claim(api, botops, "botops"))           # the task's own "new task" notice
    with api.app.state.store.transaction() as c:
        H.say(c, H.KEEPER, "bot:botops", "Time for your daily update.", kind="notice", refs={"wake": "update"})
    later = claim(api, botops, "botops")
    for alias in ALIASES:
        for how in (task_id, ("query", task_id)):
            signs_in_for_her(api, later, rid, how, alias)
            refused(api, later, rid, how, alias)
        # Its own run, with nobody's request: its own rights, refused as before (no default for its own plumbing).
        for route, body in (("logins", {"runtime": "codex", "switch": True}), ("logouts", {"runtime": "codex"})):
            own = act(api, later, "POST", f"{alias}/{rid}/{route}", body, ref=None)
            assert own.status_code == 403, (alias, route, own.text)
        assert effects(api, rid) == NOTHING
    finish(api, botops, later)


def test_a_request_another_bot_carried_never_switches_or_signs_out(api, botops, monkeypatch):
    from backend import botops_act
    # Even were sign-ins added to what a carried request does with the person's rights.
    monkeypatch.setattr(botops_act, "CARRIED", botops_act.CARRIED + botops_act._routes(
        ("POST", rf"(runners|computers)/{botops_act._S}/(logins|logouts)")))
    r = online(api)
    rid = r["runner_id"]
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    post(api, "chat/ops", {"text": "Switch the codex login on the Test Mac to my other account"}, token="ana-test")
    ops = claim(api, botops, "ops")
    post(api, "tasks", {"owner": "botops", "title": "Switch Ana's codex login", "body": "She asked in her chat with Ops.",
                        "request_id": "turn"}, token=ops["token"])
    finish(api, botops, ops)
    carried = claim(api, botops, "botops")
    for alias in ALIASES:
        for how in ("auto", "turn"):
            signs_in_for_her(api, carried, rid, how, alias)
            refused(api, carried, rid, how, alias)
    finish(api, botops, carried)


def test_a_bot_with_no_person_behind_it_is_refused_as_before(api, botops):
    r = online(api)
    rid = r["runner_id"]
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Sign the Test Mac out of codex", "Please.", "bot:botops", lint=False)
    run = claim(api, botops, "botops")
    for alias in ALIASES:
        for how in ("auto", "turn"):
            for route, body in (("logins", {"runtime": "codex", "switch": True}), ("logouts", {"runtime": "codex"}),
                                ("logins", {"runtime": "codex"})):
                no = ask(api, run, alias, rid, route, body, how)
                assert no.status_code == 403, (alias, how, route, no.text)
    assert effects(api, rid) == NOTHING
    finish(api, botops, run)


def test_the_rule_reads_who_is_acting_not_whose_rights():
    """A person's own click on a card BotOps proposed is recorded via BotOps (`confirmed`): it is still not a person
    switching from the app. The Assistant acting for a person, a token, and the person themself are people."""
    from backend.auth import Identity
    from backend.model_login import _person_only
    for who in (Identity("human:ana", "owner", via="botops", confirmed=True),
                Identity("human:ben", "human", via="botops", task_actor="bot:botops"),
                Identity("human:ana", "owner", task_actor="bot:botops"),
                Identity("bot:botops", "bot")):
        with pytest.raises(Problem) as no:
            _person_only(who, "switch accounts")
        assert no.value.status == 403
    for who in (Identity("human:ana", "owner"), Identity("human:ben", "human", via_token=True),
                Identity("human:ana", "owner", via="assistant", confirmed=True)):
        _person_only(who, "switch accounts")


@pytest.mark.parametrize("alias", ALIASES)
def test_a_person_still_switches_and_signs_out_from_the_app(api, alias):
    r = online(api)
    rid = r["runner_id"]
    cara = online(api)                                           # a computer Cara operates
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET owner_id='cara' WHERE id=?", (cara["runner_id"],))
    for token, machine, profile in (("ana-test", r, ""), ("ben-test", r, ""), ("cara-test", cara, "")):
        mid = machine["runner_id"]
        switch = post(api, f"{alias}/{mid}/logins", {"runtime": "codex", "profile": profile, "switch": True}, token=token)
        assert switch["kind"] == "switch" and switch["state"] == "requested"
        assert [row.get("kind") for row in pending(api, machine)] == ["switch"]
        assert report(api, machine, switch["id"], state="signed_in", message="Signed in") == {"state": "signed_in"}
        out = post(api, f"{alias}/{mid}/logouts", {"runtime": "codex", "profile": profile}, token=token)
        assert out["kind"] == "sign_out"
        assert effects(api, mid)["cleared"] == [("codex", profile)]
        assert report(api, machine, out["id"], state="signed_out") == {"state": "signed_out"}
        reset(api, mid)
    # Cara operates only her own computer.
    post(api, f"{alias}/{rid}/logins", {"runtime": "codex", "switch": True}, token="cara-test", expected=403)
    post(api, f"{alias}/{rid}/logouts", {"runtime": "codex"}, token="cara-test", expected=403)
    assert effects(api, rid) == NOTHING
