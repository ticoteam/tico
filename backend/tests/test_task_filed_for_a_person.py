"""`tico task create --for human:X --from-message M` (Ideas: say it once, Engineering #227): a bot files a person's own
ask, and the task is the person's. Only when X sent M and the bot reads M; else refused, and nothing is filed."""

from backend.store import H
from backend.tests.helpers.api import post
from backend.tests.helpers.tasks_board import bot_token

ASK = {"title": "Export the board to CSV", "body": "Ana asked for a CSV export of the board.", "owner": "bot:ops"}


def _count(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM tasks WHERE title=?", (ASK["title"],)).fetchone()[0]


def test_a_bot_files_a_persons_ask_for_her_from_her_message(api):
    token = bot_token(api, "ops")
    said = post(api, "chat/ops", {"text": "Could the board export to CSV?"})
    row = post(api, "tasks", {**ASK, "for_actor": "human:ana", "from_message": said["id"]}, token)
    assert row["requester_id"] == "human:ana"
    with api.app.state.store.read() as c:
        (by, note) = c.execute(
            "SELECT actor_id, note FROM history_events WHERE subject_id=? AND kind='filed_for'", (row["id"],)
        ).fetchone()
    assert by == "bot:ops" and note.startswith("Filed for ")


def test_it_is_refused_unless_that_person_sent_the_message_and_the_bot_reads_it(api):
    token = bot_token(api, "ops")
    said = post(api, "chat/ops", {"text": "Could the board export to CSV?"})
    # Someone else's message.
    post(api, "tasks", {**ASK, "for_actor": "human:ben", "from_message": said["id"]}, token, expected=403)
    # A message the bot cannot read: another bot's chat.
    other = post(api, "chat/finance", {"text": "The invoices are late."})
    post(api, "tasks", {**ASK, "for_actor": "human:ana", "from_message": other["id"]}, token, expected=403)
    # Not a person; one without the other; a person filing "for" someone.
    post(api, "tasks", {**ASK, "for_actor": "bot:finance", "from_message": said["id"]}, token, expected=422)
    post(api, "tasks", {**ASK, "for_actor": "human:ana"}, token, expected=422)
    post(api, "tasks", {**ASK, "for_actor": "human:ana", "from_message": said["id"]}, expected=422)
    assert _count(api) == 0
    with api.app.state.store.read() as c:
        assert H.message(c, said["id"])["sender_id"] == "human:ana"
