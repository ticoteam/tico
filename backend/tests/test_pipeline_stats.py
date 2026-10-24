"""Pipeline stats (backend/pipeline_stats.py): per-step counts over exactly the tickets the reader may open, snoozed
ones included unless asked, closed ones never; and a queued job that can never be handed out reported dead, not queued."""

import uuid

from backend import hubdb as H
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
    return post(api, "tasks", {"owner": owner, "title": f"Build {ask}", "body": f"Build {ask}.", "type": typ, "step": step,
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
    child = post(api, "tasks", {"owner": "cpo", "title": "Review it", "body": "Review it.",
                                "relations": [{"task": reviewed["id"], "kind": "parent"}]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_events SET ts=? WHERE task_id=? AND field='step'", (H.shift(H.now(), hours=-30), old["id"]))
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), days=3), snoozed["id"]))
        c.execute("UPDATE tasks SET status='closed', step_id=? WHERE id=?", (steps["Done"], done["id"]))
        c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,created) VALUES(?,?,?,?,?,?,?)",
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
    assert review["waits_on_actor"] == {child["owner"]: 1}
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
    task = post(api, "tasks", {"owner": "ops", "title": "Ship the fix", "body": "Please ship it."})
    attempt = claim(api, r)
    with api.app.state.store.transaction() as c:
        # The computer restarted mid-run: the run may have acted, so its task waits for a person's review.
        c.execute("UPDATE attempts SET state='interrupted', finished=?, result_json=? WHERE id=?",
                  (H.now(), '{"text": "Runner restarted during execution"}', attempt["id"]))
        c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (attempt["job_id"],))
    post(api, f"tasks/{task['id']}/comments", {"text": "Any news?", "needs_action": True})
    with api.app.state.store.read() as c:
        dead = [r[0] for r in c.execute("SELECT id FROM jobs WHERE bot='ops' AND state='queued'")]
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


def test_failure_causes():
    row = lambda state="failed", result="{}", reason=None, stop=None: {  # noqa: E731
        "state": state, "result_json": result, "end_reason": reason, "stop_requested": stop}
    assert cause_of(row(result='{"limit_source": {"runtime": "codex", "billing": true}}')) == "quota"
    assert cause_of(row(reason="Model error (429): You exceeded your current quota")) == "quota"
    assert cause_of(row(result='{"limit_source": {"runtime": "claude", "kind": "capacity"}}')) == "capacity"
    assert cause_of(row("interrupted")) == "restart"
    assert cause_of(row("expired")) == "timeout"
    assert cause_of(row(reason="Process exited (exit code 1)")) == "crash"
    assert cause_of(row(reason="Process exited (exit code 1)", stop="human:ana")) == "stopped"
