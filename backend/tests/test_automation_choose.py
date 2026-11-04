"""An llm step's `choose` (backend/automation_engine.py `_choose_turn`, `read_choice`, `_answered`, `_resume`): the turn's
strict answer picks a destination and a body, the body goes out as the reply (a draft, or a sent reply) and then the
destination's chain runs. A malformed answer, a turn that did not finish, the spend cap or a held webhook runs the
default destination with no reply. Each effect is keyed by the step's record, so a resumed run repeats none of them.
Fake services, as in test_automation_steps.py."""
import json

import pytest

from backend import automation_engine as E
from backend.tests.automation_fakes import Services

MESSAGE = {"message": {"id": "m1", "mailbox": "ana@acme.example", "from": "ceo@acme.example",
                       "subject": "Board pack", "body": "Can you send the deck?"}}
DESTS = [{"id": "legal", "name": "Legal", "then": [{"do": "create_task", "args": {"title": "Review"}}]},
         {"id": "sales", "name": "Sales", "then": [{"do": "label", "args": {"name": "Sales"}}]},
         {"id": "other", "name": "Other", "then": []}]


def flow(*steps, **extra):
    return {"name": "Test", "owner": "person:ana", "trigger": {"type": "message", "inbox": "ana@acme.example"},
            "steps": list(steps), **extra}


def llm(choose=None):
    step = {"id": "draft", "type": "llm", "enabled": True, "as": "assistant:ana",
            "prompt": "Answer {{message.from}} about {{message.subject}}.", "out": "draft"}
    return step if choose is None else {**step, "choose": choose}


def choosing(to=("legal", "sales"), default="sales", mode="draft"):
    return {"to": list(to), "default": default, "mode": mode}


def note(sid, text="hi"):
    return {"id": sid, "type": "action", "enabled": True, "do": "note", "args": {"text": text}}


def statuses(run):
    return [(r["id"], r["status"]) for r in run["steps"]]


def run_choose(text, *, ok=True, services=None, mode="draft", to=("legal", "sales"), default="sales", **extra):
    """The flow run to its turn (the first turn is `msg_1`), the turn's answer read, and the run resumed to its end."""
    services = services or Services()
    f = flow(llm(choosing(to=to, default=default, mode=mode)), destinations=DESTS, **extra)
    run = E.execute(f, MESSAGE, services)
    assert run["status"] == "waiting", run
    E.answer(run, "msg_1", text, cost=0.02, ok=ok)
    return E.advance(f, run, services), services


# ---------------------------------------------------------------------------------------------- the good answer
def test_a_good_answer_drafts_the_reply_and_then_runs_the_chosen_chain():
    run, services = run_choose(json.dumps({"to": "legal", "body": "Sending the deck today."}))
    assert services.acted == [("draft_reply", {"body": "Sending the deck today."}), ("create_task", {"title": "Review"})]
    # The reply is keyed by the step's record (0), the chain's effects follow it.
    assert services.keys == ["0.0", "0.1"]
    assert services.producers == ["draft@0.0", "draft@0.1"]
    record = run["steps"][0]
    assert record["status"] == "ok" and record["outputs"]["to"] == "legal" and record["cost"] == 0.02
    assert [(a["do"], a["from"]) for a in record["outputs"]["actions"]] == [
        ("draft_reply", "reply"), ("create_task", "destination")]
    assert run["status"] == "ok" and run["threads"][0]["state"] == "done"


def test_reply_mode_sends_the_reply_as_send_reply():
    run, services = run_choose(json.dumps({"to": "sales", "body": "Thanks, here it is."}), mode="reply")
    assert services.acted == [("send_reply", {"body": "Thanks, here it is."}), ("label", {"name": "Sales"})]
    assert run["status"] == "ok"


def test_the_body_goes_out_as_written_and_is_never_templated():
    body = "Re {{message.subject}}: {{message.body}} {{trigger.secret}} {% raw %}"
    run, services = run_choose(json.dumps({"to": "legal", "body": body}))
    assert services.acted[0] == ("draft_reply", {"body": body})


def test_a_blank_body_sends_no_reply_and_the_chosen_chain_runs_alone():
    run, services = run_choose(json.dumps({"to": "legal", "body": "   "}))
    assert services.acted == [("create_task", {"title": "Review"})]
    assert [a["from"] for a in run["steps"][0]["outputs"]["actions"]] == ["destination"]
    assert run["status"] == "ok"


def test_a_refused_reply_fails_the_step_and_skips_its_chain():
    run, services = run_choose(json.dumps({"to": "legal", "body": "Sending."}), services=Services(fail={"draft_reply"}))
    assert services.acted == [("draft_reply", {"body": "Sending."})]
    assert statuses(run) == [("draft", "failed")] and run["status"] == "failed"


# ---------------------------------------------------------------------------------------------- the fallbacks
BAD = [
    "Sure, legal would be best.",                              # not JSON
    '```json\n{"to": "legal", "body": "x"}\n```',              # fenced: the answer must be the object alone
    json.dumps({"to": "legal", "body": "x", "cc": "boss"}),    # an extra key
    json.dumps({"to": "legal"}),                                # no body
    json.dumps(["legal", "x"]),                                 # not an object
    json.dumps({"to": "other", "body": "x"}),                   # a destination of the flow, not one it may choose
    json.dumps({"to": "Legal", "body": "x"}),                   # a name, not an id
    json.dumps({"to": "legal", "body": "x" * 8001}),            # too long
    json.dumps({"to": "legal", "body": 3}),                     # a body that is not text
]


@pytest.mark.parametrize("text", BAD)
def test_a_malformed_answer_runs_the_default_chain_with_no_reply(text):
    run, services = run_choose(text)
    assert services.acted == [("label", {"name": "Sales"})]
    record = run["steps"][0]
    assert record["status"] == "ok" and record["outputs"]["to"] == "sales"
    assert record["outputs"]["error"]
    assert run["status"] == "ok" and run["threads"][0]["state"] == "done"


def test_a_turn_that_did_not_finish_runs_the_default_chain():
    run, services = run_choose("the bot stopped", ok=False)
    assert services.acted == [("label", {"name": "Sales"})]
    assert run["steps"][0]["outputs"]["error"] == "the bot stopped"


def test_the_spend_cap_runs_the_default_chain_with_no_turn():
    services = Services(spent=2.0)
    f = flow(llm(choosing()), destinations=DESTS, spend_cap={"per_30m": 1.0})
    run = E.execute(f, MESSAGE, services)
    assert services.turns == [] and services.acted == [("label", {"name": "Sales"})]
    out = run["steps"][0]["outputs"]
    assert out["skipped"] == "spend_cap" and out["to"] == "sales"
    assert [a["from"] for a in out["actions"]] == ["destination"]
    assert run["status"] == "ok"


def test_a_held_webhook_runs_the_default_chain_with_no_turn():
    services = Services(fail={"held"})
    f = flow(llm(choosing()), destinations=DESTS)
    run = E.execute(f, MESSAGE, services)
    assert services.acted == [("label", {"name": "Sales"})] and run["status"] == "ok"
    assert run["steps"][0]["outputs"]["skipped"] == "held" and run["steps"][0]["outputs"]["ref"] == "held_1"


# ---------------------------------------------------------------------------------------------- resume after a crash
class Crash(BaseException):
    """A crash between two effects: not an Exception, so the engine does not record it as the step's failure."""


class Memo(Services):
    """Services that keep each effect's answer by its key, as the hub does (`_once`), and crash on one action once."""

    def __init__(self, crash_on=None, **kw):
        super().__init__(**kw)
        self.memo, self.crash_on = {}, crash_on

    def act(self, do, args, ctx):
        if ctx["key"] not in self.memo:
            if do == self.crash_on:
                self.crash_on = None
                raise Crash(do)
            self.memo[ctx["key"]] = super().act(do, args, ctx)
        return self.memo[ctx["key"]]


def test_a_crash_after_the_draft_then_resume_repeats_no_effect():
    services = Memo(crash_on="create_task")
    f = flow(llm(choosing()), destinations=DESTS)
    run = E.execute(f, MESSAGE, services)
    E.answer(run, "msg_1", json.dumps({"to": "legal", "body": "Sending."}))
    saved = json.loads(json.dumps(run))   # what the hub saves when the answer comes in
    with pytest.raises(Crash):
        E.advance(f, run, services)       # the draft is out; the chain's create_task crashes
    resumed = E.advance(f, saved, services)
    assert services.acted == [("draft_reply", {"body": "Sending."}), ("create_task", {"title": "Review"})]
    assert services.keys == ["0.0", "0.1"]
    assert resumed["status"] == "ok"


# ---------------------------------------------------------------------------------------------- legacy llm
def test_an_llm_step_without_choose_keeps_its_outputs_and_runs_on():
    services = Services()
    f = flow(llm(), note("after"))
    run = E.execute(f, MESSAGE, services)
    assert run["status"] == "waiting"
    E.answer(run, "msg_1", "Thanks, noted.")
    run = E.advance(f, run, services)
    assert run["steps"][0]["outputs"] == {"ref": "msg_1", "text": "Thanks, noted."}
    assert services.acted == [("note", {"text": "hi"})] and run["status"] == "ok"


# ---------------------------------------------------------------------------------------------- a brief a choice reads
def test_a_bot_turn_a_chosen_destination_reads_waits_for_it_and_that_destination_gets_its_text():
    """`_reads` counts a choose step's destinations: the brief's turn waits, and the destination's note gets what the bot
    wrote, while the reply is drafted from the turn's own answer."""
    brief = {"id": "brief", "type": "bot_turn", "bot": "librarian", "prompt": "Write the briefing."}
    dests = [{"id": "legal", "name": "Legal", "then": [{"do": "note", "args": {"text": "Brief: {{brief.text}}"}}]},
             {"id": "sales", "name": "Sales", "then": []}]
    services = Services()
    f = flow(brief, llm(choosing()), destinations=dests)
    run = E.execute(f, MESSAGE, services)
    assert services.turns[0]["wait"] is True and statuses(run) == [("brief", "waiting")]
    assert E.answer(run, "msg_1", "Northwind cut its price.")
    run = E.advance(f, run, services)
    assert statuses(run) == [("brief", "ok"), ("draft", "waiting")]
    assert E.answer(run, "msg_2", json.dumps({"to": "legal", "body": "Sending."}), cost=0.02)
    run = E.advance(f, run, services)
    assert services.acted == [("draft_reply", {"body": "Sending."}),
                              ("note", {"text": "Brief: Northwind cut its price."})]
    assert run["status"] == "ok"


# ---------------------------------------------------------------------------------------------- the owner at run time
def test_a_choose_turn_runs_as_the_owner_it_was_saved_for():
    step = {**llm(choosing()), "as": "bot:ops"}
    services = Services()
    run = E.execute({**flow(step, destinations=DESTS), "owner": "bot:ops"}, MESSAGE, services)
    assert run["status"] == "waiting" and services.turns[0]["who"] == "bot:ops"


@pytest.mark.parametrize("owner, who", [("bot:other", "bot:ops"), ("person:bob", "assistant:ana"),
                                       ("group:legal-team", "assistant:ana")])
def test_a_queued_choose_run_takes_its_owner_as_it_is_now_and_starts_no_turn_as_the_old_one(owner, who):
    step = {**llm(choosing()), "as": who}
    services = Services()
    run = E.execute({**flow(step, destinations=DESTS), "owner": owner}, MESSAGE, services)
    assert services.turns == [] and services.acted == [("label", {"name": "Sales"})]
    out = run["steps"][0]["outputs"]
    assert out["skipped"] == "owner changed" and out["error"] and out["to"] == "sales"
    assert run["status"] == "ok" and run["threads"][0]["state"] == "done"


@pytest.mark.parametrize("owner", ["group:legal-team", "bot:other"])
def test_a_waiting_choose_run_whose_owner_changed_sends_no_reply_and_runs_only_the_default(owner):
    step = {**llm(choosing()), "as": "bot:ops"}
    f = {**flow(step, destinations=DESTS), "owner": "bot:ops"}
    services = Services()
    run = E.execute(f, MESSAGE, services)
    assert run["status"] == "waiting" and services.turns[0]["who"] == "bot:ops"
    assert E.answer(run, "msg_1", json.dumps({"to": "legal", "body": "Sending the deck today."}))
    run = E.advance({**f, "owner": owner}, run, services)
    assert services.acted == [("label", {"name": "Sales"})]
    out = run["steps"][0]["outputs"]
    assert out["skipped"] == "owner changed" and out["error"] and out["to"] == "sales"
    assert run["status"] == "ok" and run["threads"][0]["state"] == "done"
