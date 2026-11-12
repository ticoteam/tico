"""An OK bound to exactly what was shown, used once (backend/task_asks.py): asked of a person, given from its preview by
their own click, used by whoever asked for exactly that content, once. A push past, an undo, a send back, a change of
content and an Assistant acting for the person are never an OK."""

import pytest

from backend import task_asks as K
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X

ANA, BEN = "human:ana", "human:ben"
SENDER, OTHER = "bot:sender", "bot:other"
SUBJECT = "hq_reply:TK-1"
EMAIL = {"to": ["pat@example.com"], "subject": "Your invoice", "body_sha256": "a" * 64}


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "sender")
        X.bot(conn, "other")
        yield conn


def _refused(rule):
    return pytest.raises(H.Refused, match=rule)


def _ask(c, by=SENDER, person=ANA, content=None, **more):
    return K.ask_ok(
        c, by, person, "Send this email?", shows=["email:e1"], subject=SUBJECT, content=content or EMAIL, **more
    )


def _ok(c, asked, person=ANA, content=None):
    shown = K.show(c, person, asked["requirement_id"], content or EMAIL, own_click=True)
    K.give_ok(c, person, asked["requirement_id"], shown, own_click=True)


def test_an_ok_with_no_task_files_one_for_whoever_asked_and_waits_on_the_person_named(c):
    asked = _ask(c)
    task = H.task(c, asked["task_id"])
    assert (task["pipeline_id"], task["owner_id"], task["requester_id"]) == ("general", SENDER, SENDER)
    view = H.task_view(c, asked["task_id"], ANA)
    ok = next(r for r in view["to_leave"] if r["requirement_id"] == asked["requirement_id"])
    assert (ok["kind"], ok["who"], ok["asked_by"], ok["shows"]) == ("ok", ANA, SENDER, ["email:e1"])
    assert K.standing(c, asked["requirement_id"]) == K.WAITING
    assert _ask(c) == asked  # the same subject and content again is the same ask


def test_the_engine_asking_files_the_task_for_the_person(c):
    asked = _ask(c, by=H.KEEPER, title="Pay the March invoices")
    task = H.task(c, asked["task_id"])
    assert (task["owner_id"], task["title"]) == (ANA, "Pay the March invoices")


def test_an_ok_is_asked_of_a_person_never_a_bot(c):
    with _refused("task_requirement"):
        _ask(c, person=OTHER)


def test_the_ok_is_given_only_by_the_named_person_from_the_preview_they_were_shown(c):
    asked = _ask(c)
    rid = asked["requirement_id"]
    digest = K.content_hash(EMAIL)
    with _refused("task_requirement"):
        K.give_ok(c, ANA, rid, digest, own_click=True)  # never shown: knowing the hash is not seeing it
    K.show(c, BEN, rid, EMAIL, own_click=True)
    with _refused("task_requirement"):
        K.give_ok(c, BEN, rid, digest, own_click=True)  # Ben saw it, but it is asked of Ana
    shown = K.show(c, ANA, rid, {**EMAIL, "subject": "Something else"}, own_click=True)
    with _refused("task_changed"):
        K.give_ok(c, ANA, rid, shown, own_click=True)  # what she saw is not what was asked
    assert K.show(c, ANA, rid, EMAIL, own_click=False) == digest  # an Assistant's read records no showing
    with _refused("task_requirement"):
        K.give_ok(c, ANA, rid, digest, own_click=True)
    _ok(c, asked)
    assert K.standing(c, rid) == K.OK
    answer = H.task_view(c, asked["task_id"], ANA)["to_leave"][-1]["answer"]
    assert (answer["value"], answer["by"]) == ("ok", ANA)


def test_an_assistant_or_a_token_acting_for_the_person_is_not_their_click(c):
    asked = _ask(c)
    rid = asked["requirement_id"]
    for who in (
        Identity(ANA, "human", via="assistant", confirmed=True),
        Identity(ANA, "human", via_token=True),
        Identity(ANA, "human", via="botops", confirmed=True),
        Identity(ANA, "human", support="app"),
        Identity(SENDER, "bot"),
    ):
        assert not K.own_click(who)
        shown = K.show(c, ANA, rid, EMAIL, own_click=K.own_click(who))
        with _refused("identity"):
            K.give_ok(c, ANA, rid, shown, own_click=K.own_click(who))
    assert K.own_click(Identity(ANA, "human")) and K.own_click(Identity(ANA, "owner"))
    assert K.standing(c, rid) == K.WAITING


def test_the_ok_is_used_once_by_whoever_asked_for_exactly_that_content(c):
    asked = _ask(c)
    rid = asked["requirement_id"]
    with _refused("task_ok_missing"):
        K.use(c, rid, SUBJECT, EMAIL, SENDER)  # nobody has OK'd it yet
    _ok(c, asked)
    with _refused("identity"):
        K.use(c, rid, SUBJECT, EMAIL, OTHER)  # the wrong bot
    with _refused("identity"):
        K.use(c, rid, "hq_reply:TK-2", EMAIL, SENDER)  # another email
    with _refused("task_ok_changed"):
        K.use(c, rid, SUBJECT, {**EMAIL, "to": ["someone@else.example"]}, SENDER)
    K.check(c, rid, SUBJECT, EMAIL, SENDER)
    assert K.standing(c, rid) == K.OK  # check writes nothing
    K.use(c, rid, SUBJECT, EMAIL, SENDER)
    assert K.standing(c, rid) == K.USED
    with _refused("consumed"):
        K.use(c, rid, SUBJECT, EMAIL, SENDER)
    with _refused("consumed"):
        _ok(c, asked)
    K.settle(c, rid, "Sent the email.")
    assert H.task(c, asked["task_id"])["status"] == "done"


def test_a_push_past_is_never_an_ok(c):
    asked = _ask(c)
    rid = asked["requirement_id"]
    version = H.task(c, asked["task_id"])["version"]
    moved = H.task_move(c, SENDER, asked["task_id"], H.TASK_NEXT, "Ana is away; it is urgent.", version)
    assert moved["status"] == "done"
    assert K.standing(c, rid) == K.WITHDRAWN
    with _refused("task_ok_missing"):
        K.use(c, rid, SUBJECT, EMAIL, SENDER)
    K.show(c, ANA, rid, EMAIL, own_click=True)
    with _refused("task_requirement"):
        K.give_ok(c, ANA, rid, K.content_hash(EMAIL), own_click=True)


def test_an_ok_taken_back_or_sent_back_is_not_one(c):
    asked = _ask(c)
    rid = asked["requirement_id"]
    _ok(c, asked)
    answer_id = K.grant(c, rid)["answer_id"]
    H.task_answer_undo(c, ANA, answer_id)
    assert K.standing(c, rid) == K.WITHDRAWN  # taken back for good: a new OK needs a new ask
    with _refused("task_ok_missing"):
        K.use(c, rid, SUBJECT, EMAIL, SENDER)
    H.task_answer(c, ANA, asked["task_id"], rid, "no", "Say the amount.")
    assert K.standing(c, rid) == K.SENT_BACK
    with _refused("task_ok_missing"):
        K.use(c, rid, SUBJECT, EMAIL, SENDER)
    K.settle(c, rid, "Sent back: Say the amount.")
    assert H.task(c, asked["task_id"])["status"] == "closed"


def test_asking_again_with_other_content_withdraws_the_old_ok_even_one_that_was_given(c):
    first = _ask(c)
    _ok(c, first)
    redrafted = {**EMAIL, "body_sha256": "b" * 64}
    second = _ask(c, content=redrafted)
    assert second["requirement_id"] != first["requirement_id"]
    assert K.standing(c, first["requirement_id"]) == K.WITHDRAWN
    assert H.task(c, first["task_id"])["status"] == "closed"
    with _refused("task_ok_missing"):
        K.use(c, first["requirement_id"], SUBJECT, EMAIL, SENDER)
    _ok(c, second, content=redrafted)
    K.use(c, second["requirement_id"], SUBJECT, redrafted, SENDER)


def test_asking_about_a_subject_another_asked_about_leaves_their_ask_alone(c):
    """A bot's subject (a GENERIC kind) is its own words: another bot asking about the same subject on its own task never
    withdraws the first one's ask, private task and all. Both stand side by side, each used by whoever asked it. The
    same asker asking again on another task leaves the first too; on the same task it replaces it."""
    mine = H.task_create(c, SENDER, "Raise the launch budget", "More for the launch.", SENDER, lint=False, private=True)
    theirs = H.task_create(c, OTHER, "Answer the ticket", "Reply to TK-1.", OTHER, lint=False)
    first = _ask(c, task_id=mine["id"])
    second = _ask(c, by=OTHER, content={**EMAIL, "body_sha256": "b" * 64}, task_id=theirs["id"])
    assert K.standing(c, first["requirement_id"]) == K.WAITING
    assert H.task(c, mine["id"])["status"] == "open"
    assert K.standing(c, second["requirement_id"]) == K.WAITING
    elsewhere = H.task_create(c, SENDER, "Another reply", "Reply again.", SENDER, lint=False)
    third = _ask(c, content={**EMAIL, "body_sha256": "c" * 64}, task_id=elsewhere["id"])
    assert K.standing(c, first["requirement_id"]) == K.WAITING and third["task_id"] == elsewhere["id"]
    again = _ask(c, content={**EMAIL, "body_sha256": "d" * 64}, task_id=mine["id"])
    assert K.standing(c, first["requirement_id"]) == K.WITHDRAWN
    assert K.standing(c, again["requirement_id"]) == K.WAITING and K.standing(c, third["requirement_id"]) == K.WAITING
    _ok(c, second, content={**EMAIL, "body_sha256": "b" * 64})
    K.use(c, second["requirement_id"], SUBJECT, {**EMAIL, "body_sha256": "b" * 64}, OTHER)


def test_an_ok_handed_to_another_person_is_not_theirs_to_give(c):
    """A give hands on the ask, never the right to OK it: Ben, given Ana's ask, is refused, and so is Ana, who no
    longer holds it. It waits for a new ask."""
    asked = _ask(c)
    rid = asked["requirement_id"]
    H.set_task_readers(c, ANA, asked["task_id"], add=[BEN])  # the task is private: a give makes nobody a reader
    H.task_give(c, ANA, asked["task_id"], rid, BEN)
    K.show(c, ANA, rid, EMAIL, own_click=True)
    with _refused("task_requirement"):
        K.give_ok(c, ANA, rid, K.content_hash(EMAIL), own_click=True)
    with _refused("task_requirement"):
        _ok(c, asked, person=BEN)
    assert K.standing(c, rid) == K.WAITING
    with _refused("task_ok_missing"):
        K.use(c, rid, SUBJECT, EMAIL, SENDER)


def test_the_stored_content_is_what_the_preview_shows(c):
    asked = _ask(c)
    seen = K.preview(c, ANA, asked["requirement_id"], own_click=True)
    assert (seen["subject"], seen["content"], seen["standing"]) == (SUBJECT, EMAIL, K.WAITING)
    K.give_ok(c, ANA, asked["requirement_id"], seen["shown_hash"], own_click=True)
    assert K.standing(c, asked["requirement_id"]) == K.OK
