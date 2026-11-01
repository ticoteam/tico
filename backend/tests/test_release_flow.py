"""The release rhythm: one daily Release task, `release now`, and the wakes a published release sends."""

import json
from datetime import datetime, timezone

from backend import hubdb as H
from backend import release_flow as RF
from backend.store import encode
from backend.tests.test_github import PR, api, get, headers, hook, pr_event, post  # noqa: F401

RELEASE_URL = "https://github.com/ticoteam/tico/releases/tag/v1.2.3"


def _team(api):
    """cmo is the Release Manager and cpo the Technical Writer (by starter template); Dev tickets has a Pending Release."""
    with api.app.state.store.transaction() as c:
        bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cpo", "cmo", "qab")}
        H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}]})
        for slug, template in (("cmo", RF.MANAGER), ("cpo", RF.WRITER), ("qab", RF.QA)):
            c.execute("INSERT OR REPLACE INTO bot_config(bot,config,team,operator) VALUES(?,?,?,?)",
                      (slug, encode({**bots[slug], "template": template}), "product", "ana"))
        dev = H.type_create(c, "human:ana", "Dev tickets", numbered=True, steps=[
            {"name": "In QA", "status": "review"}, {"name": "Pending Release", "status": "review"},
            {"name": "Post Release QA", "status": "review"}, {"name": "Done", "status": "done"}])
    return dev


def _ticket(api, dev, title, number=412, tags=()):
    url = PR.replace("/412", f"/{number}")
    ticket = post(api, "tasks", {"owner": "cpo", "title": title, "body": "x", "links": [url], "type": dev["id"],
                                 "step": "Pending Release", "labels": list(tags)})
    hook(api, "pull_request", pr_event("closed", html_url=url, merged=True, merge_commit_sha="abc%d" % number,
                                       merged_at="2026-10-19T12:42:19Z"))
    return ticket


def _tasks(api, owner, prefix="Release:"):
    with api.app.state.store.read() as c:
        return [r["title"] for r in c.execute("SELECT title FROM tasks WHERE owner=? AND title LIKE ?",
                                              (H.bot_actor(owner), prefix + "%"))]


def _release_wakes(api, ticket):
    with api.app.state.store.read() as c:
        return [(r["body"], r["refs"]) for r in c.execute(
            "SELECT m.body, m.refs FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE {H.MESSAGE_TASK_SQL}=? AND json_extract(m.refs,'$.wake')='release_published'", (ticket["id"],))]


def test_the_daily_hour_sends_one_task_and_nothing_when_nothing_is_pending(api):
    dev = _team(api)
    store = api.app.state.store
    # 10:00 company time on a day in October (PDT, UTC-7) is 17:00Z
    hour = datetime(2026, 10, 20, 17, 5, tzinfo=timezone.utc)
    with store.transaction() as c:
        assert RF.tick(c, hour)["reason"] == "nothing_pending"
    assert _tasks(api, "cmo") == []
    _ticket(api, dev, "Visual replies", 412)
    _ticket(api, dev, "Pricing page", 413)
    later = datetime(2026, 10, 21, 17, 5, tzinfo=timezone.utc)
    with store.transaction() as c:
        assert RF.tick(c, later)["sent"] is True
        assert RF.tick(c, later) is None, "an hour is handled once"
    assert _tasks(api, "cmo") == ["Release: 2 tickets at Pending Release"]


def test_release_now_is_for_those_who_move_dev_tickets_and_wakes_an_open_release_task(api):
    dev = _team(api)
    _ticket(api, dev, "Hotfix", 412)
    from backend.auth import Identity
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager='cpo' WHERE id=?", (dev["id"],))
    with api.app.state.store.read() as c:
        who = lambda slug: RF.may_release(c, Identity("bot:" + slug, "bot"))
        assert who("cmo") and who("cpo"), "the Release Manager and the Dev tickets type's manager may"
        assert not who("qab"), "any other bot, though bots may work Dev tickets, may not"
    first = post(api, "release/now", {"note": "hotfix"})
    assert first["sent"] is True and _tasks(api, "cmo") == ["Release: 1 ticket at Pending Release"]
    again = post(api, "release/now", {})
    assert again["reason"] == "already_open" and again["task"] == first["task"]
    assert len(_tasks(api, "cmo")) == 1, "a second Release task is never filed beside an open one"


def test_a_persons_release_now_with_a_note_goes_out_with_nothing_pending_and_nobody_else_does(api):
    _team(api)
    assert post(api, "release/now", {})["reason"] == "nothing_pending", "a person with no note"
    assert post(api, "release/now", {"note": "  "})["reason"] == "nothing_pending"
    with api.app.state.store.transaction() as c:
        assert RF.request_now(c, "bot:cpo", "merged PRs")["reason"] == "nothing_pending", "a bot keeps today's answer"
    assert _tasks(api, "cmo") == []
    first = post(api, "release/now", {"note": "PRs merged straight to main"})
    assert first["sent"] is True and first["tickets"] == 0 and first["reason"] is None
    assert len(_tasks(api, "cmo")) == 1 and _tasks(api, "cmo")[0].startswith("Release: requested by ")
    with api.app.state.store.read() as c:
        body = H.task(c, first["task"])["body"]
    assert "PRs merged straight to main" in body and "Nothing is at Pending Release" in body
    again = post(api, "release/now", {"note": "and one more"})
    assert again["reason"] == "already_open" and again["task"] == first["task"]
    assert len(_tasks(api, "cmo")) == 1, "an open Release task is woken, not doubled"


def test_a_published_release_gives_the_writer_one_task_and_qa_a_wake_per_ticket(api):
    dev = _team(api)
    tested = _ticket(api, dev, "Visual replies", 412, tags=["qa"])
    plain = _ticket(api, dev, "Typo fix", 413)
    event = {"action": "published", "release": {"tag_name": "v1.2.3", "html_url": RELEASE_URL},
             "repository": {"full_name": "ticoteam/tico"}}
    hook(api, "release", event)
    hook(api, "release", event)      # the same delivery again
    assert [json.loads(r)["role"] for _, r in _release_wakes(api, plain)] == ["note"], "a quiet note, no wake"
    assert json.loads(_release_wakes(api, plain)[0][1])["quiet"] is True
    roles = sorted(json.loads(r)["role"] for _, r in _release_wakes(api, tested))
    assert roles == ["note", "qa"], "QA is woken for the qa-labelled ticket only"
    wakes = {json.loads(r)["role"]: json.loads(r) for _, r in _release_wakes(api, tested)}
    assert wakes["qa"]["rule"] == "release_published" and not wakes["qa"].get("quiet"), "QA's wake is the named event"
    with api.app.state.store.read() as c:
        written = c.execute("SELECT refs FROM messages WHERE body LIKE 'New task from%Docs for v1.2.3%'").fetchall()
    assert [json.loads(r[0])["rule"] for r in written] == ["release_published"], "the writer's docs task wakes under it too"
    docs = [t for t in _tasks(api, "cpo", "Docs for")]
    assert docs == ["Docs for v1.2.3: 2 tickets"], "one docs task for the release, however many tickets or replays"


def test_a_private_ticket_is_counted_but_never_named_in_the_docs_task(api):
    dev = _team(api)
    _ticket(api, dev, "Visual replies", 412)
    secret = _ticket(api, dev, "Acme payroll export", 413)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (secret["id"],))
    hook(api, "release", {"action": "published", "release": {"tag_name": "v1.2.3", "html_url": RELEASE_URL},
                          "repository": {"full_name": "ticoteam/tico"}})
    with api.app.state.store.read() as c:
        body = c.execute("SELECT body FROM tasks WHERE owner='bot:cpo' AND title LIKE 'Docs for%'").fetchone()["body"]
    assert "Visual replies" in body and "1 private ticket, not named here" in body
    assert "Acme payroll" not in body and "/413" not in body


def test_qa_hears_a_private_ticket_shipped_on_its_own_qa_subtask(api):
    dev = _team(api)
    secret = _ticket(api, dev, "Acme payroll export", 413, tags=["qa"])
    with api.app.state.store.transaction() as c:
        check = H.task_create(c, "bot:cpo", "QA: payroll export", "Check it live.", "bot:qab", parent_id=secret["id"],
                              lint=False)
        c.execute("UPDATE tasks SET private=1 WHERE id IN (?,?)", (secret["id"], check["id"]))
    hook(api, "release", {"action": "published", "release": {"tag_name": "v1.2.3", "html_url": RELEASE_URL},
                          "repository": {"full_name": "ticoteam/tico"}})
    with api.app.state.store.read() as c:
        woke = c.execute("SELECT m.body FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                         f"WHERE {H.MESSAGE_TASK_SQL}=? AND m.to_actor='bot:qab' "
                         "AND json_extract(m.refs,'$.wake')='release_published'", (check["id"],)).fetchall()
    assert len(woke) == 1 and "QA: payroll export" in woke[0][0] and "Acme payroll export" not in woke[0][0]
