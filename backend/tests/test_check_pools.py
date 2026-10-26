"""Check pools (Dev 534 A): a check step's pool, filing the check on entry, and the least busy eligible checker."""
import json

from backend import check_pools
from backend import hubdb as H
from backend.store import encode
from backend.tests.test_github import api, get, headers  # noqa: F401
from backend.tests.test_review_gate import HEAD, move, ticket as base_ticket

RECEIPT = f"Tests: base {'c' * 40} head {HEAD}: 9 passed, 0 failed"
# The builder (cpo) runs on Anthropic; rev-a and rev-b on OpenAI; rev-c on Anthropic like the builder.
MODELS = {"cpo": "claude-opus-5-5", "rev-a": "gpt-6-sol", "rev-b": "gpt-6-sol", "rev-c": "claude-haiku-5-5"}


def team(api, seen=None):
    """The reviewer bots, each on a computer seen just now (or at `seen`)."""
    with api.app.state.store.transaction() as c:
        bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cpo", "cmo", "rev-a", "rev-b", "rev-c")}
        H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}]})
        for slug, model in MODELS.items():
            config = {"name": slug, "runtime": "claude" if model.startswith("claude") else "codex", "model": model,
                      "status": "active", **({"profile": "reviewer"} if slug.startswith("rev-") else {})}
            c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES(?,?,?,?) ON CONFLICT(bot) DO UPDATE "
                      "SET config_json=excluded.config_json", (slug, encode(config), "engineering", "ana"))
        c.execute("INSERT INTO runners(id,label,operator,token_hash,created,last_seen) VALUES('r1','box','ana','h',?,?)",
                  (H.now(), seen or H.now()))
        for slug in ("rev-a", "rev-b", "rev-c"):
            c.execute("INSERT INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, "r1", H.now()))


def set_pool(api, task, step, pool, limit=2):
    """Set a step's pool the way Settings > Tasks does: the whole type saved with its steps."""
    kind = get(api, "task-types/" + task["type_id"])["type"]
    steps = [{**{k: s[k] for k in ("id", "name", "status")},
              **({"check_pool": pool, "check_limit": limit} if s["name"] == step else {})} for s in kind["steps"]]
    r = api.post("/api/v2/task-types/" + task["type_id"], headers=headers(), json={"steps": steps})
    assert r.status_code == 200, r.text
    return r.json()["type"]


def ticket(api, receipt):
    """A synthetic PR built by cpo on Anthropic, with head-specific run evidence for the family gate."""
    task = base_ticket(api, receipt)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started,finished,task_id) VALUES(?,?,?,?,?)",
                  ("builder-" + task["id"], "cpo", H.now(), H.now(), task["id"]))
        c.execute("INSERT INTO turn_usage_segments(turn_id,position,model,provider,runtime) "
                  "VALUES(?,0,'claude-opus-5-5','anthropic','claude')", ("builder-" + task["id"],))
    return task


def busy(api, slug, title="Review 1: PR #77"):
    """An open check `slug` holds on another ticket."""
    with api.app.state.store.transaction() as c:
        other = H.task_create(c, "bot:cpo", f"Another change for {slug}", "x", "bot:cpo")
        return H.task_create(c, "bot:cpo", title, "Check it.", "bot:" + slug, parent_id=other["id"])


def checks(api, task):
    with api.app.state.store.read() as c:
        kids = [H.task(c, i) for i in H.TR.child_ids(c, task["id"])]
    return [k for k in kids if k["status"] in H.ACTIVE_STATUSES]


def woken(api, slug, task_id):
    """The queued, unheld job that wakes `slug` on a task, with its age in seconds."""
    with api.app.state.store.read() as c:
        row = c.execute("SELECT j.created FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=? "
                        "AND j.state='queued' AND coalesce(j.held,0)=0 AND json_extract(m.refs_json,'$.task')=?",
                        (slug, task_id)).fetchone()
    return (H.parse_ts(H.now()) - H.parse_ts(row[0])).total_seconds() if row else None


def test_entering_review_1_files_the_check_to_the_least_busy_reviewer_and_wakes_it(api):
    """Acceptance 1: A has 0 open checks and B has 1, so the Review 1 check for head H goes to A, woken at once."""
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-b", "rev-a"])
    busy(api, "rev-b")
    r = move(api, task)
    assert r.status_code == 200, r.text
    [check] = checks(api, task)
    assert check["title"] == "Review 1: PR #412" and check["owner"] == "bot:rev-a"
    assert check["requester"] == "bot:cpo", "filed as the builder's own request, so its verdict comes back to it"
    with api.app.state.store.read() as c:
        head = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='review_head'", (check["id"],)).fetchone()
        filed = c.execute("SELECT actor,note FROM task_events WHERE task_id=? AND field='check_filed'", (task["id"],)).fetchone()
    assert head[0] == HEAD
    assert filed["actor"] == H.KEEPER and "rev-a" in filed["note"]
    age = woken(api, "rev-a", check["id"])
    assert age is not None and age < 60, "A is woken within a minute"
    assert woken(api, "rev-b", check["id"]) is None


def test_pool_candidate_model_details_show_configured_and_actual_families(api):
    """A bot label never stands in for its configured model, and a fallback run is shown separately."""
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {
            "reviewer-xai": {"name": "Reviewer xAI", "runtime": "fake", "status": "active"},
            "pr-reviewer-sol": {"name": "Reviewer GPT", "runtime": "fake", "status": "active"},
        }, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}]})
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES(?,?,?,?)", (
            "reviewer-xai", encode({"runtime": "claude", "model": "claude-haiku-5-5"}), "engineering", "ana"))
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES(?,?,?,?)", (
            "pr-reviewer-sol", encode({"runtime": "codex", "model": "gpt-6-sol"}), "engineering", "ana"))
        c.execute("INSERT INTO turns(id,bot,started,finished) VALUES('fallback-turn','pr-reviewer-sol',?,?)",
                  (H.now(), H.now()))
        c.execute("INSERT INTO turn_usage_segments(turn_id,position,model,provider,runtime) "
                  "VALUES('fallback-turn',0,'claude-opus-5-5','anthropic','claude')")
        candidates = {item["slug"]: item for item in check_pools.candidate_details(c,
                      ["reviewer-xai", "pr-reviewer-sol"])}
    assert candidates["reviewer-xai"]["configured"]["provider"] == "Anthropic"
    assert candidates["reviewer-xai"]["runtime"] is None
    assert candidates["pr-reviewer-sol"]["configured"]["provider"] == "OpenAI"
    assert candidates["pr-reviewer-sol"]["runtime"]["provider"] == "Anthropic"
    assert "Claude Opus" in candidates["pr-reviewer-sol"]["runtime"]["model"]


def test_filed_check_captures_actual_builder_and_checker_families_for_its_head(api):
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a"])
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started,finished,task_id) VALUES('builder-turn','cpo',?,?,?)",
                  (H.now(), H.now(), task["id"]))
        c.execute("INSERT INTO turn_usage_segments(turn_id,position,model,provider,runtime) "
                  "VALUES('builder-turn',0,'claude-opus-5-5','anthropic','claude')")
    assert move(api, task).status_code == 200
    [check] = checks(api, task)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started,finished,task_id) VALUES('checker-turn','rev-a',?,?,?)",
                  (H.now(), H.now(), check["id"]))
        c.execute("INSERT INTO turn_usage_segments(turn_id,position,model,provider,runtime) "
                  "VALUES('checker-turn',0,'gpt-6-sol','openai','codex')")
        check_pools.capture_attempt(c, "checker-turn")
        evidence = c.execute("SELECT * FROM check_head_provenance WHERE check_id=? AND head=?",
                             (check["id"], HEAD)).fetchone()
    assert evidence and evidence["known"] == 1
    assert json.loads(evidence["builder_families"]) == ["anthropic"]
    assert json.loads(evidence["checker_families"]) == ["openai"]


def test_same_family_fallback_verdict_reopens_the_same_check_for_an_independent_checker(api):
    """A GPT-configured reviewer that runs Claude cannot advance an Anthropic-built PR."""
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a", "rev-b"])
    assert move(api, task).status_code == 200
    [check] = checks(api, task)
    assert check["owner"] == "bot:rev-a"
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started,task_id) VALUES('review-turn','rev-a',?,?)", (H.now(), check["id"]))
        H.task_update(c, "bot:rev-a", check["id"], status="done", verdict="approve", verdict_head=HEAD)
        actual = {"input_tokens": 100, "cached_tokens": 0, "output_tokens": 10, "model": "claude-opus-5-5",
                  "provider": "anthropic", "est_cost_usd": 0.01, "billing": "subscription", "runtime": "claude",
                  "harness": "claude", "effort": "high", "profile": "engineering"}
        H.turn_finish(c, H.KEEPER, "review-turn", usage={**actual, "segments": [actual]})
        rerouted = H.task(c, check["id"])
        active = [H.task(c, i) for i in H.TR.child_ids(c, task["id"])]
        active = [child for child in active if child["status"] in H.ACTIVE_STATUSES]
    assert rerouted["id"] == check["id"] and rerouted["owner"] == "bot:rev-b"
    assert rerouted["status"] == "open" and rerouted["verdict"] == "approve"
    assert [child["id"] for child in active] == [check["id"]], "rerouting keeps exactly one active check for the head"


def test_unknown_builder_runtime_waits_instead_of_claiming_independence(api):
    team(api)
    task = base_ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a"])
    assert move(api, task).status_code == 200
    assert checks(api, task) == []
    [wait] = get(api, "tasks/" + task["id"])["task"]["check_waits"]
    assert wait["label"] == "Waiting for a reviewer"
    assert "different model company" in wait["reason"]


def test_with_every_reviewer_at_its_limit_the_check_waits_and_the_first_to_finish_takes_it(api):
    """Acceptance 2: all at the limit, so the check shows "Waiting for a reviewer"; the first one to finish a check
    is given it next. A builder filing it meanwhile is told it exists."""
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a", "rev-b"], limit=1)
    held_a, held_b = busy(api, "rev-a"), busy(api, "rev-b")
    assert move(api, task).status_code == 200
    assert checks(api, task) == []
    [wait] = get(api, "tasks/" + task["id"])["task"]["check_waits"]
    assert wait["label"] == "Waiting for a reviewer" and wait["title"] == "Review 1: PR #412"
    assert "limit" in wait["reason"] and wait["heads"] == [HEAD]
    # The builder asking for the same check is pointed at the one waiting, not given a second.
    r = api.post("/api/v2/tasks", headers=headers(), json={
        "owner": "rev-a", "title": "Review 1: PR #412", "body": f"Check PR #412 head {HEAD}",
        "relations": [{"task": task["id"], "kind": "parent"}]})
    assert r.status_code == 422 and "already exists" in r.text and "waits for a reviewer" in r.text
    # rev-b finishes its other check first: it takes this one.
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:rev-b", held_b["id"], status="done", note="VERDICT: approve")
    [check] = checks(api, task)
    assert check["owner"] == "bot:rev-b" and check["title"] == "Review 1: PR #412"
    assert woken(api, "rev-b", check["id"]) is not None
    assert get(api, "tasks/" + task["id"])["task"]["check_waits"] == []
    # rev-a finishing later finds nothing waiting.
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:rev-a", held_a["id"], status="done", note="VERDICT: approve")
    assert [k["owner"] for k in checks(api, task)] == ["bot:rev-b"]


def test_a_checker_that_finishes_ordinary_work_takes_no_check_and_a_checker_takes_only_checks(api):
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a"], limit=1)
    held = busy(api, "rev-a")
    assert move(api, task).status_code == 200
    with api.app.state.store.transaction() as c:
        # cmo is in no pool: its finished task gives nobody the waiting check.
        plain = H.task_create(c, "bot:cpo", "Write the notes", "x", "bot:cmo")
        H.task_update(c, "bot:cmo", plain["id"], status="done", note="Done.")
    assert checks(api, task) == []
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:rev-a", held["id"], status="done", note="VERDICT: approve")
    assert [k["owner"] for k in checks(api, task)] == ["bot:rev-a"]


def test_no_same_company_check_and_an_offline_reviewer_is_passed_over(api):
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-c"])
    assert move(api, task).status_code == 200
    assert checks(api, task) == [], "rev-c runs on the builder's model company: never a same-company review"
    [wait] = get(api, "tasks/" + task["id"])["task"]["check_waits"]
    assert "different model company" in wait["reason"]
    # Back to In Progress ends the wait; a pool with an offline reviewer gives the check to the online one.
    task = get(api, "tasks/" + task["id"])["task"]
    assert move(api, task, "In Progress").status_code == 200
    assert get(api, "tasks/" + task["id"])["task"]["check_waits"] == []
    set_pool(api, task, "Review 1", ["rev-a", "rev-b", "rev-c"])
    busy(api, "rev-b")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO runners(id,label,operator,token_hash,created,last_seen) VALUES('r2','old','ana','h2',?,?)",
                  (H.now(), H.shift(H.now(), hours=-2)))
        c.execute("UPDATE assignments SET runner_id='r2' WHERE bot='rev-a'")
    task = get(api, "tasks/" + task["id"])["task"]
    assert move(api, task).status_code == 200
    assert [k["owner"] for k in checks(api, task)] == ["bot:rev-b"]


def test_review_2_goes_to_a_bot_other_than_reviewer_1(api):
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a", "rev-b"])
    set_pool(api, task, "Review 2", ["rev-a", "rev-b"])
    busy(api, "rev-b")
    assert move(api, task).status_code == 200
    [first] = checks(api, task)
    assert first["owner"] == "bot:rev-a"
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:rev-a", first["id"], status="done", note=f"VERDICT: approve PR #412 head {HEAD}")
    task = get(api, "tasks/" + task["id"])["task"]
    assert move(api, task, "Review 2").status_code == 200
    [second] = checks(api, task)
    assert second["title"] == "Review 2: PR #412"
    assert second["owner"] == "bot:rev-b", "rev-a has fewer open checks but did Review 1"


def test_one_check_per_head_a_builder_check_already_open_is_kept(api):
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a"])
    with api.app.state.store.transaction() as c:
        mine = H.task_create(c, "bot:cpo", "Review 1: PR #412", f"Check head {HEAD}", "bot:rev-b", parent_id=task["id"])
    task = get(api, "tasks/" + task["id"])["task"]
    assert move(api, task).status_code == 200
    assert [k["id"] for k in checks(api, task)] == [mine["id"]]


def test_pool_setting_is_checked_and_shown(api):
    team(api)
    task = ticket(api, RECEIPT)
    kind = set_pool(api, task, "Review 1", ["bot:rev-a", "rev-a", "rev-b"], limit=3)
    step = next(s for s in kind["steps"] if s["name"] == "Review 1")
    assert step["check_pool"] == ["rev-a", "rev-b"] and step["check_limit"] == 3
    other = next(s for s in kind["steps"] if s["name"] == "Review 2")
    assert other["check_pool"] == [] and other["check_limit"] == check_pools.DEFAULT_LIMIT
    with api.app.state.store.read() as c:
        assert check_pools.pool(c, other["id"]) is None, "a step saved beside a pool does not become a check step"
    # Saving the type without pools keeps them.
    steps = [{k: s[k] for k in ("id", "name", "status")} for s in kind["steps"]]
    r = api.post("/api/v2/task-types/" + task["type_id"], headers=headers(), json={"steps": steps})
    assert next(s for s in r.json()["type"]["steps"] if s["name"] == "Review 1")["check_pool"] == ["rev-a", "rev-b"]
    for bad in ({"check_pool": ["nobody"]}, {"check_limit": 0}, {"check_limit": 21}):
        steps = [{**{k: s[k] for k in ("id", "name", "status")}, **(bad if s["name"] == "Review 1" else {})}
                 for s in kind["steps"]]
        r = api.post("/api/v2/task-types/" + task["type_id"], headers=headers(), json={"steps": steps})
        assert r.status_code == 422, (bad, r.text)


def test_dev_types_get_the_proposed_pools_once_with_the_bots_the_company_has(api):
    with api.app.state.store.transaction() as c:
        bots = {slug: {"name": slug, "runtime": "fake", "status": "active"}
                for slug in ("cpo", "cmo", "reviewer-muse", "pr-reviewer-sol", "qa-muse")}
        H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}]})
        kind = H.type_create(c, H.KEEPER, "Dev tickets", [
            {"name": "In Progress", "status": "doing"}, {"name": "Review 1", "status": "review"},
            {"name": "Review 2", "status": "review"}, {"name": "In QA", "status": "review"},
            {"name": "Post Release QA", "status": "review"}, {"name": "Done", "status": "done"}])
        steps = {s["name"]: s for s in H.type_get(c, kind["id"])["steps"]}
        assert steps["Review 1"]["check_pool"] == ["pr-reviewer-sol", "reviewer-muse"]
        assert steps["Review 2"]["check_pool"] == ["pr-reviewer-sol", "reviewer-muse"]
        assert steps["In QA"]["check_pool"] == ["qa-muse"] and steps["In QA"]["check_limit"] == 2
        assert steps["Post Release QA"]["check_pool"] == [] and steps["In Progress"]["check_pool"] == []
        # A person's change stands: the seed is given once.
        c.execute("UPDATE task_steps SET check_pool=NULL WHERE id=?", (steps["Review 1"]["id"],))
        check_pools.seed(c)
        assert H.type_get(c, kind["id"])["steps"][1]["check_pool"] == []
        assert json.loads(c.execute("SELECT detail_json FROM events WHERE action=? AND target=?",
                                    (check_pools.SEEDED, kind["id"])).fetchone()[0])["pools"]["In QA"] == ["qa-muse"]


def test_a_reviewer_whose_last_run_fell_back_to_the_builders_company_is_passed_over(api):
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a", "rev-b"])
    busy(api, "rev-b")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started,finished) VALUES('t1','rev-a',?,?)", (H.now(), H.now()))
        c.execute("INSERT INTO turn_usage_segments(turn_id,position,model,provider) "
                  "VALUES('t1',0,'claude-haiku-5-5','anthropic')")
    assert move(api, task).status_code == 200
    assert [k["owner"] for k in checks(api, task)] == ["bot:rev-b"]
