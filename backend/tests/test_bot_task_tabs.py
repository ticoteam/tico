"""The tabs over a bot's task list (backend/bot_task_tabs.py): one layout per bot that every viewer shares, the
Assistant's per person, changed only by the bot or a person who manages it, and no cap on how many."""

from backend.tests.test_api import api, as_member, get, put, setup_attempt  # noqa: F401  (api is a fixture)

DEFAULT = [{"builtin": "needs", "name": "Needs You"}, {"builtin": "active", "name": "Doing"},
           {"builtin": "blocked", "name": "Blocked"}, {"builtin": "done", "name": "Done Recently"}]
PRODUCT = {"name": "product tasks", "types": ["general"], "states": ["open", "doing", "waiting"], "waiting_on": ""}


def tabs(api, bot, token="ana-test"):
    return get(api, f"bots/{bot}/task-tabs", token)


def save(api, bot, value, token="ana-test", expected=200):
    return put(api, f"bots/{bot}/task-tabs", {"tabs": value}, token, expected)


def test_a_bot_starts_with_the_four_built_in_tabs(api):
    shown = tabs(api, "ops")
    assert shown["tabs"] == DEFAULT and shown["saved"] is False and shown["personal"] is False
    assert shown["can_edit"] is True


def test_a_layout_is_the_bots_and_changing_one_never_moves_anothers(api):
    layout = [DEFAULT[1], {**PRODUCT, "types": ["general"]}, {"builtin": "needs", "name": "For you"}]
    saved = save(api, "ops", layout)
    assert saved["tabs"] == layout and saved["saved"] is True and saved["updated_by"] == "human:ana"
    # Everyone who opens the bot sees the same tabs: Cara, who cannot change them, too.
    assert tabs(api, "ops", "cara-test")["tabs"] == layout
    assert tabs(api, "ops", "cara-test")["can_edit"] is False
    # Another bot keeps its own.
    assert tabs(api, "cpo")["tabs"] == DEFAULT
    save(api, "cpo", [DEFAULT[0]], "ben-test")
    assert tabs(api, "ops")["tabs"] == layout and tabs(api, "cpo")["tabs"] == [DEFAULT[0]]
    # Back to the defaults.
    assert save(api, "ops", None)["tabs"] == DEFAULT
    assert tabs(api, "ops", "cara-test")["saved"] is False


def test_only_the_bot_or_a_person_who_manages_it_changes_its_tabs(api):
    save(api, "ops", [DEFAULT[0]], "cara-test", expected=403)
    as_member(api, "ben@acme.example")
    save(api, "ops", [DEFAULT[0]], "ben-test", expected=403)      # Ben manages cpo, not ops
    save(api, "cpo", [DEFAULT[0]], "ben-test")
    _, _, attempt = setup_attempt(api, "ops")
    assert save(api, "ops", [PRODUCT], attempt["token"])["tabs"] == [PRODUCT]
    save(api, "cpo", [PRODUCT], attempt["token"], expected=403)
    assert tabs(api, "cpo")["tabs"] == [DEFAULT[0]]


def test_the_assistants_tabs_are_each_persons_own(api):
    assert tabs(api, "coo")["personal"] is True
    save(api, "coo", [PRODUCT])
    assert tabs(api, "coo")["tabs"] == [PRODUCT]
    assert tabs(api, "coo", "cara-test")["tabs"] == DEFAULT and tabs(api, "coo", "cara-test")["can_edit"] is True
    save(api, "coo", [DEFAULT[3]], "cara-test")
    assert tabs(api, "coo")["tabs"] == [PRODUCT] and tabs(api, "coo", "cara-test")["tabs"] == [DEFAULT[3]]


def test_no_cap_on_tabs(api):
    many = [{**PRODUCT, "name": f"Tab {n}"} for n in range(30)]
    assert len(save(api, "ops", many)["tabs"]) == 30


def test_a_filter_is_type_state_and_waiting_on_only(api):
    save(api, "ops", [DEFAULT[0], DEFAULT[0]], expected=422)                      # a built-in once
    save(api, "ops", [{**DEFAULT[1], "states": ["done"]}], expected=422)         # a built-in keeps its view
    save(api, "ops", [{**PRODUCT, "states": []}], expected=422)                  # at least one state
    save(api, "ops", [{**PRODUCT, "types": ["no-such-type"]}], expected=422)
    save(api, "ops", [{**PRODUCT, "states": ["stuck"]}], expected=422)
    save(api, "ops", [{**PRODUCT, "waiting_on": "human:ana"}], expected=422)
    save(api, "ops", [{**PRODUCT, "query": "owner=ops"}], expected=422)
    save(api, "ops", [], expected=422)
    save(api, "ops", [{**PRODUCT, "name": ""}], expected=422)
    assert tabs(api, "ops")["tabs"] == DEFAULT
    get(api, "bots/nobody/task-tabs", expected=404)
