"""A computer's heartbeat answer carries no bot list (P02): the computer reads its bots from `runners/assignments` when
the server tells it something changed, so the heartbeat stays small however many bots the computer hosts."""

import json
import uuid

from backend import bot_settings as BS
from backend.tests.helpers.api import assign, get, headers, runner  # noqa: F401  (the api fixture)
from backend.tests.helpers.bot_list_reads import add_bots

# Production's shared cloud machine on 2026-11-12: 53 bots, 20 of them with about 11 KB of written instructions.
BOTS, LONG, INSTRUCTIONS = 53, 20, ("Write plainly and check the numbers before you send. " * 220)[:11_000]
BEAT = {"version": "test", "platform": "test", "readiness": {}}


def hosting(api):
    slugs = ["host-%02d" % i for i in range(BOTS)]
    add_bots(api, {slug: {"instructions": INSTRUCTIONS} if i < LONG else {} for i, slug in enumerate(slugs)})
    computer = runner(api)
    for slug in slugs:
        assign(api, computer, slug)
    return computer, slugs


def beat(api, computer):
    response = api.post(
        "/api/v2/runners/heartbeat", json=BEAT, headers=headers(computer["token"], "heartbeat:" + str(uuid.uuid4()))
    )
    assert response.status_code == 200, response.text[:400]
    return response


def told(api, computer):
    """How many times the server has told `computer` to read its bots again."""
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT count(*) FROM changes WHERE topic='runner' AND kind='assignments' AND subject_id=?",
            (computer["runner_id"],),
        ).fetchone()[0]


def listed(api, computer):
    return {row["bot"]: row for row in get(api, "runners/assignments", computer["token"])}


def test_a_heartbeat_from_a_computer_with_53_bots_answers_under_10_kb(api):
    computer, slugs = hosting(api)
    # The fixture is production's size: the bot list the computer reads separately is still about a quarter megabyte.
    every = get(api, "runners/assignments", computer["token"])
    assert {row["bot"] for row in every} >= set(slugs) and len(json.dumps(every)) > 200_000

    response = beat(api, computer)

    assert len(response.content) < 10_000, len(response.content)
    answer = response.json()
    # Kept, empty, for the releases whose recorded contract requires the field (0.5.0, 0.6.0); none of them reads it.
    assert answer["assignments"] == []
    assert answer["server_time"] and "worktree_actions" in answer


def test_a_changed_setting_still_reaches_the_computer_through_its_bot_list(api):
    computer, slugs = hosting(api)
    beat(api, computer)
    before = told(api, computer)

    with api.app.state.store.transaction() as c:
        BS.update(c, slugs[0], {"model": "changed-model", "instructions": "Shorter now."})

    assert told(api, computer) > before
    row = listed(api, computer)[slugs[0]]
    assert row["config"]["model"] == "changed-model" and row["config"]["instructions"] == "Shorter now."
    assert beat(api, computer).json()["assignments"] == []


def test_a_bot_moved_between_computers_reaches_both_through_their_bot_lists(api):
    computer, slugs = hosting(api)
    other = runner(api, label="Other Mac")
    beat(api, computer), beat(api, other)
    before = told(api, computer), told(api, other)

    assign(api, other, slugs[1], generation=1)

    assert told(api, computer) > before[0] and told(api, other) > before[1]
    assert slugs[1] not in listed(api, computer)
    assert listed(api, other)[slugs[1]]["generation"] == 2
    assert beat(api, other).json()["assignments"] == []
