"""Lenses (backend/lenses.py, docs/lenses.md): exercises run against the published strategy or the owner's draft, with
one model call and no runner. The model is a fake that records what it was sent and answers a scripted result. Examples
use Acme."""

import json

import pytest

from backend import lenses as L
from backend import strategy as S
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_company import bot, with_strategy_bot
from backend.tests.test_goal_asks import asks, sync
from backend.tests.test_strategy import draft, publish, ready, write

ANSWERS = {"rivals": ["Globex", "Doing it by hand"], "factors": ["Price", "Setup time"],
           "scores": {"columns": ["Us", "Globex", "Doing it by hand"],
                      "rows": [{"factor": "Price", "scores": [4, 2, 5]}, {"factor": "Setup time", "scores": [3, 3, 1]}]}}
CURVE = {"kind": "value_curve", "factors": ["Price", "Setup time"],
         "series": [{"name": "Us", "scores": [4, 3]}, {"name": "Globex", "scores": [2, 3]}]}


@pytest.fixture
def model(monkeypatch):
    """Runs happen inline; the model records what it is sent and answers `state['result']`."""
    state = {"result": {}, "sent": [], "writer": True}

    def write_(system, user):
        state["sent"].append(json.loads(user))
        return state["result"]
    monkeypatch.setattr(L, "spawn", lambda work: work())
    monkeypatch.setattr(L, "writer", lambda c, settings, store: write_ if state["writer"] else None)
    return state


def result(bet_id, **more):
    return {"summary": "Price is where Acme stands out.", "sections": [{"title": "The curve today", "body": "Cheap."}],
            "chart": CURVE, "findings": [{"kind": "supports", "bet_id": bet_id, "text": "Two rivals score 2 on price."}],
            "assumptions": [], "changes": [], "sharpen": ["Globex pricing and plan limits"], **more}


def two_bets(api):
    first = ready(api)
    second = post(api, "strategy/bets", {"statement": "Landlords refer landlords", "rules_out": "Paid ads", "status": "active",
                                         "wrong_if": {"kind": "text", "text": "Referrals stay under 5%", "check_by": "2099-01-01"}})["bet"]
    publish(api)
    return first, second


def start(api, lens="blue-ocean", answers=None, token="ana-test", expected=202, **more):
    """Starts a run; the POST answers `running` and the (inline) job finishes after it, so read the run again."""
    out = post(api, f"lenses/{lens}/runs", {"answers": ANSWERS if answers is None else answers, **more}, token=token,
               expected=expected)
    if expected != 202 or out["status"] != "running":
        return out
    return get(api, f"lenses/runs/{out['id']}", token=token)


def run_row(api, run_id):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM lens_runs WHERE id=?", (run_id,)).fetchone())


def librarian(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
                  "VALUES('librarian','librarian','Librarian','','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config,team,operator) VALUES('librarian','{\"runtime\":\"fake\",\"host\":\"keeper\"}',NULL,'ana')")
    return bot(api, "librarian")


def test_every_shipped_lens_loads_and_the_list_puts_strategy_first_and_hides_the_unlisted(api):
    every = {x["id"]: x for x in L.lenses()}
    assert {"ogsm", "v2mom", "plain"} <= set(every) and not any(every[k]["listed"] for k in ("ogsm", "v2mom", "plain"))
    for token in ("cara-test", bot(api, "ops")):          # a member and a bot read the list
        listed = get(api, "lenses", token=token)["lenses"]
        assert [x["id"] for x in listed] == ["playing-to-win", "blue-ocean", "scenarios", "seven-powers", "good-strategy",
                                             "pre-mortem", "positioning", "where-to-play", "five-forces"]
    assert [x["id"] for x in get(api, "lenses?applies_to=market")["lenses"]] == ["where-to-play", "five-forces"]
    assert get(api, "lenses?applies_to=nope", expected=422)["error"]["code"] == "validation"


def test_a_draft_run_is_invisible_to_everyone_but_the_owner_in_the_api_and_hub_sql(api, model):
    first, _ = two_bets(api)
    model["result"] = result(first["id"])
    strategy_token = with_strategy_bot(api)
    published = start(api)
    mine = start(api, on="draft")
    assert mine["on_draft"] is True and published["on_draft"] is False
    assert {r["id"] for r in get(api, "lenses/runs")["runs"]} == {published["id"], mine["id"]}
    for token in ("ben-test", "cara-test", strategy_token):
        assert [r["id"] for r in get(api, "lenses/runs", token=token)["runs"]] == [published["id"]]
        get(api, f"lenses/runs/{mine['id']}", token=token, expected=404)
        assert get(api, f"lenses/runs/{published['id']}", token=token)["status"] == "done"
        post(api, f"lenses/runs/{mine['id']}/apply", {"index": 0, "revision": 0}, token=token, expected=403)
    sql = lambda token: [r[0] for r in post(api, "sql", {"sql": "SELECT id FROM lens_runs"}, token=token)["rows"]]
    assert set(sql("ana-test")) == {published["id"], mine["id"]} and sql("ben-test") == [published["id"]]


def test_only_the_owner_the_strategy_bot_and_for_market_lenses_the_librarian_start_a_run(api, model):
    first, _ = two_bets(api)
    model["result"] = result(first["id"])
    strategy_token, lib, other = with_strategy_bot(api), librarian(api), bot(api, "ops")
    for token in ("ben-test", "cara-test", other):
        start(api, token=token, expected=403)
        start(api, "where-to-play", {"segments": ["Landlords"]}, token=token, expected=403)
    start(api, token=lib, expected=403)                                 # the Librarian runs market lenses only
    assert start(api, "where-to-play", {"segments": ["Landlords"]}, token=lib)["created_by"] == "bot:librarian"
    assert start(api, token=strategy_token)["created_by"] == "bot:goal-manager"
    # a draft run is the owner's own click
    start(api, on="draft", token=strategy_token, expected=403)
    start(api, "where-to-play", {}, on="draft", token=lib)             # market lenses ignore `on`
    assert get(api, "lenses/runs?lens=where-to-play")["runs"][0]["on_draft"] is False
    assert start(api, "nope", {}, expected=404)["error"]["code"] == "not_found"


def test_skipped_steps_are_accepted_and_told_to_the_model_while_bad_answer_shapes_are_refused(api, model):
    first, _ = two_bets(api)
    model["result"] = result(first["id"], assumptions=["Globex's price assumed from its public plans."])
    run = start(api, answers={"rivals": ["Globex"]})                    # three steps skipped
    assert run["status"] == "done" and run["answers"] == {"rivals": ["Globex"]}
    assert run["result"]["assumptions"] == ["Globex's price assumed from its public plans."]
    steps = {s["key"]: s["answer"] for s in model["sent"][-1]["lens"]["steps"]}
    assert steps["rivals"] == ["Globex"] and steps["errc"].startswith("not answered: assume")
    assert start(api, answers={})["answers"] == {}                      # every step skipped is still a run
    bad = [{"nope": "x"}, {"rivals": "Globex"}, {"rivals": ["x"] * 13}, {"rivals": [7]}, {"errc": "x" * 2001},
           {"scores": {"columns": ["Us"], "rows": [{"factor": "Price", "scores": [6]}]}},
           {"scores": {"columns": ["Us", "Globex"], "rows": [{"factor": "Price", "scores": [3]}]}},
           {"scores": {"columns": ["Us"] * 7, "rows": []}},
           {"scores": {"columns": ["Us"], "rows": [{"factor": "Price", "scores": [True]}]}}]
    for answers in bad:
        assert start(api, answers=answers, expected=422)["error"]["code"] == "invalid", answers
    assert start(api, "scenarios", {"horizon": "Ten years"}, expected=422)["error"]["code"] == "invalid"
    assert start(api, "scenarios", {"horizon": "Three years"})["answers"] == {"horizon": "Three years"}


def test_with_no_model_the_answers_are_saved_and_nothing_runs(api, model):
    two_bets(api)
    model["writer"] = False
    run = start(api)
    assert run["status"] == "no_model" and run["result"] is None and run["finished_at"] and run["answers"] == ANSWERS
    assert get(api, f"lenses/runs/{run['id']}") == run and not model["sent"]


def test_a_strategy_lens_needs_a_published_version_unless_the_owner_runs_it_on_the_draft(api, model):
    model["result"] = result(None)
    assert start(api, expected=422)["error"]["code"] == "no_published"
    write(api, summary="Draft only.", sections={"serve": "Unpublished landlords."})
    run = start(api, on="draft")
    assert run["status"] == "done" and run["strategy_n"] is None
    assert "Unpublished landlords." in json.dumps(model["sent"][-1]["strategy"])
    assert start(api, "where-to-play", {})["status"] == "done"          # a market lens needs no published strategy


def test_the_models_result_is_checked_part_by_part_and_what_fails_is_dropped(api, model):
    first, _ = two_bets(api)
    good = {"summary": "Add a won't-do.", "sections": {"not": "No per-seat pricing."}}
    model["result"] = {
        "summary": "x" * 900,
        "sections": [{"title": "One", "body": "y" * 5000}, {"title": "Empty", "body": " "}] + [{"title": str(i), "body": "b"} for i in range(12)],
        "chart": {"kind": "value_curve", "factors": ["Price"], "series": [{"name": "Us", "scores": [9]}]},
        "findings": [{"kind": "supports", "bet_id": first["id"], "text": "Real bet."},
                     {"kind": "challenges", "bet_id": "not-a-bet", "text": "Made up."},
                     {"kind": "unknown", "bet_id": None, "text": "Not a kind."},
                     {"kind": "gap", "bet_id": None, "text": "About the strategy as a whole."}],
        "assumptions": ["a"] * 14,
        "changes": [{"label": "Add to what we won't do", "payload": {"sections": {"not": "No per-seat pricing."}}},
                    {"label": "A made-up bet", "payload": {"bets": [{"id": "not-a-bet", "status": "retired"}]}},
                    {"label": "Unknown key", "payload": {"summary": "Fine.", "price": 3}},
                    {"label": "No check date", "payload": {"bets": [{"statement": "New", "wrong_if": {"kind": "text", "text": "x"}}]}},
                    {"label": "Active with nothing behind it", "payload": {"bets": [{"statement": "New", "status": "active"}]}},
                    {"label": "", "payload": {"summary": "No label."}},
                    {"label": "Source is the server's", "payload": {**good, "source": "bet_tripped", "lens": "x"}}],
        "sharpen": ["one", "two", "three", "four", "five", "six", "seven"]}
    out = start(api)["result"]
    assert len(out["summary"]) <= L.SUMMARY_MAX and out["chart"] is None                # a bad chart: result kept
    assert len(out["sections"]) == L.SECTIONS_MAX and len(out["sections"][0]["body"]) <= L.BODY_MAX
    assert [f["text"] for f in out["findings"]] == ["Real bet.", "About the strategy as a whole."]
    assert len(out["assumptions"]) == L.ASSUMPTIONS_MAX and len(out["sharpen"]) == L.SHARPEN_MAX
    # Rules out and Wrong if are optional, so a bare active bet is a change like any other.
    assert [c["label"] for c in out["changes"]] == ["Add to what we won't do", "Active with nothing behind it",
                                                     "Source is the server's"]
    assert out["changes"][2]["payload"] == good                                          # source and lens are never the model's
    model["result"] = {"summary": "", "sections": [], "chart": None, "findings": [], "changes": []}
    empty = start(api)
    assert empty["status"] == "failed" and empty["error"]["code"] == "empty"
    model["result"] = {"chart": {"kind": "matrix_2x2"}, "summary": "Kept without its chart."}
    assert start(api)["result"]["chart"] is None
    model["result"] = {"summary": "Rows.", "chart": {"kind": "power_table", "rows": [
        {"bet_id": "not-a-bet", "bet": "Landlords refer landlords", "power": "network", "how": "h", "when": "w"}]}}
    assert start(api, "seven-powers", {})["result"]["chart"]["rows"][0]["bet_id"] is None


def test_a_strategy_bot_run_files_exactly_one_strategy_draft_proposal_and_never_writes_the_draft(api, model):
    first, second = two_bets(api)
    # above Passive, so the owner is asked
    strategy_token = with_strategy_bot(api, level=2)
    before = draft(api)
    model["result"] = result(first["id"], changes=[
        {"label": "Retire Bet 2", "payload": {"bets": [{"id": second["id"], "status": "retired"}]}},
        {"label": "Sharper summary", "payload": {"summary": "Acme runs rentals with bots for small landlords."}},
        {"label": "Won't do", "payload": {"sections": {"not": "No per-seat pricing."}}}])
    run = start(api, token=strategy_token)
    assert run["status"] == "done" and run["proposal_id"]
    pending = [p for p in get(api, "goal-proposals")["proposals"] if p["kind"] == "strategy_draft"]
    assert [p["id"] for p in pending] == [run["proposal_id"]] and pending[0]["proposed_by"] == "bot:goal-manager"
    payload = pending[0]["payload"]
    assert payload["source"] == "lens" and payload["lens"] == "blue-ocean" and pending[0]["lens_name"] == "Blue Ocean"
    assert payload["summary"].startswith("Acme runs rentals") and payload["sections"] == {"not": "No per-seat pricing."}
    assert payload["bets"] == [{"id": second["id"], "status": "retired"}]
    assert draft(api) == before                                                    # the draft is the owner's to change
    sync(api)
    [ask] = [a for a in asks(api) if a["kind"] == "proposal"]
    assert ask["title"] == "Decide a suggested change to your strategy from the Blue Ocean lens"
    assert "because" not in ask["body"] and "Add to draft writes it to your draft" in ask["body"]
    # No change, no proposal; an owner's run never files one.
    model["result"] = result(first["id"])
    assert start(api, token=strategy_token)["proposal_id"] is None
    model["result"] = result(first["id"], changes=[{"label": "Sharper summary", "payload": {"summary": "Owner's run."}}])
    assert start(api)["proposal_id"] is None
    assert len([p for p in get(api, "goal-proposals")["proposals"] if p["kind"] == "strategy_draft"]) == 1


def test_a_run_sent_as_a_proposal_cannot_also_be_added_to_the_draft(api, model):
    first, second = two_bets(api)
    strategy_token = with_strategy_bot(api, level=2)
    model["result"] = result(first["id"], changes=[
        {"label": "Retire Bet 2", "payload": {"bets": [{"id": second["id"], "status": "retired"}]}}])
    run = start(api, token=strategy_token)
    assert run["proposal_id"]
    before = draft(api)
    out = post(api, f"lenses/runs/{run['id']}/apply", {"index": 0, "revision": before["revision"]}, expected=409)
    assert out["error"]["code"] == "proposed"
    assert draft(api) == before


def test_the_owner_applies_each_change_once_against_the_draft_revision(api, model):
    first, _ = two_bets(api)
    model["result"] = result(first["id"], changes=[
        {"label": "Won't do", "payload": {"sections": {"not": "No per-seat pricing."}}},
        {"label": "Summary", "payload": {"summary": "A sharper summary."}}])
    run = start(api)
    revision = draft(api)["revision"]
    post(api, f"lenses/runs/{run['id']}/apply", {"index": 0, "revision": revision - 1}, expected=409)
    assert run_row(api, run["id"])["applied"] == "[]"
    done = post(api, f"lenses/runs/{run['id']}/apply", {"index": 0, "revision": revision})
    assert done["run"]["applied"] == [0] and done["strategy"]["draft"]["sections"]["not"] == "No per-seat pricing."
    assert done["strategy"]["draft"]["revision"] > revision and done["strategy"]["current"]["n"] == 1   # nothing published
    again = post(api, f"lenses/runs/{run['id']}/apply", {"index": 0, "revision": draft(api)["revision"]}, expected=409)
    assert again["error"]["code"] == "applied"
    post(api, f"lenses/runs/{run['id']}/apply", {"index": 5, "revision": draft(api)["revision"]}, expected=422)
    post(api, f"lenses/runs/{run['id']}/apply", {"index": 1, "revision": draft(api)["revision"]}, token="ben-test", expected=403)
    assert post(api, f"lenses/runs/{run['id']}/apply", {"index": 1, "revision": draft(api)["revision"]})["run"]["applied"] == [0, 1]
    assert draft(api)["summary"] == "A sharper summary."


def test_asking_the_librarian_to_sharpen_files_one_task_per_ask(api, model):
    first, _ = two_bets(api)
    librarian(api)
    model["result"] = result(first["id"], sharpen=["Globex pricing and plan limits", "Review-site complaints about Globex"])
    run = start(api)
    post(api, f"lenses/runs/{run['id']}/sharpen/0", {}, token="ben-test", expected=403)
    filed = post(api, f"lenses/runs/{run['id']}/sharpen/0", {})
    again = post(api, f"lenses/runs/{run['id']}/sharpen/0", {})
    assert filed["task_id"] == again["task_id"] and filed["run"]["sharpen_tasks"] == {"0": filed["task_id"]}
    second = post(api, f"lenses/runs/{run['id']}/sharpen/1", {})
    assert second["task_id"] != filed["task_id"] and second["run"]["sharpen_tasks"].keys() == {"0", "1"}
    post(api, f"lenses/runs/{run['id']}/sharpen/2", {}, expected=422)
    with api.app.state.store.read() as c:
        tasks = [dict(r) for r in c.execute("SELECT title, body FROM tasks WHERE owner='bot:librarian'")]
    assert len(tasks) == 2 and "Globex pricing and plan limits" in tasks[0]["body"] and "Blue Ocean" in tasks[0]["body"]
    assert "market/open-questions" in tasks[0]["body"]


def test_a_run_cut_off_reads_failed_and_start_up_fails_the_running_ones_and_a_late_answer_writes_nothing(api, monkeypatch):
    first, _ = two_bets(api)
    monkeypatch.setattr(L, "spawn", lambda work: None)                          # the run never answers by itself
    monkeypatch.setattr(L, "writer", lambda c, settings, store: lambda system, user: result(first["id"]))
    run = start(api)
    assert run["status"] == "running" and get(api, f"lenses/runs/{run['id']}")["error"] is None
    with api.app.state.store.transaction() as c:                                # older than the stale limit
        c.execute("UPDATE lens_runs SET created_at=? WHERE id=?", (H.shift(H.now(), seconds=-L.STALE_S - 1), run["id"]))
    stale = get(api, f"lenses/runs/{run['id']}")
    assert stale["status"] == "failed" and stale["error"]["code"] == "interrupted"
    with api.app.state.store.transaction() as c:
        L.finish(c, run["id"], result(first["id"]))
    assert run_row(api, run["id"])["result"] is None and get(api, f"lenses/runs/{run['id']}")["result"] is None
    fresh, other = start(api), start(api, on="draft")
    with api.app.state.store.transaction() as c:
        L.stop_running(c)
    for found in (fresh, other):
        gone = get(api, f"lenses/runs/{found['id']}")
        assert gone["status"] == "failed" and gone["error"]["code"] == "interrupted" and gone["finished_at"]


def test_a_retried_start_runs_the_model_once_and_a_failing_model_leaves_a_failed_run(api, monkeypatch):
    first, _ = two_bets(api)
    calls = []
    monkeypatch.setattr(L, "spawn", lambda work: work())
    monkeypatch.setattr(L, "writer", lambda c, settings, store: lambda system, user: calls.append(1) or result(first["id"]))
    run = start(api)
    L.run(api.app.state.store, run["id"])                                       # a replayed POST spawns the job again
    assert len(calls) == 1
    def broken(system, user):
        raise RuntimeError("boom")
    monkeypatch.setattr(L, "writer", lambda c, settings, store: broken)
    failed = start(api)
    assert failed["status"] == "failed" and failed["error"]["code"] == "failed" and failed["result"] is None


def test_the_prompt_is_capped_and_carries_the_published_strategy_not_the_draft(api, model):
    first, _ = two_bets(api)
    write(api, summary="Unannounced: a franchise model", sections={"serve": "s" * 3900, "sell": "t" * 3900, "know": "k" * 3900,
                                                                    "not": "n" * 3900})
    model["result"] = result(first["id"])
    run = start(api, answers={"rivals": ["r" * 200] * 12, "errc": "e" * 2000})
    assert run["status"] == "done"
    assert all(len(json.dumps(s)) <= L.PROMPT_CHARS for s in model["sent"])
    assert "Unannounced" not in json.dumps(model["sent"][-1]["strategy"])
    assert [b["id"] for b in model["sent"][-1]["strategy"]["bets"]][0] == first["id"]
    start(api, on="draft")
    assert "Unannounced" in json.dumps(model["sent"][-1]["strategy"]) and len(json.dumps(model["sent"][-1])) <= L.PROMPT_CHARS


def test_a_draft_whose_guide_file_moved_falls_back_to_the_default_guide(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE strategy_versions SET guide='rumelt' WHERE status='draft'")
    view = get(api, "strategy")
    assert view["draft"]["guide"] == "rumelt" and view["label"] == "Bets"
    assert S.guide("rumelt")["id"] == "default" and [g["id"] for g in get(api, "strategy/guides")] == ["default"]


def test_a_market_lens_reads_the_market_definition_and_sizes_and_a_strategy_lens_does_not(api, model):
    from backend.tests.test_market_spend import B, defined, size
    first, second, _ = defined(api)
    size(api, first, 4 * B, accept=True)
    model["result"] = result(None)
    start(api, "where-to-play", {"segments": ["Screening"]})
    spend = model["sent"][-1]["market_spend"]
    assert spend["definition"]["description"] == "Software for small landlords."
    sized = {s["id"]: s for s in spend["segments"]}
    assert sized[first]["spend_usd"] == 4 * B and sized[first]["accepted"] and sized[second]["spend_usd"] is None
    two_bets(api)
    start(api)
    assert model["sent"][-1]["market_spend"] is None
