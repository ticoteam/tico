"""Pipeline stats (backend/pipeline_stats.py): per-step counts over exactly the tickets the reader may open, snoozed
ones included unless asked, closed ones never; and a queued job that can never be handed out reported dead, not queued."""

import uuid

from backend import health
from backend import hubdb as H
from backend import pipeline_stats
from backend.pipeline_stats import cause_of
from backend.tests.test_api import api, assign, claim, get, post, ready, restrict, runner  # noqa: F401  (api is a fixture)


def board(api):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Builds", [{"name": "On Deck", "status": "open"},
                                                    {"name": "Review", "status": "review"},
                                                    {"name": "Done", "status": "done"}], numbered=True)
    return typ["id"], {s["name"]: s["id"] for s in typ["steps"]}


def ticket(api, typ, step, owner="ops", private=False):
    ask = uuid.uuid4().hex[:8]
    return post(api, "tasks", {"owner_id": owner, "title": f"Build {ask}", "body": f"Build {ask}.", "type": typ, "step": step,
                               "private": private})


def stats(api, token="ana-test", query=""):
    return get(api, "pipeline?type=Builds" + (f"&{query}" if query else ""), token)


def step(report, name):
    return next(s for s in report["steps"] if s["name"] == name)


def test_counts_follow_the_board_snoozed_in_closed_out_private_only_for_its_readers(api):
    typ, steps = board(api)
    old = ticket(api, typ, steps["On Deck"])
    snoozed = ticket(api, typ, steps["On Deck"], owner="cpo")
    private = ticket(api, typ, steps["Review"], private=True)
    reviewed = ticket(api, typ, steps["Review"])
    done = ticket(api, typ, steps["Review"])
    child = post(api, "tasks", {"owner_id": "cpo", "title": "Review it", "body": "Review it.",
                                "relations": [{"task": reviewed["id"], "kind": "parent"}]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_events SET created_at=? WHERE task_id=? AND field='step'", (H.shift(H.now(), hours=-30), old["id"]))
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), days=3), snoozed["id"]))
        c.execute("UPDATE tasks SET status='closed', step_id=? WHERE id=?", (steps["Done"], done["id"]))
        c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,created_at) VALUES(?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()), reviewed["id"], "pr", "https://github.com/acme/app/pull/7", "PR #7", "open",
                   H.shift(H.now(), hours=-5)))

    shown = stats(api)          # Ana requested every ticket, the private one included
    assert shown["type"]["name"] == "Builds" and shown["total"] == 4 and shown["truncated"] is False
    assert [s["name"] for s in shown["steps"]] == ["On Deck", "Review"]          # Done is finished, never counted
    deck, review = step(shown, "On Deck"), step(shown, "Review")
    assert deck["count"] == 2 and deck["snoozed"] == 1 and deck["oldest"] == old["number"]
    assert 29.9 <= deck["oldest_hours"] <= 30.1 and deck["median_hours"] < deck["oldest_hours"]
    assert deck["by_owner"] == {"bot:ops": 1, "bot:cpo": 1}
    assert review["count"] == 2 and sorted(review["ids"]) == sorted([private["number"], reviewed["number"]])
    assert done["number"] not in review["ids"]
    # The reviewed ticket waits on its open child's owner; the private one on nothing but its own owner.
    assert review["waits_on"] == {"bot": 1, "person": 0, "external": 0, "nothing": 1}
    assert review["waits_on_actor"] == {child["owner_id"]: 1}
    assert review["prs"]["open"] == 1 and 4.9 <= review["prs"]["oldest_hours"] <= 5.1

    # Cara is on none of them: the private ticket is not hers to count, or to see by number.
    hers = stats(api, "cara-test")
    assert hers["total"] == 3 and step(hers, "Review")["count"] == 1
    assert private["number"] not in step(hers, "Review")["ids"] and step(hers, "Review")["prs"]["open"] == 1

    # Filters: snoozed hidden, by owner, and a type named outright.
    assert step(stats(api, query="snoozed=hide"), "On Deck")["count"] == 1
    assert step(stats(api, query="owner=cpo"), "On Deck")["count"] == 1 and stats(api, query="owner=cpo")["total"] == 1
    assert get(api, "pipeline")["type"]["name"] == "Engineering"         # the engineering board unless one is named
    assert get(api, "pipeline?snoozed=maybe", expected=422)["error"]["code"] == "snoozed"
    assert get(api, "pipeline?since=30d", expected=422)["error"]["code"] == "since"


def test_a_queued_job_behind_an_unreviewed_interrupted_run_is_dead_not_queued(api):
    board(api)
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner_id": "ops", "title": "Ship the fix", "body": "Please ship it."})
    attempt = claim(api, r)
    with api.app.state.store.transaction() as c:
        # The computer restarted mid-run: the run may have acted, so its task waits for a person's review.
        c.execute("UPDATE attempts SET state='interrupted', finished_at=?, result=? WHERE id=?",
                  (H.now(), '{"text": "Runner restarted during execution"}', attempt["id"]))
        c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (attempt["job_id"],))
    post(api, f"tasks/{task['id']}/comments", {"text": "Any news?", "needs_action": True})
    with api.app.state.store.read() as c:
        dead = [r[0] for r in c.execute("SELECT id FROM jobs WHERE bot_id='ops' AND state='queued'")]
    assert len(dead) == 1
    assert claim(api, r) is None                      # never handed out while the interrupted run waits

    ops = stats(api)["queues"]["ops"]
    assert ops["dead"] == 1 and ops["dead_ids"] == dead and ops["queued"] == 0 and ops["uncertain"] == 1
    failures = stats(api)["failures"]
    assert failures["total"] == 1 and failures["by_cause"]["restart"]["n"] == 1 and failures["by_bot"] == {"ops": {"restart": 1}}

    # A person's own chat is never held by it: that one is queued.
    post(api, "chat/ops", {"text": "Separate question"})
    assert stats(api)["queues"]["ops"]["queued"] == 1 and stats(api)["queues"]["ops"]["dead"] == 1
    # A bot's queue and failures are only for those who may read its runs.
    assert "ops" in stats(api, "cara-test")["queues"]
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    hidden = stats(api, "cara-test")
    assert "ops" not in hidden["queues"] and hidden["failures"]["total"] == 0
    assert get(api, "pipeline?bot=ops", "cara-test", expected=404)["error"]["code"] == "not_found"
    assert stats(api, query="bot=ops")["queues"]["ops"]["dead"] == 1


def test_health_and_pipeline_count_a_bots_queue_the_same_way(api):
    board(api)
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner_id": "ops", "title": "Ship the fix", "body": "Please ship it."})
    attempt = claim(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='interrupted', finished_at=?, result=? WHERE id=?",
                  (H.now(), '{"text": "Runner restarted during execution"}', attempt["id"]))
        c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (attempt["job_id"],))
    post(api, f"tasks/{task['id']}/comments", {"text": "Any news?", "needs_action": True})
    post(api, "chat/ops", {"text": "Separate question"})     # the one job that can start
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'", (H.shift(H.now(), hours=-2),))
    pipe = stats(api, query="bot=ops")["queues"]["ops"]
    assert pipe["queued"] == 1 and pipe["dead"] == 1

    # Health's row for the bot carries Pipeline's split, not the sum of both: 1 queued, 1 behind the interrupted run.
    with api.app.state.store.read() as c:
        _, slow, stuck = health._waiting(c, {r["runner_id"]})
    rows = [row for row in slow + stuck if row["bot_id"] == "ops"]
    assert rows and all((row["queued"], row["dead"]) == (pipe["queued"], pipe["dead"]) for row in rows)


def test_unreadable_newer_failures_never_hide_older_readable_ones(api, monkeypatch):
    board(api)
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    failed = {}
    for name, private, age in (("open", False, 3), ("private", True, 1)):
        post(api, "tasks", {"owner_id": "ops", "title": f"Fix {name}", "body": "Please fix it.", "private": private})
        attempt = claim(api, r)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE attempts SET state='failed', finished_at=? WHERE id=?", (H.shift(H.now(), hours=-age), attempt["id"]))
            c.execute("UPDATE jobs SET state='failed' WHERE id=?", (attempt["job_id"],))
        failed[name] = attempt["id"]
    monkeypatch.setattr(pipeline_stats, "ATTEMPT_PAGE", 1)     # the newest page holds only the private run
    hers = stats(api, "cara-test")["failures"]
    assert hers["total"] == 1 and hers["truncated"] is False and hers["complete_after"] is None
    assert stats(api)["failures"]["total"] == 2

    monkeypatch.setattr(pipeline_stats, "ATTEMPT_CAP", 1)      # a stop at the cap says from when counts are whole
    capped = stats(api)["failures"]
    assert capped["total"] == 1 and capped["truncated"] is True
    with api.app.state.store.read() as c:
        assert capped["complete_after"] == c.execute("SELECT finished_at FROM attempts WHERE id=?", (failed["open"],)).fetchone()[0]


def test_failure_causes():
    row = lambda state="failed", result="{}", reason=None, stop=None: {  # noqa: E731
        "state": state, "result": result, "end_reason": reason, "stop_requested_at": stop}
    assert cause_of(row(result='{"limit_source": {"runtime": "codex", "billing": true}}')) == "quota"
    assert cause_of(row(reason="Model error (429): You exceeded your current quota")) == "quota"
    assert cause_of(row(result='{"limit_source": {"runtime": "claude", "kind": "capacity"}}')) == "capacity"
    assert cause_of(row("interrupted")) == "restart"
    assert cause_of(row("expired")) == "timeout"
    assert cause_of(row(reason="Process exited (exit code 1)")) == "crash"
    assert cause_of(row(reason="Process exited (exit code 1)", stop="human:ana")) == "stopped"


def test_a_held_or_dismissed_run_lists_its_decision_beside_the_bots_queue(api):
    """The Dev 563 shape on Pipeline: a run held for review, then dismissed by BotOps with a note; the row reads the
    decision record, names who and when, and a later decision replaces it. No decision, no row."""
    from backend.tests.test_api import expire
    from backend.tests.test_run_view import DEV_563_NOTE, botops_decides, task_attempt
    typ, _ = board(api)
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    _, attempt = task_attempt(api, machine, "Build the QA waiver")
    expire(api, attempt["id"])
    assert claim(api, machine) is None
    assert stats(api)["queues"]["ops"]["decisions"] == []          # held, but nobody has decided yet

    botops_decides(api, attempt["job_id"], attempt["id"], "dismiss", DEV_563_NOTE, H.now())
    [row] = stats(api)["queues"]["ops"]["decisions"]
    assert (row["job"], row["run"], row["current"], row["state"], row["decision"], row["by"], row["by_kind"],
            row["note"]) == (attempt["job_id"], attempt["id"], True, "canceled", "dismiss", "bot:botops", "botops",
                             DEV_563_NOTE)
    assert row["label"].startswith("Cancelled: dismissed by BotOps at ") and row["label"].endswith("Z, " + DEV_563_NOTE)

    with api.app.state.store.transaction() as c:
        c.execute("UPDATE job_recovery SET decision='resume', actor='human:ana', note='Run it again after all, please.' "
                  "WHERE job_id=?", (attempt["job_id"],))
    [row] = stats(api)["queues"]["ops"]["decisions"]
    assert (row["decision"], row["by"], row["by_kind"]) == ("resume", "human:ana", "person")
    assert ": resumed by " in row["label"] and row["label"].endswith(", Run it again after all, please.")

    # The resume runs a new try that stops undecided: the job is held again, but the row is the earlier decision's
    # history, never "held" beside it, and it says only what was decided.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='queued' WHERE id=?", (attempt["job_id"],))
    ready(api, machine, ["ops"])
    again = claim(api, machine)
    assert again["job_id"] == attempt["job_id"]
    post(api, f"attempts/{again['id']}/started", {"thread_id": "again"}, machine["token"])
    expire(api, again["id"])
    assert claim(api, machine) is None
    [row] = stats(api)["queues"]["ops"]["decisions"]
    assert (row["run"], row["current"], row["state"]) == (attempt["id"], False, None)
    assert row["label"].startswith("Resumed by ") and row["label"].endswith(", Run it again after all, please.")

    # Outside the window it is no longer listed; a reader who cannot read the bot never sees it.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE job_recovery SET created_at=? WHERE job_id=?", (H.shift(H.now(), days=-2), attempt["job_id"]))
    assert stats(api)["queues"].get("ops", {}).get("decisions", []) == []


def test_a_decisions_note_is_left_out_for_a_reader_who_cannot_open_the_runs_conversation(api):
    """A person's direct message to the bot is theirs: another reader of the bot counts the held job and sees the
    decision, who and when, but never the decider's note, which may tell what the run was for."""
    from backend.tests.test_api import expire
    from backend.tests.test_run_view import DEV_563_NOTE, botops_decides
    board(api)
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Run the payroll export."})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "dm"}, machine["token"])
    expire(api, attempt["id"])
    assert claim(api, machine) is None
    botops_decides(api, attempt["job_id"], attempt["id"], "dismiss", DEV_563_NOTE, H.now())
    [row] = stats(api)["queues"]["ops"]["decisions"]
    assert row["note"] == DEV_563_NOTE and row["label"].endswith(DEV_563_NOTE)
    [row] = stats(api, token="cara-test")["queues"]["ops"]["decisions"]
    assert (row["job"], row["decision"], row["by_kind"], row["note"]) == (attempt["job_id"], "dismiss", "botops", None)
    assert row["label"].startswith("Cancelled: dismissed by BotOps at ") and row["label"].endswith("Z")


def test_job_queues_privacy_checks_messages_in_one_batch(api, monkeypatch):
    """/status and Operations used to call message_readable per queued job; one readable_messages pass covers them."""
    from backend import task_privacy as privacy

    board(api)
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    for i in range(8):
        post(api, "chat/ops", {"text": f"Queue item {i}"})
    calls = []
    real = privacy.readable_messages

    def once(c, actor, messages):
        calls.append(len(list(messages)))
        return real(c, actor, messages)

    monkeypatch.setattr(privacy, "readable_messages", once)
    who = api.app.state.store.settings.test_identities["ana-test"]
    with api.app.state.store.read_transaction() as c:
        queues = pipeline_stats.job_queues(c, who, ["ops"])
    assert queues["ops"]["queued"] == 8
    assert calls.count(8) == 1
