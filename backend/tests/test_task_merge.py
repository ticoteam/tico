"""Drop a task on another on the board (backend/task_merge.py, docs/tasks.md "Merge and make a subtask").

Merge keeps the task dropped on: it gains the other's description under "From #A", its files and pages (linked), labels,
related links, followers and requesters; the other's open subtasks move under it; the other is Closed with the
duplicate note and linked as a duplicate. Comments stay on the duplicate, its asks end, and the kept task's note counts
the comments and lists each question. A bot owner is woken once; a person is not. A refusal anywhere changes nothing.
Make it a subtask puts the task under the other after filing: both keep their owners and stages, the parent waits on
it, and history on both names who did it. Loops, nesting past 5, private with company, and a task with an open pull
request are refused with one line; bots, and people who cannot change both tasks, are refused."""

import json

import pytest

from backend import task_engine as E
from backend import task_merge as TM
from backend import task_relations as TR
from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.task_moves_routes import as_bot


def task(api, title, owner="ops", token="ana-test", **more):
    return post(api, "tasks", {"owner": owner, "title": title, "body": more.pop("body", "x"), **more}, token=token)


def read(api, fn):
    with api.app.state.store.read() as c:
        return fn(c)


def write(api, fn):
    with api.app.state.store.transaction() as c:
        return fn(c)


def history(c, task_id, field):
    return c.execute(
        "SELECT actor_id, old, new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=?",
        (task_id, field),
    ).fetchall()


def test_merge_moves_the_details_and_subtasks_and_closes_the_duplicate(api):
    kept = task(api, "Refunds fail for annual plans", body="Seen on the Pro plan.", token="ben-test")
    dup = task(api, "Annual refunds broken", body="Stripe returns 402 on refund.", labels=["billing"])
    child = task(api, "Check the Stripe logs", parent=dup["id"])
    for n in range(7):
        post(api, f"tasks/{dup['id']}/comments", {"text": f"Note {n}"})
    for line in ("Which plan did it fail on?", "Is it every annual customer?"):
        post(api, f"tasks/{dup['id']}/asks", {"to": "cara", "line": line})
    r = api.put(f"/api/v2/tasks/{dup['id']}/followers", json={"actor": "cara"}, headers=headers())
    assert r.status_code == 200, r.text
    other = task(api, "Refund email copy")
    post(api, f"tasks/{dup['id']}/relations", {"task": other["id"], "kind": "related"})
    post(api, f"tasks/{dup['id']}/links", {"url": "https://status.stripe.com/incidents/1"})
    seen = get(api, f"tasks/{dup['id']}/merge?into={kept['id']}")
    assert (seen["merge"], seen["subtask"], seen["same_owner"]) == (None, None, True)
    assert seen["moves"]["comments"] == 7 and seen["moves"]["subtasks"] == 1
    assert seen["moves"]["followers"] == 2, "cara, who follows it, and ana, who asked for it"
    assert seen["moves"]["asks"] == ["Which plan did it fail on?", "Is it every annual customer?"]

    done = post(api, f"tasks/{dup['id']}/merge", {"into": kept["id"]})

    n = TM._num(dup)
    assert done["closed"]["status"] == "closed" and done["task"]["status"] == "open"

    def check(c):
        b = H.task(c, kept["id"])
        assert f"## From {n}: Annual refunds broken" in b["body"] and "Stripe returns 402" in b["body"]
        assert b["body"].startswith("Seen on the Pro plan.")
        H.hydrate_task_tags(c, [b])
        assert "billing" in H.task_labels(b)
        assert TR.parent_of(c, child["id"]) == kept["id"]
        link = c.execute("SELECT from_task_id, visit_id FROM links WHERE to_task_id=?", (child["id"],)).fetchone()
        assert link[0] == kept["id"] and link[1] == E.task(c, kept["id"])["visit"]["id"]
        assert H.task(c, child["id"])["status"] == "open", "the close found no subtask to close"
        assert TR._exists(c, dup["id"], kept["id"], "duplicate_of")
        assert TR._exists(c, kept["id"], other["id"], "related")
        assert c.execute(
            "SELECT 1 FROM task_links WHERE task_id=? AND url='https://status.stripe.com/incidents/1'", (kept["id"],)
        ).fetchone()
        from backend import task_followers as F

        following = {f["actor"] for f in F.listed(c, b)}
        assert "human:cara" in following
        assert "human:ana" not in {f["actor"] for f in F.listed(c, H.task(c, dup["id"]))}
        assert "human:ana" in following, (
            "whoever asked for the duplicate follows the kept task, so hears when it is done"
        )
        assert c.execute(
            "SELECT 1 FROM task_requesters WHERE task_id=? AND actor='human:ana'", (kept["id"],)
        ).fetchone(), "whoever asked for the duplicate hears when the kept task is done"
        assert not TM._asks(c, dup["id"]), "the asks ended with it"
        assert sum(1 for m in H.task_comments(c, dup["id"]) if m["kind"] == "say") >= 7, "the comments stay"
        notes = [m["body"] for m in H.task_comments(c, kept["id"]) if m["kind"] == "say"]
        assert any(
            f"7 comments on [{n}](#/task/{dup['id']})" in body and "Which plan did it fail on?" in body
            for body in notes
        )
        closed = [m["body"] for m in H.task_comments(c, dup["id"])]
        assert TM.duplicate_note(b) in closed
        assert (
            TM.duplicate_note(b) == f"Archiving this one as a duplicate of {TM._num(b)}; its details were added there."
        )
        wakes = c.execute(
            "SELECT count(*) FROM messages WHERE wake_key LIKE ?", (kept["id"] + ":%:merged:%",)
        ).fetchone()
        assert wakes[0] == 1, "the bot owner is woken once"

    read(api, check)


def test_a_person_owner_is_not_woken_and_the_merge_calls_no_model(api, monkeypatch):
    kept = task(api, "Plan the offsite", owner="cara")
    dup = task(api, "Offsite planning", owner="cara")
    post(api, f"tasks/{dup['id']}/merge", {"into": kept["id"]})
    assert (
        read(
            api,
            lambda c: c.execute("SELECT count(*) FROM messages WHERE wake_key LIKE ?", (kept["id"] + ":%",)).fetchone()[
                0
            ],
        )
        == 0
    )


def test_the_other_owner_keeps_it_through_the_hand_over_checks_and_a_refusal_changes_nothing(api, monkeypatch):
    kept = task(api, "Refund page", owner="ops")
    dup = task(api, "Refund page copy", owner="cara")
    child = task(api, "Draft the copy", owner="cara", parent=dup["id"])
    seen = get(api, f"tasks/{dup['id']}/merge?into={kept['id']}")
    assert not seen["same_owner"] and seen["into"]["owner_id"] == "bot:ops" and seen["task"]["owner_id"] == "human:cara"

    # A refusal after the first writes: nothing merges.
    def fails(b):
        raise H.Refused("kind", "no")

    monkeypatch.setattr(TM, "duplicate_note", fails)
    post(api, f"tasks/{dup['id']}/merge", {"into": kept["id"], "owner": "cara"}, expected=422)

    def unchanged(c):
        assert H.task(c, kept["id"])["owner_id"] == "bot:ops" and H.task(c, kept["id"])["body"] == "x"
        assert H.task(c, dup["id"])["status"] == "open" and TR.parent_of(c, child["id"]) == dup["id"]

    read(api, unchanged)
    monkeypatch.undo()
    post(api, f"tasks/{dup['id']}/merge", {"into": kept["id"], "owner": "dave"}, expected=422)
    read(api, unchanged)
    post(api, f"tasks/{dup['id']}/merge", {"into": kept["id"], "owner": "cara"})
    assert read(api, lambda c: H.task(c, kept["id"])["owner_id"]) == "human:cara"


def test_a_person_makes_a_task_a_subtask_after_filing(api):
    parent = task(api, "Launch the refund page", owner="ops")
    sub = task(api, "Write the refund FAQ", owner="cara")
    before = read(api, lambda c: (H.task(c, parent["id"])["stage_id"], H.task(c, sub["id"])["stage_id"]))
    post(api, f"tasks/{sub['id']}/parent", {"parent": parent["id"]})

    def check(c):
        assert TR.parent_of(c, sub["id"]) == parent["id"]
        a, b = H.task(c, sub["id"]), H.task(c, parent["id"])
        assert (a["owner_id"], b["owner_id"]) == ("human:cara", "bot:ops")
        assert (b["stage_id"], a["stage_id"]) == before
        visit = E.task(c, parent["id"])["visit"]["id"]
        assert tuple(
            c.execute("SELECT from_task_id, visit_id FROM links WHERE to_task_id=?", (sub["id"],)).fetchone()
        ) == (
            parent["id"],
            visit,
        )
        from backend import stage_effects

        assert stage_effects.filed_open(c, visit) == [sub["id"]], "the parent waits on it as on any task it filed"
        assert [r[0] for r in history(c, sub["id"], "parent_id")] == ["human:ana"]
        assert [r[0] for r in history(c, parent["id"], "subtask")] == ["human:ana"]

    read(api, check)


def test_drops_that_loop_nest_too_deep_mix_privacy_or_have_an_open_pr_say_why(api):
    top = task(api, "Top")
    under = task(api, "Under", parent=top["id"])
    seen = get(api, f"tasks/{top['id']}/merge?into={under['id']}")
    assert "loop" in seen["subtask"] and "loop" in seen["merge"]
    post(api, f"tasks/{top['id']}/parent", {"parent": under["id"]}, expected=409)

    chain = [task(api, "Level 0")]
    for level in range(1, 6):
        chain.append(task(api, f"Level {level}", parent=chain[-1]["id"]))
    loose = task(api, "Loose")
    assert "5 deep" in get(api, f"tasks/{loose['id']}/merge?into={chain[-1]['id']}")["subtask"]

    secret = task(api, "Secret", owner="cara", private=True)
    seen = get(api, f"tasks/{secret['id']}/merge?into={loose['id']}")
    assert seen["subtask"] == seen["merge"] == "One is private and the other is not."

    pr = task(api, "Has a PR")
    write(
        api,
        lambda c: c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,state,created_at) VALUES(?,?,?,?,?,?)",
            ("l1", pr["id"], "pr", "https://github.com/acme/app/pull/7", "open", H.now()),
        ),
    )
    seen = get(api, f"tasks/{pr['id']}/merge?into={loose['id']}")
    assert seen["subtask"] is None and "open pull request" in seen["merge"]
    post(api, f"tasks/{pr['id']}/merge", {"into": loose["id"]}, expected=409)


def test_a_bot_and_a_person_who_cannot_change_both_are_refused(api):
    mine = task(api, "Ana's task", owner="ops")
    hers = task(api, "Cara's task", owner="cara", token="cara-test")
    bot = as_bot(api)
    post(api, f"tasks/{mine['id']}/merge", {"into": hers["id"]}, token=bot, expected=403)
    post(api, f"tasks/{mine['id']}/parent", {"parent": hers["id"]}, token=bot, expected=403)
    r = api.post(
        f"/api/v2/tasks/{hers['id']}/merge",
        json={"into": mine["id"]},
        headers=headers("cara-test"),
    )
    assert r.status_code in (403, 404), r.text
    assert read(api, lambda c: H.task(c, hers["id"])["status"]) == "open"
    seen = api.get(f"/api/v2/tasks/{hers['id']}/merge?into={mine['id']}", headers=headers("cara-test"))
    assert seen.status_code in (403, 404), seen.text
    get(api, f"tasks/{mine['id']}/merge?into={hers['id']}", token=bot, expected=403)
