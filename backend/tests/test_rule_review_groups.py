"""Rule review shows what was held and the part that broke the rule, one review per rule and reviewer (Product #563).

A held output's reviewer reads, on the hold, the part of the text that breaks each rule (`rule-part@1`, one call only
for an output a Block or Escalate rule holds); holds of one rule for one reviewer share one open review task, from any
bot on any day; and the review tasks made one per hold before are regrouped once at start."""

import json

from backend import org_rule_check as K
from backend import task_rule_gate
from backend.store import H
from backend.tests.helpers.api import get, post
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    COLOR,
    color_rule,
    hold_row,
    not_a_friday,
    ruled,
    say,
    sql,
)

SPLIT = "Thanks for asking about the launch.\nOur favorite color is gold.\nSee you on Monday."


def escalating(api, bots=("ops", "finance")):
    return color_rule(api, if_broken="escalate", reviewer="ben", who={"bots": list(bots)})


def view(api, hold_id, token="ben-test"):
    return get(api, f"rule-holds/{hold_id}", token=token)


def task(api, task_id):
    return sql(api, "SELECT id, title, status, note FROM tasks WHERE id=?", (task_id,))[0]


# ---- the part that broke the rule ---------------------------------------------------------------------------------


def test_a_held_output_points_at_the_line_that_breaks_the_rule_with_one_more_call(ruled):
    api, judge = ruled
    escalating(api)
    held = say(api, SPLIT)["held"]
    card = view(api, held["id"])
    assert card["text"] == SPLIT
    assert [r["part"] for r in card["rules"]] == ["Our favorite color is gold."]
    assert len(judge.calls) == 1 and len(judge.parts) == 1 and not any(judge.locked)
    assert [p["text"] for p in judge.parts[0]["state"]["parts"]] == SPLIT.split("\n")


def test_a_one_line_output_is_split_into_sentences(ruled):
    api, judge = ruled
    escalating(api)
    held = say(api, "We ship on Monday. Our favorite color is gold.")["held"]
    assert view(api, held["id"])["rules"][0]["part"] == "Our favorite color is gold."
    assert len(judge.parts) == 1


def test_an_output_of_one_part_asks_nothing_more_and_names_no_part(ruled):
    api, judge = ruled
    escalating(api)
    held = say(api, "Our favorite color is gold")["held"]
    assert view(api, held["id"])["rules"][0]["part"] is None
    assert judge.parts == []


def test_an_output_that_passes_or_only_warns_asks_nothing_more(ruled):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    assert "held" not in say(api, SPLIT)
    assert "held" not in say(api, "A safe line.\nAnother safe line.")
    assert judge.parts == [] and len(judge.calls) == 2


def test_a_part_call_that_fails_still_holds_and_names_no_part(ruled):
    api, judge = ruled
    escalating(api)
    judge.part_fail = RuntimeError("down")
    held = say(api, SPLIT)["held"]
    row = hold_row(api, held["id"])
    assert row["state"] == "held" and row["task_id"]
    shown = view(api, held["id"])
    assert shown["rules"][0]["part"] is None and not shown["part_pending"]  # an open card stops asking for it


def test_a_repeat_of_a_cached_verdict_keeps_its_part_with_no_call(ruled):
    api, judge = ruled
    escalating(api)
    first = say(api, SPLIT)["held"]
    again = say(api, SPLIT)["held"]
    assert view(api, again["id"])["rules"][0]["part"] == view(api, first["id"])["rules"][0]["part"]
    assert len(judge.calls) == 1 and len(judge.parts) == 1


def test_a_part_never_shows_to_someone_who_may_not_read_the_hold(ruled):
    api, _ = ruled
    escalating(api)
    held = say(api, SPLIT)["held"]
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)
    listed = get(api, f"rule-holds?task={hold_row(api, held['id'])['task_id']}", token="ben-test")["holds"]
    assert listed and all("part" not in r for h in listed for r in h["rules"])  # a summary leaves the text out


def test_a_part_only_from_the_reviewers_copy_is_kept():
    shown = K._shown_parts(
        {"r1": "the private words", "r2": "Our favorite color is gold."}, "Hi.\nOur favorite color is gold."
    )
    assert shown == {"r2": "Our favorite color is gold."}


def test_split_parts_drops_an_asks_labels_and_refuses_one_part_or_too_many():
    assert K.split_parts("Ask: May I send it?\n\nShows: page:abc@1") == ["May I send it?", "page:abc@1"]
    assert K.split_parts("One line only") == []
    assert K.split_parts("\n".join(f"line {n}" for n in range(K.PARTS_MAX + 1))) == []


def test_a_held_asks_copy_reads_without_its_labels():
    said = [("Ask", "May I send the refund note?"), ("Shows", "page:abc@1")]
    assert task_rule_gate.said_text(None, said, plain=True) == "May I send the refund note?\n\npage:abc@1"
    assert task_rule_gate.said_text(None, said) == "Ask: May I send the refund note?\n\nShows: page:abc@1"


# ---- one review per rule and reviewer -----------------------------------------------------------------------------


def test_holds_of_one_rule_from_two_bots_on_two_days_share_one_review(ruled):
    api, _ = ruled
    escalating(api)
    first = hold_row(api, say(api, SPLIT)["held"]["id"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rule_holds SET created_at=? WHERE id=?", ("2026-01-02T10:00:00Z", first["id"]))
    second = hold_row(api, say(api, "Our favorite color is gold, says finance", token="finance-test")["held"]["id"])
    assert second["author"] == "bot:finance" and second["task_id"] == first["task_id"]
    made = task(api, first["task_id"])
    assert made["title"] == f"Held by: {COLOR}"
    assert len(get(api, f"rule-holds?task={first['task_id']}", token="ben-test")["holds"]) == 2


def test_a_decided_review_is_not_joined(ruled):
    api, _ = ruled
    escalating(api)
    first = hold_row(api, say(api, SPLIT)["held"]["id"])
    post(api, f"rule-holds/{first['id']}/keep", {}, token="ben-test")
    assert task(api, first["task_id"])["status"] in ("done", "closed")
    second = hold_row(api, say(api, "Our favorite color is gold")["held"]["id"])
    assert second["task_id"] and second["task_id"] != first["task_id"]


def test_the_review_closes_only_once_every_hold_on_it_is_decided(ruled):
    api, _ = ruled
    escalating(api)
    holds = [say(api, f"Try {n}.\nOur favorite color is gold.")["held"]["id"] for n in range(3)]
    task_id = hold_row(api, holds[0])["task_id"]
    post(api, f"rule-holds/{holds[0]}/keep", {}, token="ben-test")
    nonce = view(api, holds[1])["nonce"]
    post(api, f"rule-holds/{holds[1]}/send", {"nonce": nonce}, token="ben-test")
    assert task(api, task_id)["status"] == "open"
    post(api, f"rule-holds/{holds[2]}/keep", {}, token="ben-test")
    assert task(api, task_id)["status"] in ("done", "closed")


def test_a_review_title_is_one_line_and_cut():
    long = {"text": "Never   share\n" + "x" * 400}
    title = K.review_title(long)
    assert "\n" not in title and len(title) == K.REVIEW_TITLE_MAX and title.endswith("…")
    assert K.review_title({"text": ""}) == K.REVIEW_TITLE
    assert K.review_title("No Markdown") == "Held by: No Markdown"  # an appeal names its rule as text


# ---- the reviews made one per hold, regrouped at start ------------------------------------------------------------


def _old_style(api, n=3):
    """`n` holds of one rule for Ben, each on its own review task titled as before."""
    escalating(api)
    holds = [say(api, f"Try {i}.\nOur favorite color is gold.")["held"]["id"] for i in range(n)]
    with api.app.state.store.transaction() as c:
        first = c.execute("SELECT task_id FROM org_rule_holds WHERE id=?", (holds[0],)).fetchone()[0]
        c.execute("UPDATE tasks SET title=? WHERE id=?", (K.REVIEW_TITLE, first))
        for hid in holds[1:]:
            made = H.task_create(
                c, H.TICO_ACTOR, K.REVIEW_TITLE, "Hold: " + hid, "human:ben", labels=[K.REVIEW_LABEL], deduplicate=False
            )
            c.execute("UPDATE org_rule_holds SET task_id=? WHERE id=?", (made["id"], hid))
    return holds, first


def test_regroup_moves_one_rules_holds_to_its_oldest_review_and_closes_the_rest(ruled):
    api, _ = ruled
    holds, first = _old_style(api)
    others = [hold_row(api, h)["task_id"] for h in holds[1:]]
    with api.app.state.store.transaction() as c:
        assert K.regroup_reviews(c) == 2
    assert {hold_row(api, h)["task_id"] for h in holds} == {first}
    assert task(api, first)["status"] == "open" and task(api, first)["title"] == f"Held by: {COLOR}"
    for old in others:
        closed = task(api, old)
        assert closed["status"] in ("done", "closed")
        assert "Moved to the review for the rule" in json.dumps(
            sql(api, "SELECT * FROM history_events WHERE subject_id=?", (old,))
        )
    with api.app.state.store.transaction() as c:
        assert K.regroup_reviews(c) == 0  # a later start changes nothing


def test_regroup_leaves_decided_holds_where_they_are(ruled):
    api, _ = ruled
    holds, first = _old_style(api, n=2)
    old = hold_row(api, holds[1])["task_id"]
    post(api, f"rule-holds/{holds[1]}/keep", {}, token="ben-test")
    with api.app.state.store.transaction() as c:
        assert K.regroup_reviews(c) == 0
    assert hold_row(api, holds[1])["task_id"] == old and hold_row(api, holds[0])["task_id"] == first


def test_the_hold_detail_never_keeps_text_outside_the_reviewers_copy(ruled):
    api, _ = ruled
    escalating(api)
    held = say(api, SPLIT)["held"]
    detail = json.loads(hold_row(api, held["id"])["verdict"])
    assert set(detail["parts"].values()) <= set(SPLIT.split("\n"))


# ---- after Review 1 -----------------------------------------------------------------------------------------------


def test_a_slow_part_call_never_delays_the_hold(ruled):
    import threading

    api, judge = ruled
    escalating(api)
    judge.part_gate = threading.Event()
    held = say(api, SPLIT, settle=False)["held"]  # answered while the part call still waits
    row = hold_row(api, held["id"])
    assert row["state"] == "held" and row["task_id"] and "parts" not in json.loads(row["verdict"])
    # A card opened now says the part is coming, in the list too, so it reads the hold again once the call ends.
    assert view(api, held["id"])["part_pending"]
    assert [h["part_pending"] for h in get(api, f"rule-holds?task={row['task_id']}", token="ben-test")["holds"]] == [
        True
    ]
    judge.part_gate.set()
    K.settle_parts()
    shown = view(api, held["id"])
    assert shown["rules"][0]["part"] == "Our favorite color is gold." and not shown["part_pending"]


def test_an_appealed_hold_joins_the_rules_open_review(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben", who={"bots": ["ops", "finance"]})
    tasks = []
    for token in ("ops-test", "finance-test"):
        held = say(api, "Our favorite color is gold", token=token)["held"]
        tasks.append(post(api, f"rule-holds/{held['id']}/appeal", {"why": "It is public."}, token=token)["task_id"])
    assert tasks[0] and tasks[0] == tasks[1]
    assert len(sql(api, "SELECT id FROM tasks WHERE status='open' AND title LIKE 'Held by: %'")) == 1


def test_a_mails_third_block_joins_another_bots_open_review(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben", who={"bots": ["ops", "finance"]})
    for n in range(3):  # finance's third block escalates: the rule's open review for Ben
        say(api, f"Try {n}: our favorite color is gold", token="finance-test")
    [review] = sql(api, "SELECT id FROM tasks WHERE status='open' AND title LIKE 'Held by: %'")
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    for _ in range(3):
        post(api, "bots/ops/mail-check", mail, token="ops-test")
    [held] = sql(api, "SELECT task_id FROM org_rule_holds WHERE operation='mail'")
    assert held["task_id"] == review["id"]


def test_a_hold_names_the_rule_it_is_on_first_never_a_warn(ruled):
    from backend.tests.helpers.org_rule_check import rule

    api, judge = ruled
    judge.needles["Never mention gold."] = "gold"
    rule(api, {"kind": "internal", "text": "Never mention gold.", "who": {"bots": ["ops"]}, "if_broken": "warn"})
    escalating(api)
    with api.app.state.store.transaction() as c:  # the Warn rule first in the verdict's order
        c.execute("UPDATE org_rules SET created_at='2000-01-01T00:00:00Z' WHERE text='Never mention gold.'")
    held = say(api, SPLIT)["held"]
    card = view(api, held["id"])
    assert len(card["rules"]) == 2
    assert card["rules"][0]["text"] == COLOR and card["rules"][0]["if_broken"] == "escalate"
