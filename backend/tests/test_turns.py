"""Turn annotations accept SQLite task rows and preserve task visibility."""

import pytest

from backend import turns
from backend.store import H
from backend.tests.test_api import api, as_member, assign, claim, get, post, ready, runner  # noqa: F401


@pytest.mark.parametrize("reader", ["ana-test", "ben-test"])
def test_messages_include_readable_tasks_created_during_the_turn(api, reader):
    as_member(api, "ben@acme.example")
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        room = H.open_conversation(c, "human:ana", ["human:ana", "human:ben", "bot:ops"], subject="Launch")
    msg = post(api, "messages", {"to": "ops", "text": "Please plan the launch", "conversation_id": room["id"]})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "task-turn"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        visible = H.task_create(c, "bot:ops", "Draft the launch post", "For the team.", "bot:ops", lint=False)
        private = H.task_create(c, "bot:ops", "Review private mail", "Private.", "bot:inbox", lint=False)
        child = H.task_create(c, "bot:ops", "Draft the budget", "For the launch.", "bot:finance",
                              parent_id=visible["id"], lint=False)
        # A party files the subtask; ordinary read alone does not permit filing it.
        H.say(c, "bot:ops", "human:ana", "Filed the work.", conversation_id=msg["conversation_id"],
              in_reply_to=msg["id"], refs={"turn_id": attempt["id"], "tasks": [visible["id"], private["id"], child["id"]]})
    token = reader
    for path in ("messages", "snapshot"):
        result = get(api, f"conversations/{msg['conversation_id']}/{path}", token)
        messages = result["messages"] if path == "snapshot" else result
        reply = next(m for m in messages if m["sender_id"] == "bot:ops")
        created = {item["task_id"] for item in reply["run"]["did"] if item["kind"] == "task"}
        assert visible["id"] in created
        assert child["id"] in reply["ref_tasks"]
        assert (private["id"] in created) == (reader != "ben-test")
        assert (private["id"] in reply["ref_tasks"]) == (reader != "ben-test")


def test_a_loose_message_says_what_kind_of_sender_started_its_run(api):
    """A bot that relays another bot's work to a person mid-run is marked as prompted by a bot, so the chat can fold it;
    one sent during a run a person started stays the person's. Only the kind of sender is said, never who."""
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:finance", "bot:ops", "The census is delivered; please tell Ana.", refs={"needs_action": True})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "relay"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        relay = H.say(c, "bot:ops", "human:ana", "From Finance: the census is delivered.")
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "", "last_seq": 0}, token=r["token"])
    asked = post(api, "chat/ops", {"text": "What is next?"})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "own"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        own = H.say(c, "bot:ops", "human:ana", "Starting the outreach now.", conversation_id=asked["conversation_id"])
    messages = {m["id"]: m for m in get(api, f"conversations/{asked['conversation_id']}/snapshot")["messages"]}
    assert messages[relay["id"]]["prompted_by"] == "bot"
    assert messages[own["id"]]["prompted_by"] == "person"
    assert "prompted_by" not in messages[asked["id"]]


def test_a_run_that_failed_before_starting_does_not_claim_later_messages(api):
    """An attempt that failed on its computer before it started has no `finished_at`; it must not stay open and fold
    every later message from that bot."""
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:finance", "bot:ops", "Please tell Ana the census is delivered.", refs={"needs_action": True})
    failed = claim(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='failed', started_at=NULL, finished_at=NULL WHERE id=?", (failed["id"],))
        loose = H.say(c, "bot:ops", "human:ana", "The census is delivered.")
    asked = post(api, "chat/ops", {"text": "What is next?"})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "own"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        own = H.say(c, "bot:ops", "human:ana", "Starting the outreach now.", conversation_id=asked["conversation_id"])
    messages = {m["id"]: m for m in get(api, f"conversations/{asked['conversation_id']}/snapshot")["messages"]}
    assert "prompted_by" not in messages.get(loose["id"], {})
    assert messages[own["id"]]["prompted_by"] == "person"


def test_a_tool_call_reads_as_its_work_in_plain_words():
    assert turns.tool_label({"tool": "mcp__hub__hub_task_create"}) == "Filing a task"
    assert turns.tool_label({"tool": "mcp__hub__task_create"}) == "Filing a task"
    assert turns.tool_label({"tool": "Bash", "summary": "hub task close"}) == "Closing a task"
    assert turns.tool_label({"tool": "Bash", "summary": "npm"}) == "Ran npm"
    assert turns.tool_label({"tool": "shell", "summary": "hub release now"}) == "Ran hub"
    assert turns.tool_label({"tool": "Bash"}) == "Ran Bash"
    assert turns.tool_label({"tool": "mcp__peec-ai__list_brands"}) == "Ran peec ai list brands"


def test_a_shell_label_never_shows_an_unknown_program_or_its_arguments():
    assert turns.tool_label({"tool": "Bash", "summary": "run.sh --key sk-live-1"}) == "Ran Bash"
    assert turns.tool_label({"tool": "Bash", "summary": "hub task close --token sk-live-1"}) == "Closing a task"


def test_a_legacy_shell_summary_naming_a_file_or_arguments_is_never_shown():
    """Older runners sent a shell line's summary as the line's words, so a file name or its arguments can arrive. None
    of it is shown: the tool's own name is."""
    assert turns.tool_label({"tool": "Bash", "summary": "customer-private.json"}) == "Ran Bash"
    assert turns.tool_label({"tool": "Bash", "summary": "/tmp/customer-private.json"}) == "Ran Bash"
    assert turns.tool_label({"tool": "Bash", "summary": "npm publish --token x"}) == "Ran Bash"
    assert turns.tool_label({"tool": "Bash", "summary": "hub customer-private.json"}) == "Ran hub"
    assert turns.tool_label({"tool": "Bash", "summary": "cd"}) == "Ran cd"


@pytest.mark.parametrize("summary, label", [
    ("cd hub task list", "Listing tasks"),
    ("cd npm", "Ran npm"),
    ("cd web hub task close", "Closing a task"),
    ("cd web npm", "Ran npm"),
    (". env.sh hub task close", "Closing a task"),
    ("source env.sh npm", "Ran npm"),
    ("export HUB_TOKEN=sk-live-1 npm", "Ran npm"),
    ("export HUB_TOKEN=sk-live-1 hub task close", "Closing a task"),
    ("env HUB_TOKEN=sk-live-1 hub task list", "Listing tasks"),
    ("env HUB_TOKEN=sk-live-1 cargo", "Ran cargo"),
    ("cd web export A=sk-live-1 cargo", "Ran cargo"),
])
def test_a_setup_prefix_is_skipped_and_the_work_after_it_is_named(summary, label):
    """A stored summary may start with the setup words a shell line ran first. They are skipped unread, so the label
    names the work and never a setup word's argument or a secret assignment."""
    got = turns.tool_label({"tool": "Bash", "summary": summary})
    assert got == label
    assert "sk-live-1" not in got and "env.sh" not in got


@pytest.mark.parametrize("summary, label", [
    ("cd", "Ran cd"),
    ("export HUB_TOKEN=sk-live-1", "Ran Bash"),
    ("cd customer-private.json", "Ran Bash"),
    ("env HUB_TOKEN=sk-live-1", "Ran Bash"),
])
def test_a_setup_prefix_with_no_work_after_it_names_nothing_it_should_not(summary, label):
    """A bare `cd` is the last resort (`Ran cd`). A setup prefix with no program after it names no program, so it reads
    as the tool itself."""
    assert turns.tool_label({"tool": "Bash", "summary": summary}) == label


def test_a_folded_step_carries_its_label_and_keeps_its_tool_name():
    rows = [("tool", {"tool": "Bash", "status": "started", "item_id": "b1", "summary": "npm"}, "t1"),
            ("tool", {"tool": "Bash", "status": "completed", "item_id": "b1"}, "t2")]
    [step] = turns.build_steps(rows)
    assert (step["tool"], step["label"], step["text"]) == ("Bash", "Ran npm", "")


def test_a_hub_phrase_is_the_longest_command_it_starts_with():
    assert turns.tool_label({"tool": "Bash", "summary": "hub approval read-back"}) == "Preparing approvals for your yes"
    assert turns.tool_label({"tool": "mcp__hub__hub_approval_read_back"}) == "Preparing approvals for your yes"
    assert turns.tool_label({"tool": "Bash", "summary": "hub approval approve"}) == "Approving"
    assert turns.tool_label({"tool": "Bash", "summary": "hub needs-you list"}) == "Reading the Needs you list"
    assert turns.tool_label({"tool": "Bash", "summary": "hub finance report"}) == "Working in Finance"
    assert turns.tool_label({"tool": "Bash", "summary": "hub meeting search"}) == "Searching"
    assert turns.tool_label({"tool": "Bash", "summary": "hub settings set"}) == "Changing a setting"
    assert turns.tool_label({"tool": "shell", "summary": "hub history"}) == "Reading the chat"
    assert turns.tool_label({"tool": "Bash", "summary": "hub ask"}) == "Asking a teammate"


def test_an_unmapped_hub_command_never_shows_its_words():
    assert turns.tool_label({"tool": "Bash", "summary": "hub release now"}) == "Ran hub"
    assert turns.tool_label({"tool": "Bash", "summary": "hub webhook pause"}) == "Ran hub"
    assert turns.tool_label({"tool": "Bash", "summary": "hub release sk-live-1"}) == "Ran hub"
