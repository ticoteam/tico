"""One open question per topic: a bot asking a person again on a topic it already has an open question about updates
that question (hubdb.open_question, hubdb.fold_question) instead of filing a second card."""

from backend import ask_deadlines, hubdb as H, task_relations as TR, team_rules
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner, setup_attempt  # noqa: F401
from backend.tests.test_mcp import call


def _ask(api, token, question, **extra):
    err, out = call(api, "hub_task_question", {"question": question, "on": "ana", **extra}, token=token)
    assert not err, out
    return out


def _to_ana(api):
    """Every message the bot has sent Ana, and every job queued for anyone: what a new ask would add."""
    with api.app.state.store.read() as c:
        said = c.execute("SELECT count(*) FROM messages WHERE sender_id='bot:ops' AND recipient_id='human:ana'").fetchone()[0]
        jobs = c.execute("SELECT count(*) FROM jobs").fetchone()[0]
    return said, jobs


def _cards(api):
    return [i for i in get(api, "needs-you")["items"] if i["kind"] == "question"]


def test_asked_again_on_the_same_task_it_updates_the_open_question(api):
    """Acceptance 1 and 4: no second card, no new message or run, the latest wording with the earlier ask under it."""
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    work = post(api, "tasks", {"owner": "ops", "title": "Assess the vendor agent", "body": "Is it worth a test?"})
    attempt = claim(api, r)
    first = _ask(api, attempt["token"], "Run the vendor trial once?")
    before = _to_ana(api)
    again = _ask(api, attempt["token"], "Run the vendor trial this week, with the smaller plan?",
                 options=[{"label": "This week", "then": "Go with This week"}, {"label": "Next month", "then": "Go with Next month"}])
    assert again["folded"] and not first["folded"]
    assert again["task"]["id"] == first["task"]["id"] and again["question"]["id"] == first["question"]["id"]
    assert _to_ana(api) == before, "nothing new was sent and no run was queued"
    cards = [i for i in _cards(api) if i["id"] == first["task"]["id"]]
    assert len(cards) == 1 and len(_cards(api)) == 1
    card = cards[0]
    assert card["title"] == "Run the vendor trial this week, with the smaller plan?"
    assert "Asked before:\n- Run the vendor trial once?" in card["body"]
    assert card["ask"]["refs"]["questions"][0]["question"] == card["title"]
    assert [o["label"] for o in card["ask"]["refs"]["questions"][0]["options"]] == ["This week", "Next month"]
    assert [e["question"] for e in card["ask"]["refs"]["earlier"]] == ["Run the vendor trial once?"]
    with api.app.state.store.read() as c:
        assert TR.parent_of(c, first["task"]["id"]) == work["id"]
        assert c.execute("SELECT count(*) FROM tasks WHERE owner_id='bot:ops' AND title LIKE 'Run the vendor trial%'"
                         ).fetchone()[0] == 1
        folded = c.execute("SELECT detail FROM events WHERE action=? AND target=?",
                           (H.QUESTION_FOLDED, first["task"]["id"])).fetchall()
        assert len(folded) == 1 and H._json(folded[0][0], {})["asks"] == 2
    # A third ask keeps every earlier wording, newest first in the body.
    third = _ask(api, attempt["token"], "Run the vendor trial in November?")
    assert third["folded"] and third["task"]["id"] == first["task"]["id"]
    card = next(i for i in _cards(api) if i["id"] == first["task"]["id"])
    assert card["body"].index("this week") < card["body"].index("trial once?")


def test_one_topic_key_joins_questions_filed_under_different_tasks(api):
    """BotOps' case: a backup model for each bot, asked from each bot's own task, is one question for the person."""
    _, _, attempt = setup_attempt(api)
    builder = post(api, "tasks", {"owner": "ops", "title": "Builder stops at the usage limit", "body": "No backup."})
    analyst = post(api, "tasks", {"owner": "ops", "title": "Analyst stops at the usage limit", "body": "No backup."})
    first = _ask(api, attempt["token"], "Give Builder a backup model?", parent=builder["id"], topic="Backup model")
    second = _ask(api, attempt["token"], "Give Builder and Analyst a backup model?", parent=analyst["id"],
                  topic="backup-model")
    assert second["folded"] and second["task"]["id"] == first["task"]["id"]
    assert [i["title"] for i in _cards(api)] == ["Give Builder and Analyst a backup model?"]
    # Acceptance 2: a different topic, or no topic under another task, still opens its own question.
    billing = _ask(api, attempt["token"], "Move the team to yearly billing?", parent=analyst["id"], topic="billing")
    plain = _ask(api, attempt["token"], "Retire the old Analyst prompt?", parent=analyst["id"])
    loose = _ask(api, attempt["token"], "Archive the September drafts?")
    assert not any(q["folded"] for q in (billing, plain, loose))
    assert len({q["task"]["id"] for q in (first, billing, plain, loose)}) == 4
    assert len(_cards(api)) == 4


def test_a_second_decision_on_the_same_task_takes_its_own_topic(api):
    """Under one task, a keyed question and an unkeyed one are different topics; two different keys are too."""
    _, _, attempt = setup_attempt(api)
    work = post(api, "tasks", {"owner": "ops", "title": "Launch the autumn page", "body": "Copy and banner."})
    ship = _ask(api, attempt["token"], "Ship the autumn page today?", parent=work["id"])
    banner = _ask(api, attempt["token"], "Which banner?", parent=work["id"], topic="banner",
                  options=[{"label": "A", "then": "Go with A"}, {"label": "B", "then": "Go with B"}])
    copy = _ask(api, attempt["token"], "Shorter copy?", parent=work["id"], topic="copy")
    assert not any(q["folded"] for q in (ship, banner, copy))
    assert len({q["task"]["id"] for q in (ship, banner, copy)}) == 3
    again = _ask(api, attempt["token"], "Which banner, now that both are final?", parent=work["id"], topic="banner",
                 options=[{"label": "A", "then": "Go with A"}, {"label": "B", "then": "Go with B"}])
    assert again["folded"] and again["task"]["id"] == banner["task"]["id"]


def test_once_answered_or_closed_the_next_question_on_the_topic_opens_a_new_one(api):
    """Acceptance 3."""
    _, _, attempt = setup_attempt(api)
    first = _ask(api, attempt["token"], "Give Builder a backup model?", topic="backup-model")
    post(api, f"tasks/{first['task']['id']}/answers",
         {"target": {"comment": first["question"]["id"]}, "answers": {"decision": ["Yes"]}})
    second = _ask(api, attempt["token"], "Give Analyst a backup model?", topic="backup-model")
    assert not second["folded"] and second["task"]["id"] != first["task"]["id"]
    with api.app.state.store.transaction() as c:
        H.task_close(c, "human:ana", second["task"]["id"], "Not now")
    third = _ask(api, attempt["token"], "Give Support a backup model?", topic="backup-model")
    assert not third["folded"] and third["task"]["id"] not in (first["task"]["id"], second["task"]["id"])


def test_a_private_question_never_joins_a_public_one(api):
    """Not across tasks, and not under the same task either: either way the private words would reach a public card."""
    _, _, attempt = setup_attempt(api)
    work = post(api, "tasks", {"owner": "ops", "title": "Plan the Q3 bonus", "body": "Pool and split."})
    public = _ask(api, attempt["token"], "Approve the Q3 bonus pool?", topic="bonus", parent=work["id"])
    private = _ask(api, attempt["token"], "Approve the Q3 bonus pool for Ben?", topic="bonus", parent=work["id"],
                   private=True)
    elsewhere = _ask(api, attempt["token"], "Approve the Q3 bonus pool for Cara?", topic="bonus", private=True)
    assert not private["folded"] and not elsewhere["folded"]
    assert len({q["task"]["id"] for q in (public, private, elsewhere)}) == 3
    assert private["task"]["private"] and not public["task"]["private"]


def test_a_folded_question_gives_the_person_the_teams_days_from_the_latest_wording(api):
    """The ask's clock restarts on a fold: not expired for its first wording's age, not gone ahead early, gone ahead
    once at the team's days from the latest wording, and not at all when the person spoke after it."""
    _, _, attempt = setup_attempt(api)
    default = {"do": "Yes", "kind": "reversible"}
    first = _ask(api, attempt["token"], "Archive the September drafts?", topic="drafts", if_no_answer=default)
    quiet = _ask(api, attempt["token"], "Archive the spring drafts?", topic="spring", if_no_answer=default)
    with api.app.state.store.transaction() as c:
        old = H.shift(H.now(), days=-(ask_deadlines.EXPIRE_DAYS + 1))
        c.execute("UPDATE messages SET created_at=? WHERE id IN (?,?)", (old, first["question"]["id"], quiet["question"]["id"]))
    again = _ask(api, attempt["token"], "Archive the September and October drafts?", topic="drafts",
                 if_no_answer=default)
    spring = _ask(api, attempt["token"], "Archive the spring and summer drafts?", topic="spring", if_no_answer=default)
    assert again["folded"] and spring["folded"]
    post(api, f"tasks/{spring['task']['id']}/comments", {"text": "Checking with the editor first."})
    ids = {first["question"]["id"], quiet["question"]["id"]}
    with api.app.state.store.transaction() as c:
        done = ask_deadlines.sweep(c)
        assert not ids & set(done["went_ahead"] + done["expired"]), "15 days after the first wording, 0 after the latest"
        asked = H.message(c, first["question"]["id"])
        assert H.ask_clock(asked) == asked["refs"]["asked_again_at"] > asked["created_at"]
        assert not H.ask_older(asked)
        later = H.shift(H.now(), days=team_rules.load(c)["go_ahead_days"], hours=1)
        done = ask_deadlines.sweep(c, at=later)
        assert first["question"]["id"] in done["went_ahead"], "due at the team's days from the latest wording"
        assert quiet["question"]["id"] in done["expired"], "a note leaves the decision open until its deadline"
        assert quiet["question"]["id"] not in done["went_ahead"], "a note never selects the fallback action"


def test_an_answer_picked_on_the_earlier_wording_does_not_settle_the_new_one(api):
    """Ana loads "Archive the September drafts?" and picks Yes; the bot rewords it first; her Yes is refused, the new
    wording stays open, and her answer on the new wording (a pick, or Other alone) is taken."""
    _, _, attempt = setup_attempt(api)
    first = _ask(api, attempt["token"], "Archive the September drafts?", topic="drafts")
    seen = first["question"]["ask"]["questions"][0]["id"]
    again = _ask(api, attempt["token"], "Archive the September and October drafts?", topic="drafts")
    assert again["folded"]
    url = f"tasks/{first['task']['id']}/answers"
    target = {"comment": first["question"]["id"]}
    for stale in ({"answers": {seen: ["Yes"]}}, {"answers": {}, "other": "Yes, archive them"}):
        r = api.post(f"/api/v2/{url}", json={"target": target, **stale}, headers=headers())
        assert r.status_code == 409 and r.json()["error"]["code"] == "changed", r.text
    with api.app.state.store.read() as c:
        assert H.message(c, first["question"]["id"])["answers"] == []
    assert [i["id"] for i in _cards(api)] == [first["task"]["id"]]
    now_id = again["question"]["ask"]["questions"][0]["id"]
    assert now_id != seen
    post(api, url, {"target": target, "answers": {now_id: []}, "other": "Only September"})
    assert _cards(api) == []


def test_a_topic_key_in_any_script_is_kept_and_an_empty_one_is_refused(api):
    _, _, attempt = setup_attempt(api)
    work = post(api, "tasks", {"owner": "ops", "title": "Plan the launch", "body": "Budget and date."})
    budget = _ask(api, attempt["token"], "Raise the launch budget?", parent=work["id"], topic="预算")
    date = _ask(api, attempt["token"], "Move the launch date?", parent=work["id"], topic="发布")
    plain = _ask(api, attempt["token"], "Launch at all?", parent=work["id"])
    again = _ask(api, attempt["token"], "Raise the launch budget by a tenth?", parent=work["id"], topic="预算")
    assert not any(q["folded"] for q in (budget, date, plain)) and again["folded"]
    assert again["task"]["id"] == budget["task"]["id"]
    assert len({q["task"]["id"] for q in (budget, date, plain)}) == 3
    err, out = call(api, "hub_task_question", {"question": "Launch at all, really?", "on": "ana", "parent": work["id"],
                                               "topic": "--"}, token=attempt["token"])
    assert err and out["error"] == "validation", out
