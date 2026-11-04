"""Email choose steps use one structured call; bad output cannot send or discard mail."""
import io
import json

import pytest

from clients import judge as J
from backend import automation_engine as E, automation_triage as T, automations
from backend.tests.automation_fakes import Services
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_automation_api import patch
from backend.tests.test_automation_runs import queued, hub, run_row, sql

MAIL = {"message": {"source": "mail", "mailbox": "ana@acme.example", "id": "m1", "thread": "t1",
                    "from": "sender@acme.example", "subject": "Hello", "body": "Please send the deck."}}


def flow(mode="draft", model=None):
    step = {"id": "draft", "type": "llm", "as": "assistant:ana", "prompt": "Route and answer the email.",
            "out": "draft", "choose": {"to": ["me", "archive", "team"], "default": "archive", "mode": mode}}
    if model:
        step["model"] = model
    return {"name": "Inbox", "owner": "person:ana", "trigger": {"type": "message", "inbox": "ana@acme.example"},
            "steps": [step], "destinations": [
                {"id": "me", "name": "Me", "then": [{"do": "leave_for_you", "args": {}}]},
                {"id": "archive", "name": "Archive", "then": [{"do": "archive", "args": {}}]},
                {"id": "team", "name": "Team", "then": [{"do": "create_task", "args": {
                    "title": "Follow up", "owner": "bot:ops", "body": "{{message.body}}"}}]}]}


class Calls:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.calls = []
        self.model = T.DEFAULT_MODEL

    def __call__(self, system, user, schema):
        self.calls.append((system, json.loads(user), schema))
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return {"answer": answer, "model": self.model, "usage": {"input_tokens": 100, "output_tokens": 20},
                "cost_usd": 0.001}


class Direct(Services):
    def __init__(self, f, calls):
        super().__init__()
        self.flow, self.calls = f, calls

    def triage(self, step, values, ctx):
        return T.run(self.flow, step, values, self.calls)


def execute(*answers, mode="draft"):
    f, calls = flow(mode), Calls(*answers)
    services = Direct(f, calls)
    return E.execute(f, MAIL, services), services, calls


def test_one_call_drafts_then_routes_without_a_turn():
    run, services, calls = execute({"to": "me", "body": "Here is the deck. {{literal}}"})
    assert len(calls.calls) == 1 and not services.turns
    assert services.acted == [("draft_reply", {"body": "Here is the deck. {{literal}}"}), ("leave_for_you", {})]
    assert calls.calls[0][1]["message"] == MAIL["message"]
    assert [d["id"] for d in calls.calls[0][1]["destinations"]] == ["me", "archive", "team"]
    assert calls.calls[0][2]["additionalProperties"] is False
    assert run["status"] == "ok" and run["cost"] == 0.001
    assert set(run["steps"][0]["outputs"]) == {"to", "text", "model", "actions"}


def test_invalid_answer_retries_once_and_accepts_valid_retry():
    run, services, calls = execute({"to": "elsewhere", "body": "Send me!"}, {"to": "team", "body": ""})
    assert len(calls.calls) == 2 and not services.turns
    assert services.acted == [("create_task", {"title": "Follow up", "owner": "bot:ops", "body": MAIL["message"]["body"]})]
    assert run["cost"] == 0.002


@pytest.mark.parametrize("answer", [
    {"to": "me"}, {"to": "archive", "body": 42}, {"to": "archive", "body": "x" * 8001},
    {"to": "me", "body": "", "actions": [{"do": "send_reply"}]}, ["me", "reply"],
])
def test_invalid_answers_leave_for_me_with_reason_even_with_archive_default(answer):
    run, services, calls = execute(answer, answer)
    assert len(calls.calls) == 2
    assert [do for do, _ in services.acted] == ["leave_for_you"]
    assert services.acted[0][1]["reason"]
    assert run["steps"][0]["outputs"]["to"] == "me"
    assert run["steps"][0]["outputs"]["error"]
    assert run["status"] == "ok" and not services.turns


def test_bad_json_retries_once_but_provider_failure_does_not_retry():
    calls = Calls(J.JudgeError("shape", "bad json"), J.JudgeError("shape", "bad json"))
    f = flow()
    result = T.run(f, f["steps"][0], MAIL, calls)
    assert result["fallback"] and len(calls.calls) == 2
    calls = Calls(J.JudgeError("unavailable", "private provider error"))
    result = T.run(f, f["steps"][0], MAIL, calls)
    assert result["fallback"] and len(calls.calls) == 1 and "private" not in result["error"]


def test_explicit_reply_and_sort_keep_their_modes():
    run, services, _ = execute({"to": "me", "body": "Reply."}, mode="reply")
    assert services.acted[0][0] == "send_reply"
    run, services, _ = execute({"to": "me", "body": ""}, mode="sort")
    assert services.acted == [("leave_for_you", {})]
    run, services, calls = execute(*[{"to": "archive", "body": "Unexpected reply"}] * 2, mode="sort")
    assert len(calls.calls) == 2 and [do for do, _ in services.acted] == ["leave_for_you"]


@pytest.mark.parametrize("provider,model", [("anthropic", T.DEFAULT_MODEL), ("openai", "gpt-6-luna"),
                                           ("google", "gemini-3.1-pro-preview"), ("openrouter", "catalog/model")])
def test_transport_requests_schema_without_tools_or_sessions(provider, model):
    sent = []
    answer = json.dumps({"to": "me", "body": "Hello"})
    payload = ({"content": [{"type": "text", "text": answer}]} if provider == "anthropic" else
               {"candidates": [{"content": {"parts": [{"text": answer}]}}]} if provider == "google" else
               {"choices": [{"message": {"content": answer}}]})
    def opener(request, timeout):
        sent.append(json.loads(request.data))
        return io.BytesIO(json.dumps(payload).encode())
    schema = T.schema(flow()["steps"][0]["choose"])
    result = J.structured_writer(provider, "synthetic-key", model, opener=opener)("route", "email", schema)
    assert result["answer"] == {"to": "me", "body": "Hello"} and len(sent) == 1
    body = sent[0]
    assert not {"tools", "session", "files", "conversation", "previous_response_id"} & set(body)
    given = (body["output_config"]["format"]["schema"] if provider == "anthropic" else
             body["generationConfig"]["responseJsonSchema"] if provider == "google" else
             body["response_format"]["json_schema"]["schema"])
    assert given == schema


def test_transport_keeps_paid_usage_for_bad_json():
    payload = {"content": [{"text": "not JSON"}], "usage": {"input_tokens": 100, "output_tokens": 5}}
    write = J.structured_writer("anthropic", "synthetic", T.DEFAULT_MODEL,
                               opener=lambda *a, **kw: io.BytesIO(json.dumps(payload).encode()))
    with pytest.raises(J.JudgeError) as failed:
        write("route", "message", T.schema(flow()["steps"][0]["choose"]))
    assert failed.value.code == "shape" and failed.value.paid[0]["usage"]["input_tokens"] == 100


def test_voice_note_only_contains_owner_explicit_voice_lines(api):
    with api.app.state.store.transaction() as c:
        entries = [("ana", "Tone: warm and concise"), ("ana", "Sign-off: Thanks, Ana"),
                   ("ana", "Private financial detail"), ("ben", "Tone: another person's secret")]
        for i, (person, text) in enumerate(entries):
            c.execute("INSERT INTO learned_entries(id,layer,person,text,created_at,created_by) VALUES(?,?,?,?,?,?)",
                      (f"voice_{i}", "personal", f"human:{person}", text, f"2026-11-01T00:00:0{i}Z", f"human:{person}"))
        voice = T.voice_note(c, "person:ana")
        assert "warm and concise" in voice and "Thanks, Ana" in voice
        assert "secret" not in voice and "financial" not in voice and len(voice) <= T.VOICE_MAX
        assert T.voice_note(c, "bot:ops") == "" and T.voice_note(c, "person:nobody") == ""
    f = flow()
    calls = Calls({"to": "me", "body": "Thanks"})
    T.run(f, f["steps"][0], MAIL, calls, voice)
    assert calls.calls[0][1]["voice_note"] == voice
    f = flow("sort")
    assert "voice_note" not in json.loads(T.prompts(f, f["steps"][0], MAIL, voice)[1])


def test_new_default_and_existing_explicit_modes_survive_save(api):
    f = flow()
    del f["steps"][0]["choose"]["mode"]
    made = post(api, "automations", f)
    assert made["steps"][0]["choose"]["mode"] == "draft"
    for mode in ("sort", "reply"):
        made = post(api, "automations", flow(mode))
        changed = patch(api, made["id"], {"name": "Renamed"})
        assert changed["steps"][0]["choose"]["mode"] == mode
    bad = flow(model="not-a-model")
    post(api, "automations", bad, expected=422)


def test_hub_model_override_one_call_no_tasks_and_cached_result(api, monkeypatch):
    chosen, calls = [], Calls({"to": "me", "body": "A draft"})
    def writer(model):
        chosen.append(model)
        calls.model = model
        return calls
    monkeypatch.setattr(T, "writer", writer)
    f = flow(model="claude-sonnet-5-5")
    aid = post(api, "automations", f)["id"]
    rid = queued(api, aid, MAIL)
    before = sql(api, "SELECT id FROM tasks")
    hub(api).drain()
    assert chosen == ["claude-sonnet-5-5"] and len(calls.calls) == 1
    assert sql(api, "SELECT id FROM tasks") == before
    actions = sql(api, "SELECT action FROM automation_mail_actions WHERE run_id=? ORDER BY rowid", (rid,))
    assert [a["action"] for a in actions] == ["draft", "leave"]
    row = sql(api, "SELECT * FROM automations WHERE id=?", (aid,))[0]
    service = automations.HubServices(hub(api), row, f, rid, [])
    result = service.triage(f["steps"][0], MAIL, {"key": "0", "cost": 0})
    assert result["body"] == "A draft" and len(calls.calls) == 1
    assert not sql(api, "SELECT * FROM automation_holds WHERE run_id=?", (rid,))


def test_hub_uses_haiku_and_does_not_call_at_spend_cap(api, monkeypatch):
    chosen, calls = [], Calls({"to": "me", "body": ""})
    def writer(model):
        chosen.append(model)
        return calls
    monkeypatch.setattr(T, "writer", writer)
    f = flow()
    aid = post(api, "automations", f)["id"]
    queued(api, aid, MAIL)
    hub(api).drain()
    assert chosen == [T.DEFAULT_MODEL]
    f["spend_cap"] = {"per_30m": 0}
    aid = post(api, "automations", f)["id"]
    rid = queued(api, aid, MAIL)
    hub(api).drain()
    assert len(chosen) == 1
    assert "cap" in run_row(api, rid)["state"]["steps"][0]["outputs"]["error"]
    assert sql(api, "SELECT action FROM automation_mail_actions WHERE run_id=?", (rid,)) == [{"action": "leave"}]
