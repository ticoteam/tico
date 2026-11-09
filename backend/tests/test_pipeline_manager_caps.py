"""An active pipeline manager gets actionable escalations through both hourly bot-chat caps."""

from backend import bot_to_bot, hubdb as H, threads
from backend.tests.helpers.pipeline_manager import pipeline
from backend.tests.helpers.threads_delivery import bot_post, delivery, new_thread, queued_job, rows
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401 (each bot's id is its slug)


BUILDER = "bot:ops"
OTHER_BUILDER = "bot:cpo"
MANAGER = "bot:coo"
OTHER_MANAGER = "bot:product-design"


def manager_card(api, type_id, owner=BUILDER, title="Escalation card"):
    with api.app.state.store.transaction() as c:
        return H.task_create(
            c,
            "human:ana",
            title,
            "Synthetic escalation test card.",
            owner,
            type=type_id,
            step="Building",
            lint=False,
            private=False,
        )


def manager_pipeline(api, name, manager="coo"):
    with api.app.state.store.transaction() as c:
        return pipeline(c, manager=manager, name=name)


def fill_general_guard(api, thread_id, count=2):
    for number in range(count):
        bot_post(
            api,
            "bot:cpo",
            thread_id,
            f"Synthetic bot wake {number}",
            to_actor=BUILDER,
            refs={"needs_action": True},
        )


def notes_wakes(api, root_id):
    return rows(
        api,
        "SELECT j.id,j.state FROM jobs j JOIN message_refs m ON m.id=j.message_id "
        "JOIN conversations t ON t.id=m.conversation_id WHERE j.bot_id=? AND j.state IN ('queued','leased') "
        "AND m.ref_wake=? AND coalesce(t.root_id,t.id)=?",
        H.actor_id(MANAGER),
        threads.NOTE_WAKE,
        root_id,
    )


def test_actionable_pipeline_escalation_passes_the_general_guard(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    type_id = manager_pipeline(api, "General-guard pipeline")
    card = manager_card(api, type_id)
    thread = new_thread(api, audience="company", title="General-guard root")
    fill_general_guard(api, thread["id"])

    escalation = bot_post(
        api,
        BUILDER,
        thread["id"],
        "@coo please review this card",
        to_actor=MANAGER,
        refs={"task": card["id"], "needs_action": True},
    )

    row = delivery(api, escalation["id"], MANAGER)
    root_id = rows(api, "SELECT root_id FROM conversations WHERE id=?", thread["id"])[0]["root_id"]
    assert row["state"] == "pending"
    assert escalation["refs"][H.PIPELINE_MANAGER_PATH] == MANAGER
    with api.app.state.store.read() as c:
        assert threads._guard_used(c, root_id) == 2
    assert rows(api, "SELECT id FROM events WHERE action='thread.capped' AND target=?", root_id) == []


def test_mention_only_pipeline_escalation_passes_the_general_guard(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    type_id = manager_pipeline(api, "Mention-only pipeline")
    card = manager_card(api, type_id)
    thread = new_thread(api, audience="company", title="Mention-only root")
    fill_general_guard(api, thread["id"])

    escalation = bot_post(
        api,
        BUILDER,
        thread["id"],
        "@coo please review the card",
        to_actor=OTHER_BUILDER,
        refs={"task": card["id"]},
    )

    assert escalation["refs"][H.PIPELINE_MANAGER_PATH] == MANAGER
    assert delivery(api, escalation["id"], MANAGER)["state"] == "pending"
    root_id = rows(api, "SELECT root_id FROM conversations WHERE id=?", thread["id"])[0]["root_id"]
    assert rows(api, "SELECT id FROM events WHERE action='thread.capped' AND target=?", root_id) == []


def test_plain_pipeline_message_still_waits_under_bot_to_bot(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    thread = new_thread(api, audience="company", title="Plain-message root")
    fill_general_guard(api, thread["id"])

    plain = bot_post(api, BUILDER, thread["id"], "A status line for the manager", to_actor=MANAGER)

    assert H.PIPELINE_MANAGER_PATH not in plain["refs"]
    assert plain["refs"]["quiet"] is True
    assert plain["refs"][bot_to_bot.MARK] == bot_to_bot.RULE
    assert rows(api, "SELECT id FROM notes WHERE message_id=? AND to_actor=?", plain["id"], MANAGER)
    assert rows(api, "SELECT id FROM jobs WHERE message_id=? AND state IN ('queued','leased')", plain["id"]) == []
    assert delivery(api, plain["id"], MANAGER)["state"] != "capped"
    assert (
        rows(
            api,
            "SELECT id FROM events WHERE action='thread.capped' AND target=(SELECT root_id FROM conversations WHERE id=?)",
            thread["id"],
        )
        == []
    )


def test_manager_ceiling_folds_escalations_into_one_notes_wake(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    type_id = manager_pipeline(api, "Fold pipeline")
    card = manager_card(api, type_id)
    thread = new_thread(api, audience="company", title="Fold root")
    sent = [
        bot_post(
            api,
            BUILDER,
            thread["id"],
            f"Manager escalation {number}",
            to_actor=MANAGER,
            refs={"task": card["id"], "needs_action": True},
        )
        for number in range(2)
    ]
    sent.append(
        bot_post(
            api,
            BUILDER,
            thread["id"],
            "@coo please review one more card",
            to_actor=OTHER_BUILDER,
            refs={"task": card["id"]},
        )
    )
    root_id = rows(api, "SELECT root_id FROM conversations WHERE id=?", thread["id"])[0]["root_id"]

    assert all(delivery(api, message["id"], MANAGER)["state"] == "pending" for message in sent)
    assert delivery(api, sent[2]["id"], MANAGER)["mode"] == "defer"
    assert rows(api, "SELECT id FROM notes WHERE message_id=? AND to_actor=?", sent[2]["id"], MANAGER)
    assert len(notes_wakes(api, root_id)) == 1
    assert rows(api, "SELECT id FROM events WHERE action='thread.capped' AND target=?", root_id) == []


def test_seven_manager_escalations_are_kept_once_and_in_order(api):
    type_id = manager_pipeline(api, "Seven-message pipeline")
    cards = {
        BUILDER: manager_card(api, type_id, BUILDER, "First builder card"),
        OTHER_BUILDER: manager_card(api, type_id, OTHER_BUILDER, "Second builder card"),
    }
    thread = new_thread(api, audience="company", title="Seven-message root")
    sent = [
        bot_post(
            api,
            BUILDER if number % 2 == 0 else OTHER_BUILDER,
            thread["id"],
            f"Manager escalation {number}",
            to_actor=MANAGER,
            refs={
                "task": cards[BUILDER if number % 2 == 0 else OTHER_BUILDER]["id"],
                "needs_action": True,
            },
        )
        for number in range(7)
    ]
    ordered = rows(
        api,
        "SELECT id FROM messages WHERE conversation_id=? AND json_valid(refs) "
        "AND json_extract(refs, '$." + H.PIPELINE_MANAGER_PATH + "')=? ORDER BY seq",
        thread["id"],
        MANAGER,
    )
    root_id = rows(api, "SELECT root_id FROM conversations WHERE id=?", thread["id"])[0]["root_id"]

    assert [row["id"] for row in ordered] == [message["id"] for message in sent]
    assert all(delivery(api, message["id"], MANAGER)["state"] == "pending" for message in sent)
    assert all(queued_job(api, message["id"]) == "queued" for message in sent[:-1])
    assert queued_job(api, sent[-1]["id"]) == "canceled"
    assert delivery(api, sent[-1]["id"], MANAGER)["mode"] == "defer"
    assert rows(api, "SELECT id FROM notes WHERE message_id=? AND to_actor=?", sent[-1]["id"], MANAGER)
    assert len(notes_wakes(api, root_id)) == 1
    assert rows(api, "SELECT id FROM events WHERE action='thread.capped' AND target=?", root_id) == []


def test_manager_ceiling_is_shared_across_managers_in_one_root(api):
    type_ids = {
        MANAGER: manager_pipeline(api, "First shared-ceiling pipeline", "coo"),
        OTHER_MANAGER: manager_pipeline(api, "Second shared-ceiling pipeline", "product-design"),
    }
    cards = {
        (manager, builder): manager_card(api, type_ids[manager], builder, f"{manager} card for {builder}")
        for manager in (MANAGER, OTHER_MANAGER)
        for builder in (BUILDER, OTHER_BUILDER)
    }
    thread = new_thread(api, audience="company", title="Shared manager-ceiling root")
    for number in range(6):
        manager = MANAGER if number % 2 == 0 else OTHER_MANAGER
        builder = BUILDER if number % 3 else OTHER_BUILDER
        bot_post(
            api,
            builder,
            thread["id"],
            f"Escalation {number}",
            to_actor=manager,
            refs={"task": cards[(manager, builder)]["id"], "needs_action": True},
        )
    seventh = bot_post(
        api,
        BUILDER,
        thread["id"],
        "Seventh escalation in the shared root",
        to_actor=MANAGER,
        refs={"task": cards[(MANAGER, BUILDER)]["id"], "needs_action": True},
    )
    root_id = rows(api, "SELECT root_id FROM conversations WHERE id=?", thread["id"])[0]["root_id"]

    assert delivery(api, seventh["id"], MANAGER)["mode"] == "defer"
    assert rows(api, "SELECT id FROM notes WHERE message_id=? AND to_actor=?", seventh["id"], MANAGER)
    assert len(notes_wakes(api, root_id)) == 1


def test_other_bot_wake_still_caps_without_changing_the_general_count(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    type_id = manager_pipeline(api, "Other-recipient pipeline")
    card = manager_card(api, type_id)
    thread = new_thread(api, audience="company", title="Other-recipient root")
    fill_general_guard(api, thread["id"])
    root_id = rows(api, "SELECT root_id FROM conversations WHERE id=?", thread["id"])[0]["root_id"]

    other = bot_post(
        api,
        BUILDER,
        thread["id"],
        "Needs-action for another bot",
        to_actor=OTHER_BUILDER,
        refs={"task": card["id"], "needs_action": True},
    )

    assert H.PIPELINE_MANAGER_PATH not in other["refs"]
    assert delivery(api, other["id"], OTHER_BUILDER)["state"] == "capped"
    with api.app.state.store.read() as c:
        assert threads._guard_used(c, root_id) == 2
    assert len(rows(api, "SELECT id FROM events WHERE action='thread.capped' AND target=?", root_id)) == 1


def test_manager_escalations_do_not_inflate_bot_chat_count(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 6)
    type_id = manager_pipeline(api, "Separate-count pipeline")
    cards = {
        BUILDER: manager_card(api, type_id, BUILDER, "Count builder one"),
        OTHER_BUILDER: manager_card(api, type_id, OTHER_BUILDER, "Count builder two"),
    }
    thread = new_thread(api, audience="company", title="Separate-count root")
    for number in range(4):
        builder = BUILDER if number % 2 == 0 else OTHER_BUILDER
        bot_post(
            api,
            builder,
            thread["id"],
            f"Manager escalation {number}",
            to_actor=MANAGER,
            refs={"task": cards[builder]["id"], "needs_action": True},
        )
    bot_post(api, OTHER_BUILDER, thread["id"], "Bot chat one", to_actor=BUILDER, refs={"needs_action": True})
    bot_post(api, BUILDER, thread["id"], "Bot chat two", to_actor=OTHER_BUILDER, refs={"needs_action": True})
    root_id = rows(api, "SELECT root_id FROM conversations WHERE id=?", thread["id"])[0]["root_id"]

    with api.app.state.store.read() as c:
        assert threads._manager_guard_used(c, root_id) == 4
        assert threads._guard_used(c, root_id) == 2


def test_missing_manager_or_card_does_not_get_an_exemption(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    type_id = manager_pipeline(api, "Cleared-manager pipeline")
    card = manager_card(api, type_id)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager=NULL WHERE id=?", (type_id,))
    thread = new_thread(api, audience="company", title="Cleared-manager root")
    fill_general_guard(api, thread["id"])
    cleared = bot_post(
        api,
        BUILDER,
        thread["id"],
        "No active manager",
        to_actor=MANAGER,
        refs={"task": card["id"], "needs_action": True},
    )
    assert H.PIPELINE_MANAGER_PATH not in cleared["refs"]
    assert delivery(api, cleared["id"], MANAGER)["state"] == "capped"

    thread_without_card = new_thread(api, audience="company", title="Missing-card root")
    fill_general_guard(api, thread_without_card["id"])
    no_card = bot_post(
        api,
        BUILDER,
        thread_without_card["id"],
        "Forged marker does not exempt this message",
        to_actor=MANAGER,
        refs={"needs_action": True, H.PIPELINE_MANAGER_PATH: MANAGER},
    )
    assert H.PIPELINE_MANAGER_PATH not in no_card["refs"]
    assert delivery(api, no_card["id"], MANAGER)["state"] == "capped"


def test_manager_reply_remains_subject_to_the_general_guard(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    type_id = manager_pipeline(api, "Manager-reply pipeline")
    card = manager_card(api, type_id)
    thread = new_thread(api, audience="company", title="Manager-reply root")
    fill_general_guard(api, thread["id"])

    reply = bot_post(
        api,
        MANAGER,
        thread["id"],
        "I reviewed your card",
        to_actor=BUILDER,
        refs={"task": card["id"], "needs_action": True},
    )

    assert H.PIPELINE_MANAGER_PATH not in reply["refs"]
    assert delivery(api, reply["id"], BUILDER)["state"] == "capped"


def test_task_assignment_to_manager_still_uses_a_keeper_notice(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 0)
    thread = new_thread(api, audience="company", title="Assignment-cap root")
    blocked = bot_post(
        api,
        OTHER_BUILDER,
        thread["id"],
        "A normal bot-chat wake",
        to_actor=BUILDER,
        refs={"needs_action": True},
    )
    assert delivery(api, blocked["id"], BUILDER)["state"] == "capped"
    root_id = rows(api, "SELECT root_id FROM conversations WHERE id=?", thread["id"])[0]["root_id"]
    cap_events_before = rows(api, "SELECT id FROM events WHERE action='thread.capped' AND target=?", root_id)

    with api.app.state.store.transaction() as c:
        task = H.task_create(
            c, BUILDER, "Assign to pipeline manager", "Synthetic task.", BUILDER, lint=False, private=False
        )
        reassigned = H.task_update(c, BUILDER, task["id"], owner=MANAGER)
        notices = [
            message
            for message in H.messages(c, reassigned["conversation_id"])
            if message["sender_id"] == H.KEEPER
            and message["recipient_id"] == MANAGER
            and message["body"] == f"Open: {task['title']}"
        ]
        assert len(notices) == 1
        assert not c.execute(
            "SELECT 1 FROM message_deliveries WHERE message_id=? AND state='capped'", (notices[0]["id"],)
        ).fetchone()

    assert rows(api, "SELECT id FROM events WHERE action='thread.capped' AND target=?", root_id) == cap_events_before
