"""An OK bound to exactly what was shown (backend/task_asks.py), through the routes a person and a bot use: the preview
(GET /api/v2/oks/{rid}), the person's own OK (POST /api/v2/tasks/{id}/asks/{rid}/ok), a bot's ask (POST
/api/v2/tasks/{id}/oks) and its use (POST /api/v2/oks/{rid}/use). Each case is one way an act could have gone ahead
without a named, entitled person's own click on that exact content."""

import threading

import pytest

from backend import task_asks as K
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers

FORUM = "forum_post:note-1"
POST = {"text": "We ship on Friday."}


def as_bot(api, slug="ops"):
    """A token for a bot of the company, as its own command would carry: an agent's (backend/agents.py)."""
    token = slug + "-bot-test"
    with api.app.state.store.transaction() as c:
        X.bot(c, slug)
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "hermes", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.auth.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token


def call(api, method, path, token, body=None):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    out = r.json() if r.content else {}
    err = out.get("error") if isinstance(out, dict) else None
    code = err.get("code") if isinstance(err, dict) else err
    detail = err.get("detail") if isinstance(err, dict) else out.get("detail") if isinstance(out, dict) else None
    return r.status_code, code, detail, out


def bots_task(api, bot, title=None):
    with api.app.state.store.transaction() as c:
        title = title or "Post the release note " + H.new_id()[:6]
        made = H.task_create(c, "human:ana", title, "Write it and post it.", "bot:ops", lint=False)
    return made["id"]


def version(api, tid):
    with api.app.state.store.read() as c:
        return H.task(c, tid)["version"]


def ask(api, bot, tid, content=POST, to="ben", subject=FORUM):
    status, code, detail, out = call(
        api,
        "POST",
        f"tasks/{tid}/oks",
        bot,
        {"to": to, "line": "OK to post this?", "subject": subject, "content": content, "version": version(api, tid)},
    )
    assert status == 200, (status, code, detail)
    return out["requirement_id"]


def give(api, tid, rid, token="ben-test"):
    shown = get(api, f"oks/{rid}", token=token)["shown_hash"]
    return call(api, "POST", f"tasks/{tid}/asks/{rid}/ok", token, {"shown_hash": shown})


def use(api, rid, bot, content=POST, subject=FORUM):
    return call(api, "POST", f"oks/{rid}/use", bot, {"subject": subject, "content": content, "note": "Posted."})


def standing(api, rid):
    with api.app.state.store.read() as c:
        return K.standing(c, rid)


# ----------------------------------------------------------------------------- finding 6: the giver is still here
def test_an_ok_whose_giver_left_or_lost_sign_in_lets_nothing_go(api):
    bot = as_bot(api)
    for gone in (X.no_sign_in, X.left):
        tid = bots_task(api, bot)
        rid = ask(api, bot, tid)
        assert give(api, tid, rid)[0] == 200
        with api.app.state.store.transaction() as c:
            gone(c, "human:ben")
        status, code, _, _ = use(api, rid, bot)
        assert (status, code) == (409, "task_ok_missing")
        assert standing(api, rid) != K.USED
        with api.app.state.store.transaction() as c:  # back for the next round
            import json

            from backend import access as Access
            from backend.views import roster

            whole = roster(c)
            people = [
                {k: v for k, v in p.items() if k not in ("hidden", "sign_in", "left_at")} if p["id"] == "ben" else p
                for p in whole["people"]
            ]
            Access.save_roster(c, {**whole, "people": people})
            assert json.dumps(people)


# ----------------------------------------------------------------------------- finding 3: authority is the person's
def test_a_proposal_handed_to_a_teammate_is_not_theirs_to_ok(api):
    """The bot that asked for a spend change owns its task and gives the ask to Ben, an ordinary teammate. Ben opens
    it and clicks OK: refused, and nothing is applied. Only the top of the org OKs a proposal."""
    from backend import proposals

    as_bot(api)
    with api.app.state.store.transaction() as c:
        asked = proposals.propose(c, Identity("bot:ops", "bot"), "spend_limit", {"bot": "ops", "daily_usd": 900})
        assert proposals.decider(c) == "human:ana"
        tid, rid = asked["task_id"], asked["requirement_id"]
        H.task_give(c, "bot:ops", tid, rid, "human:ben", H.task(c, tid)["version"])
    status, code, detail, _ = give(api, tid, rid)
    assert (status, code) == (409, "task_requirement") and "top of the org" in detail
    assert standing(api, rid) == K.WAITING
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM audit_log WHERE object='spend:ops'").fetchone()


def test_an_ask_its_module_named_someone_for_is_not_handed_on_by_a_give(api):
    """A support report is OK'd by the person it was asked of. Given to Ben, his click is refused; at use, an OK by
    anyone else than that person lets nothing go."""
    as_bot(api)
    with api.app.state.store.transaction() as c:
        asked = K.ask_ok(
            c, "bot:ops", "human:ana", "Send this report?", shows=[], subject="support:r1", content={"message": "x"}
        )
        tid, rid = asked["task_id"], asked["requirement_id"]
        H.task_give(c, "human:ana", tid, rid, "human:ben", H.task(c, tid)["version"])
    status, code, detail, _ = give(api, tid, rid)
    assert (status, code) == (409, "task_requirement") and "Ana" in detail
    assert standing(api, rid) == K.WAITING


# ----------------------------------------------------------------------------- finding 8: the OK that moved the task
def test_an_ok_that_was_the_last_thing_the_stage_needed_moves_the_task_and_stays_usable(api):
    """The bot finishes its own part first; Ben's OK is then the last requirement, and the task moves on in the same
    step. The OK did its work: the bot can still use it. A move back afterwards takes it away."""
    bot = as_bot(api)
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid)
    with api.app.state.store.transaction() as c:
        view = H.task_view(c, tid, "bot:ops")
        own = next(r for r in view["to_leave"] if r["kind"] == "owner_done")
        H.task_answer(c, "bot:ops", tid, own["requirement_id"], "done", None, H.task(c, tid)["version"])
        assert H.task(c, tid)["status"] == "open"  # still waiting on Ben
    assert give(api, tid, rid)[0] == 200
    with api.app.state.store.read() as c:
        assert H.task(c, tid)["status"] == "done"
    assert standing(api, rid) == K.OK
    status, _, _, _ = use(api, rid, bot)
    assert status == 200 and standing(api, rid) == K.USED

    # The same, then reopened before the bot uses it: the OK went with the move it made.
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid)
    with api.app.state.store.transaction() as c:
        own = next(r for r in H.task_view(c, tid, "bot:ops")["to_leave"] if r["kind"] == "owner_done")
        H.task_answer(c, "bot:ops", tid, own["requirement_id"], "done", None, H.task(c, tid)["version"])
    assert give(api, tid, rid)[0] == 200
    with api.app.state.store.transaction() as c:
        H.task_move(c, "human:ana", tid, H.TASK_REOPEN, "Not yet.", H.task(c, tid)["version"])
    assert standing(api, rid) == K.WITHDRAWN
    assert use(api, rid, bot)[:2] == (409, "task_ok_missing")


# ----------------------------------------------------------------------------- finding 7: a plain answer is not one
def test_a_plain_ok_answer_never_gives_a_bound_ok_whoever_sends_it(api):
    bot = as_bot(api)
    api.app.state.auth.settings.test_identities["ben-token"] = Identity(
        "human:ben", "human", "ben@acme.example", via_token=True
    )
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid)
    for token in ("ben-test", "ben-token"):
        status, _, detail, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/answer", token, {"value": "ok"})
        assert status == 409 and K.FROM_PREVIEW in detail
    with api.app.state.store.transaction() as c:
        try:
            H.task_answer(c, "human:ben", tid, rid, "ok")
            raise AssertionError("a plain ok was taken")
        except H.Refused as exc:
            assert K.FROM_PREVIEW in str(exc.detail)
    assert standing(api, rid) == K.WAITING
    assert use(api, rid, bot)[:2] == (409, "task_ok_missing")
    # A send back stays a plain answer.
    status, _, _, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/answer", "ben-test", {"value": "no", "note": "Later."})
    assert status == 200 and standing(api, rid) == K.SENT_BACK


# ----------------------------------------------------------------------------- one use wins
def test_two_uses_at_once_only_one_wins(api):
    bot = as_bot(api)
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid)
    assert give(api, tid, rid)[0] == 200
    start, out = threading.Barrier(2), []

    def go():
        start.wait()
        out.append(use(api, rid, bot)[:2])

    runs = [threading.Thread(target=go) for _ in range(2)]
    for run in runs:
        run.start()
    for run in runs:
        run.join()
    assert sorted(out) == [(200, None), (422, "consumed")]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='ok.used' AND target=?", (rid,)).fetchone()[0] == 1


# ----------------------------------------------------------------------------- finding 9: no context, no OK
def test_an_ok_whose_own_step_needs_the_person_is_refused_without_them_and_stays_asked(api):
    """A subject whose module acts as the person who OK'd it (a page rebuilt, an email or letter released, a Finance
    action, a proposal applied) is never OK'd by a caller that has no person's session: refused before anything is
    answered, and the ask still waits."""
    as_bot(api)
    for kind in sorted(K.ON_OK):
        with api.app.state.store.transaction() as c:
            asked = K.ask_ok(c, "bot:ops", "human:ana", "OK?", shows=[], subject=f"{kind}:x1", content={"n": 1})
            rid = asked["requirement_id"]
            shown = K.show(c, "human:ana", rid, {"n": 1}, own_click=True)
        with api.app.state.store.transaction() as c:
            try:
                K.give_ok(c, "human:ana", rid, shown, own_click=True, ctx=None)
                raise AssertionError(f"{kind}: an OK was given with no one's session behind it")
            except H.Refused as exc:
                assert exc.rule == "identity", kind
        assert standing(api, rid) == K.WAITING, kind


# ----------------------------------------------------------------------------- the Assistant and BotOps never OK
def test_the_assistant_and_botops_never_answer_ok_to_an_ok_even_a_plain_one(api):
    """A plain OK (no grant behind it) asked of Ana. Her Assistant, confirmed or not, and BotOps acting for her, are
    refused an `ok` at the answer route, before anything is written; their send back still goes through."""
    as_bot(api)
    ids = api.app.state.auth.settings.test_identities
    ids["ana-assistant"] = Identity("human:ana", "owner", "ana@acme.example", via="assistant")
    ids["ana-assistant-confirmed"] = Identity("human:ana", "owner", "ana@acme.example", via="assistant", confirmed=True)
    ids["ana-botops"] = Identity("human:ana", "owner", "ana@acme.example", via="botops")
    assistant = as_bot(api, "assistant")
    tid = bots_task(api, "ops-bot-test")
    with api.app.state.store.transaction() as c:
        version = H.task(c, tid)["version"]
        rid = H.task_ask(c, "bot:ops", tid, "human:ana", "OK?", kind="ok", seen_version=version)["requirement_id"]
    for token in ("ana-assistant", "ana-assistant-confirmed", "ana-botops", assistant):
        status, code, _, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/answer", token, {"value": "ok"})
        # Unconfirmed (BotOps never is), the write waits for the person's tap on a card first: refused either way.
        assert status == 403 and code in ("ok_not_yours", "confirm_required"), (token, code)
        assert code == "ok_not_yours" or token in ("ana-assistant", "ana-botops")
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM answers WHERE requirement_id=?", (rid,)).fetchone()
    status, _, _, _ = call(
        api, "POST", f"tasks/{tid}/asks/{rid}/answer", "ana-assistant-confirmed", {"value": "no", "note": "Not now."}
    )
    assert status == 200
