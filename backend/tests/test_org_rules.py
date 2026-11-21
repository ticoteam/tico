"""Org rules over the API (docs/dev/rules-plan.md §5, §6): who a rule reaches, who may change it, what a bot may do, what a
person sees of a quote, and the bot's settings read back as sentences. Acme is the fictional company here.

The org: Ana owns it (owner), Ben is an admin, Cara is a plain member in the Support group, with a nested Support
Tier 2 group under it. Finance is a bot in Support Tier 2; Ops is a bot on the product team; `ops-test` signs in as Ops."""

import json
from datetime import timedelta
from urllib.parse import quote

from backend import org_rules as O
from backend import rule_due as D
from backend.store import H, encode
from backend.tests.helpers.api import get, headers, post, put  # noqa: F401  (the api fixture)
from backend import bot_settings as BS
from backend.tests.helpers.org_rules import as_bot, edit, org, rule
from backend.tests.helpers.org_rules import builtin_off


def matched(api, author, recipients=()):
    with api.app.state.store.read() as c:
        return [r["id"] for r in O.matching(c, author, recipients)]


def audit(api, rule_id):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM audit_log WHERE object=? ORDER BY created_at, id", ("org_rule:" + rule_id,)
            )
        ]


def test_who_matches_a_group_a_nested_group_a_person_all_bots_and_a_later_bot(api):
    org(api)
    support = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})["id"]
    person = rule(api, {"kind": "internal", "text": "Cara signs off on credits.", "who": {"people": ["cara"]}})["id"]
    everyone_bots = rule(api, {"kind": "internal", "text": "Bots cite their source.", "who": {"all_bots": True}})["id"]
    # Finance sits in Support Tier 2, which is under Support: the parent group's rule reaches it.
    assert support in matched(api, "bot:finance")
    assert person not in matched(api, "bot:finance")
    assert everyone_bots in matched(api, "bot:finance")
    # A bot made after the rule is covered by all_bots, with no edit to the rule.
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('late-bot', 'late-bot', 'Late', 'active')")
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('late-bot','','ana')")
        BS.write(c, 'late-bot', {})
    as_bot(api, "late-bot", "late-test")
    assert everyone_bots in matched(api, "bot:late-bot")
    assert support not in matched(api, "bot:late-bot")
    # A person in Who matches only that person.
    assert person in matched(api, "human:cara")
    assert person not in matched(api, "human:ana")


def test_a_bot_changing_its_group_changes_which_group_rules_reach_it(api):
    org(api)
    support = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})["id"]
    assert support not in matched(api, "bot:ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET team='support-tier-2' WHERE bot='ops'")
        BS.update(c, 'ops', {'team': 'support-tier-2'})
    assert support in matched(api, "bot:ops")


def test_with_matches_any_of_several_recipients(api):
    org(api)
    r = rule(
        api,
        {
            "kind": "internal",
            "text": "Finance checks every bot-to-bot note.",
            "who": {"people": ["cara"]},
            "with": {"bots": ["ops", "finance"]},
        },
    )["id"]
    assert r in matched(api, "human:cara", ["bot:ops"])
    assert r in matched(api, "human:cara", ["bot:doc-updater", "bot:finance"])
    assert r not in matched(api, "human:cara", ["bot:doc-updater"])


def test_a_peer_rule_from_add_rule_keeps_its_mention_with_and_ask_first_reviewer(api):
    """Add rule sends Who, With, If broken and (under Ask first) a reviewer in one write; the text keeps its @mention as
    written, for the Rules page to show as a chip. With a person, it reaches a bot only when that person is a recipient."""
    org(api)
    made = rule(
        api,
        {
            "kind": "internal",
            "text": "Ask @cara before you assign her a task.",
            "who": {"all_bots": True},
            "with": {"people": ["cara"]},
            "if_broken": "escalate",
            "reviewer": "ben",
        },
    )
    assert made["text"] == "Ask @cara before you assign her a task."
    assert made["if_broken"] == "escalate" and made["reviewer"]["id"] == "ben"
    assert made["who"]["all_bots"] and [p["id"] for p in made["with"]["people"]] == ["cara"]
    assert made["id"] in matched(api, "bot:ops", ["human:cara"])
    assert made["id"] not in matched(api, "bot:ops", ["human:ben"])
    # Stop is the default when If broken is left as the dialog opens it.
    plain = rule(api, {"kind": "internal", "text": "Cite a source.", "who": {"all_bots": True}, "if_broken": "block"})
    assert plain["if_broken"] == "block" and plain["reviewer"] is None


def test_a_party_named_in_with_cannot_edit_the_rule(api):
    org(api)
    r = rule(
        api,
        {
            "kind": "internal",
            "text": "Ben reviews anything for Cara.",
            "who": {"people": ["ben"]},
            "with": {"people": ["cara"]},
        },
    )
    edit(api, r["id"], {"version": 1, "text": "Cara edits this."}, token="cara-test", expected=403)


def test_a_bot_suggests_and_cannot_accept(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}}, token="ops-test")
    assert r["status"] == "suggested"
    assert (
        api.post(f"/api/v2/rules/{r['id']}/accept", json={"version": 1}, headers=headers("ops-test")).status_code == 403
    )
    assert matched(api, "bot:ops") == []


def test_accept_writes_an_audit_row_with_the_actor_and_how_it_came(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}}, token="ops-test")
    accepted = api.post(f"/api/v2/rules/{r['id']}/accept", json={"version": 1}, headers=headers("ana-test"))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["rule"]["status"] == "active"
    rows = audit(api, r["id"])
    assert [row["actor"] for row in rows] == ["bot:ops", "human:ana"]
    assert rows[-1]["via"] and json.loads(rows[-1]["after"])["status"] == "active"


def test_a_stale_version_is_a_conflict(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    edit(api, r["id"], {"version": 1, "text": "Ops answers within the hour."})
    edit(api, r["id"], {"version": 1, "text": "Ops answers today."}, expected=409)


def test_a_quote_hides_when_its_file_is_not_readable(api):
    org(api)
    r = rule(
        api,
        {
            "kind": "external",
            "text": "Keep receipts for seven years.",
            "who": {"bots": ["finance"]},
            "source_id": _source(api),
        },
    )
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE org_rules SET cite=? WHERE id=?",
            (json.dumps({"file_id": "no-such-file", "quote": "SECRET-QUOTE-MARKER"}), r["id"]),
        )
    for path in ("rules", "bots/finance/rules", "rules?mine=true&bot=finance"):
        body = get(api, path, token="cara-test")
        text = json.dumps(body)
        assert "SECRET-QUOTE-MARKER" not in text, path
        assert "hidden" in text, path


def _source(api):
    return post(api, "rule-sources", {"name": "Acme Tax Office", "kind": "government"})["source"]["id"]


def test_mine_reads_the_bots_rules_and_settings_without_writing(api):
    builtin_off(api)
    org(api)
    rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    before = len(_events(api))
    body = get(api, "rules?mine=true&bot=ops", token="ops-test")
    assert [r["text"] for r in body["rules"]] == ["Ops answers within a day."]
    assert body["settings"] and all("text" in s and "source" in s for s in body["settings"])
    assert len(_events(api)) == before


def test_retire_returns_the_due_dates_and_leaves_the_history(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    due = post(api, f"rules/{r['id']}/due", {"first_due": "2026-01-15", "repeats": "monthly", "owner": "ops"})
    retired = post(api, f"rules/{r['id']}/retire", {"version": 1})
    assert retired["rule"]["status"] == "retired"
    assert [d["id"] for d in retired["due"]] == [due["id"]]
    assert any(row["action"] == "org_rule.retired" for row in _events(api))


def _events(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT action FROM events")]


def test_two_open_occurrences_need_a_day_to_satisfy(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    start = (D.local_today("America/Los_Angeles") - timedelta(days=30)).isoformat()
    due = post(api, f"rules/{r['id']}/due", {"first_due": start, "repeats": "weekly", "owner": "ops"})
    post(api, f"rule-due/{due['id']}/satisfied", {}, expected=422)
    marked = post(api, f"rule-due/{due['id']}/satisfied", {"due_on": start})
    assert marked["satisfied"] == start


def test_a_bot_with_no_team_of_its_own_is_in_its_managers_group(api):
    org(api)
    support = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})["id"]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('intern', 'intern', 'Intern', 'active')")
        c.execute("INSERT INTO bot_config(bot,team,owner_id,reports_to) VALUES('intern','','ana','finance')")
        BS.write(c, 'intern', {})
    assert support in matched(api, "bot:intern")


def _lead(api, gid, lead):
    with api.app.state.store.transaction() as c:
        raw = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0]
        doc = json.loads(raw)
        doc["org_groups"][gid]["lead"] = lead
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))


def _add_group(api, gid):
    with api.app.state.store.transaction() as c:
        raw = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0]
        doc = json.loads(raw)
        doc["org_groups"][gid] = {"name": gid.title()}
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))


def _ids(items):
    return [item["id"] for item in items]


def _access(api, bot, everyone, people):
    grant = {"everyone": everyone, "people": people, "teams": [], "bots": []}
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE bot_config SET access=? WHERE bot=?",
            (json.dumps({"see": grant, "read": grant, "write": grant}), bot),
        )


def test_a_co_owner_of_the_bot_can_edit_and_retire_its_rule(api):
    org(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET bot_owners=? WHERE bot='ops'", (json.dumps(["cara"]),))
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    edited = edit(api, r["id"], {"version": 1, "text": "Ops answers today."}, token="cara-test")
    assert edited["text"] == "Ops answers today."
    retired = post(api, f"rules/{r['id']}/retire", {"version": 2}, token="cara-test")
    assert retired["rule"]["status"] == "retired"


def test_a_conversation_member_of_the_bot_cannot_edit_or_retire_its_rule(api):
    org(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_ids=? WHERE bot='ops'", (json.dumps(["cara"]),))
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    edit(api, r["id"], {"version": 1, "text": "Ops answers today."}, token="cara-test", expected=403)
    post(api, f"rules/{r['id']}/retire", {"version": 1}, token="cara-test", expected=403)


def test_a_replayed_write_checks_the_quote_again_once_file_access_is_revoked(api):
    org(api)
    _lead(api, "support", "person:cara")
    _access(api, "ops", True, [])
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,first_activity_at,last_activity_at)"
            " VALUES('receipt-file','ops','bot','ops','Receipts','document','receipts','2026','2026')"
        )
    r = rule(
        api,
        {
            "kind": "external",
            "text": "Keep receipts for seven years.",
            "who": {"groups": ["support"]},
            "source_id": _source(api),
        },
    )
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE org_rules SET cite=? WHERE id=?",
            (json.dumps({"file_id": "receipt-file", "version": 1, "quote": "QUOTE-IN-THE-FILE"}), r["id"]),
        )
    body = {"version": 1, "text": "Keep receipts for seven years, then discard them."}
    key = "replay-after-revoke"
    first = api.post(f"/api/v2/rules/{r['id']}", json=body, headers=headers("cara-test", key))
    assert first.status_code == 200, first.text
    assert first.json()["cite"]["quote"] == "QUOTE-IN-THE-FILE"
    _access(api, "ops", False, ["ana"])
    replay = api.post(f"/api/v2/rules/{r['id']}", json=body, headers=headers("cara-test", key))
    assert replay.status_code == 200, replay.text
    assert replay.json()["cite"] == {"hidden": True}
    assert "QUOTE-IN-THE-FILE" not in replay.text
    fresh = api.get(f"/api/v2/rules/{r['id']}", headers=headers("cara-test"))
    assert fresh.json()["cite"] == {"hidden": True}


def test_a_due_date_keeps_its_edit_chain_and_is_readable_by_id(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    start = (D.local_today("America/Los_Angeles") - timedelta(days=1)).isoformat()
    due = post(api, f"rules/{r['id']}/due", {"first_due": start, "repeats": "once", "owner": "ops"})
    post(api, f"rule-due/{due['id']}/satisfied", {})
    second = post(api, f"rule-due/{due['id']}", {"first_due": "2027-03-01"})["id"]
    listed = get(api, f"rules/{r['id']}")
    assert listed["due"] and all("chain" not in d for d in listed["due"])
    third = post(api, f"rule-due/{second}", {"first_due": "2027-06-01"})["id"]
    old = get(api, f"rule-due/{due['id']}")
    assert old["replaced_by"] == second and old["replaces"] is None
    assert old["chain"][0]["ended_on"] is not None
    assert [link["id"] for link in old["chain"]] == [due["id"], second, third]
    assert old["chain"][0]["satisfied"][0]["due_on"] == start
    mid = get(api, f"rule-due/{second}")
    assert mid["replaces"] == due["id"] and mid["replaced_by"] == third
    assert [link["id"] for link in mid["chain"]] == [due["id"], second, third]
    assert not old["can_edit"] and not old["can_satisfy"]
    live = get(api, f"rule-due/{third}")
    assert live["can_edit"] and live["can_satisfy"]
    as_bot = api.get(f"/api/v2/rule-due/{third}", headers=headers("ops-test")).json()
    assert not as_bot["can_edit"] and as_bot["can_satisfy"]
    post(api, f"rule-due/{third}/remove", {})
    removed = get(api, f"rule-due/{third}")
    assert removed["chain"][-1]["retired_at"] is not None
    assert get(api, f"rule-due/{due['id']}")["chain"][0]["satisfied"][0]["due_on"] == start


def test_a_page_of_the_longest_rules_stays_under_the_byte_cap(api):
    builtin_off(api)
    org(api)
    text = "\U0001f642" * O.TEXT_MAX  # four bytes each in UTF-8
    description = "\U0001f642" * O.DESCRIPTION_MAX
    for n in range(100):
        rule(api, {"kind": "internal", "text": text, "description": description, "who": {"bots": ["ops"]}})
    seen, cursor, pages = [], None, 0
    while True:
        r = api.get("/api/v2/rules?limit=100" + (f"&cursor={cursor}" if cursor else ""), headers=headers())
        assert r.status_code == 200, r.text
        assert len(r.content) <= O.LIST_BYTES + 16_000
        body = r.json()
        assert body["rules"]
        seen += [item["id"] for item in body["rules"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages > 1 and len(seen) == 100 == len(set(seen))
    cursor, internal = None, []
    while True:
        r = api.get("/api/v2/bots/ops/rules" + (f"?internal_cursor={cursor}" if cursor else ""), headers=headers())
        assert r.status_code == 200 and len(r.content) <= O.LIST_BYTES + 16_000
        part = r.json()["internal"]
        assert len(part["rules"]) <= O.BOT_LIST_MAX
        internal += [item["id"] for item in part["rules"]]
        cursor = part["next_cursor"]
        if cursor is None:
            break
    assert len(set(internal)) == 100


def test_every_external_rule_that_applies_to_a_bot_is_reachable_by_its_cursor(api):
    org(api)
    source = _source(api)
    for n in range(101):
        rule(
            api,
            {"kind": "external", "text": f"Source rule {n} applies.", "who": {"bots": ["ops"]}, "source_id": source},
        )
    collected, cursor = [], None
    while True:
        body = get(api, "bots/ops/rules" + (f"?external_cursor={cursor}" if cursor else ""))
        collected += [item["id"] for item in body["external"]["rules"]]
        cursor = body["external"]["next_cursor"]
        if cursor is None:
            break
    assert len(collected) == 101 == len(set(collected))


def test_an_owner_or_admin_everyone_rule_is_active_at_once(api):
    org(api)
    mine = rule(api, {"kind": "internal", "text": "Everyone logs time.", "who": {"everyone": True}})
    admin = rule(
        api,
        {"kind": "internal", "text": "Everyone uses the shared inbox.", "who": {"everyone": True}},
        token="ben-test",
    )
    assert mine["status"] == "active" and admin["status"] == "active"
    assert {mine["id"], admin["id"]} <= set(matched(api, "bot:ops"))
    rule(
        api,
        {"kind": "internal", "text": "Everyone has a badge.", "who": {"everyone": True}},
        token="cara-test",
        expected=403,
    )


def test_a_group_and_a_bot_with_one_name_need_a_prefix(api):
    org(api)
    _add_group(api, "ops")
    bot_rule = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"names": ["bot:ops"]}})
    assert _ids(bot_rule["who"]["bots"]) == ["ops"] and _ids(bot_rule["who"]["groups"]) == []
    rule(api, {"kind": "internal", "text": "Ops team meets Mondays.", "who": {"names": ["ops"]}}, expected=400)
    group_rule = rule(api, {"kind": "internal", "text": "Ops team meets Mondays.", "who": {"names": ["group:ops"]}})
    assert _ids(group_rule["who"]["groups"]) == ["ops"]
    rule(api, {"kind": "internal", "text": "Ops team meets Mondays.", "who": {"groups": ["bot:ops"]}}, expected=422)
    assert api.get("/api/v2/rules?who=ops", headers=headers()).status_code == 400
    assert api.get("/api/v2/rules?who=bot:ops", headers=headers()).status_code == 200


def _routine_due(api):
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    start = (D.local_today("America/Los_Angeles") - timedelta(days=1)).isoformat()
    due = post(api, f"rules/{r['id']}/due", {"first_due": start, "repeats": "once", "owner": "ops"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE rule_due_dates SET routine_id='routine-1' WHERE id=?", (due["id"],))
    return r, due, start


def test_two_marks_of_one_day_that_race_answer_the_same_conflict(api, monkeypatch):
    org(api)
    _, due, start = _routine_due(api)
    post(api, f"rule-due/{due['id']}/satisfied", {"due_on": start})
    # The second mark reads the day as open (as a racing request would), so the insert meets the stored mark.
    monkeypatch.setattr(D, "satisfied_days", lambda c, due_id: set())
    refused = post(api, f"rule-due/{due['id']}/satisfied", {"due_on": start}, expected=409)
    assert refused is not None


REFUND_CAP = {"kind": "max_usd", "action": "refund", "field": "amount_usd", "max_usd": 100}


def test_a_typed_check_is_kept_exactly_and_judges_one_actions_params(api):
    """Checked exactly: the check people adopt is the one that runs. Unknown keys are refused, not dropped; a missing
    or non-numeric amount breaks a cap; another action is not this rule's; {} on edit takes the check off."""
    from backend import org_rule_check as OC

    org(api)
    builtin_off(api)
    body = {"kind": "internal", "text": "Refunds over $100 go to Ben first.", "who": {"all_bots": True}}
    rule(api, {**body, "exact_check": {**REFUND_CAP, "per": "day"}}, expected=422)
    toward = {**body, "toward_scope": ["entity:organization/acme-lights"]}
    rule(api, {**toward, "exact_check": REFUND_CAP}, expected=422)  # a rule checked in code takes no typed check
    outside = {"kind": "internal", "text": "Editions quote no refund over $100.", "who": {"outside_editions": True}}
    rule(api, {**outside, "exact_check": REFUND_CAP}, expected=422)  # no teammate's write meets it
    coded = rule(api, toward)
    edit(api, coded["id"], {"version": coded["version"], "exact_check": REFUND_CAP}, expected=422)
    made = rule(api, {**body, "exact_check": REFUND_CAP})
    assert made["exact_check"] == REFUND_CAP
    # The Who it ends with is checked with its check, on edit and on accept with a new Who.
    moved = edit(api, made["id"], {"version": made["version"], "who": {"outside_editions": True}}, expected=422)
    assert "outside" in moved["error"]["detail"]
    drafted = rule(api, {**body, "who": {"bots": ["ops"]}, "exact_check": REFUND_CAP}, token="ops-test")
    accepted = api.post(
        f"/api/v2/rules/{drafted['id']}/accept",
        headers=headers("ana-test"),
        json={"version": drafted["version"], "who": {"outside_editions": True}},
    )
    assert accepted.status_code == 422, accepted.text
    lists = rule(
        api,
        {
            "kind": "internal",
            "text": "Only mail the two partner domains.",
            "who": {"bots": ["ops"]},
            "exact_check": {
                "kind": "allow_list",
                "action": "mail",
                "field": "to",
                "values": [" Acme.example ", "beta.example"],
            },
        },
    )
    assert lists["exact_check"]["values"] == ["acme.example", "beta.example"]

    def broken(author, action, params):
        with api.app.state.store.read() as c:
            return [(r["id"], r["reason"]) for r in OC.exact_check(c, author, action, params)]

    assert broken("bot:ops", "refund", {"amount_usd": 80}) == []
    assert broken("bot:ops", "refund", {"amount_usd": 150}) == [(made["id"], "amount_usd 150 is over $100")]
    assert broken("bot:ops", "refund", {"amount_usd": "80"}) == [(made["id"], "amount_usd is not given in dollars")]
    assert broken("bot:ops", "refund", {"amount_usd": float("nan")})
    assert broken("bot:ops", "credit", {"amount_usd": 500}) == []
    assert broken("human:cara", "refund", {"amount_usd": 500}) == []  # not in Who
    assert broken("bot:ops", "mail", {"to": ["ACME.example", "beta.example"]}) == []
    assert broken("bot:ops", "mail", {"to": ["acme.example", "evil.example"]}) == [
        (lists["id"], "to evil.example is not on the list")
    ]
    assert broken("bot:finance", "mail", {"to": "evil.example"}) == []  # the allow list's Who is Ops alone

    off = edit(api, made["id"], {"version": made["version"], "exact_check": {}})
    assert off["exact_check"] is None and off["version"] == made["version"] + 1
    assert broken("bot:ops", "refund", {"amount_usd": 150}) == []
    rows = audit(api, made["id"])
    assert json.loads(rows[0]["after"])["exact_check"] == REFUND_CAP
    assert json.loads(rows[-1]["after"])["exact_check"] is None


def test_a_rules_history_pages_its_versions_apart_from_its_due_dates_and_its_recent_holds(api):
    """Versions are the rule's own audit rows (a due date's row is not one), newest first, and continue by their
    cursor; holds are the rule's from the last 30 days the reader may read, and continue by theirs."""
    from backend import audit_log

    org(api)
    made = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"all_bots": True}})
    edit(api, made["id"], {"version": made["version"], "text": "Log every refund the same day."})
    other = rule(api, {"kind": "internal", "text": "Cite your source.", "who": {"all_bots": True}})
    old = H.shift(H.now(), days=-31)
    with api.app.state.store.transaction() as c:
        audit_log.write(c, "human:ana", "org_rule:" + made["id"], None, {"due": "d1"}, None)
        for hid, rules_, at in (
            ("h-now", [made["id"]], H.now()),
            ("h-next", [made["id"]], H.shift(H.now(), seconds=1)),
            ("h-old", [made["id"]], old),
            ("h-other", [other["id"]], H.now()),
        ):
            c.execute(
                "INSERT INTO org_rule_holds(id, operation, payload, author, rule_ids, nonce, state, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (hid, "comment", encode({"text": "x"}), "bot:ops", json.dumps(rules_), "n-" + hid, "held", at),
            )
    seen = get(api, f"rules/{made['id']}/history")
    assert [v["after"]["version"] for v in seen["versions"]] == [2, 1] and seen["versions_next"] is None
    assert "counts" not in seen
    assert [h["id"] for h in seen["holds"]] == ["h-now", "h-next"]
    first = seen["versions"][0]
    cursor = quote(f"{first['created_at']}|{first['id']}")
    rest = get(api, f"rules/{made['id']}/history?versions_cursor={cursor}")
    assert [v["after"]["version"] for v in rest["versions"]] == [1]
    nxt = f"{seen['holds'][0]['created_at']}|h-now"  # the cursor a page that stopped after h-now hands back
    more = get(api, f"rules/{made['id']}/history?holds_cursor={quote(nxt)}")
    assert [h["id"] for h in more["holds"]] == ["h-next"]
    # Cara, a member, reads the versions and none of Ops's holds.
    cara = get(api, f"rules/{made['id']}/history", token="cara-test")
    assert cara["holds"] == [] and len(cara["versions"]) == 2
    assert api.get("/api/v2/rules/nope/history", headers=headers("ana-test")).status_code == 404


def test_a_completed_company_without_a_constitution_gets_it_at_its_next_start(api):
    """A company that completed onboarding before the constitution shipped has none; the start's repair step
    (onboarding.complete_company) makes it, and a read never writes it."""
    from backend import docs, onboarding

    org(api)
    assert get(api, "rules/counts")["constitution"] is None  # a read makes nothing
    with api.app.state.store.transaction() as c:
        state = onboarding.load(c)
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
            (onboarding.KEY, encode({**state, "completed": H.now()})),
        )
        assert docs.constitution_id(c) is None
        api.app.state.onboarding.complete_company(c)
    made = get(api, "rules/counts")["constitution"]
    assert made and made["title"] == "Constitution"
