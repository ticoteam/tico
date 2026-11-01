"""Org rules over the API (docs/rules-plan.md §5, §6): who a rule reaches, who may change it, what a bot may do, what a
person sees of a quote, and the bot's settings read back as sentences. Acme is the fictional company here.

The org: Ana owns it (owner), Ben is an admin, Cara is a plain member in the Support group, with a nested Support
Tier 2 group under it. Finance is a bot in Support Tier 2; Ops is a bot on the product team; `ops-test` signs in as Ops."""
import json
from datetime import timedelta

from backend import org_rules as O
from backend import rule_due as D
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.test_api import api, get, headers, post, put  # noqa: F401  (the api fixture)


def set_roster(api, doc):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))


def as_bot(api, slug, token):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  (slug, "test", "x-" + slug, H.now(), "human:ana"))
    api.app.state.store.settings.test_identities[token] = Identity(f"bot:{slug}", "bot", agent="test")


def org(api):
    set_roster(api, {
        "people": [
            {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
            {"id": "ben", "email": "ben@acme.example", "primary_for": ["cpo", "product-design", "ops"]},
            {"id": "cara", "email": "cara@acme.example", "team": "support", "name": "Cara"},
        ],
        "org_groups": {"support": {"name": "Support"},
                       "support-tier-2": {"name": "Support Tier 2", "parent": "support"}},
    })
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET team='support-tier-2', config=json_set(config, '$.team', 'support-tier-2') WHERE bot='finance'")
        c.execute("UPDATE bot_config SET team='product', config=json_set(config, '$.team', 'product') WHERE bot='ops'")
    as_bot(api, "ops", "ops-test")
    as_bot(api, "finance", "finance-test")


def rule(api, body, token="ana-test", expected=200):
    r = api.post("/api/v2/rules", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def edit(api, rule_id, body, token="ana-test", expected=200):
    r = api.post(f"/api/v2/rules/{rule_id}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def matched(api, author, recipients=()):
    with api.app.state.store.read() as c:
        return [r["id"] for r in O.matching(c, author, recipients)]


def audit(api, rule_id):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit_log WHERE object=? ORDER BY created_at, id", ("org_rule:" + rule_id,))]


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
        c.execute("INSERT INTO bot_config(bot,config,team,owner_id) VALUES('late-bot','{}','', 'ana')")
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
        c.execute("UPDATE bot_config SET team='support-tier-2', config=json_set(config, '$.team', 'support-tier-2') WHERE bot='ops'")
    assert support in matched(api, "bot:ops")


def test_with_matches_any_of_several_recipients(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Finance checks every bot-to-bot note.",
                   "who": {"people": ["cara"]}, "with": {"bots": ["ops", "finance"]}})["id"]
    assert r in matched(api, "human:cara", ["bot:ops"])
    assert r in matched(api, "human:cara", ["bot:doc-updater", "bot:finance"])
    assert r not in matched(api, "human:cara", ["bot:doc-updater"])


def test_everyone_by_a_plain_member_is_refused(api):
    org(api)
    rule(api, {"kind": "internal", "text": "Everyone reads the handbook.", "who": {"everyone": True}},
         token="cara-test", expected=403)


def test_a_party_named_in_with_cannot_edit_the_rule(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ben reviews anything for Cara.",
                   "who": {"people": ["ben"]}, "with": {"people": ["cara"]}})
    edit(api, r["id"], {"version": 1, "text": "Cara edits this."}, token="cara-test", expected=403)


def test_a_bot_suggests_and_cannot_accept(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}},
             token="ops-test")
    assert r["status"] == "suggested"
    assert api.post(f"/api/v2/rules/{r['id']}/accept", json={"version": 1},
                    headers=headers("ops-test")).status_code == 403
    assert matched(api, "bot:ops") == []


def test_accept_writes_an_audit_row_with_the_actor_and_how_it_came(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}},
             token="ops-test")
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
    r = rule(api, {"kind": "external", "text": "Keep receipts for seven years.", "who": {"bots": ["finance"]},
                   "source_id": _source(api)})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rules SET cite=? WHERE id=?",
                  (json.dumps({"file_id": "no-such-file", "quote": "SECRET-QUOTE-MARKER"}), r["id"]))
    for path in ("rules", "bots/finance/rules", "rules?mine=true&bot=finance"):
        body = get(api, path, token="cara-test")
        text = json.dumps(body)
        assert "SECRET-QUOTE-MARKER" not in text, path
        assert "hidden" in text, path


def _source(api):
    return post(api, "rule-sources", {"name": "Acme Tax Office", "kind": "government"})["source"]["id"]


def test_the_settings_sentences_match_the_live_setting(api):
    org(api)
    settings = get(api, "bots/ops/rules")["settings"]
    texts = [s["text"] for s in settings]
    assert any(t.startswith("Everyone can message") for t in texts)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET access=? WHERE bot='ops'",
                  (json.dumps({"write": {"everyone": False, "people": ["cara"], "teams": [], "bots": []}}),))
    texts = [s["text"] for s in get(api, "bots/ops/rules")["settings"]]
    assert "Only Cara can message ops" in texts


def test_mine_reads_the_bots_rules_and_settings_without_writing(api):
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
    due = post(api, f"rules/{r['id']}/due", {"first_due": "2026-01-15", "repeats": "monthly", "owner_id": "ops"})
    retired = post(api, f"rules/{r['id']}/retire", {"version": 1})
    assert retired["rule"]["status"] == "retired"
    assert [d["id"] for d in retired["due_at"]] == [due["id"]]
    assert any(row["action"] == "org_rule.retired" for row in _events(api))


def _events(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT action FROM events")]


def test_two_open_occurrences_need_a_day_to_satisfy(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    start = (D.local_today("America/Los_Angeles") - timedelta(days=30)).isoformat()
    due = post(api, f"rules/{r['id']}/due", {"first_due": start, "repeats": "weekly", "owner_id": "ops"})
    post(api, f"rule-due/{due['id']}/satisfied", {}, expected=422)
    marked = post(api, f"rule-due/{due['id']}/satisfied", {"due_on": start})
    assert marked["satisfied"] == start


def test_editing_a_due_date_keeps_its_history(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    start = (D.local_today("America/Los_Angeles") - timedelta(days=1)).isoformat()
    due = post(api, f"rules/{r['id']}/due", {"first_due": start, "repeats": "once", "owner_id": "ops"})
    post(api, f"rule-due/{due['id']}/satisfied", {})
    edited = post(api, f"rule-due/{due['id']}", {"first_due": "2027-03-01"})
    assert edited["replaces"] == due["id"]
    with api.app.state.store.read() as c:
        assert D.satisfied_days(c, due["id"]) == {start}
        assert D.one(c, due["id"])["ended_on"] is not None


def test_the_rules_list_is_paged(api):
    org(api)
    for n in range(3):
        rule(api, {"kind": "internal", "text": f"Rule {n} says hello.", "who": {"bots": ["ops"]}})
    first = get(api, "rules?limit=2")
    assert len(first["rules"]) == 2 and first["next_cursor"] == "2"
    assert len(get(api, "rules?limit=2&cursor=2")["rules"]) == 1


def test_a_turn_limit_set_on_the_bot_reads_back_as_its_sentence(api):
    org(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config, '$.turns_per_hour', 7) WHERE bot='ops'")
    texts = [s["text"] for s in get(api, "bots/ops/rules")["settings"]]
    assert "Up to 7 turns per hour" in texts
    assert "Team default: 2500 turns per day" in texts
    assert "ops cannot send mail outside Tico" in texts


def test_a_bot_with_no_team_of_its_own_is_in_its_managers_group(api):
    org(api)
    support = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})["id"]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('intern', 'intern', 'Intern', 'active')")
        c.execute("INSERT INTO bot_config(bot,config,team,owner_id,reports_to) VALUES('intern','{}','','ana','finance')")
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
        c.execute("UPDATE bot_config SET access=? WHERE bot=?",
                  (json.dumps({"see": grant, "read": grant, "write": grant}), bot))


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
        c.execute("INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,first_activity_at,last_activity_at)"
                  " VALUES('receipt-file','ops','bot','ops','Receipts','document','receipts','2026','2026')")
    r = rule(api, {"kind": "external", "text": "Keep receipts for seven years.", "who": {"groups": ["support"]},
                   "source_id": _source(api)})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rules SET cite=? WHERE id=?",
                  (json.dumps({"file_id": "receipt-file", "version": 1, "quote": "QUOTE-IN-THE-FILE"}), r["id"]))
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
    due = post(api, f"rules/{r['id']}/due", {"first_due": start, "repeats": "once", "owner_id": "ops"})
    post(api, f"rule-due/{due['id']}/satisfied", {})
    second = post(api, f"rule-due/{due['id']}", {"first_due": "2027-03-01"})["id"]
    listed = get(api, f"rules/{r['id']}")
    assert listed["due_at"] and all("chain" not in d for d in listed["due_at"])
    third = post(api, f"rule-due/{second}", {"first_due": "2027-06-01"})["id"]
    old = get(api, f"rule-due/{due['id']}")
    assert old["replaced_by_id"] == second and old["replaces"] is None
    assert old["chain"][0]["ended_on"] is not None
    assert [link["id"] for link in old["chain"]] == [due["id"], second, third]
    assert old["chain"][0]["satisfied"][0]["due_on"] == start
    mid = get(api, f"rule-due/{second}")
    assert mid["replaces"] == due["id"] and mid["replaced_by_id"] == third
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
    org(api)
    text = "\U0001F642" * O.TEXT_MAX          # four bytes each in UTF-8
    description = "\U0001F642" * O.DESCRIPTION_MAX
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
        rule(api, {"kind": "external", "text": f"Source rule {n} applies.", "who": {"bots": ["ops"]},
                   "source_id": source})
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
    admin = rule(api, {"kind": "internal", "text": "Everyone uses the shared inbox.", "who": {"everyone": True}},
                 token="ben-test")
    assert mine["status"] == "active" and admin["status"] == "active"
    assert {mine["id"], admin["id"]} <= set(matched(api, "bot:ops"))
    rule(api, {"kind": "internal", "text": "Everyone has a badge.", "who": {"everyone": True}},
         token="cara-test", expected=403)


def test_a_group_and_a_bot_with_one_name_need_a_prefix(api):
    org(api)
    _add_group(api, "ops")
    bot_rule = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"names": ["bot:ops"]}})
    assert _ids(bot_rule["who"]["bots"]) == ["ops"] and _ids(bot_rule["who"]["groups"]) == []
    rule(api, {"kind": "internal", "text": "Ops team meets Mondays.", "who": {"names": ["ops"]}}, expected=400)
    group_rule = rule(api, {"kind": "internal", "text": "Ops team meets Mondays.",
                            "who": {"names": ["group:ops"]}})
    assert _ids(group_rule["who"]["groups"]) == ["ops"]
    rule(api, {"kind": "internal", "text": "Ops team meets Mondays.", "who": {"groups": ["bot:ops"]}}, expected=422)
    assert api.get("/api/v2/rules?who=ops", headers=headers()).status_code == 400
    assert api.get("/api/v2/rules?who=bot:ops", headers=headers()).status_code == 200


def test_spend_sentences_cover_the_company_default_and_the_pools_with_edit_links(api):
    org(api)
    put(api, "usage/limits", {"monthly_usd": 25})
    put(api, "usage/limits/ops", {"daily_usd": 5})
    put(api, "usage/pools/groups/support", {"weekly_usd": 40})
    put(api, "usage/pools/company", {"weekly_usd": 400})
    ops = {s["text"]: s for s in get(api, "bots/ops/rules")["settings"]}
    finance = {s["text"]: s for s in get(api, "bots/finance/rules")["settings"]}
    assert "Spends up to $5 a day" in ops
    assert ops["Spends up to $5 a day"]["edit"]["path"] == "/api/v2/usage/limits/ops"
    assert "Spends up to $400 a week across the team" in ops
    assert ops["Spends up to $400 a week across the team"]["edit"]["path"] == "/api/v2/usage/pools/company"
    assert "Follows the company limit: up to $25 a month" in finance
    assert finance["Follows the company limit: up to $25 a month"]["edit"]["path"] == "/api/v2/usage/limits"
    support = [s for s in finance if "across the Support group" in s]
    assert support and finance[support[0]]["edit"]["path"] == "/api/v2/usage/pools/groups/support"


def _routine_due(api):
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    start = (D.local_today("America/Los_Angeles") - timedelta(days=1)).isoformat()
    due = post(api, f"rules/{r['id']}/due", {"first_due_on": start, "repeats": "once", "owner": "ops"})
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
