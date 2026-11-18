"""A task is a title and a chat (spec v3.4, change 8): no description. What a task asks is its first chat message, from
whoever wrote it; words Tico files for a bot are its instructions, read in its runs; the migration moves each old
description the same way."""

import importlib

from backend import task_engine as E
from backend import task_wakes
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.tasks_board import api, get, headers, post  # noqa: F401  (fixture)

ANA = "human:ana"
MIGRATION = "backend.migrations.20261117062614_task_descriptions_to_first_messages"


def _said(c, task_id):
    """The task's chat as people read it: (sender, body) in timeline order."""
    return [
        (r[0], r[1])
        for r in c.execute(
            "SELECT sender_id, body FROM messages WHERE json_extract(refs, '$.task') = ? AND seq IS NOT NULL "
            "ORDER BY seq, created_at",
            (task_id,),
        )
    ]


def test_a_new_task_is_its_title_and_a_first_message_from_whoever_wrote_it(company):
    with company.transaction() as c:
        X.bot(c, "cpo")
        made = H.task_create(
            c, ANA, "Add the export button", "On the reports page, beside Print.", "bot:cpo", lint=False
        )
        assert H.task(c, made["id"])["body"] == ""
        request = H.task_request(c, made["id"])
        assert request["from"] == ANA and request["text"] == "On the reports page, beside Print."
        assert _said(c, made["id"])[0] == (ANA, "On the reports page, beside Print.")
        bare = H.task_create(c, ANA, "Rename the export button", None, "bot:cpo", lint=False)
        assert H.task_request(c, bare["id"]) is None  # a title alone is a task


def test_what_tico_files_for_a_bot_is_its_instructions_not_a_message(company):
    with company.transaction() as c:
        X.bot(c, "cpo")
        made = H.task_create(
            c, H.TICO_ACTOR, "Set yourself up", "Run your setup: read the docs.", "bot:cpo", lint=False
        )
        assert H.task_request(c, made["id"]) is None
        assert task_wakes.instructions(c, made["id"]) == "Run your setup: read the docs."
        assert all(sender != H.TICO_ACTOR for sender, _ in _said(c, made["id"]))


def test_the_api_takes_a_message_and_refuses_a_description(api):
    made = post(api, "tasks", {"owner": "cpo", "title": "Draft the launch note", "message": "Two lines, no emoji."})
    task = get(api, "tasks/" + made["id"])["task"]
    assert task["request"]["text"] == "Two lines, no emoji."
    assert "body" not in task
    refused = api.post(
        "/api/v2/tasks/" + made["id"], json={"body": "New words", "version": task["version"]}, headers=headers()
    )
    assert refused.status_code == 422


def _old(c, by, title, body, owner, *, status=None):
    """A task as the old server left it: its words in tasks.body and no request message."""
    made = H.task_create(c, by, title, None, owner, lint=False, deduplicate=False)
    c.execute("UPDATE tasks SET body=? WHERE id=?", (body, made["id"]))
    if status:
        E.move(c, made["id"], E.DONE, ANA, "Done", seen_version=E.task(c, made["id"])["version"])
    return H.task(c, made["id"])


def test_the_migration_posts_a_persons_description_first_and_keeps_bot_instructions_out_of_the_chat(company):
    with company.transaction() as c:
        X.bot(c, "cpo")
        X.place(c, "cpo")
        asked = _old(c, ANA, "Add the export button", "On the reports page.", "bot:cpo")
        later = H.task_comment(c, ANA, asked["id"], "Also a CSV option.")
        filed = _old(c, H.TICO_ACTOR, "Check the backups", "Bot id: cpo\nRead the last report.", "bot:cpo")
        setup = _old(c, ANA, "Set yourself up", "Run your setup: read the docs.", "bot:cpo", status="done")
        plain = _old(c, ANA, "Order paper", "Order paper", "bot:cpo")
        unread = c.execute(
            "SELECT last_seq, last_unread_seq FROM conversations WHERE id=?", (asked["conversation_id"],)
        ).fetchone()

        migration = importlib.import_module(MIGRATION)
        migration.up(c)
        migration.up(c)  # again: nothing more

        for task in (asked, filed, setup, plain):
            assert H.task(c, task["id"])["body"] == ""
        # A person's words: the first message, from her, before what came after; nobody is told again.
        said = _said(c, asked["id"])
        assert said[0] == (ANA, "On the reports page.")
        assert (ANA, "Also a CSV option.") in said[1:]
        assert H.task_request(c, asked["id"])["text"] == "On the reports page."
        assert later
        assert tuple(
            c.execute(
                "SELECT last_seq, last_unread_seq FROM conversations WHERE id=?", (asked["conversation_id"],)
            ).fetchone()
        ) == tuple(unread)
        # What Tico filed for a bot: its instructions, in no timeline.
        assert H.task_request(c, filed["id"]) is None
        assert task_wakes.instructions(c, filed["id"]) == "Bot id: cpo\nRead the last report."
        assert all(sender != H.TICO_ACTOR for sender, _ in _said(c, filed["id"]))
        # A finished setup's steps: dropped; the template keeps them.
        assert H.task_request(c, setup["id"]) is None and task_wakes.instructions(c, setup["id"]) == ""
        # Words that only repeat the title: nothing to post.
        assert H.task_request(c, plain["id"]) is None
