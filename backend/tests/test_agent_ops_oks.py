"""Agent Ops gives an OK for a person on their word in their own Agent Ops chat, when they turned that on (step 2 of the
spec "Your assistant gives an OK when you tell it to", backend/assistant_oks.py).

The same rules as their Assistant (backend/tests/test_assistant_oks.py): the same setting, the same 15 minutes, one task
per message and one OK per message, the same page checks. What differs is where the words come from: the person's own
message in their own chat with Agent Ops, which starts the run (a real Agent Ops turn: the run's token, asking to be
answered as the person, `X-Tico-On-Behalf-Of: turn`). Words anywhere else lend nothing, their Assistant room included.
Pages and the citation's other rooms are driven through the engine with the request state the route sets (H.VIA,
H.DELEGATION)."""

import pytest

from backend import answers as A
from backend import assistant_oks as AO
from backend import task_asks as K
from backend import task_engine as E
from backend import task_requirements as R
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.agent_ops_parity import act
from backend.tests.helpers.api import claim, post
from backend.tests.helpers.member_bots import agent_ops  # noqa: F401  (fixture)
from backend.tests.helpers.page_requirement import _page, _pipeline, _say, _task
from backend.tests.helpers.api import headers

AGENT_OPS = "bot:botops"
TITLE = "Hide suggestion cards when typing"
WORDS = "OK the spec for the suggestion cards task"


def call(api, method, path, token, body=None):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    return r.status_code, r.json() if r.content else {}


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


def gives_oks(api, on=True, token="ana-test"):
    return call(api, "POST", "assistant/settings", token, {"gives_oks": on})


def answers(api, rid):
    with api.app.state.store.read() as c:
        return c.execute("SELECT * FROM answers WHERE requirement_id=? AND undone_at IS NULL", (rid,)).fetchall()


def ops_turn(api, machine, text=WORDS, token="ana-test"):
    """Agent Ops' turn for Ana's message `text` in her own Agent Ops chat: the attempt, and the message it cites."""
    message = post(api, "chat/botops", {"text": text}, token)
    return claim(api, machine, "botops"), message["id"]


def ok(api, attempt, tid, rid, ref="turn"):
    r = act(api, attempt, "POST", f"tasks/{tid}/asks/{rid}/answer", {"value": "ok"}, ref=ref)
    out = r.json() if r.content else {}
    err = out.get("error") if isinstance(out, dict) else None
    return r.status_code, (err or {}).get("code"), (err or {}).get("detail")


def room_of(api, message_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT conversation_id FROM messages WHERE id=?", (message_id,)).fetchone()[0]


# ----------------------------------------------------------------------------- the setting
def test_off_by_default_agent_ops_is_told_to_have_it_turned_on_and_nothing_is_recorded(api, agent_ops):
    tid, rid = plain_ok(api)
    attempt, _ = ops_turn(api, agent_ops)
    assert ok(api, attempt, tid, rid) == (403, "ok_not_yours", AO.OFF)
    assert not answers(api, rid)


# ----------------------------------------------------------------------------- the OK on her word
def test_on_her_word_in_her_agent_ops_chat_the_ok_is_hers_via_agent_ops_with_her_words(api, agent_ops):
    tid, rid = plain_ok(api)
    assert gives_oks(api)[0] == 200
    attempt, cited = ops_turn(api, agent_ops)
    room = room_of(api, cited)
    status, code, detail = ok(api, attempt, tid, rid)
    assert status == 200, (code, detail)
    (given,) = answers(api, rid)
    assert (given["by_id"], given["answer"]) == ("human:ana", "ok")
    with api.app.state.store.read() as c:
        line = c.execute(
            "SELECT * FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='answer'", (tid,)
        ).fetchone()
        (receipt,) = c.execute(
            "SELECT * FROM messages WHERE conversation_id=? AND json_extract(refs,'$.assistant_ok') IS NOT NULL",
            (room,),
        ).fetchall()
    assert (line["actor_id"], line["via"]) == ("human:ana", "botops")
    assert H._json(line["data"], {})["delegation"] == {
        "cited": {"message_id": cited, "conversation_id": room},
        "words": WORDS,
    }
    # The receipt is Agent Ops' own line in the chat she spoke in, with the answer to undo.
    assert (receipt["sender_id"], receipt["recipient_id"]) == (AGENT_OPS, "human:ana")
    assert H._json(receipt["refs"], {})["assistant_ok"]["answer_id"] == given["id"]
    # The history shows her words and links her chat to her.
    shown = call(api, "GET", f"tasks/{tid}", "ana-test")[1]
    (seen,) = [e for e in shown["events"] if e.get("field") == "answer"]
    assert seen["delegation"] == {"words": WORDS, "cited": {"message_id": cited, "conversation_id": room}}
    # Her own Undo takes it back.
    assert call(api, "POST", f"answers/{given['id']}/undo", "ana-test", {})[0] == 200
    assert not answers(api, rid)


@pytest.mark.parametrize(
    "words",
    [
        "OK the spec for the pricing page task",  # names another task
        "OK it",  # names no task
        "Don't OK the suggestion cards task yet",  # says no
    ],
)
def test_words_that_do_not_say_yes_to_this_task_lend_no_ok(api, agent_ops, words):
    tid, rid = plain_ok(api)
    gives_oks(api)
    attempt, _ = ops_turn(api, agent_ops, words)
    assert ok(api, attempt, tid, rid) == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, rid)


def test_words_older_than_fifteen_minutes_lend_no_ok(api, agent_ops):
    tid, rid = plain_ok(api)
    gives_oks(api)
    attempt, cited = ops_turn(api, agent_ops)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), seconds=-16 * 60), cited))
    assert ok(api, attempt, tid, rid) == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, rid)


def test_one_message_lends_one_ok_even_after_her_undo(api, agent_ops):
    tid, rid = plain_ok(api)
    gives_oks(api)
    attempt, _ = ops_turn(api, agent_ops)
    assert ok(api, attempt, tid, rid)[0] == 200
    (given,) = answers(api, rid)
    assert call(api, "POST", f"answers/{given['id']}/undo", "ana-test", {})[0] == 200
    assert ok(api, attempt, tid, rid) == (403, "ok_not_yours", AO.NEEDS_WORDS)
    assert not answers(api, rid)


# ----------------------------------------------------------------------------- where the words come from
def _engine_ok(api, tid, rid, cited, *, via="botops", run="live"):
    """The engine's answer with the request state an Agent Ops turn for Ana sets: None when it counts, else the refusal."""
    via_token = H.VIA.set(via)
    lent = H.DELEGATION.set({"for": "human:ana", "cited": cited, "run": run})
    try:
        with api.app.state.store.transaction() as c:
            row = E.task(c, tid)
            return AO.refusal(c, "human:ana", row, R.get(c, rid))
    finally:
        H.DELEGATION.reset(lent)
        H.VIA.reset(via_token)


def _line(api, room, sender="human:ana", recipient=AGENT_OPS, refs="{}", body=WORDS):
    with api.app.state.store.transaction() as c:
        mid = H.new_id()
        c.execute(
            "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (mid, room, sender, recipient, "say", body, refs, H.now()),
        )
    return mid


def test_her_words_count_only_from_her_own_agent_ops_chat(api, agent_ops):
    """Agent Ops cites her words; they lend an OK only as her own line to Agent Ops in her chat with it."""
    tid, rid = plain_ok(api)
    gives_oks(api)
    _, cited = ops_turn(api, agent_ops)
    room = room_of(api, cited)
    assert _engine_ok(api, tid, rid, cited) is None
    # Her words to her Assistant are the Assistant's to act on, not Agent Ops'.
    post(api, "assistant/messages", {"text": WORDS}, "ana-test")
    with api.app.state.store.read() as c:
        (to_assistant,) = c.execute(
            "SELECT id FROM messages WHERE sender_id='human:ana' AND json_extract(refs,'$.assistant') IS NOT NULL"
        ).fetchone()
    assert _engine_ok(api, tid, rid, to_assistant) == AO.NEEDS_WORDS
    # A bot's line, a line the Assistant wrote for her, and one routed from Slack, all in her Agent Ops chat.
    assert _engine_ok(api, tid, rid, _line(api, room, sender=AGENT_OPS, recipient="human:ana")) == AO.NEEDS_WORDS
    assert _engine_ok(api, tid, rid, _line(api, room, refs='{"assistant": true}')) == AO.NEEDS_WORDS
    assert _engine_ok(api, tid, rid, _line(api, room, refs='{"slack": {"kind": "channel"}}')) == AO.NEEDS_WORDS
    # A run that holds her rights another way (her comment on a task, a request a bot carried) never OKs, as today.
    assert _engine_ok(api, tid, rid, cited, run="comment") == K.NOT_YOURS
    assert _engine_ok(api, tid, rid, cited, run="carried") == K.NOT_YOURS


def test_an_ok_bound_to_exact_content_stays_her_own_click(api, agent_ops):
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
    _, cited = ops_turn(api, agent_ops, f"OK {title}")
    assert _engine_ok(api, okd["task_id"], okd["requirement_id"], cited) == K.NOT_YOURS
    with api.app.state.store.read() as c:
        assert K.standing(c, okd["requirement_id"]) == K.WAITING


def test_the_tools_owner_acting_for_her_still_never_oks(api, agent_ops):
    from backend import tools_delegation

    tid, rid = plain_ok(api)
    gives_oks(api)
    _, cited = ops_turn(api, agent_ops)
    assert _engine_ok(api, tid, rid, cited, via=tools_delegation.VIA) == K.NOT_YOURS


# ----------------------------------------------------------------------------- a page, through the engine
@pytest.fixture
def page_case(company):
    """A Spec page Ana approves, Jev passed, and her words in her own Agent Ops chat as the turn's citation."""
    company.blobs = Blobs(company.settings)
    with company.transaction() as c:
        for slug in ("writer", "manager", "botops"):
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
        room = H.new_id()
        c.execute(
            "INSERT INTO conversations (id, participants, created_at) VALUES (?,?,?)",
            (room, H._dump([X.ANA, AGENT_OPS]), H.now()),
        )
        said = H.new_id()
        c.execute(
            "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (said, room, X.ANA, AGENT_OPS, "say", "OK the importer spec", "{}", H.shift(H.now(), seconds=1)),
        )
    via = H.VIA.set("botops")
    lent = H.DELEGATION.set({"for": X.ANA, "cited": said, "run": "live"})
    try:
        yield company, row, rid, fid, said
    finally:
        H.DELEGATION.reset(lent)
        H.VIA.reset(via)


def test_agent_ops_oks_a_page_jev_passed_on_her_word(page_case):
    store, row, rid, fid, _ = page_case
    with store.transaction() as c:
        done = A.answer(c, row["id"], rid, X.ANA, "ok", page_version=f"{fid}@1")
    assert done["stage"]["name"] == "Done"


def test_a_page_the_decision_model_did_not_pass_stays_her_own_click(page_case):
    store, row, rid, fid, _ = page_case
    with store.transaction() as c:
        c.execute("UPDATE page_verdicts SET verdict='unsure' WHERE file_id=?", (fid,))
    with store.transaction() as c, pytest.raises(H.Refused) as refused:
        A.answer(c, row["id"], rid, X.ANA, "ok", "It meets the check", page_version=f"{fid}@1")
    assert AO.NOT_PASSED in str(refused.value)


def test_a_page_attached_after_she_spoke_is_refused(page_case):
    store, row, rid, fid, said = page_case
    with store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), seconds=-60), said))
    with store.transaction() as c, pytest.raises(H.Refused) as refused:
        A.answer(c, row["id"], rid, X.ANA, "ok", page_version=f"{fid}@1")
    assert AO.PAGE_CHANGED in str(refused.value)
