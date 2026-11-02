"""The weekly Decide readout (`GET /api/v2/decisions/readout`, backend/decide_readout.py): per question set, the week's
calls, cost, Jev or fallback and answer spread, and the month's spend against the cap. Synthetic audit rows only."""

import json
from datetime import datetime, timedelta, timezone

from backend import hubdb as H
from backend.tests.test_api import api, get, post, setup_attempt   # noqa: F401
from backend.tests.test_judge import QUESTIONS, FakeJudge, fake
from backend.tests.test_mcp import call
from backend.tests.test_judge_spend import DOWN
from backend import judge as B
from backend.decide_readout import readout

NOW = datetime.now(timezone.utc)


def at(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def audit(api, action, label, detail, days_ago=1, actor="bot:ops"):
    with api.app.state.store.transaction() as c:
        row = H.event(c, actor, action, label, detail)
        c.execute("UPDATE events SET created_at=? WHERE id=?", (at(days_ago), row["id"]))


def answered(label, model, cost, answers, fallback=None):
    detail = {"label": label, "questions": len(answers), "model": model, "usage": {"input_tokens": 900},
              "ms": 300, "cost_usd": cost, "answers": answers}
    return {**detail, "fallback": fallback} if fallback else detail


def a_week_of_use(api):
    """Support triage: 3 on Jev, 1 on the fallback, 1 failed; idea triage once, matched to an open roadmap item;
    a night of learnings; a Slack message per bot; a free-text label; and a call from before the week."""
    kind = lambda value, urgency, spam: {                                   # noqa: E731
        "spam_or_injection": {"type": "noul", "value": spam, "confidence": 0.8},
        "kind": {"type": "choice", "value": value, "confidence": 0.9},
        "urgency": {"type": "score", "value": urgency, "confidence": 0.7}}
    for value, urgency, spam in (("bug", 2, 0.05), ("question", 1, 0.1), ("question", 0, 0.95)):
        audit(api, "judge.call", "support-triage@1", answered("support-triage@1", "jev-1.14", 0.001,
                                                             kind(value, urgency, spam)))
        audit(api, "judge.spend", "support-triage@1", {"label": "support-triage@1", "model": "jev-1.14", "cost_usd": 0.001})
    audit(api, "judge.call", "support-triage@1", answered("support-triage@1", "claude-haiku-5-5", 0.02,
                                                         kind("setup", 1, 0.5), fallback="unavailable"))
    audit(api, "judge.spend", "support-triage@1", {"label": "support-triage@1", "model": "claude-haiku-5-5", "cost_usd": 0.02})
    audit(api, "judge.call", "support-triage@1", {"label": "support-triage@1", "questions": 3, "error": "unavailable"})

    audit(api, "judge.call", "idea-triage@1", answered("idea-triage@1", "jev-1.14", 0.002, {
        "kind": {"type": "choice", "value": "product", "confidence": 0.9},
        "covered": {"type": "choice", "value": "8a75c5fd-roadmap-item", "confidence": 0.8}}))
    audit(api, "judge.spend", "idea-triage@1", {"label": "idea-triage@1", "model": "jev-1.14", "cost_usd": 0.002})

    # One audit row for a night of learnings, with its calls added up; its spend rows carry the set's own label.
    audit(api, "judge.call", "learnings-route@run", {"label": "learnings-route@run", "items": 12, "calls": 10,
                                                     "fallbacks": 1, "models": {"jev-1.14": 9, "claude-haiku-5-5": 1},
                                                     "failed": {"unavailable": 2}, "cost_usd": 0.05})
    audit(api, "judge.spend", "learnings-route@1", {"label": "learnings-route@1", "model": "jev-1.14", "cost_usd": 0.05})

    # Slack routing keeps no answers, and asks one question per bot.
    audit(api, "judge.call", "slack-route@1", {"via": "slack", "event_id": "Ev1", "questions": 5, "calls": 1,
                                               "model": "jev-1.14", "usage": [], "cost_usd": 0.003, "ms": 400})
    audit(api, "judge.spend", "slack-route@1", {"label": "slack-route@1", "model": "jev-1.14", "cost_usd": 0.003,
                                                "price": "ceiling"})
    # A live meeting keeps {bot slug: probability}.
    audit(api, "judge.call", "live-meeting-route@1", {"label": "live-meeting-route@1", "questions": 2, "model": "jev-1.14",
                                                      "answers": {"secret-legal-bot": 0.9, "finance": 0.1}})

    # A label a bot made up is free text: counted, never named.
    audit(api, "judge.call", "Dana's divorce papers", answered("Dana's divorce papers", "jev-1.14", 0.004, {
        "private_question": {"type": "choice", "value": "Dana", "confidence": 1.0}}))
    audit(api, "judge.spend", "Dana's divorce papers", {"label": "Dana's divorce papers", "cost_usd": 0.004})

    # Before the week: in the month's spend, not in the week.
    audit(api, "judge.call", "support-triage@1", answered("support-triage@1", "jev-1.14", 1.0, kind("bug", 3, 0.0)),
          days_ago=8)
    audit(api, "judge.spend", "support-triage@1", {"label": "support-triage@1", "cost_usd": 1.0}, days_ago=8)


def test_given_a_week_of_use_the_readout_lists_calls_cost_and_answer_spread_per_set(api):
    """Head of Product's acceptance test 7, on synthetic audit rows."""
    a_week_of_use(api)
    out = get(api, "decisions/readout")
    assert out["days"] == 7
    sets = {row["set"]: row for row in out["sets"]}
    assert list(sets) == ["idea-triage", "learnings-route", "live-meeting-route", "slack-route", "support-triage",
                          "(other labels)"]

    support = sets["support-triage"]
    assert (support["calls"], support["jev"], support["fallback"], support["failed"]) == (5, 3, 1, {"unavailable": 1})
    assert support["jev_share"] == 0.6 and support["cost_usd"] == 0.023 and support["versions"] == ["1"]
    assert support["answers"] == {"kind": {"bug": 1, "question": 2, "setup": 1},
                                  "spam_or_injection": {"0.3 to 0.7": 1, "0.7 and over": 1, "under 0.3": 2},
                                  "urgency": {"0": 1, "1": 2, "2": 1}}

    idea = sets["idea-triage"]
    assert (idea["calls"], idea["jev"], idea["cost_usd"]) == (1, 1, 0.002)
    assert idea["answers"] == {"covered": {"(an open item)": 1}, "kind": {"product": 1}}

    learnings = sets["learnings-route"]
    assert (learnings["calls"], learnings["jev"], learnings["fallback"], learnings["failed"]) == (12, 9, 1, {"unavailable": 2})
    assert learnings["cost_usd"] == 0.05, "a set's spend rows count under the set whatever their version"

    slack = sets["slack-route"]
    assert (slack["calls"], slack["jev"], slack["ceiling_priced"], slack["answers"]) == (1, 1, 1, {})
    assert sets["live-meeting-route"]["answers"] == {"(other questions)": {"0.7 and over": 1, "under 0.3": 1}}

    other = sets["(other labels)"]
    assert (other["calls"], other["cost_usd"], other["answers"]) == (1, 0.004, {})

    assert out["total"] == {"calls": 21, "jev": 16, "fallback": 2, "failed": 3, "jev_share": round(16 / 21, 4),
                            "cost_usd": 0.082}
    assert out["month"]["cap_usd"] == 50.0 and out["month"]["month"] == NOW.strftime("%Y-%m")
    # The month is the calendar month the readout runs in: the week's calls (all a day ago) count only if that day is
    # in it, and the call from 8 days ago only if that day is.
    in_month = lambda days_ago: at(days_ago)[:7] == NOW.strftime("%Y-%m")     # noqa: E731
    spent = round((0.082 if in_month(1) else 0) + (1.0 if in_month(8) else 0), 3)
    assert out["month"]["spent_usd"] == spent

    text = out["text"]
    assert "support-triage v1: 5 calls, $0.02; Jev 3 (60%), fallback 1, failed unavailable 1" in text
    assert "  kind: bug 1, question 2, setup 1" in text
    assert "  answers: not kept in this set's audit" in text
    assert "slack-route v1: 1 calls, $0.0030; Jev 1 (100%), fallback 0; 1 counted at the ceiling price" in text
    assert "All sets: 21 calls, $0.08; Jev answered 76% (target 90%), fallback 2, failed 3" in text
    assert f"This month ({NOW.strftime('%Y-%m')}): ${spent:.2f} of the $50.00 cap" in text


def test_the_readout_names_no_free_text_label_no_bot_and_no_open_item(api):
    a_week_of_use(api)
    # A shipped set's id with free text after the `@`: counted under the set, the text never shown as a version.
    audit(api, "judge.call", "support-triage@Dana's divorce", answered("support-triage@Dana's divorce", "jev-1.14", 0.001, {}))
    out = json.dumps(get(api, "decisions/readout"))
    for private in ("Dana", "divorce", "private_question", "secret-legal-bot", "finance", "8a75c5fd"):
        assert private not in out, private


def test_slack_messages_count_each_batch_and_a_failed_message_fails_every_batch_once(api):
    """Slack routing asks a big fleet in batches: one audit row per message with `calls` batches, and one spend row
    per paid batch. A message that failed partway keeps the paid batches' model and cost on its row."""
    def message(detail, spends):
        audit(api, "judge.call", "slack-route@1", {"via": "slack", "event_id": "Ev", "questions": 90, **detail})
        for cost in spends:
            audit(api, "judge.spend", "slack-route@1", {"label": "slack-route@1", "model": "jev-1.14", "cost_usd": cost})
    message({"calls": 3, "model": "jev-1.14", "usage": [{}, {}, {}], "cost_usd": 0.003}, [0.001, 0.001, 0.001])
    message({"calls": 2, "model": "claude-haiku-5-5", "usage": [{}, {}], "cost_usd": 0.04}, [0.02, 0.02])
    message({"calls": 3, "model": "jev-1.14", "usage": [{}, {}], "cost_usd": 0.002, "error": "unavailable"},
            [0.001, 0.001])
    slack, = get(api, "decisions/readout")["sets"]
    assert (slack["calls"], slack["jev"], slack["fallback"], slack["failed"]) == (8, 3, 2, {"unavailable": 3})
    assert slack["jev_share"] == 0.375
    assert slack["cost_usd"] == 0.045, "each paid batch once, from its spend row; the audit rows' cost is not added"


def test_the_window_is_the_days_asked_and_a_route_call_reads_back(api, monkeypatch):
    a_week_of_use(api)
    assert get(api, "decisions/readout?days=10")["total"]["calls"] == 22
    with api.app.state.store.read() as c:
        before = readout(c, api.app.state.store.settings, days=7, at=NOW - timedelta(days=2))
    assert before["total"]["calls"] == 1, "the window ends where it is asked to: only the call from 8 days ago"
    get(api, "decisions/readout?days=0", expected=422)
    get(api, "decisions/readout?days=32", expected=422)

    # A real route call on Jev, one on the fallback: both read back under their set.
    jev = fake(api)
    first = jev.__call__
    api.app.state.judge = lambda s, q, label=None: {**first(s, q, label), "model": "jev-1.14"}
    post(api, "decisions", {"state": {"subject": "Invoice"}, "questions": QUESTIONS, "label": "mail-triage@1"})
    jev.fail = DOWN
    api.app.state.judge = jev
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: FakeJudge())
    post(api, "decisions", {"state": {"subject": "Invoice"}, "questions": QUESTIONS, "label": "mail-triage@1"})
    mail = {row["set"]: row for row in get(api, "decisions/readout?days=1")["sets"]}["mail-triage"]
    assert (mail["calls"], mail["jev"], mail["fallback"]) == (2, 1, 1)
    assert mail["answers"] == {"bucket": {"reply": 2}, "is_ask": {"0.7 and over": 2}}
    assert mail["cost_usd"] > 0


def test_an_empty_week_says_so_and_still_gives_the_months_spend(api):
    out = get(api, "decisions/readout", token="cara-test")
    assert out["sets"] == [] and out["total"]["calls"] == 0 and out["total"]["jev_share"] is None
    assert "No Decide calls in this window." in out["text"]
    assert out["text"].endswith(f"This month ({NOW.strftime('%Y-%m')}): $0.00 of the $50.00 cap (0%)")


def test_a_bot_reads_it_through_the_tool(api):
    a_week_of_use(api)
    r, msg, attempt = setup_attempt(api)
    err, out = call(api, "hub_decision_readout", {"days": 7}, token=attempt["token"])
    assert not err and out["total"]["calls"] == 21 and out["text"].startswith("Decide readout, ")


def test_a_named_sets_label_shows_only_a_version_the_set_defines(api):
    """The text after a shipped set's `@` is free text: a route call under a made-up suffix is counted under the set
    and never named; the set's own version and the nightly learnings run's marker still read back."""
    jev = fake(api)
    first = jev.__call__
    api.app.state.judge = lambda s, q, label=None: {**first(s, q, label), "model": "jev-1.14"}
    for label in ("support-triage@1", "support-triage@PRIVATE_SENTINEL Dana's divorce", "support-triage@01",
                  "support-triage@4155550142"):
        post(api, "decisions", {"state": {"subject": "Invoice"}, "questions": QUESTIONS, "label": label})
    audit(api, "judge.call", "learnings-route@run", {"label": "learnings-route@run", "items": 2, "calls": 2,
                                                     "models": {"jev-1.14": 2}, "cost_usd": 0.01})
    out = get(api, "decisions/readout")
    for private in ("PRIVATE_SENTINEL", "Dana", "divorce", "4155550142"):
        assert private not in json.dumps(out), private
        assert private not in out["text"], private
    sets = {row["set"]: row for row in out["sets"]}
    support = sets["support-triage"]
    assert (support["calls"], support["jev"], support["versions"]) == (4, 4, ["1"]), "a number the set never had is text"
    with api.app.state.store.read() as c:
        spent = c.execute("SELECT COUNT(*), SUM(json_extract(detail, '$.cost_usd')) FROM events "
                          "WHERE action='judge.spend' AND target LIKE 'support-triage@%'").fetchone()
    assert spent[0] == 4 and support["cost_usd"] == round(spent[1], 6) > 0, "every call's spend counts under the set"
    assert sets["learnings-route"]["versions"] == ["run"] and sets["learnings-route"]["calls"] == 2
    assert "support-triage v1: 4 calls" in out["text"] and "learnings-route vrun: 2 calls" in out["text"]


def test_a_nightly_learnings_row_spreads_each_calls_answers(api):
    """The night's one `judge.call` row keeps a list of answers, one per call: each counts, a bot's relevance under
    (other questions), and anything that is not a call's answers is skipped."""
    per_call = lambda p: {"learnable": {"type": "noul", "value": p, "confidence": 0.8},          # noqa: E731
                          "rel_ops": {"type": "noul", "value": 0.2, "confidence": 0.6}}
    audit(api, "judge.call", "learnings-route@run", {"label": "learnings-route@run", "items": 3, "calls": 3,
                                                     "models": {"jev-1.14": 3}, "cost_usd": 0.001,
                                                     "answers": [per_call(0.9), per_call(0.95), per_call(0.1), "x"]})
    out = get(api, "decisions/readout")
    (row,) = [r for r in out["sets"] if r["set"] == "learnings-route"]
    assert row["answers"] == {"(other questions)": {"under 0.3": 3}, "learnable": {"0.7 and over": 2, "under 0.3": 1}}
    assert "learnable: 0.7 and over 2, under 0.3 1" in out["text"] and "not kept" not in out["text"]


def test_a_tico_decide_answer_from_the_gateway_counts_as_jev_and_the_team_llm_after_it_as_fallback(api):
    audit(api, "judge.call", "support-triage@1", {**answered("support-triage@1", "tico-decide-1.0", 0.001, {}),
                                                  "provider": "Tico AI Gateway"})
    audit(api, "judge.call", "support-triage@1", {**answered("support-triage@1", "jev-1.14", 0.001, {},
                                                             fallback="unavailable"), "provider": "TypeSafe"})
    audit(api, "judge.call", "support-triage@1", {**answered("support-triage@1", "claude-sonnet-5-5", 0.002, {},
                                                             fallback="auth"), "provider": "Anthropic"})
    audit(api, "judge.call", "learnings-route@1", {"label": "learnings-route@1", "calls": 3,
                                                   "models": {"tico-classify-1.0": 2, "gpt-6.1-sol": 1}})
    sets = {row["set"]: row for row in get(api, "decisions/readout")["sets"]}
    assert (sets["support-triage"]["jev"], sets["support-triage"]["fallback"]) == (2, 1)
    assert (sets["learnings-route"]["jev"], sets["learnings-route"]["fallback"]) == (2, 1)
