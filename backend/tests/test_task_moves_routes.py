"""The task routes of the cut-over (docs/dev/pipelines-v3-cutover.md, "HTTP"): each calls one task call and answers
the task as JSON; a refusal is flat, with what is missing, the commands allowed next and the version."""

from backend.auth import Identity
from backend.tests.helpers.api import get, headers, post


def as_bot(api, slug="ops"):
    """A token for a bot of the company, as its own command would carry: an agent's (backend/agents.py)."""
    from backend.store import H

    token = slug + "-bot-test"
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "hermes", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.auth.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token


def task(api, title="Plan the offsite", owner="ben", token="ana-test"):
    return post(api, "tasks", {"title": title, "body": "Pick a date and a place.", "owner": owner}, token=token)


def show(api, tid, token="ana-test"):
    return get(api, "tasks/" + tid, token=token)["task"]


def refused(api, path, body, token="ana-test", status=409):
    r = api.post("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == status, r.text
    return r.json()


def test_a_new_task_is_in_general_and_reads_with_the_engines_fields(api):
    made = task(api)
    shown = show(api, made["id"])
    assert shown["status"] == "open" and shown["pipeline"]["id"] == "general"
    assert shown["stage"]["id"] == "general-todo" and shown["stage"]["kind"] == "work"
    assert shown["owner_id"] == "human:ben" and isinstance(shown["version"], int)
    assert [r["kind"] for r in shown["to_leave"]] == ["owner_done"]
    assert shown["turn"]["state"] == "person" and shown["waits"][0] == shown["turn"]
    for gone in ("type", "step", "waiting_on", "holds", "check_back", "next_run", "type_id", "step_id"):
        assert gone not in shown, gone
    row = next(t for t in get(api, "tasks")["tasks"] if t["id"] == made["id"])
    assert row["stage_id"] == "general-todo" and row["turn"]["state"] == "person"
    assert row["late"] is None and row["snoozed"] is False and "to_leave" not in row


def test_done_finishes_a_general_task_and_reopen_needs_a_why(api):
    made = task(api, owner="ana")
    done = post(api, f"tasks/{made['id']}/done", {"note": "Booked the barn."})
    assert done["status"] == "done" and done["stage"]["kind"] == "done"
    body = refused(api, f"tasks/{made['id']}/move", {"to": "reopen"})
    assert body["error"] == "task_why" and body["detail"]
    back = post(api, f"tasks/{made['id']}/move", {"to": "reopen", "why": "The barn cancelled."})
    assert back["status"] == "open"


def test_a_stale_version_is_refused_flat_with_the_version_now(api):
    made = task(api)
    version = show(api, made["id"])["version"]
    body = refused(api, f"tasks/{made['id']}/move", {"to": "closed", "why": "Not needed", "version": version + 5})
    assert body["error"] == "task_changed" and body["version"] == version and body["detail"]
    # The replay of the same request carries the same fields.
    key = "replayed-refusal"
    first = api.post(
        f"/api/v2/tasks/{made['id']}/move", json={"to": "closed", "version": 0, "why": "x"}, headers=headers(key=key)
    )
    again = api.post(
        f"/api/v2/tasks/{made['id']}/move", json={"to": "closed", "version": 0, "why": "x"}, headers=headers(key=key)
    )
    assert first.status_code == again.status_code == 409
    assert again.json()["error"] == "task_changed" and again.json()["version"] == version


def test_a_bots_field_edit_carries_its_version_and_a_persons_may_leave_it_out(api):
    bot = as_bot(api)
    made = task(api, owner="ops")
    version = show(api, made["id"])["version"]
    body = refused(api, f"tasks/{made['id']}", {"title": "Plan the spring offsite"}, token=bot)
    assert body["error"] == "task_changed" and body["version"] == version
    body = refused(api, f"tasks/{made['id']}", {"title": "Plan the spring offsite", "version": version - 1}, token=bot)
    assert body["error"] == "task_changed" and body["version"] == version
    edited = post(api, f"tasks/{made['id']}", {"title": "Plan the spring offsite", "version": version}, token=bot)
    assert edited["title"] == "Plan the spring offsite" and edited["version"] == version + 1
    by_person = post(api, f"tasks/{made['id']}", {"due": "2026-12-01T17:00:00Z"})
    assert by_person["version"] == version + 2
    for gone in ({"status": "done"}, {"owner": "cara"}, {"step": "x"}, {"waiting_on": "ana"}, {"close": True}):
        refused(api, f"tasks/{made['id']}", gone, status=422)


def test_reassign_snooze_and_unsnooze(api):
    made = task(api)
    moved = post(api, f"tasks/{made['id']}/owner", {"owner": "cara", "why": "Cara runs offsites"})
    assert moved["owner_id"] == "human:cara"
    snoozed = post(api, f"tasks/{made['id']}/snooze", {"until": "2099-01-05", "why": "After the holidays"})
    assert snoozed["snooze"]["until"].startswith("2099-01-05T08:00:00") and snoozed["turn"]["state"] == "snoozed"
    assert next(t for t in get(api, "tasks?snoozed=all")["tasks"] if t["id"] == made["id"])["snoozed"] is True
    back = post(api, f"tasks/{made['id']}/unsnooze", {"why": "Dates came early"})
    assert back["snooze"] is None
    refused(api, f"tasks/{made['id']}/snooze", {"until": "not a date"}, status=422)


def test_an_ask_is_answered_given_and_withdrawn_on_the_task(api):
    made = task(api, owner="ana")
    asked = api.post(
        f"/api/v2/tasks/{made['id']}/asks",
        json={"to": "ben", "line": "Which month suits the team?", "choices": ["May", "June"]},
        headers=headers(),
    )
    assert asked.status_code == 200, asked.text
    rid = asked.json()["requirement_id"]
    ask = next(r for r in asked.json()["task"]["to_leave"] if r["requirement_id"] == rid)
    assert ask["kind"] == "answer" and ask["who"] == "human:ben" and ask["choices"] == ["May", "June"]
    items = get(api, "needs-you", token="ben-test")["items"]
    item = next(i for i in items if i["kind"] == "ask")
    assert item["line"] == "Which month suits the team?" and item["task"]["id"] == made["id"]
    assert item["requirement_id"] == rid and item["ask"]["choices"] == ["May", "June"]
    given = post(api, f"tasks/{made['id']}/asks/{rid}/give", {"to": "cara"}, token="ben-test")
    assert next(r for r in given["to_leave"] if r["requirement_id"] == rid)["who"] == "human:cara"
    answered = api.post(
        f"/api/v2/tasks/{made['id']}/asks/{rid}/answer", json={"value": "June"}, headers=headers("cara-test")
    )
    assert answered.status_code == 200, answered.text
    aid = answered.json()["answer_id"]
    assert next(r for r in answered.json()["task"]["to_leave"] if r["requirement_id"] == rid)["met"] is True
    undone = post(api, f"answers/{aid}/undo", {}, token="cara-test")
    assert next(r for r in undone["to_leave"] if r["requirement_id"] == rid)["met"] is False
    gone = api.request("DELETE", f"/api/v2/tasks/{made['id']}/asks/{rid}", json={"why": "Decided"}, headers=headers())
    assert gone.status_code == 200, gone.text
    assert rid not in [r["requirement_id"] for r in gone.json()["task"]["to_leave"]]
    assert not [i for i in get(api, "needs-you", token="cara-test")["items"] if i["kind"] == "ask"]
    refused(api, f"tasks/{made['id']}/asks/nope/answer", {"value": "ok"}, status=404)


def test_needs_you_lists_a_persons_own_task_and_counts_the_list(api):
    made = task(api, owner="ben")
    needs = get(api, "needs-you", token="ben-test")
    mine = [i for i in needs["items"] if i["kind"] == "task" and i["task"]["id"] == made["id"]]
    assert len(mine) == 1 and mine[0]["task"]["stage_id"] == "general-todo"
    assert needs["count"] == len(needs["items"]) == get(api, "needs-you?count=1", token="ben-test")["count"]


def test_a_persons_mention_is_in_needs_you_until_they_reply(api):
    made = task(api, owner="ana")
    post(api, f"tasks/{made['id']}/comments", {"text": "@cara can you check the barn's dates?"})
    mention = [i for i in get(api, "needs-you", token="cara-test")["items"] if i["kind"] == "mention"]
    assert len(mention) == 1 and mention[0]["task"]["id"] == made["id"]
    post(api, f"tasks/{made['id']}/comments", {"text": "Checked: both weekends are free."}, token="cara-test")
    assert not [i for i in get(api, "needs-you", token="cara-test")["items"] if i["kind"] == "mention"]


def test_bulk_is_for_people_and_moves_each_task_with_its_call(api):
    first, second = task(api, "One"), task(api, "Two")
    out = post(api, "task-bulk", {"filter": {"ids": [first["id"], second["id"]]}, "action": "close", "why": "Done"})
    assert out["changed"] == 2 and show(api, first["id"])["status"] == "closed"
    refused(api, "task-bulk", {"filter": {"ids": [first["id"]]}, "action": "close"}, token=as_bot(api), status=403)


def test_the_pipelines_read_and_save_their_settings_record(api):
    pipelines = get(api, "pipelines")["pipelines"]
    general = next(p for p in pipelines if p["id"] == "general")
    assert general["stages"][0]["id"] == "general-todo" and general["may_change"] is True
    record = get(api, "pipelines/general")["pipeline"]
    saved = post(api, "pipelines/general", {**record, "goal": "Everything else."})
    assert saved["pipeline"]["goal"] == "Everything else."
    assert saved["pipeline"]["settings_version"] == record["settings_version"] + 1
    assert get(api, "pipelines/general", token="cara-test")["pipeline"]["may_change"] is False
    refused(api, "pipelines/general", {**saved["pipeline"], "goal": "x"}, token="cara-test")


def test_a_replayed_write_answers_as_the_first_did_and_acts_once(api):
    """The same Idempotency-Key answers the stored response, checked again against what the caller may read now
    (backend/task_privacy.py `require_payload`), on a shared task and on a private one."""
    shared = task(api, owner="ana")
    private = post(
        api, "tasks", {"title": "Salary review", "body": "Next year's bands.", "owner": "ana", "private": True}
    )
    for made in (shared, private):
        key = "done-" + made["id"]
        first = api.post(f"/api/v2/tasks/{made['id']}/done", json={"note": "Booked."}, headers=headers(key=key))
        again = api.post(f"/api/v2/tasks/{made['id']}/done", json={"note": "Booked."}, headers=headers(key=key))
        assert first.status_code == again.status_code == 200, again.text
        assert again.json() == first.json() and first.json()["task"]["status"] == "done"
        assert show(api, made["id"])["version"] == first.json()["task"]["version"]


# ---------------------------------------------------------------------- OKs bound to what was shown
FORUM = "forum_post:note-1"


def ask_ok(api, bot, tid, content, to="ben"):
    version = show(api, tid)["version"]
    r = api.post(
        f"/api/v2/tasks/{tid}/oks",
        json={
            "to": to,
            "line": "OK to post this to the forum?",
            "subject": FORUM,
            "content": content,
            "version": version,
        },
        headers=headers(bot),
    )
    assert r.status_code == 200, r.text
    return r.json()["requirement_id"]


def no(api, path, body, token, status):
    """The refusal's code and words: flat for the engine's task_* rules, under `error` for the rest."""
    r = api.post("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == status, r.text
    out = r.json()
    return out["error"] if isinstance(out["error"], dict) else {"code": out["error"], "detail": out["detail"]}


def test_a_bot_asks_for_an_ok_only_with_a_version_and_only_for_a_generic_subject(api):
    bot = as_bot(api)
    made = task(api, owner="ops")
    version = show(api, made["id"])["version"]
    ask = {"to": "ben", "line": "OK to post this?", "subject": FORUM, "content": {"text": "Hi"}}
    assert no(api, f"tasks/{made['id']}/oks", ask, bot, 409)["code"] == "task_changed"
    owned = {**ask, "subject": "email_send:1", "version": version}
    assert no(api, f"tasks/{made['id']}/oks", owned, bot, 422)["code"] == "validation"
    rid = ask_ok(api, bot, made["id"], {"text": "Hi"})
    assert rid == ask_ok(api, bot, made["id"], {"text": "Hi"})  # the same ask, asked again
    item = next(i for i in get(api, "needs-you", token="ben-test")["items"] if i["kind"] == "ask")
    assert item["requirement_id"] == rid and item["task_id"] == made["id"]


def test_an_ok_is_only_the_asked_persons_own_click_on_exactly_what_they_were_shown(api):
    from backend.auth import Identity

    bot = as_bot(api)
    api.app.state.auth.settings.test_identities["ben-token"] = Identity(
        "human:ben", "human", "ben@acme.example", via_token=True
    )
    made = task(api, owner="ops")
    content = {"text": "We ship on Friday."}
    rid = ask_ok(api, bot, made["id"], content)
    give = f"tasks/{made['id']}/asks/{rid}/ok"
    use = {"subject": FORUM, "content": content, "note": "Posted."}

    shown = get(api, f"oks/{rid}", token="ben-test")
    assert shown["content"] == content and shown["standing"] == "waiting" and shown["task_id"] == made["id"]
    right = shown["shown_hash"]
    # Nothing is used before an OK, and a plain "ok" answer is not one.
    assert no(api, f"oks/{rid}/use", use, bot, 409)["code"] == "task_ok_missing"
    # A bot never gives an OK, not even its own ask's with the right hash.
    assert no(api, give, {"shown_hash": right}, bot, 403)["code"] == "identity"
    # Nor does the person through a token: only their own click in their own session.
    assert no(api, give, {"shown_hash": right}, "ben-token", 403)["code"] == "identity"
    # A person who is not the one asked is refused, though she opened it too.
    cara = get(api, f"oks/{rid}", token="cara-test")["shown_hash"]
    assert "Ben" in no(api, give, {"shown_hash": cara}, "cara-test", 409)["detail"]
    # A hash that is not what was asked about is refused.
    wrong = no(api, give, {"shown_hash": "0" * 64}, "ben-test", 409)
    assert wrong["code"] == "task_changed"
    assert get(api, f"oks/{rid}", token="ben-test")["standing"] == "waiting"

    key = "ok-" + rid
    first = api.post("/api/v2/" + give, json={"shown_hash": right}, headers=headers("ben-test", key))
    again = api.post("/api/v2/" + give, json={"shown_hash": right}, headers=headers("ben-test", key))
    assert first.status_code == again.status_code == 200, again.text
    assert again.json() == first.json()
    assert get(api, f"oks/{rid}", token="ben-test")["standing"] == "ok"

    # Only whoever asked uses it, for exactly that content, once.
    assert no(api, f"oks/{rid}/use", use, "ana-test", 403)["code"] == "identity"
    changed = {**use, "content": {"text": "We ship on Monday."}}
    assert no(api, f"oks/{rid}/use", changed, bot, 409)["code"] == "task_ok_changed"
    used = api.post(f"/api/v2/oks/{rid}/use", json=use, headers=headers(bot))
    assert used.status_code == 200, used.text
    assert no(api, f"oks/{rid}/use", use, bot, 409)["code"] == "consumed"
    assert get(api, f"oks/{rid}", token="ben-test")["standing"] == "used"
    assert api.get("/api/v2/oks/nope", headers=headers("ben-test")).status_code == 404
