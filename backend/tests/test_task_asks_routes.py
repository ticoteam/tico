"""An OK bound to exactly what was shown (backend/task_asks.py), through the routes a person and a bot use: the preview
(GET /api/v2/oks/{rid}), the person's own OK (POST /api/v2/tasks/{id}/asks/{rid}/ok), a bot's ask (POST
/api/v2/tasks/{id}/oks) and its use (POST /api/v2/oks/{rid}/use). Each case is one way an act could have gone ahead
without a named, entitled person's own click on that exact content."""

import json
import threading

import pytest

from backend import task_asks as K
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers

# A reply to a Tico HQ ticket: a kind the generic routes take (task_asks.GENERIC). Ben, asked by default, manages the
# bots made here (as_bot), so he may OK it.
FORUM = "hq_reply:note-1"
POST = {"text": "We ship on Friday."}


def as_bot(api, slug="ops"):
    """A token for a bot of the company, as its own command would carry: an agent's (backend/agents.py)."""
    token = slug + "-bot-test"
    with api.app.state.store.transaction() as c:
        X.bot(c, slug)
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot=?", (slug,))
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
    status, _, detail, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/answer", "ben-test", {"value": "ok"})
    assert status == 409 and K.FROM_PREVIEW in detail
    # A token never gives an OK at all, so it is refused before the preview is even asked for.
    status, _, detail, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/answer", "ben-token", {"value": "ok"})
    assert status == 403 and K.OWN_IN_TICO in detail
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


def test_a_personal_token_never_gives_an_ok_on_anyones_ask_but_sends_back_and_her_own_click_oks(api):
    """A plain OK asked of Ana, by a bot and by Ben. Her personal token is refused `ok` at the answer route and in a
    batch decision, with the same words; its send back goes through; her own signed-in click OKs."""
    as_bot(api)
    ids = api.app.state.auth.settings.test_identities
    ids["ana-token"] = Identity("human:ana", "owner", "ana@acme.example", via_token=True)
    tid = bots_task(api, "ops-bot-test")
    asks = []
    with api.app.state.store.transaction() as c:
        for by in ("bot:ops", "human:ben"):
            version = H.task(c, tid)["version"]
            asked = H.task_ask(c, by, tid, "human:ana", "OK?", kind="ok", seen_version=version)
            asks.append(asked["requirement_id"])
    for rid in asks:
        status, code, detail, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/answer", "ana-token", {"value": "ok"})
        assert (status, code) == (403, "ok_not_yours"), (rid, status, code)
        assert detail == K.OWN_IN_TICO, detail
    with api.app.state.store.transaction() as c:
        from backend import batch

        item = {"key": "ask:" + asks[0], "title": "OK?"}
        with pytest.raises(Exception) as refused:
            batch._apply_decide(c, ids["ana-token"], item, {"decision": "ok"})
        assert getattr(refused.value, "detail", "") == K.OWN_IN_TICO, refused.value
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM answers WHERE requirement_id IN (?, ?)", tuple(asks)).fetchone()
    status, _, _, _ = call(
        api, "POST", f"tasks/{tid}/asks/{asks[0]}/answer", "ana-token", {"value": "no", "note": "Not now."}
    )
    assert status == 200
    status, _, detail, _ = call(api, "POST", f"tasks/{tid}/asks/{asks[1]}/answer", "ana-test", {"value": "ok"})
    assert status == 200, detail
    with api.app.state.store.read() as c:
        assert c.execute("SELECT answer FROM answers WHERE requirement_id=?", (asks[1],)).fetchone()[0] == "ok"


# ----------------------------------------------------------------------------- the generic use (tico ok use)
def test_the_generic_use_lets_only_the_asker_act_once_on_exactly_what_was_okd(api):
    """`tico ok use <rid> --for KIND:ID --content JSON` (POST /api/v2/oks/{rid}/use), as the Video Producer uses a
    `budget_raise:<slug>` OK: refused for an OK that does not exist, still waits, is for other content or another
    subject, or is used by anyone but the bot that asked; used once; and an OK whose task was closed lets nothing go."""
    bot, other = as_bot(api), as_bot(api, "video")
    subject, content = "budget_raise:video", {"cap_usd": 400}
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid, content=content, subject=subject)
    assert use(api, "nope", bot, content, subject)[0] == 404
    assert use(api, rid, bot, content, subject)[:2] == (409, "task_ok_missing")  # still waits
    # The OK is bound to what she was shown: a hash of anything else is refused, and so is a preview never opened.
    status, code, _, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/ok", "ben-test", {"shown_hash": "0" * 64})
    assert status == 409 and standing(api, rid) == K.WAITING, code
    assert give(api, tid, rid)[0] == 200
    assert use(api, rid, bot, {"cap_usd": 4000}, subject)[:2] == (409, "task_ok_changed")
    assert use(api, rid, bot, content, "budget_raise:ops")[:2] == (403, "identity")
    assert use(api, rid, other, content, subject)[0] in (403, 404)
    assert standing(api, rid) == K.OK
    assert use(api, rid, bot, content, subject)[0] == 200
    assert use(api, rid, bot, content, subject)[:2] == (422, "consumed")
    # Closed before use: withdrawn, nothing goes.
    tid2 = bots_task(api, bot)
    rid2 = ask(api, bot, tid2, content=content, subject=subject + "-2")
    assert give(api, tid2, rid2)[0] == 200
    with api.app.state.store.transaction() as c:
        H.task_close(c, "human:ana", tid2, "Not needed.")
    assert use(api, rid2, bot, content, subject + "-2")[:2] == (409, "task_ok_missing")


def test_the_assistant_and_botops_never_give_a_bound_ok_from_its_preview(api):
    bot = as_bot(api)
    ids = api.app.state.auth.settings.test_identities
    ids["ben-assistant"] = Identity("human:ben", "human", "ben@acme.example", via="assistant", confirmed=True)
    ids["ben-botops"] = Identity("human:ben", "human", "ben@acme.example", via="botops", confirmed=True)
    ids["ben-token"] = Identity("human:ben", "human", "ben@acme.example", via_token=True)
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid)
    for token in ("ben-assistant", "ben-botops", "ben-token"):
        shown = api.get(f"/api/v2/oks/{rid}", headers=headers("ben-test")).json()["shown_hash"]
        status, _, _, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/ok", token, {"shown_hash": shown})
        assert status in (403, 409), token
    assert standing(api, rid) == K.WAITING


# ----------------------------------------------------------------------------- finding 5: an OK never widens who sees
def test_an_ok_filed_for_a_subject_is_private_to_its_parties_and_its_title_names_nothing_of_it(api):
    """A bot asks Ben to OK an email whose subject line is confidential. The task filed for it is private, reaches Ben,
    and is titled only for the kind of act; Cara, a teammate, can neither read it nor open its preview."""
    as_bot(api)
    secret = {"subject": "Acquisition of Globex: terms", "to": ["ceo@globex.example"]}
    with api.app.state.store.transaction() as c:
        asked = K.ask_ok(
            c,
            "bot:ops",
            "human:ben",
            "Send this email?",
            shows=[],
            subject="email_send:e1",
            content=secret,
            title="Send: Acquisition of Globex: terms",
        )
        task = H.task(c, asked["task_id"])
    assert task["title"] == "Send an email" and task["private"]
    rid, tid = asked["requirement_id"], asked["task_id"]
    assert get(api, f"oks/{rid}", token="ben-test")["content"] == secret
    for path in (f"oks/{rid}", f"tasks/{tid}"):
        r = api.get("/api/v2/" + path, headers=headers("cara-test"))
        assert r.status_code == 404 and "Globex" not in r.text, path


def test_an_owned_oks_preview_on_a_task_others_read_shows_only_to_its_parties(api):
    """The same email OK asked on a company-visible task Cara reads: she sees the task, never what the OK is for."""
    as_bot(api)
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "human:ana", "Outreach", "Reach out.", "bot:ops", lint=False, private=False)["id"]
        asked = K.ask_ok(
            c,
            "bot:ops",
            "human:ben",
            "Send this email?",
            shows=[],
            subject="email_send:e2",
            content={"subject": "Globex terms"},
            task_id=tid,
        )
    rid = asked["requirement_id"]
    assert api.get(f"/api/v2/tasks/{tid}", headers=headers("cara-test")).status_code == 200
    r = api.get(f"/api/v2/oks/{rid}", headers=headers("cara-test"))
    assert r.status_code == 404 and "Globex" not in r.text
    assert get(api, f"oks/{rid}", token="ben-test")["content"] == {"subject": "Globex terms"}
    # A generic kind too: only its parties open its preview, whoever reads the task.
    tid2 = bots_task(api, "ops-bot-test")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (tid2,))
    rid2 = ask(api, "ops-bot-test", tid2)
    assert api.get(f"/api/v2/oks/{rid2}", headers=headers("cara-test")).status_code == 404
    assert api.get(f"/api/v2/oks/{rid2}", headers=headers("ben-test")).status_code == 200


# ----------------------------------------------------------------------------- Needs you: a batch never gives a bound OK
def test_a_needs_you_batch_never_gives_a_bound_ok_but_sends_one_back(api):
    """Ben walks Needs you through the batch routes. The bound OK is shown preview-only: deciding OK is refused, a
    send back is kept and applied on commit, and the OK is never given."""
    bot = as_bot(api)
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid)
    status, code, detail, out = call(api, "POST", "batch", "ben-test", {})
    assert status == 200, (status, code, detail)
    batch = out
    keys = [batch["item"]["key"]] if batch.get("item") else []
    n = None
    for i in range(1, batch["total"] + 1):
        if batch.get("item") and batch["item"]["key"] == "ask:" + rid:
            n = i
            break
        _, _, _, batch = call(api, "POST", f"batch/{out['id']}/next", "ben-test", {})
        keys.append((batch.get("item") or {}).get("key"))
    assert n is not None, keys
    assert batch["item"]["preview_only"]
    status, _, detail, _ = call(
        api, "POST", f"batch/{out['id']}/respond", "ben-test", {"kind": "decide", "decision": "ok", "item": n}
    )
    assert status in (409, 422) and "preview" in (detail or ""), (status, detail)
    status, code, detail, _ = call(
        api,
        "POST",
        f"batch/{out['id']}/respond",
        "ben-test",
        {"kind": "decide", "decision": "send_back", "text": "Say Thursday.", "item": n},
    )
    assert status == 200, (status, code, detail)
    status, code, detail, done = call(api, "POST", f"batch/{out['id']}/commit", "ben-test", {})
    assert status == 200 and done["errors"] == [], (status, code, detail, done)
    assert standing(api, rid) == K.SENT_BACK
    assert use(api, rid, bot)[0] == 409


# ----------------------------------------------------------------------------- review 2: files, revocation, kinds
def a_file(api, bot="ops", fid=None, text_digest="d1"):
    """A bot's published file with one version, as files.py keeps it."""
    fid = fid or "f" + H.new_id()[:8]
    at = H.now()
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,current_version,first_activity_at,"
            "last_activity_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fid, bot, "bot", "cuts/" + fid + ".md", "Intro cut", "doc", "tico_blob", 1, at, at),
        )
        c.execute(
            "INSERT OR IGNORE INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES(?,?,?,?,?,?,?)",
            ("b" + fid, "bot:" + bot, text_digest, 10, "intro.md", "text/markdown", at),
        )
        c.execute(
            "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,repo_path,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fid, 1, "b" + fid, text_digest, 10, "intro.md", "text/markdown", "cuts/" + fid + ".md", "bot:" + bot, at),
        )
    return fid


def test_a_person_or_the_assistant_never_approves_a_file_by_a_direct_review(api):
    """Codex review 2, finding 1: POST /api/v2/file-reviews took a person's or the Assistant's verdict and counted it
    as a person's approval with no OK at all. A person approves a file only by a bound OK; a direct review is a bot's."""
    from backend.tests.helpers.assistant import assistant_turn

    bot = as_bot(api)
    fid = a_file(api)
    tid = bots_task(api, bot)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (tid,))
    _, attempt = assistant_turn(api, "ana-test")
    for token in ("ana-test", attempt["token"]):
        status, code, _, _ = call(api, "POST", "file-reviews", token, {"file": fid, "task": tid, "verdict": "approve"})
        assert status == 403, (token, status, code)
    with api.app.state.store.transaction() as c:  # a person's review row from before still counts for nothing
        c.execute(
            "INSERT INTO bot_file_activity(file_id,actor,action,task_id,version,digest,created_at,detail) "
            "VALUES(?,?,'reviewed',?,1,'d1',?,?)",
            (fid, "human:ana", tid, H.now(), '{"verdict": "approve"}'),
        )
    found = get(api, f"file-approvals?file={fid}", token="ana-test")
    assert not found["approved"], found
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM ok_grants WHERE subject LIKE ?", (f"file:{fid}%",)).fetchone()


def test_an_ok_sent_back_stays_dead_when_the_send_back_is_undone(api):
    """Codex review 2, finding 2: OK, then send back, then undo the send back used to bring the first OK back, and the
    bot used it with no new click. A send back (or an OK taken back) revokes the grant for good; the Assistant never
    reaches an undo that touches an OK."""
    from backend.tests.helpers.assistant import assistant_turn

    bot = as_bot(api)
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid)
    assert give(api, tid, rid)[0] == 200
    status, _, _, out = call(
        api, "POST", f"tasks/{tid}/asks/{rid}/answer", "ben-test", {"value": "no", "note": "Wait."}
    )
    assert status == 200
    with api.app.state.store.read() as c:
        back = c.execute(
            "SELECT id FROM answers WHERE requirement_id=? AND answer='no' ORDER BY created_at DESC LIMIT 1", (rid,)
        ).fetchone()[0]
        given = c.execute("SELECT answer_id FROM ok_grants WHERE requirement_id=?", (rid,)).fetchone()[0]
    _, attempt = assistant_turn(api, "ana-test")
    for answer_id in (back, given):  # the Assistant reaches no undo on an OK
        assert call(api, "POST", f"answers/{answer_id}/undo", attempt["token"], {})[0] in (403, 409)
    assert call(api, "POST", f"answers/{back}/undo", "ben-test", {})[0] == 200
    assert standing(api, rid) != K.OK
    status, code, _, _ = use(api, rid, bot)
    assert (status, code) == (409, "task_ok_missing")
    # Nor does a fresh click bring that grant back: the bot asks again.
    status, code, _, _ = give(api, tid, rid)
    assert status == 409
    assert use(api, rid, bot)[0] == 409
    # Taking the OK itself back revokes it too.
    tid2 = bots_task(api, bot)
    rid2 = ask(api, bot, tid2)
    assert give(api, tid2, rid2)[0] == 200
    with api.app.state.store.read() as c:
        given2 = c.execute("SELECT answer_id FROM ok_grants WHERE requirement_id=?", (rid2,)).fetchone()[0]
    assert call(api, "POST", f"answers/{given2}/undo", "ben-test", {})[0] == 200
    assert use(api, rid2, bot)[0] == 409


@pytest.mark.parametrize("value", [" ok ", "\tok\n", "OK", " Ok"])
def test_the_assistant_never_answers_ok_with_space_or_case_around_it(api, value):
    """Codex review 2, finding 3: the Assistant's guard compared the raw value, and the engine stripped it after: " ok "
    answered a plain OK as the person. The value is normalised before the guard, and the engine refuses it too."""
    as_bot(api)
    ids = api.app.state.auth.settings.test_identities
    ids["ana-assistant-confirmed"] = Identity("human:ana", "owner", "ana@acme.example", via="assistant", confirmed=True)
    tid = bots_task(api, "ops-bot-test")
    with api.app.state.store.transaction() as c:
        version = H.task(c, tid)["version"]
        rid = H.task_ask(c, "bot:ops", tid, "human:ana", "OK?", kind="ok", seen_version=version)["requirement_id"]
    status, code, _, _ = call(
        api, "POST", f"tasks/{tid}/asks/{rid}/answer", "ana-assistant-confirmed", {"value": value}
    )
    assert status in (403, 409, 422) and code != "ok", (status, code)
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM answers WHERE requirement_id=? AND answer='ok'", (rid,)).fetchone()
    # The engine itself refuses it, whoever calls it, while the request is the Assistant's.
    token = H.VIA.set("assistant")
    try:
        with api.app.state.store.transaction() as c, pytest.raises(H.Refused):
            H.task_answer(c, "human:ana", tid, rid, value)
    finally:
        H.VIA.reset(token)


def test_an_ok_of_a_file_counts_only_while_its_giver_may_still_open_the_file(api):
    """Codex review 2, finding 4: file access was checked only when the preview opened. Taken away after, the cached
    hash still gave the OK, the bot used it, and `tico file approved` kept counting it. The giver's access to that file
    is checked again at the OK, at the use and when the verdict is read."""
    bot = as_bot(api)
    fid = a_file(api)

    def asked():
        tid = bots_task(api, bot)
        status, code, detail, out = call(
            api,
            "POST",
            f"tasks/{tid}/oks",
            bot,
            {
                "to": "ben",
                "line": "Post this cut?",
                "subject": f"file:{fid}",
                "content": {},
                "version": version(api, tid),
            },
        )
        assert status == 200, (status, code, detail)
        return tid, out["requirement_id"]

    def shut(open_again=False):
        with api.app.state.store.transaction() as c:
            if open_again:
                c.execute("UPDATE bot_files SET task_id=NULL WHERE id=?", (fid,))
                return
            hidden = H.task_create(c, "human:ana", "Ana's own " + H.new_id()[:6], "Private.", "human:ana", lint=False)
            c.execute("UPDATE tasks SET private=1 WHERE id=?", (hidden["id"],))
            c.execute("UPDATE bot_files SET task_id=? WHERE id=?", (hidden["id"], fid))

    def pinned(rid):
        with api.app.state.store.read() as c:
            row = K.grant(c, rid)
        return row["subject"], row["content"]

    def approved():
        return get(api, f"file-approvals?file={fid}", token="ana-test")["approved"]

    # At the OK: shown, then shut out before the click.
    tid, rid = asked()
    shown = get(api, f"oks/{rid}", token="ben-test")["shown_hash"]
    shut()
    status, _, _, _ = call(api, "POST", f"tasks/{tid}/asks/{rid}/ok", "ben-test", {"shown_hash": shown})
    assert status in (403, 404, 409) and standing(api, rid) == K.WAITING
    shut(open_again=True)
    # At the use and the verdict: OK'd, then shut out.
    tid, rid = asked()
    assert give(api, tid, rid)[0] == 200 and approved()
    shut()
    assert not approved()
    subject, content = pinned(rid)
    assert use(api, rid, bot, content=content, subject=subject)[0] == 409
    assert standing(api, rid) != K.USED


# ----------------------------------------------------------------------------- review 2, finding 5: known kinds only
def test_the_generic_routes_take_only_known_kinds_and_a_budget_raise_only_from_whom_may_give_it(api):
    """A kind Tico has no rule for is refused when asked and when used. A budget raise is OK'd only by the person it
    was asked of, while they are the bot's manager or the company's owner or an Admin: not by Cara, an ordinary
    teammate it was asked of, nor by Ana, the owner, when it was asked of Ben and handed to her; and Ben's OK is
    good only while he still manages the bot or is an Admin."""
    bot = as_bot(api)
    tid = bots_task(api, bot)
    unknown = {
        "to": "ben",
        "line": "OK?",
        "subject": "forum_post:note-1",
        "content": POST,
        "version": version(api, tid),
    }
    assert call(api, "POST", f"tasks/{tid}/oks", bot, unknown)[:2] == (422, "validation")
    assert use(api, "nope", bot, POST, "forum_post:note-1")[:2] == (422, "validation")
    raise_ = "budget_raise:launch"
    money = {"project": "launch", "usd": "10.00"}
    # Asked of Cara, who neither manages the bot nor runs the company.
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid, content=money, to="cara", subject=raise_)
    status, code, detail, _ = give(api, tid, rid, token="cara-test")
    assert (status, code) == (409, "task_requirement") and "manager" in detail, (status, code, detail)
    assert standing(api, rid) == K.WAITING
    # Asked of Ben and handed to Ana: the owner's click does not count, the ask was not hers.
    tid = bots_task(api, bot)
    rid = ask(api, bot, tid, content=money, subject=raise_ + "-2")
    with api.app.state.store.transaction() as c:
        H.task_give(c, "human:ben", tid, rid, "human:ana", H.task(c, tid)["version"])
    status, code, detail, _ = give(api, tid, rid, token="ana-test")
    assert (status, code) == (409, "task_requirement") and "Ben" in detail, (status, code, detail)
    # Ben's own OK, then he is neither an Admin nor the bot's manager: nothing goes.
    from backend.tests.helpers.api import as_member

    tid = bots_task(api, bot)
    rid = ask(api, bot, tid, content=money, subject=raise_ + "-3")
    assert give(api, tid, rid)[0] == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:cara' WHERE bot='ops'")
    as_member(api, "ben@acme.example")
    assert use(api, rid, bot, money, raise_ + "-3")[:2] == (409, "task_ok_missing")
    assert standing(api, rid) != K.USED


# ----------------------------------------------------------------------------- review 2, finding 6: parties only
def test_what_an_ok_is_for_shows_only_to_its_parties_in_every_read_of_its_task(api):
    """A bot asks Ben, who manages it, to OK a reply on a task the whole company reads; the line and what it shows name
    a confidential deal. Cara reads the task, its list row and its brief: she learns that it waits on a person, never
    what for, and the preview is not hers. Ben, a party, reads it all, in the task and in his Needs you."""
    from backend.tests.helpers.api import as_member

    bot = as_bot(api)
    as_member(api, "ben@acme.example")  # Ben is a party as the one asked, not as an Admin
    tid = bots_task(api, bot, title="Answer the ticket")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (tid,))
    status, code, detail, out = call(
        api,
        "POST",
        f"tasks/{tid}/oks",
        bot,
        {
            "to": "ben",
            "line": "Post the Globex merger terms to TK-9?",
            "subject": "hq_reply:TK-9",
            "content": {"text": "Globex pays 40M."},
            "show": ["page:globex-terms"],
            "version": version(api, tid),
        },
    )
    assert status == 200, (status, code, detail)
    rid = out["requirement_id"]
    task = api.get(f"/api/v2/tasks/{tid}", headers=headers("cara-test"))
    assert task.status_code == 200 and "Globex" not in task.text and "globex" not in task.text, task.text
    ok = next(r for r in (task.json().get("task") or task.json())["to_leave"] if r["requirement_id"] == rid)
    assert (ok["line"], ok["shows"], ok["asked_by"]) == (K.PRIVATE_LINE, [], None)
    listed = api.get("/api/v2/tasks", params={"status": "open"}, headers=headers("cara-test"))
    assert listed.status_code == 200 and "Globex" not in listed.text, listed.text
    assert api.get(f"/api/v2/oks/{rid}", headers=headers("cara-test")).status_code == 404
    # Ben, the one asked, reads what it is for, in the task and in Needs you.
    mine = api.get(f"/api/v2/tasks/{tid}", headers=headers("ben-test")).json()
    ok = next(r for r in (mine.get("task") or mine)["to_leave"] if r["requirement_id"] == rid)
    assert ok["line"] == "Post the Globex merger terms to TK-9?" and ok["shows"] == ["page:globex-terms"]
    item = next(i for i in get(api, "needs-you", token="ben-test")["items"] if i.get("requirement_id") == rid)
    assert item["line"] == "Post the Globex merger terms to TK-9?"


# ----------------------------------------------------------------------------- review 2, finding 8: the OK's events
def test_the_audit_of_an_ok_reads_only_to_its_parties_whoever_reads_its_task(api):
    """ok.asked, ok.given, ok.used and ok.not_done name what the OK was for. On a task the whole company reads, Cara
    finds none of them, through the SQL route or the event check (task_privacy.event_readable, which passed an event
    with no task named); Ben, the one asked, passes the check for each, and each names its task."""
    from backend import task_privacy

    bot = as_bot(api)
    tid = bots_task(api, bot, title="Answer the ticket")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (tid,))
    rid = ask(api, bot, tid, subject="hq_reply:TK-9")
    assert give(api, tid, rid)[0] == 200
    assert use(api, rid, bot, subject="hq_reply:TK-9")[0] == 200
    with api.app.state.store.transaction() as c:
        K.not_done(c, rid, "HQ refused it")
    query = {"sql": "SELECT action, detail FROM events WHERE target=? AND action LIKE 'ok.%'", "params": [rid]}
    # The SQL route: the owner reads the company's whole audit, Ben his own OK, Cara nothing of it.
    for token, seen in ((None, 4), ("ben-test", 1), ("cara-test", 0)):
        r = api.post("/api/v2/sql", json=query, headers=headers(token) if token else headers())
        assert r.status_code == 200, r.text
        assert len(r.json()["rows"]) == seen, (token, r.text)
    with api.app.state.store.read() as c:
        events = [dict(row) for row in c.execute("SELECT * FROM events WHERE target=? AND action LIKE 'ok.%'", (rid,))]
        assert {e["action"] for e in events} == {"ok.asked", "ok.given", "ok.used", "ok.not_done"}
        for event in events:
            assert json.loads(event["detail"])["task"] == tid, event
            assert not task_privacy.event_readable(c, "human:cara", event), event
            assert task_privacy.event_readable(c, "human:ben", event), event
