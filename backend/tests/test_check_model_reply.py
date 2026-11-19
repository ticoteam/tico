"""A check subtask's reply lands in its own room (backend/check_models.py, execution.complete `own_check`).

The bot files the check for itself, so the reply's target is the bot: before Nov 18 nothing was posted, the check was
done, and the parent's turn found an empty room (a codex review on TIDY, run 8bb398b9)."""

from backend.tests.helpers.api import claim, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.check_models import GROK, parent_turn, start


TOKENS = {"input_tokens": 30, "output_tokens": 30}


def filed_check(api):
    machine, parent, turn = parent_turn(api)
    sub = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Review: second opinion",
            "body": "Review the draft email for anything wrong, risky or unclear.",
            "check_model": "grok",
            "check_role": "review",
            "parent": parent["id"],
        },
        turn["token"],
    )
    with api.app.state.store.transaction() as c:
        # As on TIDY: nobody but the bot is in the check's room (here the person of the parent's chat followed it in).
        c.execute("UPDATE conversations SET participants=? WHERE id=?", ('["bot:ops"]', sub["conversation_id"]))
    start_and_complete(api, machine, turn, "Filed the review.")
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    return machine, sub, check


def start_and_complete(api, machine, turn, text, during=None):
    with api.app.state.store.read() as c:
        if c.execute("SELECT state FROM attempts WHERE id=?", (turn["id"],)).fetchone()[0] == "leased":
            start(api, machine, turn)
    if during:
        during()
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {
            "outcome": "completed",
            "last_seq": 0,
            "text": text,
            **({"usage": {**GROK, **TOKENS, "segments": [{**GROK, **TOKENS}]}} if text else {}),
        },
        machine["token"],
    )


def finish_check(api, check, sub):
    return lambda: post(
        api,
        "tasks/" + sub["id"] + "/done",
        {"note": "Findings are in the review reply.", "version": check["task"]["version"]},
        check["token"],
    )


def room(api, sub):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT sender_id,recipient_id,kind,body,refs FROM messages WHERE conversation_id=? ORDER BY created_at",
                (sub["conversation_id"],),
            )
        ]


def test_a_check_that_finishes_its_task_posts_its_findings_in_its_room(api):
    machine, sub, check = filed_check(api)
    findings = "**Clarify the last sentence.** Deadline: the remaining records are due at 3:04 p.m."
    start_and_complete(api, machine, check, findings, during=finish_check(api, check, sub))
    said = [m for m in room(api, sub) if m["sender_id"] == "bot:ops" and m["body"] == findings]
    assert said, room(api, sub)
    assert said[0]["recipient_id"] is None, "open in the check's room, not a message to itself"


def test_a_check_that_ends_with_no_reply_says_why_in_its_room(api):
    machine, sub, check = filed_check(api)
    start_and_complete(api, machine, check, "", during=finish_check(api, check, sub))
    notes = [m for m in room(api, sub) if m["kind"] == "notice" and "check_unanswered" in (m["refs"] or "")]
    assert len(notes) == 1, room(api, sub)
    assert notes[0]["recipient_id"] == "bot:ops"
    assert "grok:grok-4.6" in notes[0]["body"] and "no final message" in notes[0]["body"]
