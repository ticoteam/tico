"""A bot BotOps makes on an ordinary task ("Add Builder 3", not its own Set up task) says on its page what it waits on
until it is live (#411): the ask on that task, opened by the one button for the person it is addressed to, whose it is
for anyone else, else who is still making it. Never a Finish setup button that only opens the Info tab."""

from backend.store import H
from backend.tests.helpers.api import claim, get
from backend.tests.helpers.member_bots import botops, call  # noqa: F401  (fixture)

ASK = "OK to start Builder 3's own Large cloud machine, about 9 cents an hour while it runs?"


def made_on_a_task(api, botops):  # noqa: F811
    """Ana asks BotOps, on a General task, for a bot; BotOps registers it in that task's turn."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Add Builder 3", "A third builder like Builder 2.", "bot:botops")
    attempt = claim(api, botops, "botops")
    made = call(
        api,
        "post",
        "bots/register",
        attempt["token"],
        {"slug": "builder-3", "display_name": "Builder 3", "on_behalf_of": "turn"},
    )
    assert made.status_code == 200, made.text
    return task["id"]


def top_line(api, token="ana-test"):
    return get(api, "bots/builder-3", token)["cant_run"]


def test_a_bot_made_on_a_general_task_names_the_task_not_finish_setup(api, botops):  # noqa: F811
    task_id = made_on_a_task(api, botops)
    line = top_line(api)
    assert line["cause"] == "BotOps is still setting up Builder 3"
    assert line["fix"] is None  # nothing is Ana's to do: no button
    assert line["task"] == {"id": task_id, "title": "Add Builder 3"}


def test_the_start_ok_is_the_button_for_the_person_asked_and_named_for_anyone_else(api, botops):  # noqa: F811
    task_id = made_on_a_task(api, botops)
    with api.app.state.store.transaction() as c:
        asked = H.task_ask(
            c,
            "bot:botops",
            task_id,
            "human:ana",
            ASK,
            choices=["Yes start it", "No keep it off"],
            seen_version=H.task(c, task_id)["version"],
        )
    mine = top_line(api)
    assert mine["cause"] == "Builder 3 is waiting for your answer"
    assert mine["fix"] == "open_ask" and mine["task"]["id"] == task_id
    assert mine["ask"] == {"kind": "answer", "mine": True, "line": ASK}

    theirs = top_line(api, "ben-test")  # an Admin, not the one asked
    assert theirs["cause"].startswith("Builder 3 is waiting for ") and theirs["cause"].endswith("'s answer")
    assert "your" not in theirs["cause"] and theirs["fix"] is None and not theirs["ask"]["mine"]

    with api.app.state.store.transaction() as c:
        H.task_answer(
            c, "human:ana", task_id, asked["requirement_id"], "Yes start it", None, H.task(c, task_id)["version"]
        )
    after = top_line(api)  # answered: it no longer waits on her
    assert after["cause"] == "BotOps is still setting up Builder 3" and after["fix"] is None


def test_a_planned_bot_nothing_is_making_says_what_finishes_it_with_no_info_button(api, botops):  # noqa: F811
    task_id = made_on_a_task(api, botops)
    with api.app.state.store.transaction() as c:
        H.task_close(c, "human:ana", task_id, "Not needed after all.")
    line = top_line(api)
    assert line["cause"] == "Builder 3 has not started yet: ask BotOps to take it live"
    assert line["fix"] is None and "task" not in line
