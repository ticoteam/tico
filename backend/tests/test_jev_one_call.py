"""Every Jev use asks all its questions of one input in as few calls as it can (the "one call per input" spec, Fix 1):
Jev charges for what it reads, so the same input goes out again only when its questions do not fit beside it in one
call (`J.MAX_CALL_CHARS`) or there are more than the Gateway answers in one call (`J.MAX_QUESTIONS`, 40: seen in
production on 2026-11-13, where 41 or more came back as an error).

Each test hands in a fake Jev that counts its calls and answers each question from its own content and the state it
was asked about, never from its id, so answers that moved to a merged call can be compared with the answers alone."""

import hashlib
import json
import math

import pytest

from backend import automation_engine as E
from backend import decide_groups as DG
from backend import learnings as L
from backend.tests.automation_fakes import Services
from clients import judge as J
from connectors.mail import judge as jd


def answer(state, question):
    """The fake's answer to one question about `state`: fixed by both, whatever the question's id."""
    seed = hashlib.sha256(json.dumps([state, question], sort_keys=True, default=str).encode()).digest()[0] / 255
    if question["type"] == "choice":
        options = list(question["criteria"])
        return {"choice": options[int(seed * 1000) % len(options)], "confidence": round(seed, 3)}
    if question["type"] == "score":
        return {"score": float(int(seed * 1000) % len(question["criteria"])), "confidence": round(seed, 3)}
    return {"noul": round(seed, 3)}


GATEWAY_MOST = 40  # the Gateway answers a call of 41 or more questions with an error (production, 2026-11-13)


class Jev:
    """Counts calls, refuses more than GATEWAY_MOST questions as the Gateway does, checks each call as the real client
    does (`J.validate`) and answers every question (`answer`)."""

    def __init__(self):
        self.calls = []

    def __call__(self, state, questions, label=None):
        if len(questions) > GATEWAY_MOST:
            raise J.JudgeError("unavailable", "The decision model answered with an error")
        J.validate(state, questions, label)
        self.calls.append({"state": state, "questions": dict(questions), "label": label})
        return {
            "model": J.MODEL,
            "usage": {},
            "ms": 1,
            "answers": J.check_answers({qid: answer(state, q) for qid, q in questions.items()}, questions),
        }


class FairJev(Jev):
    """A Jev that answers each item of a grouped call as if it were asked about that item alone (`answer` on the
    item's own state and question), so a grouped item's answers must equal its answers alone; `drop` names items
    whose answers it leaves out of a grouped call, and `fail` fails every grouped call."""

    def __init__(self, drop=(), fail=False):
        super().__init__()
        self.drop, self.fail = set(drop), fail

    def __call__(self, state, questions, label=None):
        held = DG.split(state, questions)
        if held is None:
            return super().__call__(state, questions, label)
        if self.fail:
            raise J.JudgeError("timeout", "The decision model took too long", retryable=True)
        J.validate(state, questions, label)
        self.calls.append({"state": state, "questions": dict(questions), "label": label, "items": len(held)})
        answers = {}
        for name, one, ids in held:
            if name in self.drop:
                continue
            lead = f"About {name} only (state.items.{name}): "
            for _own, key in ids.items():
                q = dict(questions[key])
                q["instructions"] = q["instructions"].removeprefix(lead)
                answers[key] = answer(one, q)
        return {"model": J.MODEL, "usage": {}, "ms": 1, "answers": answers}


def nouls(count, text="Is this about item {n}?"):
    return {f"q{n:03}": {"type": "noul", "instructions": text.format(n=n)} for n in range(count)}


# ------------------------------------------------------------------------------------------- the client: size and count
def test_a_call_takes_up_to_the_gateways_40_questions_and_is_refused_past_them_or_its_size():
    assert J.MAX_QUESTIONS == GATEWAY_MOST
    J.validate({"text": "hello"}, nouls(40))
    with pytest.raises(J.JudgeError) as refused:
        J.validate({"text": "hello"}, nouls(41))
    assert refused.value.code == "invalid" and "40" in refused.value.detail
    big = {f"q{n}": {"type": "noul", "instructions": "x" * 7_000} for n in range(40)}
    with pytest.raises(J.JudgeError) as refused:
        J.validate({"text": "hello"}, big)
    assert refused.value.code == "invalid" and str(J.MAX_CALL_CHARS) in refused.value.detail
    assert J.fits({"text": "hello"}, nouls(40)) and not J.fits({"text": "hello"}, nouls(41))


def test_questions_that_fit_go_in_one_call_unchanged():
    questions = nouls(40)
    assert J.split({"text": "hello"}, questions) == [questions]


@pytest.mark.parametrize("count, calls", [(41, 2), (59, 2), (80, 2), (81, 3), (300, 8)])
def test_more_than_40_small_questions_split_into_the_fewest_calls_of_40_each_question_once(count, calls):
    state, questions = {"text": "hello"}, nouls(count)
    parts = J.split(state, questions)
    assert len(parts) == calls == math.ceil(count / GATEWAY_MOST)
    assert all(len(part) <= GATEWAY_MOST for part in parts)
    assert sorted(qid for part in parts for qid in part) == sorted(questions)
    assert all(list(part) == [q for q in questions if q in part] for part in parts)
    jev = Jev()
    for part in parts:
        jev(state, part)  # each call is one the Gateway answers


def test_questions_too_big_for_one_call_split_into_the_fewest_that_fit_and_none_twice(monkeypatch):
    """AC-5: the fewest calls that fit, every question in exactly one, in its own order."""
    state, questions = {"text": "hello"}, nouls(90, "Is this about item {n:03}? " + "x" * 200)
    each = J.chars({"q010": questions["q010"]})
    assert {J.chars({qid: q}) for qid, q in questions.items()} == {each}  # one size: the fewest is exact
    room = 25 * each
    monkeypatch.setattr(J, "MAX_CALL_CHARS", J.chars(state) + room)
    parts = J.split(state, questions)
    assert len(parts) == math.ceil(90 / 25) == 4
    assert sorted(qid for part in parts for qid in part) == sorted(questions)
    assert all(list(part) == [q for q in questions if q in part] for part in parts)
    for part in parts:
        J.validate(state, part)
    assert J.fits(state, parts[0]) and not J.fits(state, questions)


def test_a_mix_of_sizes_packs_large_first_into_calls_with_room(monkeypatch):
    state = {"text": "hello"}
    questions = {"big": {"type": "noul", "instructions": "b" * 600}}
    questions.update({f"s{n}": {"type": "noul", "instructions": "s" * 100} for n in range(8)})
    sizes = {qid: J.chars({qid: q}) for qid, q in questions.items()}
    monkeypatch.setattr(J, "MAX_CALL_CHARS", J.chars(state) + sizes["big"] + 3 * sizes["s0"])
    parts = J.split(state, questions)
    assert len(parts) == math.ceil(sum(sizes.values()) / (sizes["big"] + 3 * sizes["s0"])) == 2
    assert sorted(q for part in parts for q in part) == sorted(questions)


def test_pack_puts_every_item_in_one_call_unless_they_do_not_fit(monkeypatch):
    def build(items):
        return {f"i{n}": {"type": "noul", "instructions": f"About {n}"} for n in items}, {"items": items}

    assert [items for items, _, _ in J.pack(list(range(40)), build)] == [list(range(40))]
    assert [len(items) for items, _, _ in J.pack(list(range(60)), build)] == [40, 20]  # the Gateway's 40 a call
    monkeypatch.setattr(J, "fits", lambda state, questions: len(questions) <= 25)
    assert [len(items) for items, _, _ in J.pack(list(range(60)), build)] == [25, 25, 10]
    monkeypatch.setattr(J, "fits", lambda state, questions: False)
    assert J.pack([1], build) is None


# ------------------------------------------------------------------------------------------- nightly learnings
def test_a_learning_with_39_possible_recipients_is_sent_once():
    """AC-2: its text is read once, with every recipient's question in the same call."""
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n} looks after area {n}."} for n in range(39)}
    item = {"audience": None, "source": "chat", "label": "Lesson", "text": "Deploys need a second pair of eyes."}
    jev = Jev()
    learnable, scores = L.score(jev, qset, item, team)
    assert len(jev.calls) == 1 and len(jev.calls[0]["questions"]) == 40
    assert sorted(scores) == sorted(team) and learnable is not None
    state = jev.calls[0]["state"]
    assert state["item"]["text"] == item["text"] and len(state["roster"]) == 39
    # Each recipient's score is its own question's answer.
    assert scores["bot-7"] == round(answer(state, jev.calls[0]["questions"]["rel_bot-7"])["noul"], 3)


def test_a_learning_with_80_possible_recipients_makes_3_calls_and_scores_every_recipient():
    """Past the Gateway's 40 questions a call: 3 calls of at most 40 (the first also asks `learnable`), the item's text
    once in each, every recipient asked once and scored."""
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n} looks after area {n}."} for n in range(80)}
    item = {"audience": None, "source": "chat", "label": "Lesson", "text": "Deploys need a second pair of eyes."}
    jev = Jev()
    learnable, scores = L.score(jev, qset, item, team)
    assert len(jev.calls) == 3 and all(len(call["questions"]) <= GATEWAY_MOST for call in jev.calls)
    assert [call["state"]["item"]["text"] for call in jev.calls] == [item["text"]] * 3
    asked = [qid for call in jev.calls for qid in call["questions"]]
    assert sorted(asked) == sorted(["learnable", *("rel_" + s for s in team)]), "each question once"
    assert sorted(scores) == sorted(team) and learnable is not None
    for call in jev.calls:  # each call's roster is the recipients it asks about
        assert sorted(call["state"]["roster"]) == sorted(q[4:] for q in call["questions"] if q != "learnable")
    last = jev.calls[-1]
    some = next(q[4:] for q in last["questions"] if q != "learnable")
    assert scores[some] == round(answer(last["state"], last["questions"]["rel_" + some])["noul"], 3)


@pytest.mark.parametrize(
    "recipients, calls", [(39, [40]), (40, [40, 1]), (79, [40, 40]), (80, [40, 40, 1]), (119, [40] * 3)]
)
def test_a_learning_makes_the_fewest_calls_of_40_with_learnable_asked_once(recipients, calls):
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n}."} for n in range(recipients)}
    item = {"audience": None, "source": "chat", "label": "Lesson", "text": "Ship on Tuesdays."}
    jev = Jev()
    _, scores = L.score(jev, qset, item, team)
    assert [len(call["questions"]) for call in jev.calls] == calls == [40] * (len(calls) - 1) + [calls[-1]]
    assert len(calls) == math.ceil((recipients + 1) / GATEWAY_MOST), "the fewest calls"
    assert [q for call in jev.calls for q in call["questions"]].count("learnable") == 1 and len(scores) == recipients


def test_a_learning_whose_recipients_do_not_fit_in_one_call_is_split_once_per_needed_call(monkeypatch):
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n}."} for n in range(80)}
    item = {"audience": None, "source": "chat", "label": "Lesson", "text": "Ship on Tuesdays."}
    monkeypatch.setattr(J, "fits", lambda state, questions: len(questions) <= 30)  # as if 30 fit by size
    jev = Jev()
    _, scores = L.score(jev, qset, item, team)
    assert [len(call["questions"]) for call in jev.calls] == [30, 30, 21]
    assert "learnable" in jev.calls[0]["questions"] and "learnable" not in jev.calls[1]["questions"]
    assert len(scores) == 80


def small_items(count):
    return [
        {"key": f"chat:{n}", "audience": None, "source": "chat", "label": f"Lesson {n}", "text": f"Lesson number {n}."}
        for n in range(count)
    ]


def scored_alone(qset, items, team):
    jev = Jev()
    return [L.score(jev, qset, dict(item), team) for item in items]


def test_ten_small_learnings_items_are_scored_in_one_call_each_with_all_its_recipients(monkeypatch):
    """AC-8 for nightly learnings: 10 small items for 3 bots (4 questions each) go in one call, and each item gets the
    learnable and relevance it gets alone."""
    monkeypatch.setattr(L, "GROUP", DG.MOST)
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n} looks after area {n}."} for n in range(3)}
    items = small_items(10)
    jev = FairJev()
    assert L.route(jev, qset, items, team) == {}
    assert len(jev.calls) == 1 and jev.calls[0]["items"] == 10 and len(jev.calls[0]["questions"]) == 40
    assert [(i["learnable"], i["scores"]) for i in items] == scored_alone(qset, small_items(10), team)
    assert not any(i["failed"] for i in items)


def test_learnings_items_ship_one_per_call_until_the_replay_check():
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n}."} for n in range(3)}
    jev = FairJev()
    L.route(jev, qset, small_items(4), team)
    assert L.GROUP == 1 and len(jev.calls) == 4 and "items" not in jev.calls[0]


def test_a_learnings_item_whose_answers_are_missing_is_asked_again_alone(monkeypatch):
    """AC-9: the item missing from the grouped answer is asked again on its own and never gets another's scores."""
    monkeypatch.setattr(L, "GROUP", DG.MOST)
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n}."} for n in range(3)}
    items = small_items(5)
    jev = FairJev(drop={"item3"})
    assert L.route(jev, qset, items, team) == {}
    assert [c.get("items") for c in jev.calls] == [5, None], "one grouped call, then item3 alone"
    assert jev.calls[1]["state"]["item"]["text"] == items[2]["text"]
    assert [(i["learnable"], i["scores"]) for i in items] == scored_alone(qset, small_items(5), team)


def test_a_failed_grouped_learnings_call_fails_each_of_its_items_as_one_failed_call_does(monkeypatch):
    """AC-10: a timeout on the grouped call skips each item, counted under its error, and the night goes on."""
    monkeypatch.setattr(L, "GROUP", DG.MOST)
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n}."} for n in range(3)}
    items = small_items(6)
    assert L.route(FairJev(fail=True), qset, items, team) == {"timeout": 6}
    assert all(i["failed"] and i["learnable"] is None and i["scores"] == {} for i in items)


def test_a_learnings_item_too_big_to_share_keeps_its_own_split_beside_grouped_small_ones(monkeypatch):
    """An item for 80 recipients needs 3 calls alone and keeps them; items for 3 recipients share one call."""
    monkeypatch.setattr(L, "GROUP", DG.MOST)
    qset = J.load_set(L.QUESTION_SET)
    team = {f"bot-{n}": {"profile": f"Bot {n}."} for n in range(80)}
    few = {f"bot-{n}" for n in range(3)}
    items = [{**item, "audience": few} for item in small_items(4)]
    items.insert(2, {**small_items(1)[0], "key": "big", "text": "For everyone."})
    jev = FairJev()
    assert L.route(jev, qset, items, team) == {}
    assert sorted(c.get("items") or 0 for c in jev.calls) == [0, 0, 0, 4]
    assert len(items[2]["scores"]) == 80 and all(len(i["scores"]) == 3 for i in items if i["key"] != "big")


# ------------------------------------------------------------------------------------------- automations
class JudgingServices(Services):
    """The automation fakes with a judge that answers each question from its content (`Jev`)."""

    def __init__(self):
        super().__init__()
        self.jev = Jev()

    def judge(self, values, questions, label):
        self.judged.append({"values": values, "questions": questions, "label": label})
        return {**self.jev(values, questions, label), "cost_usd": 0.01}


TRIAGE = {
    "bucket": {"type": "choice", "instructions": "Where does this go?", "criteria": {"reply": "", "archive": ""}},
    "urgent": {"type": "noul", "instructions": "Is it urgent?"},
}
TONE = {
    "angry": {"type": "noul", "instructions": "Is the sender angry?"},
    "urgent": {"type": "noul", "instructions": "Does it name a deadline?"},  # the same id as TRIAGE's, another question
}
MESSAGE = {"message": {"from": "ana@acme.example", "subject": "Contract", "text": "Please sign by Friday."}}


def flow(*steps):
    return {"name": "T", "owner": "person:ana", "trigger": {"type": "message"}, "steps": list(steps)}


def note(sid):
    return {"id": sid, "type": "action", "do": "note", "args": {"text": "hi"}}


def alone(questions):
    state = {"from": "ana@acme.example", "subject": "Contract", "text": "Please sign by Friday."}
    return J.check_answers({qid: answer(state, q) for qid, q in questions.items()}, questions)


def test_two_decide_steps_on_one_object_make_one_call_and_each_gets_its_own_answers():
    """AC-4: one call, and each step's answers are what it got when it asked alone."""
    services = JudgingServices()
    first = {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}
    second = {"id": "tone", "type": "decide", "questions": TONE, "out": "d2"}
    run = E.execute(flow(first, note("between"), second, note("after")), MESSAGE, services)
    assert run["status"] == "ok" and len(services.jev.calls) == 1
    assert sorted(services.jev.calls[0]["questions"]) == ["tone:angry", "tone:urgent", "triage:bucket", "triage:urgent"]
    records = {r["id"]: r for r in run["steps"]}
    assert records["triage"]["outputs"] == alone(TRIAGE) and records["tone"]["outputs"] == alone(TONE)
    assert records["triage"]["cost"] == 0.01 and records["tone"]["cost"] == 0 and run["cost"] == 0.01


def test_a_decide_step_on_another_cut_of_the_object_still_asks_on_its_own():
    services = JudgingServices()
    services.fields["only-subject@1"] = ["subject"]
    first = {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}
    other = {"id": "tone", "type": "decide", "set": "only-subject@1", "out": "d2"}  # another cut of the object
    E.execute(flow(first, other), MESSAGE, services)
    assert len(services.jev.calls) == 2


def test_merged_decide_steps_that_do_not_fit_in_one_call_ask_apart(monkeypatch):
    services = JudgingServices()
    monkeypatch.setattr(J, "fits", lambda state, questions: len(questions) <= 2)
    first = {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}
    second = {"id": "tone", "type": "decide", "questions": TONE, "out": "d2"}
    run = E.execute(flow(first, second), MESSAGE, services)
    assert [sorted(call["questions"]) for call in services.jev.calls] == [sorted(TRIAGE), sorted(TONE)]
    assert {r["id"]: r["outputs"] for r in run["steps"]} == {"triage": alone(TRIAGE), "tone": alone(TONE)}


def test_merged_decide_steps_past_the_gateways_40_questions_ask_apart():
    """25 + 20 questions on one object: merged they are 45, past what the Gateway answers, so each step asks alone."""
    services = JudgingServices()
    many, more = nouls(25, "Is this message about topic {n}?"), nouls(20, "Does it name person {n}?")
    first = {"id": "topics", "type": "decide", "questions": many, "out": "d1"}
    second = {"id": "people", "type": "decide", "questions": more, "out": "d2"}
    run = E.execute(flow(first, second), MESSAGE, services)
    assert run["status"] == "ok" and [len(call["questions"]) for call in services.jev.calls] == [25, 20]
    assert {r["id"]: r["outputs"] for r in run["steps"]} == {"topics": alone(many), "people": alone(more)}


def test_a_decide_step_edited_while_its_run_waited_asks_its_new_questions():
    """The answers an earlier step got for a later one stand only for the questions it asked."""
    services = JudgingServices()
    first = {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}
    llm = {"id": "draft", "type": "llm", "as": "assistant:ana", "prompt": "Draft", "out": "reply"}
    second = {"id": "tone", "type": "decide", "questions": TONE, "out": "d2"}
    run = E.execute(flow(first, llm, second), MESSAGE, services)
    assert run["status"] == "waiting" and len(services.jev.calls) == 1
    edited = {"angry": TONE["angry"]}
    assert E.answer(run, "msg_1", "Draft text.")
    run = E.advance(flow(first, llm, {**second, "questions": edited}), run, services)
    assert run["status"] == "ok" and len(services.jev.calls) == 2
    assert services.jev.calls[1]["questions"] == edited and run["steps"][-1]["outputs"] == alone(edited)


# ------------------------------------------------------------------------------------------- Decide after a Find
class GroupingServices(JudgingServices):
    """JudgingServices that also asks several find items at once, as the hub does (`judge_many`)."""

    def __init__(self, jev=None, items=()):
        super().__init__()
        self.jev, self.items, self.off = jev or FairJev(), list(items), False

    def judge_many(self, items, label):
        if self.off:
            return None
        replies, _calls = DG.ask(lambda s, q, lb: {**self.jev(s, q, lb), "cost_usd": 0.01}, items, label, E.GROUP)
        return replies

    def judge(self, values, questions, label):
        return None if self.off else super().judge(values, questions, label)


FOUND = [{"id": f"t{n}", "title": f"Task {n}", "status": "open", "body": f"Body {n}"} for n in range(10)]
FIND = {"id": "find", "type": "find", "source": "tasks", "query": "", "limit": 100}


def item_alone(item, questions):
    return J.check_answers({qid: answer(dict(item), q) for qid, q in questions.items()}, questions)


def test_a_decide_step_after_a_find_asks_ten_items_in_one_call_each_its_own_answers(monkeypatch):
    """AC-8 for the Decide step after a Find: 10 small items, one call, each item's answers what it gets alone."""
    monkeypatch.setattr(E, "GROUP", DG.MOST)
    services = GroupingServices(items=FOUND)
    step = {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}
    run = E.execute(flow(FIND, step, note("after")), {}, services)
    assert run["status"] == "ok" and len(services.jev.calls) == 1 and services.jev.calls[0]["items"] == 10
    decided = [r for r in run["steps"] if r["id"] == "triage"]
    assert [r["outputs"] for r in decided] == [item_alone(item, TRIAGE) for item in FOUND]
    assert all(r["status"] == "ok" for r in decided) and round(run["cost"], 6) == 0.01
    assert len([r for r in run["steps"] if r["id"] == "after"]) == 10


def test_a_decide_step_after_a_find_ships_one_item_per_call_until_the_replay_check():
    services = GroupingServices(items=FOUND[:3])
    E.execute(flow(FIND, {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}), {}, services)
    assert E.GROUP == 1 and len(services.jev.calls) == 3 and "items" not in services.jev.calls[0]


def test_two_decide_steps_after_a_find_ask_every_item_and_both_steps_in_one_call(monkeypatch):
    """AC-4 and AC-8 together: both steps' questions for all 5 items in one call; each step gets its own answers."""
    monkeypatch.setattr(E, "GROUP", DG.MOST)
    services = GroupingServices(items=FOUND[:5])
    first = {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}
    second = {"id": "tone", "type": "decide", "questions": TONE, "out": "d2"}
    run = E.execute(flow(FIND, first, second), {}, services)
    assert run["status"] == "ok" and len(services.jev.calls) == 1 and len(services.jev.calls[0]["questions"]) == 20
    for item, (one, two) in zip(FOUND, zip(*[[r for r in run["steps"] if r["id"] == s] for s in ("triage", "tone")])):
        merged = item_alone(
            item, {f"{sid}:{qid}": q for sid, qs in (("triage", TRIAGE), ("tone", TONE)) for qid, q in qs.items()}
        )
        assert one["outputs"] == {qid: merged[f"triage:{qid}"] for qid in TRIAGE}
        assert two["outputs"] == {qid: merged[f"tone:{qid}"] for qid in TONE}


def test_a_find_item_missing_from_the_grouped_answer_is_asked_again_alone(monkeypatch):
    """AC-9: item3 is asked again on its own and gets its own answers, never another item's."""
    monkeypatch.setattr(E, "GROUP", DG.MOST)
    services = GroupingServices(jev=FairJev(drop={"item3"}), items=FOUND[:5])
    run = E.execute(flow(FIND, {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}), {}, services)
    assert [c.get("items") for c in services.jev.calls] == [5, None] and services.jev.calls[1]["state"] == FOUND[2]
    assert [r["outputs"] for r in run["steps"] if r["id"] == "triage"] == [item_alone(i, TRIAGE) for i in FOUND[:5]]


def test_a_failed_grouped_decide_call_fails_each_find_item_as_its_own_failed_call_does(monkeypatch):
    """AC-10: a timeout fails each item's Decide step with the call's error; the next steps do not run."""
    monkeypatch.setattr(E, "GROUP", DG.MOST)
    services = GroupingServices(jev=FairJev(fail=True), items=FOUND[:4])
    run = E.execute(
        flow(FIND, {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}, note("n")), {}, services
    )
    decided = [r for r in run["steps"] if r["id"] == "triage"]
    assert run["status"] == "failed" and [r["status"] for r in decided] == ["failed"] * 4
    assert len({r["outputs"]["error"] for r in decided}) == 1 and not [r for r in run["steps"] if r["id"] == "n"]


def test_decide_off_skips_each_find_item_when_asked_together(monkeypatch):
    monkeypatch.setattr(E, "GROUP", DG.MOST)
    services = GroupingServices(items=FOUND[:3])
    services.off = True
    run = E.execute(flow(FIND, {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}), {}, services)
    assert [r["status"] for r in run["steps"] if r["id"] == "triage"] == ["skipped"] * 3


def test_a_find_item_whose_step_changed_after_the_grouped_call_asks_again(monkeypatch):
    """An answer kept for a later item stands only for what it was asked: a run resumed on an edited flow asks again."""
    monkeypatch.setattr(E, "GROUP", DG.MOST)
    services = GroupingServices(items=FOUND[:2])
    services.on_hold = lambda: len(services.jev.calls) >= 1 and not getattr(services, "resumed", False)
    step = {"id": "triage", "type": "decide", "questions": TRIAGE, "out": "d1"}
    run = E.execute(flow(FIND, step), {}, services)
    assert run["status"] == "running" and len(services.jev.calls) == 1
    services.resumed = True
    edited = {"urgent": TRIAGE["urgent"]}
    run = E.advance(flow(FIND, {**step, "questions": edited}), run, services)
    assert run["status"] == "ok" and len(services.jev.calls) == 2
    assert [r["outputs"] for r in run["steps"] if r["id"] == "triage"][-1] == item_alone(FOUND[1], edited)


# ------------------------------------------------------------------------------------------- the bots' mail tool
MAILS = [
    {"id": f"m{n}", "from": f"sender{n}@acme.example", "subject": f"Subject {n}", "snippet": "Hello", "to": []}
    for n in range(3)
]


def test_the_mail_tool_asks_every_set_about_a_message_in_one_call(tmp_path):
    jev = Jev()
    msgs = [dict(m) for m in MAILS]
    stats = jd.judge_sets(msgs, ["mail-triage", "reply-intent"], jev, "ana@acme.example")
    assert len(jev.calls) == 3, "one call per message, not one per message and set"
    assert {call["label"] for call in jev.calls} == {"mail-triage@1+reply-intent@1"}
    for m in msgs:
        state = jd.message_state(m, "ana@acme.example")
        for sid in ("mail-triage", "reply-intent"):
            questions = J.load_set(sid)["questions"]
            want = J.check_answers({qid: answer(state, q) for qid, q in questions.items()}, questions)
            assert m["judgments"][sid] == J.summary(want)
    assert {sid: (s["count"], s["errors"]) for sid, s in stats.items()} == {
        "mail-triage": (3, 0),
        "reply-intent": (3, 0),
    }


def test_the_mail_tool_asks_only_the_sets_not_cached_and_a_failed_call_fails_each_set(tmp_path):
    from connectors.mail import db

    conn = db.connect(tmp_path / "mail.db")
    jev = Jev()
    jd.judge_sets([dict(m) for m in MAILS], ["mail-triage"], jev, "box", conn)
    assert len(jev.calls) == 3
    jev.calls.clear()
    msgs = [dict(m) for m in MAILS]
    stats = jd.judge_sets(msgs, ["mail-triage", "reply-intent"], jev, "box", conn)
    assert [sorted(call["questions"]) for call in jev.calls] == [["intent"]] * 3
    assert stats["mail-triage"]["cached"] == 3 and all(
        set(m["judgments"]) == {"mail-triage", "reply-intent"} for m in msgs
    )

    def down(state, questions, label=None):
        raise J.JudgeError("unavailable", "down", retryable=True)

    msgs = [dict(MAILS[0], id="new")]
    stats = jd.judge_sets(msgs, ["mail-triage", "reply-intent"], down, "other-box", None)
    assert msgs[0]["judge_errors"] == {"mail-triage": "unavailable", "reply-intent": "unavailable"}
    assert stats["mail-triage"]["errors"] == stats["reply-intent"]["errors"] == 1
