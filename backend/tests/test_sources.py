"""Who wrote a line (backend/sources.py; docs/threads.md "Who wrote it"): one rule labels each line from its sender and
its refs, and a keeper line says what made it through refs.source. Unit checks cover each kind, the names a reader may
see, and the scope that stamps keeper writes; the API check reads a Home's timeline; the write-site check names a
built-in's scope on a keeper line. Fictional people only: ana (owner); the bots come from the api fixture.
"""

import pytest

from backend import sources as SRC
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture)

NAMES = {"human:ana": "Ana Lee", "bot:ops": "Ops", "automation:a1": "Nightly sweep"}


@pytest.fixture(autouse=True)
def default_assistant_name():
    yield
    SRC.set_assistant_name(None)


def message(sender, **refs):
    return {"sender_id": sender, "refs": refs}


def test_a_person_line_is_named_for_the_person():
    assert SRC.message_source(NAMES, message("human:ana")) == {"kind": "person", "name": "Ana Lee", "link": None}


def test_a_bot_line_is_named_for_the_bot_and_falls_back_to_its_slug():
    assert SRC.message_source(NAMES, message("bot:ops")) == {"kind": "bot", "name": "Ops", "link": None}
    assert SRC.message_source({}, message("bot:ops"))["name"] == "ops"


def test_the_assistant_writing_for_a_person_is_named_for_that_person():
    row = message(SRC.KEEPER, source=SRC.ASSISTANT_FOR, **{"for": "human:ana"})
    assert SRC.message_source(NAMES, row, assistant="Atlas") == {
        "kind": "assistant_for", "name": "Ana's Atlas", "link": None}
    bot = message("bot:ops", **{"for": "human:ana"})
    assert SRC.message_source(NAMES, bot, assistant="Atlas")["kind"] == "assistant_for"


def test_a_person_the_reader_may_not_see_is_not_named_in_the_assistant_line():
    row = message("bot:ops", **{"for": "human:ben"})
    assert SRC.message_source(NAMES, row, assistant="Atlas") == {"kind": "assistant_for", "name": "Atlas", "link": None}


def test_an_automation_line_names_the_rule_and_links_to_it():
    row = message(SRC.KEEPER, source="automation:a1")
    assert SRC.message_source(NAMES, row) == {"kind": "automation", "name": "Nightly sweep",
                                              "link": "#/automations/a1"}
    assert SRC.message_source({}, row)["name"] == "Automation"


def test_a_builtin_key_names_the_built_in():
    assert SRC.message_source({}, message(SRC.KEEPER, source="follow-up-timer")) == {
        "kind": "builtin", "name": "Follow-up timer", "link": None}


def test_system_keys_and_unnamed_keeper_lines_are_system_lines():
    assert SRC.message_source({}, message(SRC.KEEPER, source="system:update"))["kind"] == "system"
    assert SRC.message_source({}, message(SRC.KEEPER)) == {"kind": "system", "name": "", "link": None}


def test_a_keeper_line_on_a_task_is_a_task_update_unless_a_source_names_it():
    assert SRC.message_source({}, message(SRC.KEEPER, task="t1")) == {
        "kind": "task_update", "name": "Task update", "link": None}
    assert SRC.message_source({}, message(SRC.KEEPER, task="t1", source="stuck-check"))["kind"] == "builtin"


def test_refs_stored_as_text_are_read_the_same_way():
    assert SRC.message_source({}, {"sender_id": SRC.KEEPER, "refs": '{"task": "t1"}'})["kind"] == "task_update"
    assert SRC.message_source({}, {"sender_id": SRC.KEEPER, "refs": "not json"})["kind"] == "system"


def test_history_rows_use_their_data_and_have_no_task_rule():
    assert SRC.actor_source({}, SRC.KEEPER, {"source": "rule-check"})["kind"] == "builtin"
    assert SRC.actor_source({}, SRC.KEEPER, {"task": "t1"})["kind"] == "system"
    assert SRC.source_of(NAMES, {"actor": "human:ana", "detail": {}})["kind"] == "person"
    assert SRC.source_of({}, {"actor": SRC.KEEPER, "data": {"source": "automation:a1"}})["kind"] == "automation"


def test_a_scope_stamps_keeper_writes_and_keeps_a_source_already_set():
    assert SRC.scope() is None
    assert SRC.stamp({}, SRC.KEEPER) == {}
    with SRC.writing_as("follow-up-timer"):
        assert SRC.stamp({}, SRC.KEEPER) == {"source": "follow-up-timer"}
        assert SRC.stamp({}, "human:ana") == {}
        assert SRC.stamp({"source": "stuck-check"}, SRC.KEEPER) == {"source": "stuck-check"}
        assert SRC.stamp_data(None, SRC.KEEPER) == {"source": "follow-up-timer"}
        assert SRC.stamp_data(["not", "a", "dict"], SRC.KEEPER) == ["not", "a", "dict"]
    assert SRC.scope() is None


def test_a_nested_scope_names_its_own_key_and_the_decorator_wraps_a_call():
    @SRC.scoped("rule-check")
    def write():
        return SRC.scope()

    with SRC.writing_as("system:update"):
        assert write() == "rule-check"
        assert SRC.scope() == "system:update"


def test_the_install_assistant_name_is_used_with_a_default():
    SRC.set_assistant_name("  Atlas ")
    assert SRC.assistant_name() == "Atlas"
    SRC.set_assistant_name("")
    assert SRC.assistant_name() == "Assistant"


def test_a_keeper_line_written_in_a_built_in_scope_names_the_built_in(api):
    with api.app.state.store.transaction() as c:
        with SRC.writing_as("earlier-messages"):
            named = H.say(c, H.KEEPER, "bot:ops", "Earlier messages were sent", kind="notice")
        plain = H.say(c, H.KEEPER, "bot:ops", "A line with no scope", kind="notice")
        named_row = H.message(c, named["id"])
        plain_row = H.message(c, plain["id"])
    assert named_row["refs"]["source"] == "earlier-messages"
    assert "source" not in plain_row["refs"]
    assert SRC.message_source({}, named_row)["name"] == "Earlier messages"
    assert SRC.message_source({}, plain_row)["kind"] == "system"


def test_a_home_timeline_says_who_wrote_each_line(api):
    thread = api.get("/api/v2/threads/home/bot:ops", headers=headers()).json()["id"]
    with api.app.state.store.transaction() as c:
        H.say(c, "human:ana", "bot:ops", "Can you check the build?", conversation_id=thread)
        with SRC.writing_as("follow-up-timer"):
            H.say(c, H.KEEPER, "bot:ops", "Follow up on the build", kind="notice", conversation_id=thread)
        H.say(c, H.KEEPER, "bot:ops", "The build is green", kind="notice", conversation_id=thread)
    lines = {m["body"]: m for m in api.get(f"/api/v2/threads/{thread}/timeline", headers=headers()).json()["messages"]}
    assert lines["Can you check the build?"]["source"]["kind"] == "person"
    assert lines["Can you check the build?"]["source"]["name"]
    assert lines["Follow up on the build"]["source"] == {"kind": "builtin", "name": "Follow-up timer", "link": None}
    assert lines["The build is green"]["source"]["kind"] == "system"
