"""The team's vision is the Strategy Owner's question to the owner, not a to-do (Tasks spec "Bots ask people a question
with choices", AC-10): a task the Strategy Owner holds with its question on it, no choices. A vision they send is saved
by the Strategy Owner in their words, said on the task, and the question closes; anything else leaves it open."""

from backend import goal_asks as GA
from backend import strategy as S
from backend.store import H
from backend.tests.helpers.api import get, post
from backend.tests.helpers.goal_asks import MANAGER, goal_manager, sync
from backend.tests.helpers.strategy_bot import as_bot

VISION = "Every small landlord runs on bots."
BOT = "strategy-owner-test"


def vision_row(api):
    with api.app.state.store.read() as c:
        row = H._one(c, "SELECT * FROM goal_asks WHERE kind='vision'")
        return row and H.task(c, row["task_id"])


def open_asks(api, task_id):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT r.* FROM requirements r JOIN visits v ON v.id=r.visit_id WHERE v.task_id=? AND v.left_at IS NULL "
                "AND r.withdrawn_at IS NULL AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.requirement_id=r.id)",
                (task_id,),
            )
        ]


def say(api, task_id, text, who="human:ana"):
    with api.app.state.store.transaction() as c:
        H.task_comment(c, who, task_id, text)


def test_the_owner_gets_the_vision_as_the_strategy_owners_question_not_a_to_do(api):
    goal_manager(api, level=2)
    sync(api)
    task = vision_row(api)
    # The Strategy Owner holds it and asks it: the owner's list has the question, and no to-do of theirs.
    assert (task["owner_id"], task["requester_id"], task["title"]) == (MANAGER, MANAGER, GA.VISION_QUESTION)
    [ask] = open_asks(api, task["id"])
    assert (ask["kind"], ask["person_id"], ask["added_by_id"], ask["line"]) == (
        "answer",
        "human:ana",
        MANAGER,
        GA.VISION_LINE,
    )
    assert ask["choices"] is None
    mine = [i for i in get(api, "needs-you")["items"] if i.get("task_id") == task["id"]]
    assert [i["kind"] for i in mine] == ["ask"]
    assert not [i for i in get(api, "needs-you")["items"] if "what this team is for" in str(i.get("title"))]
    # Its why goes with it, as a bot's question carries one.
    detail = get(api, f"tasks/{task['id']}")
    assert GA.VISION_WHY in str(detail)
    # Another pass asks nothing twice.
    assert sync(api)["created"] == [] and vision_row(api)["id"] == task["id"]


def test_a_vision_to_do_an_older_release_filed_is_closed_and_asked_as_the_question(api):
    goal_manager(api, level=2)
    with api.app.state.store.transaction() as c:
        old = H.task_create(
            c, MANAGER, "Tell your Strategy Owner what this team is for", "Write it", "human:ana", lint=False
        )
        c.execute(
            "INSERT INTO goal_asks (kind, ref, owner, task_id, created_at) VALUES ('vision','company','human:ana',?,?)",
            (old["id"], H.now()),
        )
    made = sync(api)
    task = vision_row(api)
    assert task["id"] != old["id"] and task["id"] in made["created"] and old["id"] in made["closed"]
    assert task["owner_id"] == MANAGER and open_asks(api, task["id"])
    with api.app.state.store.read() as c:
        assert H.task(c, old["id"])["status"] == "closed"
    assert old["id"] not in {i.get("task_id") for i in get(api, "needs-you")["items"]}


def test_a_vision_the_owner_sends_is_saved_in_their_words_said_and_the_question_closes(api):
    goal_manager(api, level=2)
    sync(api)
    task = vision_row(api)
    as_bot(api, "strategy-owner")
    say(api, task["id"], f"Our vision: {VISION} Ask me if that is not enough.")
    out = post(api, "strategy/vision", {"task": task["id"], "vision": VISION}, token=BOT)
    assert out["vision"] == VISION
    with api.app.state.store.read() as c:
        assert S.vision(c, editor=True) == VISION
        row = H.task(c, task["id"])
        assert (row["status"], row["closed_by_id"]) == ("closed", MANAGER)
        said = [m["body"] for m in H.task_comments(c, task["id"]) if m["sender_id"] == MANAGER]
    assert any(VISION in body and "Saved as the team's vision" in body for body in said)
    assert task["id"] not in {i.get("task_id") for i in get(api, "needs-you")["items"]}
    # The team has a vision now: a second save is the owner's, on the Strategy page.
    post(api, "strategy/vision", {"task": task["id"], "vision": VISION}, token=BOT, expected=409)


def test_a_question_back_leaves_the_question_open_and_nothing_is_saved(api):
    goal_manager(api, level=2)
    sync(api)
    task = vision_row(api)
    as_bot(api, "strategy-owner")
    say(api, task["id"], "What do you mean by a vision?")
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "open" and not S.vision(c, editor=True)
    assert open_asks(api, task["id"])
    # The Strategy Owner may not save words the owner did not write there, nor a scrap of what they did write.
    post(api, "strategy/vision", {"task": task["id"], "vision": VISION}, token=BOT, expected=422)
    post(api, "strategy/vision", {"task": task["id"], "vision": "a vision"}, token=BOT, expected=422)
    with api.app.state.store.read() as c:
        assert not S.vision(c, editor=True)


def test_a_vision_written_without_spaces_is_saved_and_a_scrap_of_it_is_not(api):
    # Japanese puts no spaces between words: the whole vision is one "word" to split(), so its characters count.
    vision = "小さなチームが毎日もっと速く届けられるようにする"
    goal_manager(api, level=2)
    sync(api)
    task = vision_row(api)
    as_bot(api, "strategy-owner")
    say(api, task["id"], vision)
    post(api, "strategy/vision", {"task": task["id"], "vision": "チーム"}, token=BOT, expected=422)
    assert post(api, "strategy/vision", {"task": task["id"], "vision": vision}, token=BOT)["vision"] == vision
    with api.app.state.store.read() as c:
        assert S.vision(c, editor=True) == vision and H.task(c, task["id"])["status"] == "closed"


def test_only_the_strategy_owner_saves_and_only_on_the_vision_question(api):
    goal_manager(api, level=2)
    sync(api)
    task = vision_row(api)
    say(api, task["id"], VISION)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('planner','planner','Planner','active')")
        other = H.task_create(c, MANAGER, "Another task", "", MANAGER, lint=False)
    as_bot(api, "planner")
    post(api, "strategy/vision", {"task": task["id"], "vision": VISION}, token="planner-test", expected=403)
    # A person, the owner included, writes it on the Strategy page, not through this.
    post(api, "strategy/vision", {"task": task["id"], "vision": VISION}, expected=403)
    as_bot(api, "strategy-owner")
    post(api, "strategy/vision", {"task": other["id"], "vision": VISION}, token=BOT, expected=404)
    with api.app.state.store.read() as c:
        assert not S.vision(c, editor=True)
