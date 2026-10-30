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
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)


def set_roster(api, doc):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(doc),))


def as_bot(api, slug, token):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO agents(bot,harness,token_hash,created,created_by) VALUES(?,?,?,?,?)",
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
        c.execute("UPDATE bot_config SET team='support-tier-2', config_json=json_set(config_json, '$.team', 'support-tier-2') WHERE bot='finance'")
        c.execute("UPDATE bot_config SET team='product', config_json=json_set(config_json, '$.team', 'product') WHERE bot='ops'")
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
        return [dict(r) for r in c.execute("SELECT * FROM audit_log WHERE object=? ORDER BY at, id", ("org_rule:" + rule_id,))]


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
        c.execute("INSERT INTO bots(slug, display_name, state) VALUES('late-bot', 'Late', 'active')")
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('late-bot','{}','', 'ana')")
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
        c.execute("UPDATE bot_config SET team='support-tier-2', config_json=json_set(config_json, '$.team', 'support-tier-2') WHERE bot='ops'")
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
    assert rows[-1]["via"] and json.loads(rows[-1]["after_json"])["status"] == "active"


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
        c.execute("UPDATE org_rules SET cite_json=? WHERE id=?",
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
        c.execute("UPDATE bot_config SET access_json=? WHERE bot='ops'",
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


def test_editing_a_due_date_keeps_its_history(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Ops answers within a day.", "who": {"bots": ["ops"]}})
    start = (D.local_today("America/Los_Angeles") - timedelta(days=1)).isoformat()
    due = post(api, f"rules/{r['id']}/due", {"first_due": start, "repeats": "once", "owner": "ops"})
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


def test_a_page_of_long_rules_stays_under_its_byte_budget(api):
    org(api)
    long_text = "x" * 2000
    for n in range(50):
        rule(api, {"kind": "internal", "text": f"Rule {n} says hello.", "description": long_text,
                   "who": {"bots": ["ops"]}})
    page = api.get("/api/v2/rules?limit=50", headers=headers())
    assert page.status_code == 200 and len(page.content) <= 256 * 1024
    bot = api.get("/api/v2/bots/ops/rules", headers=headers())
    assert bot.status_code == 200 and len(bot.content) <= 256 * 1024


def test_a_turn_limit_set_on_the_bot_reads_back_as_its_sentence(api):
    org(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json, '$.turns_per_hour', 7) WHERE bot='ops'")
    texts = [s["text"] for s in get(api, "bots/ops/rules")["settings"]]
    assert "Up to 7 turns per hour" in texts
    assert "Team default: 2500 turns per day" in texts
    assert "ops cannot send mail outside Tico" in texts


def test_a_bot_with_no_team_of_its_own_is_in_its_managers_group(api):
    org(api)
    support = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})["id"]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug, display_name, state) VALUES('intern', 'Intern', 'active')")
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator,reports_to) VALUES('intern','{}','','ana','finance')")
    assert support in matched(api, "bot:intern")
