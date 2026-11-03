"""The person's own Assistant acts as them on any write they could make, citing the message that started its turn
(backend/assistant.py `admits_on_turn`, backend/app.py request_guard). The NEVER list, the 30-minute window, the member's
ceiling, the owner's switch, and approvals that only the person decides stay as they were."""

import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import assistant
from clients import hubtools
from backend import hubdb as H
from backend.tests.test_api import api, as_member, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.test_assistant import assistant_turn, say
from backend.tests.test_automation_access import flow
from backend.store import Problem
from backend.tests.test_batch import approval_for_ana


def set_rule(api, **rules):
    assert api.put("/api/v2/access/rules", json=rules, headers=headers()).status_code == 200


def test_the_turn_closes_a_task_the_person_owns_as_them_on_the_cite(api):
    """A task a bot asked the person to do: the person's Assistant settles it on their own cited words."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Send the invoice", "Monday.", "human:ben")["id"]
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    current = get(api, "tasks/" + task, token)
    current = current.get("task", current)
    closed = post(api, "tasks/" + task, {"version": current["version"], "status": "done"}, token=token)
    assert closed["status"] == "done"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT via FROM task_events WHERE task_id=? AND field='status' ORDER BY created_at DESC LIMIT 1",
                         (task,)).fetchone()[0] == "assistant"


def test_a_bulk_change_on_the_turn_records_each_task_as_the_person_via_assistant(api):
    with api.app.state.store.transaction() as c:
        ids = [H.task_create(c, "bot:ops", f"Follow up {n}", "Pending.", "human:ana")["id"] for n in range(2)]
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    done = post(api, "task-bulk", {"filter": {"owner": "human:ana", "status": "open"}, "action": "close"}, token=token)
    assert done["changed"] >= 2, done
    with api.app.state.store.read() as c:
        latest = {}                                  # newest first: keep the first row seen for each task
        for task_id, via in c.execute("SELECT task_id, via FROM task_events WHERE task_id IN (?,?) AND field='status' "
                                      "ORDER BY created_at DESC", ids).fetchall():
            latest.setdefault(task_id, via)
    assert latest == {ids[0]: "assistant", ids[1]: "assistant"}, latest


def test_an_automation_edit_and_a_team_rule_run_on_the_turn_as_the_person(api):
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    renamed = api.patch(f"/api/v2/automations/{aid}", json={"name": "Weekly digest"}, headers=headers(token))
    assert renamed.status_code == 200, renamed.text
    rule = {"kind": "external", "text": "Keep a consent record for five years.", "who": {"bots": ["ops"]}}
    # A rule needs its source and quote: the route's own check decides, so a refusal is never the gate's card.
    refused = api.post("/api/v2/rules", json=rule, headers=headers(token))
    assert refused.status_code == 200 or refused.json()["error"]["code"] != "confirm_required", refused.text


def test_the_never_list_stays_the_persons_own_tap(api):
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    never = [
        ("POST", "/api/v2/me/tokens", {"name": "Chat token"}),
        ("POST", "/api/v2/service-keys", {"name": "Key"}),
        ("POST", "/api/v2/enrollments", {}),
        ("POST", "/api/v2/access/owner", {"person": "human:ben"}),
        ("POST", "/api/v2/sql", {"query": "SELECT body FROM messages"}),
        ("POST", "/api/v2/support/tickets", {"subject": "Help", "body": "Please"}),
        ("DELETE", "/api/v2/people/ben", None),
        ("DELETE", "/api/v2/company/server-connection", None),
        ("PUT", "/api/v2/assistant/settings", {"enabled": False}),
    ]
    for method, path, body in never:
        res = api.request(method, path, json=body, headers=headers(token))
        assert res.status_code == 403, (method, path, res.text)
        assert res.json()["error"]["code"] == "confirm_required", (method, path)


def test_a_write_that_carries_a_secret_is_never_admitted_from_a_chat(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Send the invoice", "Monday.", "human:ben")["id"]
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    # A status change is gated; a body that names a secret key is never admitted from a chat, whatever the words.
    leaked = {"status": "done", "version": get(api, "tasks/" + task, token).get("task", {}).get("version", 1),
              "api_key": "not-for-a-chat"}
    res = api.post("/api/v2/tasks/" + task, json=leaked, headers=headers(token))
    assert res.status_code == 403 and res.json()["error"]["code"] == "confirm_required", res.text


def test_a_message_older_than_thirty_minutes_no_longer_lends_its_words(api):
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=(SELECT message_id FROM jobs ORDER BY created_at DESC LIMIT 1)",
                  (H.shift(H.now(), seconds=-(assistant.INSTRUCTION_TTL_S + 60)),))
    # Archiving a bot is gated (not a task create, which is exempt), so the stale citation leaves only a card.
    revision = post(api, "bots/ops/archive", {"expected_revision": 1}, token=token, expected=403)
    assert revision["error"]["code"] == "confirm_required"


def test_a_member_keeps_the_ceiling_on_their_own_assistant(api):
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        secret = H.task_create(c, "human:ana", "Ana's private review", "Nothing for ben.", "bot:inbox")["id"]
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    get(api, "tasks/" + secret, token, expected=404)
    post(api, "bots/ops/archive", {"expected_revision": 1}, token=token, expected=403)


def test_the_owners_switch_puts_every_write_back_on_a_card(api):
    set_rule(api, assistant_direct=False)
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    # Archiving a bot is gated: with the owner's switch off, the Assistant only proposes it.
    revision = post(api, "bots/ops/archive", {"expected_revision": 1}, token=token, expected=403)
    assert revision["error"]["code"] == "confirm_required"
    set_rule(api, assistant_direct=True)


def test_a_run_that_is_not_the_persons_own_chat_lends_nothing(api):
    """The routine or Slack run (not the person's Assistant room) never acts as the person."""
    with api.app.state.store.read() as c:
        who = SimpleNamespace(via="assistant", confirmed=False, attempt_id=None, actor="human:ana")
        with pytest.raises(Problem) as refused:
            assistant.instruction(c, who, "turn")
        assert refused.value.code == "on_behalf_of"


def test_a_batch_commit_never_approves_on_an_assistant_walk(api):
    _, _, finance = setup_attempt(api, "finance")
    approval = approval_for_ana(api, token=finance["token"])
    bid = post(api, "batch", {})["id"]
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "approve", "text": "Go ahead",
                                       "heard": "go ahead"}, token=token)
    done = post(api, f"batch/{bid}/commit", {}, token=token)
    assert any("Approving is the person's own decision" in str(err) for err in done["errors"]), done["errors"]
    assert not get(api, "approvals/" + approval["id"]).get("decision")
    # A decline is the Assistant's to make on a walk: a fresh batch (the committed one is closed).
    bid = post(api, "batch", {}, token=token)["id"]
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "decline", "text": "No"}, token=token)
    post(api, f"batch/{bid}/commit", {}, token=token)
    assert get(api, "approvals/" + approval["id"])["decision"] == "declined"


def test_admits_on_turn_refuses_what_the_person_alone_decides():
    from backend.assistant import admits_on_turn
    assert admits_on_turn("POST", "/api/v2/tasks", {"title": "x"})
    assert admits_on_turn("PATCH", "/api/v2/automations/a1", {"name": "x"})
    assert admits_on_turn("POST", "/api/v2/bots/ops/archive", {"expected_revision": 1})
    assert not admits_on_turn("POST", "/api/v2/me/tokens", {})
    assert not admits_on_turn("POST", "/api/v2/sql", {"query": "select 1"})
    assert not admits_on_turn("DELETE", "/api/v2/people/ben", None)
    assert not admits_on_turn("PUT", "/api/v2/access/rules", {"assistant_direct": False})
    assert not admits_on_turn("GET", "/api/v2/tasks", None)
    assert not admits_on_turn("POST", "/api/v2/tasks/t1", {"status": "done", "api_key": "x"})


def api_room(api, token):
    return get(api, "assistant", token)["room_id"]


def card(api, token, path, body, method="POST", summary="Archive the Ops bot", for_person=None, expected=200):
    sent = {"summary": summary, "method": method, "path": path, "body": body}
    if for_person:
        sent["for"] = for_person
    return post(api, "assistant/actions", sent, token=token, expected=expected)


def test_the_assistants_background_run_puts_one_card_in_the_persons_own_chat(api):
    coo = setup_attempt(api, "coo")[2]["token"]           # the Assistant bot's own identity (its daily sweep)
    made = card(api, coo, "/api/v2/bots/ops/archive", {"expected_revision": 1}, for_person="human:ana")["action"]
    assert made["owner"] == "human:ana" and made["proposer"] == "assistant" and made["status"] == "pending"
    assert made["conversation_id"] == api_room(api, "ana-test")
    # Another bot may not put a card in anyone's chat, and a person may not for someone else.
    other = setup_attempt(api, "ops")[2]["token"]
    card(api, other, "/api/v2/bots/ops/archive", {"expected_revision": 1}, for_person="human:ana", expected=403)
    card(api, "ben-test", "/api/v2/bots/ops/archive", {"expected_revision": 1}, for_person="human:ana", expected=403)


def test_a_background_card_never_approves_and_is_capped_per_person_per_day(api):
    coo = setup_attempt(api, "coo")[2]["token"]
    approve = {"actions": [{"path": "/api/v2/approvals/a1", "body": {"decision": "approved"}}]}
    card(api, coo, "/api/v2/assistant/batch", approve, summary="Approve the spend", for_person="human:ana", expected=403)
    for n in range(5):
        card(api, coo, "/api/v2/bots/ops/archive", {"expected_revision": n + 1}, for_person="human:ana")
    card(api, coo, "/api/v2/bots/ops/archive", {"expected_revision": 9}, for_person="human:ana", expected=429)


def test_one_card_holds_closes_an_automation_change_and_a_rule_stop(api):
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Send the invoice", "Monday.", "human:ana")["id"]
    token = assistant_turn(api, "ana-test")[1]["token"]
    batch = {"actions": [
        {"path": "/api/v2/tasks/" + task, "body": {"status": "done", "version": 1}},
        {"method": "PATCH", "path": "/api/v2/automations/" + aid, "body": {"name": "Weekly digest"}},
    ]}
    made = card(api, token, "/api/v2/assistant/batch", batch, summary="Close it and rename the automation")["action"]
    assert [s["method"] for s in made["body"]["actions"]] == ["POST", "PATCH"]
    # A step that is never admitted (the Assistant's own switches) is refused on the card as well.
    switch = {"actions": [{"method": "POST", "path": "/api/v2/rules", "body": {"kind": "external", "assistant_direct": False}}]}
    card(api, token, "/api/v2/assistant/batch", switch, summary="Switch the Assistant off", expected=422)


def test_a_background_card_is_only_for_a_real_active_person(api):
    coo = setup_attempt(api, "coo")[2]["token"]
    card(api, coo, "/api/v2/bots/ops/archive", {"expected_revision": 1}, for_person="human:ghost", expected=404)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM conversations WHERE owner_id='human:ghost' OR "
                         "id IN (SELECT conversation_id FROM assistant_actions WHERE owner='human:ghost')").fetchone()[0] == 0


def test_a_routine_run_of_the_assistant_bot_is_never_the_persons_turn(api):
    """The daily sweep is a routine of the coo bot: its attempt is not a turn the person started in their Assistant room,
    so the Assistant principal is None and the run keeps the bot's own identity (via is never "assistant")."""
    _, _, attempt = setup_attempt(api, "coo")
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
        assert auth.assistant_principal(c, row) is None
    assert get(api, "me", attempt["token"])["actor"] == "bot:coo"


def test_the_providers_keys_are_never_a_chat_write(api):
    assert not assistant.admits_on_turn("PUT", "/api/v2/providers", {"enabled": []})
    token = assistant_turn(api, "ana-test")[1]["token"]
    res = api.put("/api/v2/providers", json={"enabled": [], "expected_revision": 0}, headers=headers(token))
    assert res.status_code == 403 and res.json()["error"]["code"] == "confirm_required", res.text


def _turn_message_id(api, attempt):
    """The person's message that started this turn (the `turn` a run cites)."""
    with api.app.state.store.read() as c:
        return c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                         (attempt["id"],)).fetchone()["message_id"]


def test_a_turn_never_edits_deletes_or_redacts_a_message_the_person_wrote(api):
    """A "No" the person wrote cannot become a "yes" on a turn: the edit, the delete and the redaction are the person's own
    tap, so the gate holds them (NEVER), and the message keeps its words."""
    _, attempt = assistant_turn(api, "ana-test", "No")
    mid = _turn_message_id(api, attempt)
    cited = {**headers(attempt["token"]), "X-Tico-On-Behalf-Of": "turn"}
    assert not assistant.admits_on_turn("PATCH", f"/api/v2/messages/{mid}", {"body": "yes"})
    assert not assistant.admits_on_turn("DELETE", f"/api/v2/messages/{mid}")
    assert not assistant.admits_on_turn("POST", f"/api/v2/messages/{mid}/redact", {})
    for method, body in (("PATCH", {"body": "yes"}), ("DELETE", None), ("POST", {})):
        raw = api.request(method, f"/api/v2/messages/{mid}", json=body, headers=cited)
        assert raw.status_code == 403 and raw.json()["error"]["code"] == "confirm_required", (method, raw.text)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT body FROM messages WHERE id=?", (mid,)).fetchone()["body"] == "No"


def test_a_message_edited_after_it_was_posted_lends_no_words(api):
    """`instruction` refuses a cited message that was edited, or that has any revision row: the words are not what the
    person posted (backend/assistant.py `edited`)."""
    _, attempt = assistant_turn(api, "ana-test", "Archive the Ops bot")
    mid = _turn_message_id(api, attempt)
    cited = {**headers(attempt["token"]), "X-Tico-On-Behalf-Of": "turn"}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET edited_at=? WHERE id=?", (H.now(), mid))
    raw = api.post("/api/v2/bots/ops/archive", json={"expected_revision": 1}, headers=cited)
    assert raw.status_code == 403 and raw.json()["error"]["code"] == "on_behalf_of" and "edited" in raw.text, raw.text
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET edited_at=NULL WHERE id=?", (mid,))
        assert not assistant.edited(c, {"id": mid, "edited_at": None, "revision": 0})
        c.execute("INSERT INTO message_revisions(id,message_id,origin_id,revision,body,deleted,actor,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", ("rev-1", mid, "origin-1", 1, "yes", 0, "human:ana", H.now()))
        assert assistant.edited(c, {"id": mid, "edited_at": None, "revision": 0})
        assert assistant.edited(c, {"id": mid, "edited_at": None, "revision": 1})


def test_every_route_that_touches_a_credential_or_a_sign_in_is_never_a_chat_write(api):
    """Walks app.routes: any route whose handler reads or writes a credential, a grant, a computer's sign-in or keys, a
    subscription, a federation or signing key, an invite, or the providers, must be on NEVER for every write it takes.
    A new such route that is not listed fails here, not in a chat."""
    touches = re.compile(r"credential|model_login|computer_keys|shared_key|attach_shared|login|logout|password|service_key|"
                         r"enroll|pairing|subscription|federation|signing|replace_lost|invite|provider")
    checked, offenders = 0, []
    for route in api.app.routes:
        methods = getattr(route, "methods", None)
        endpoint = getattr(route, "endpoint", None)
        if not methods or endpoint is None:
            continue
        try:
            source = inspect.getsource(endpoint)
        except (OSError, TypeError):
            continue
        if not touches.search(source):
            continue
        path = re.sub(r"\{[^}]+\}", "x1", route.path)
        for method in sorted(set(methods) - {"GET", "HEAD", "OPTIONS"}):
            checked += 1
            if assistant.admits_on_turn(method, path, None):
                offenders.append(f"{method} {route.path}")
    assert checked >= 20, checked
    assert not offenders, offenders


def _template_tools():
    """Every command the Assistant template tells it to run, as the tool it runs: the hubcli command tree walked for each
    `hub ...` span (clients/remotecli.tool_name names the tool), not a pattern."""
    import argparse
    from clients import hubcli, remotecli

    def fn_of(words):
        node, fn = hubcli.parser(), None
        for word in words:
            sub = next((a for a in node._actions if isinstance(a, argparse._SubParsersAction)), None)
            if not sub or word not in sub.choices:
                break
            node = sub.choices[word]
            fn = node._defaults.get("fn", fn)
        return fn

    template = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "assistant"
    tools = set()
    for path in template.rglob("*.md"):
        for span in re.findall(r"\bhub ([^\n`]+)", path.read_text(encoding="utf-8")):
            words = []
            for token in span.split():
                if not re.fullmatch(r"[a-z][a-z-]*", token):
                    break
                words.append(token)
            fn = fn_of(words)
            if fn:
                tools.add(remotecli.tool_name(fn))
    return tools


def test_the_assistant_turn_is_offered_the_tools_its_template_runs_and_never_the_hidden_ones():
    """The Assistant's MCP listing: every tool its template runs is offered, the NEVER tools are not, and the listing stays
    small enough for the model (about 165 KB today; the cliff is past 200 KB)."""
    everything = {t["name"] for t in hubtools.TOOLS}
    offered = hubtools.listing(local=True, kind="assistant")
    names = {t["name"] for t in offered}
    template = _template_tools()
    assert len(template) >= 40, sorted(template)
    assert template - names == set(), sorted(template - names)
    assert not set(hubtools.ASSISTANT_HIDDEN) & names and set(hubtools.ASSISTANT_HIDDEN) <= everything
    assert len(json.dumps(offered)) <= 200_000, len(json.dumps(offered))
    assert "hub_assistant_propose" in {t["name"] for t in hubtools.listing(local=True, kind="bot")}
