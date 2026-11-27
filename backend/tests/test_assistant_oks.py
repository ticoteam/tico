"""A person's own Assistant gives an OK for them on their word, when they turned that on (backend/assistant_oks.py).

Each test is one acceptance line of the spec "Your assistant gives an OK when you tell it to": a real Assistant turn
(the person's own message starts the run, which acts with the run's token and cites that message), against the answer
route. Pages are driven through the engine with the same request state the route sets (H.VIA, H.DELEGATION)."""

import pytest

from backend import answers as A
from backend import assistant_oks as AO
from backend import task_asks as K
from backend import task_engine as E
from backend.auth import Identity
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.assistant import assistant_turn, turn_message
from backend.tests.helpers.page_requirement import _page, _pipeline, _say, _task

TITLE = "Hide suggestion cards when typing"
WORDS = "OK the spec for the suggestion cards task"


def call(api, method, path, token, body=None):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    out = r.json() if r.content else {}
    err = out.get("error") if isinstance(out, dict) else None
    return r.status_code, (err or {}).get("code"), (err or {}).get("detail"), out


def plain_ok(api, title=TITLE, of="human:ana"):
    """A plain OK (no content bound to it) a bot asked of Ana on a task of hers: (task id, requirement id)."""
    with api.app.state.store.transaction() as c:
        X.bot(c, "ops")
        tid = H.task_create(c, "human:ana", title, "Build it.", "bot:ops", lint=False)["id"]
        version = H.task(c, tid)["version"]
        rid = H.task_ask(c, "bot:ops", tid, of, "OK the spec?", kind="ok", shows=X.shown(c), seen_version=version)[
            "requirement_id"
        ]
    return tid, rid


def turn(api, text=WORDS):
    """The Assistant's turn for Ana's message `text`: its run token, and the message it cites."""
    _, attempt = assistant_turn(api, "ana-test", text)
    return attempt["token"], turn_message(api, attempt)


def gives_oks(api, on=True, token="ana-test"):
    return call(api, "POST", "assistant/settings", token, {"gives_oks": on})


def answers(api, rid):
    with api.app.state.store.read() as c:
        return c.execute("SELECT * FROM answers WHERE requirement_id=? AND undone_at IS NULL", (rid,)).fetchall()


def ok(api, token, tid, rid):
    return call(api, "POST", f"tasks/{tid}/asks/{rid}/answer", token, {"value": "ok"})


# ----------------------------------------------------------------------------- the setting
def test_off_by_default_the_assistant_is_told_to_have_it_turned_on_and_nothing_is_recorded(api):
    tid, rid = plain_ok(api)
    token, _ = turn(api)
    status, code, detail, _ = ok(api, token, tid, rid)
    assert (status, code, detail) == (403, "ok_not_yours", AO.OFF)
    assert not answers(api, rid)


def test_only_the_persons_own_click_turns_it_on_or_off(api):
    """Her Assistant's turn, Agent Ops acting for her and her personal token are refused; her own click is not."""
    ids = api.app.state.auth.settings.test_identities
    ids["ana-token"] = Identity("human:ana", "owner", "ana@acme.example", via_token=True)
    ids["ana-agent-ops"] = Identity("human:ana", "owner", "ana@acme.example", via="botops")
    token, _ = turn(api, "Turn on giving OKs for me")
    for who in (token, "ana-token", "ana-agent-ops"):
        for on in (True, False):
            status, _, _, _ = gives_oks(api, on, token=who)
            assert status == 403, (who, on)
    with api.app.state.store.read() as c:
        assert AO.on(c, "human:ana") is False
    assert call(api, "GET", "assistant/settings", "ana-test")[3] == {"gives_oks": False, "may_change": True}
    # Read by anyone acting for her, the switch says it is not theirs to change.
    assert call(api, "GET", "assistant/settings", "ana-token")[3] == {"gives_oks": False, "may_change": False}
    assert gives_oks(api, True)[0] == 200
    assert call(api, "GET", "assistant/settings", "ana-test")[3] == {"gives_oks": True, "may_change": True}
    with api.app.state.store.read() as c:
        assert AO.on(c, "human:ana") is True and AO.on(c, "human:ben") is False
    assert gives_oks(api, False)[0] == 200
    with api.app.state.store.read() as c:
        assert AO.on(c, "human:ana") is False


# ----------------------------------------------------------------------------- the OK on her word
def test_on_her_word_the_assistant_oks_and_the_history_says_via_assistant_with_her_words(api):
    tid, rid = plain_ok(api)
    assert gives_oks(api)[0] == 200
    token, cited = turn(api)
    with api.app.state.store.read() as c:
        task_stage_before = E.task(c, tid)["stage"]["name"]
    status, code, detail, _ = ok(api, token, tid, rid)
    assert status == 200, (code, detail)
    (given,) = answers(api, rid)
    assert (given["by_id"], given["answer"]) == ("human:ana", "ok")
    with api.app.state.store.read() as c:
        line = c.execute(
            "SELECT * FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='answer'", (tid,)
        ).fetchone()
    data = H._json(line["data"], {})
    assert (line["actor_id"], line["via"]) == ("human:ana", "assistant")
    with api.app.state.store.read() as c:
        room = c.execute("SELECT conversation_id FROM messages WHERE id=?", (cited,)).fetchone()[0]
        card = c.execute(
            "SELECT * FROM messages WHERE conversation_id=? AND json_extract(refs,'$.assistant_ok') IS NOT NULL",
            (room,),
        ).fetchall()
    assert data["delegation"] == {"cited": {"message_id": cited, "conversation_id": room}, "words": WORDS}
    # The receipt in the chat she spoke in names the task and stage back and carries the answer to undo.
    (receipt,) = card
    assert receipt["sender_id"].startswith("bot:") and receipt["recipient_id"] == "human:ana"
    assert H._json(receipt["refs"], {})["assistant_ok"] == {
        "answer_id": given["id"],
        "task_id": tid,
        "title": TITLE,
        "stage": task_stage_before,
    }
    # Her own Undo takes it back, as for an OK she clicked.
    assert call(api, "POST", f"answers/{given['id']}/undo", "ana-test", {})[0] == 200
    assert not answers(api, rid)


@pytest.mark.parametrize(
    "words",
    [
        "OK the spec for the pricing page task",  # names another task
        "OK it",  # names no task
    ],
)
def test_words_that_do_not_name_the_task_lend_no_ok(api, words):
    tid, rid = plain_ok(api)
    gives_oks(api)
    token, _ = turn(api, words)
    status, code, detail, _ = ok(api, token, tid, rid)
    assert (status, code, detail) == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, rid)


def test_the_task_number_names_it(api):
    tid, rid = plain_ok(api)
    gives_oks(api)
    with api.app.state.store.read() as c:
        number = H.task(c, tid)["number"]
    token, _ = turn(api, f"OK #{number}")
    assert ok(api, token, tid, rid)[0] == 200


def test_words_older_than_fifteen_minutes_lend_no_ok(api):
    tid, rid = plain_ok(api)
    gives_oks(api)
    token, cited = turn(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), seconds=-16 * 60), cited))
    status, code, detail, _ = ok(api, token, tid, rid)
    assert (status, code, detail) == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, rid)


def test_a_bots_message_lends_no_ok(api):
    """The turn may cite only a message of hers in her own Assistant room: a bot's line there is refused."""
    tid, rid = plain_ok(api)
    gives_oks(api)
    token, cited = turn(api)
    with api.app.state.store.transaction() as c:
        room = c.execute("SELECT conversation_id FROM messages WHERE id=?", (cited,)).fetchone()[0]
        bot_line = H.new_id()
        c.execute(
            "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (bot_line, room, "bot:ops", "human:ana", "say", WORDS, '{"assistant": true}', H.now()),
        )
    r = api.post(
        f"/api/v2/tasks/{tid}/asks/{rid}/answer",
        json={"value": "ok"},
        headers={**headers(token), "X-Tico-On-Behalf-Of": bot_line},
    )
    assert r.status_code == 403, r.text
    assert not answers(api, rid)


@pytest.mark.parametrize("words", ["OK both suggestion cards tasks", "OK #{a} and #{b}"])
def test_one_message_oks_one_task(api, words):
    """ "OK both" is not an OK: words that fit two tasks, by number or by title, name neither."""
    first, first_rid = plain_ok(api)
    second, second_rid = plain_ok(api, title="Show suggestion cards on phones")
    gives_oks(api)
    with api.app.state.store.read() as c:
        a, b = H.task(c, first)["number"], H.task(c, second)["number"]
    token, _ = turn(api, words.format(a=a, b=b))
    for tid, rid in ((first, first_rid), (second, second_rid)):
        assert ok(api, token, tid, rid)[:3] == (403, "ok_not_yours", AO.NEEDS_WORDS), tid
    assert not answers(api, first_rid) and not answers(api, second_rid)


@pytest.mark.parametrize(
    "words",
    [
        "Don't OK the suggestion cards task yet",
        "Should I OK the suggestion cards task?",
        "Send the suggestion cards task back",
        "What does the suggestion cards task say",
    ],
)
def test_words_that_do_not_say_yes_lend_no_ok(api, words):
    """Naming the task is not saying yes to it: a no, a question, a send back or no yes at all lends nothing."""
    tid, rid = plain_ok(api)
    gives_oks(api)
    token, _ = turn(api, words)
    assert ok(api, token, tid, rid)[:3] == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, rid)


def test_another_tasks_number_wins_over_title_words(api):
    """ "OK #<other>, the suggestion cards follow-up" points at the other task, whatever this title shares with it."""
    tid, rid = plain_ok(api)
    other, _ = plain_ok(api, title="Price the follow-up")
    gives_oks(api)
    with api.app.state.store.read() as c:
        number = H.task(c, other)["number"]
    token, _ = turn(api, f"OK #{number}, the suggestion cards follow-up")
    assert ok(api, token, tid, rid)[:3] == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, rid)


def test_title_words_that_fit_two_open_tasks_name_neither(api):
    first, first_rid = plain_ok(api)
    plain_ok(api, title="Hide suggestion cards on phones")
    gives_oks(api)
    token, _ = turn(api, "OK the hide suggestion cards task")
    assert ok(api, token, first, first_rid)[:3] == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, first_rid)


def test_an_ok_she_undid_is_not_given_again_on_the_same_words(api):
    """One message lends one OK: after her Undo, the same words lend nothing, on this task or any other."""
    tid, rid = plain_ok(api)
    gives_oks(api)
    token, _ = turn(api)
    assert ok(api, token, tid, rid)[0] == 200
    (given,) = answers(api, rid)
    assert call(api, "POST", f"answers/{given['id']}/undo", "ana-test", {})[0] == 200
    assert ok(api, token, tid, rid)[:3] == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, rid)


def test_a_message_edited_before_the_ok_is_written_lends_nothing(api):
    """The words are read again in the answer's own transaction: an edit since the run was told lends nothing."""
    tid, rid = plain_ok(api)
    gives_oks(api)
    _, cited = turn(api)
    via = H.VIA.set("assistant")
    lent = H.DELEGATION.set({"for": "human:ana", "cited": cited, "run": "asked"})
    try:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE messages SET edited_at=? WHERE id=?", (H.now(), cited))
            row = E.task(c, tid)
            with pytest.raises(H.Refused) as refused:
                A.answer(c, tid, rid, "human:ana", "ok")
        assert AO.NEEDS_WORDS in str(refused.value)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE messages SET edited_at=NULL WHERE id=?", (cited,))
            assert AO.refusal(c, "human:ana", row, _req(c, rid)) is None
    finally:
        H.DELEGATION.reset(lent)
        H.VIA.reset(via)


def _req(c, rid):
    from backend import task_requirements as R

    return R.get(c, rid)


def test_the_history_line_shows_her_words_and_links_her_message_to_whoever_may_read_it(api):
    tid, rid = plain_ok(api)
    gives_oks(api)
    token, cited = turn(api)
    assert ok(api, token, tid, rid)[0] == 200
    with api.app.state.store.read() as c:
        room = c.execute("SELECT conversation_id FROM messages WHERE id=?", (cited,)).fetchone()[0]
    shown = call(api, "GET", f"tasks/{tid}", "ana-test")[3]
    (line,) = [e for e in shown["events"] if e.get("field") == "answer"]
    assert "data" not in line
    assert line["delegation"] == {"words": WORDS, "cited": {"message_id": cited, "conversation_id": room}}
    # Someone who may read the task but not her Assistant chat reads her words without the link.
    status, _, _, other = call(api, "GET", f"tasks/{tid}", "ben-test")
    assert status == 200
    (seen,) = [e for e in other["events"] if e.get("field") == "answer"]
    assert seen["delegation"] == {"words": WORDS, "cited": None}


def test_an_ok_bound_to_exact_content_stays_her_own_click(api):
    """A budget raise (and any OK with content behind it: a send, a file, an access grant) keeps today's refusal."""
    gives_oks(api)
    with api.app.state.store.transaction() as c:
        X.bot(c, "finance")
        okd = K.ask_ok(
            c,
            "bot:finance",
            "human:ana",
            "Raise the ads budget?",
            shows=[],
            subject="budget_raise:ads",
            content={"amount": 1200},
        )
        title = H.task(c, okd["task_id"])["title"]
    token, _ = turn(api, f"OK {title}")
    status, code, detail, _ = ok(api, token, okd["task_id"], okd["requirement_id"])
    assert (status, code, detail) == (403, "ok_not_yours", K.NOT_YOURS)
    with api.app.state.store.read() as c:
        assert K.standing(c, okd["requirement_id"]) == K.WAITING


def test_a_personal_token_is_refused_as_today(api):
    tid, rid = plain_ok(api)
    gives_oks(api)
    ids = api.app.state.auth.settings.test_identities
    ids["ana-token"] = Identity("human:ana", "owner", "ana@acme.example", via_token=True)
    assert ok(api, "ana-token", tid, rid)[:3] == (403, "ok_not_yours", K.OWN_IN_TICO)
    assert not answers(api, rid)


# ----------------------------------------------------------------------------- a page, through the engine
@pytest.fixture
def page_case(company, monkeypatch):
    """A Spec page Ana approves, Jev passed, and Ana's words as the turn's citation (H.VIA, H.DELEGATION): what the
    route sets for an Assistant turn. Her words are read through `assistant.cited_message`, stubbed here."""
    company.blobs = Blobs(company.settings)
    said = {"id": "m1", "sender_id": X.ANA, "body": "OK the importer spec"}
    from backend import assistant

    monkeypatch.setattr(assistant, "cited_message", lambda c, cited: dict(said) if cited == said["id"] else None)
    with company.transaction() as c:
        for slug in ("writer", "manager"):
            X.bot(c, slug)
        rid = _pipeline(c, person=X.ANA)
        row = _task(c)
        fid, _ = _page(c, company)
        seen = H.task(c, row["id"])["version"]
        from backend import task_pages as P

        _say(c, P.attach(c, row["id"], "Spec page", "page:" + fid, "bot:writer", seen_version=seen), "met")
        c.execute(
            "INSERT INTO assistant_ok_settings (person_id, gives_oks, updated_at) VALUES (?,1,?)", (X.ANA, H.now())
        )
    said["created_at"] = H.now()  # she speaks after the page is attached
    via = H.VIA.set("assistant")
    lent = H.DELEGATION.set({"for": X.ANA, "cited": "m1", "run": "asked"})
    try:
        yield company, row, rid, fid, said
    finally:
        H.DELEGATION.reset(lent)
        H.VIA.reset(via)


def test_the_assistant_oks_a_page_jev_passed_on_her_word(page_case):
    store, row, rid, fid, _ = page_case
    with store.transaction() as c:
        done = A.answer(c, row["id"], rid, X.ANA, "ok", page_version=f"{fid}@1")
    assert done["stage"]["name"] == "Done"


def test_a_page_attached_after_she_spoke_is_refused(page_case):
    store, row, rid, fid, said = page_case
    said["created_at"] = H.shift(H.now(), seconds=-60)
    with store.transaction() as c:
        c.execute("UPDATE requirement_pages SET created_at=? WHERE requirement_id=?", (H.now(), rid))
    with store.transaction() as c, pytest.raises(H.Refused, match="task_requirement") as refused:
        A.answer(c, row["id"], rid, X.ANA, "ok", page_version=f"{fid}@1")
    assert AO.PAGE_CHANGED in str(refused.value)


def test_a_page_jev_has_not_passed_is_refused_as_for_her_own_click(page_case, company):
    store, row, rid, fid, _ = page_case
    with store.transaction() as c:
        _page(c, store, "<p>v2</p>", fid=fid)
        from backend import task_pages as P

        P.attach(c, row["id"], "Spec page", "page:" + fid, "bot:writer", seen_version=H.task(c, row["id"])["version"])
    with store.transaction() as c, pytest.raises(H.Refused):
        A.answer(c, row["id"], rid, X.ANA, "ok", page_version=f"{fid}@2")


def test_a_page_the_decision_model_did_not_pass_stays_her_own_click(page_case):
    """Attached before she spoke, but Jev was unsure: a person may still OK it in Tico, her Assistant may not."""
    store, row, rid, fid, _ = page_case
    with store.transaction() as c:
        c.execute("UPDATE page_verdicts SET verdict='unsure' WHERE file_id=?", (fid,))
    with store.transaction() as c, pytest.raises(H.Refused) as refused:
        A.answer(c, row["id"], rid, X.ANA, "ok", "It meets the check", page_version=f"{fid}@1")
    assert AO.NOT_PASSED in str(refused.value)


def test_without_her_setting_a_page_stays_refused(page_case):
    store, row, rid, fid, _ = page_case
    with store.transaction() as c:
        c.execute("DELETE FROM assistant_ok_settings")
    with store.transaction() as c, pytest.raises(H.Refused) as refused:
        A.answer(c, row["id"], rid, X.ANA, "ok", page_version=f"{fid}@1")
    assert AO.OFF in str(refused.value)


def test_names_reads_number_title_words_and_not_one_word_alone():
    task = {"id": "t1", "number": 3555, "prefix": "E", "title": TITLE}
    assert AO.names(WORDS, task)
    assert AO.names("ok #3555", task) and AO.names("OK E-3555 please", task)
    assert not AO.names("OK the suggestion task", task), "one title word is not the task"
    assert not AO.names("OK 3555", task), "a bare number may be anything"
    assert not AO.names("OK the pricing spec", task)
