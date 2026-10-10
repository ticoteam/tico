"""`bot_contact: replies` — a bot the rest of the fleet cannot ping, and the ways back in.

Every run costs its operator money. A bot with a narrow job and an expensive model should not
be a free help desk for every other bot in the company, but it must still be reachable by a
person, by its manager, and by anyone it asked something of.
"""

from backend.tests.test_api import api, get, headers, post  # noqa: F401


def bot(api, slug, name, reports_to=None):
    return post(api, "bots", {"slug": slug, "display_name": name, "description": "A test bot.",
                              "reports_to": reports_to, "status": "active", "repo": "emp-" + slug,
                              "thread_mode": "personal", "model": "hermes-profile",
                              "effort": "as-configured", "harness": "hermes",
                              "operator": "ana", "owners": ["ana"], "runner_id": None})


def token(api, slug):
    return post(api, "bots/" + slug + "/agent-credential", {})["token"]


def quiet(api, slug, mode="replies", revision=1):
    return post(api, "bots/" + slug + "/definition",
                {"bot_contact": mode, "expected_revision": revision})


def fleet(api):
    """An analyst under a manager, and an unrelated bot with no business with it."""
    bot(api, "boss", "Manager")
    bot(api, "analyst", "Analyst", reports_to="boss")
    bot(api, "stranger", "Stranger")
    quiet(api, "analyst")
    return token(api, "boss"), token(api, "analyst"), token(api, "stranger")


def listed(api, slug):
    return next(row for row in get(api, "bots") if row["slug"] == slug)


def test_an_unrelated_bot_cannot_chat_it_or_put_a_task_on_it(api):
    _, _, stranger = fleet(api)
    refused = post(api, "chat/analyst", {"text": "Quick question about the funnel."},
                   token=stranger, expected=403)
    assert refused["error"]["code"] == "bot_contact"
    assert "manager or a person" in refused["error"]["detail"]
    post(api, "tasks", {"owner": "analyst", "title": "Pull these numbers", "body": "Please."},
         token=stranger, expected=403)
    # And it is not a silent drop: nothing was written either way.
    assert [t for t in get(api, "tasks")["tasks"] if t["owner"] == "bot:analyst"] == []


# ---------- `tasks`: work arrives as a task and nothing else ----------

def notices_to(api, slug):
    """Every message the hub wrote to this bot, and whether it started a run. A `quiet` ref is
    the difference between news in the room and a whole run spent reading it."""
    import json
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT body,refs_json FROM messages WHERE to_actor=? ORDER BY created",
                         ("bot:" + slug,)).fetchall()
    return [(r["body"].split("\n")[0], bool((json.loads(r["refs_json"] or "{}")).get("quiet")))
            for r in rows]


def tasks_only(api):
    bot(api, "boss", "Manager")
    bot(api, "analyst", "Analyst", reports_to="boss")
    bot(api, "stranger", "Stranger")
    quiet(api, "analyst", "tasks")
    return token(api, "boss"), token(api, "analyst"), token(api, "stranger")


def test_another_bot_may_file_work_and_do_nothing_else(api):
    _, _, stranger = tasks_only(api)
    filed = post(api, "tasks", {"owner": "analyst", "title": "Check the churned feeds",
                                "body": "Two clients still syncing."}, token=stranger)
    assert filed["owner"] == "bot:analyst"
    refused = post(api, "chat/analyst", {"text": "Did you see my ticket?"}, token=stranger, expected=403)
    assert refused["error"]["code"] == "bot_contact"
    assert "takes work as a task and nothing else" in refused["error"]["detail"]
    post(api, "messages", {"to": "analyst", "text": "Which column?", "kind": "ask"},
         token=stranger, expected=403)


def test_its_manager_and_botops_still_leave_notes_and_another_bot_may_not(api):
    boss, _, stranger = tasks_only(api)
    bot(api, "botops", "BotOps")
    botops = token(api, "botops")
    for sender in (boss, botops):
        assert post(api, "notes", {"to": "analyst", "text": "The feed moved to v2."}, token=sender)["note"]
    refused = post(api, "notes", {"to": "analyst", "text": "The feed moved to v2."}, token=stranger, expected=403)
    assert refused["error"]["code"] == "bot_contact"


# ---------- news lands in the room; it does not cost a run ----------
