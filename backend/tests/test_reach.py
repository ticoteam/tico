"""Reach over the API (docs/reach.md, backend/reach.py): settings and their inheritance, lines and their rules, who may
change what, and what a teammate who may not See someone reads. Acme is the fictional company here.

The org: Ana owns it, Ben is an admin, Cara leads Support, Fay is in Support Tier 2 (under Support) with the Finance bot,
Eve is on no team and owns the Helper bot."""
import json

from backend.store import encode
from backend.tests.test_api import api, get, headers  # noqa: F401  (the api fixture)


def org(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({
            "people": [
                {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                {"id": "ben", "email": "ben@acme.example"},
                {"id": "cara", "email": "cara@acme.example", "team": "support", "name": "Cara"},
                {"id": "eve", "email": "eve@acme.example", "name": "Eve"},
                {"id": "fay", "email": "fay@acme.example", "team": "support-tier-2", "name": "Fay"},
            ],
            "org_groups": {"support": {"name": "Support", "lead": "person:cara"},
                           "support-tier-2": {"name": "Support Tier 2", "parent": "support"}},
        }),))
        for pid, name in (("ben", "Ben"), ("cara", "Cara"), ("eve", "Eve"), ("fay", "Fay")):
            c.execute("INSERT OR IGNORE INTO humans(id, name, email) VALUES(?,?,?)", (pid, name, pid + "@acme.example"))
        c.execute("UPDATE bot_config SET team='support-tier-2', config=json_set(config, '$.team', 'support-tier-2') "
                  "WHERE bot='finance'")
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('helper', 'helper', 'Helper', 'active')")
        c.execute("INSERT INTO bot_config(bot, config, team, owner_id) VALUES('helper', '{}', '', 'eve')")


def put_setting(api, party, body, token="ana-test", expected=200):
    r = api.put(f"/api/v2/reach/settings/{party}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def add_line(api, body, token="ana-test", expected=200):
    r = api.post("/api/v2/reach/lines", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def edit_line(api, line_id, body, token="ana-test", expected=200):
    r = api.post(f"/api/v2/reach/lines/{line_id}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def reach(api, token="ana-test"):
    return get(api, "reach", token)


def rule_row(api, rule_id):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM org_rules WHERE id=?", (rule_id,)).fetchone())


def audit(api, obj):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit_log WHERE object=? ORDER BY created_at, id", (obj,))]


def test_each_field_comes_from_the_nearest_party_that_sets_it_then_everyone_then_the_default(api):
    org(api)
    put_setting(api, "*", {"mode": "closed"})
    put_setting(api, "group:support", {"chats_per_day": 50})
    saved = put_setting(api, "bot:finance", {"tasks_per_day": 5})
    assert saved["effective"]["tasks_per_day"] == 5
    found = reach(api)["effective"]
    assert found["bot:finance"] == {"mode": "closed", "chats_per_day": 50, "tasks_per_day": 5,
                                    "from": {"mode": "*", "chats_per_day": "group:support", "tasks_per_day": "bot:finance"}}
    # A team takes it from the teams above it; someone on no team from Everyone, then the default.
    assert found["group:support-tier-2"]["chats_per_day"] == 50
    assert found["group:support-tier-2"]["from"]["chats_per_day"] == "group:support"
    assert found["human:eve"]["chats_per_day"] == 999 and found["human:eve"]["from"]["chats_per_day"] is None
    assert found["human:eve"]["mode"] == "closed"
    # A nearer team wins over a farther one, and clearing a field (null) takes it from above again.
    put_setting(api, "group:support-tier-2", {"chats_per_day": 10})
    assert reach(api)["effective"]["human:fay"]["chats_per_day"] == 10
    cleared = put_setting(api, "bot:finance", {"tasks_per_day": None})
    assert cleared["setting"] == {"party": "bot:finance", "mode": None, "chats_per_day": None, "tasks_per_day": None}
    assert "bot:finance" not in {s["party"] for s in reach(api)["settings"]}      # nothing left set: the row goes
    assert reach(api)["effective"]["bot:finance"]["tasks_per_day"] == 999


def test_a_setting_change_is_audited_and_bad_values_and_unknown_parties_are_refused(api):
    org(api)
    put_setting(api, "group:support", {"chats_per_day": 50})
    put_setting(api, "group:support", {"mode": "closed"})                      # a field not sent stays
    rows = audit(api, "reach_setting:group:support")
    assert [r["before"] is None for r in rows] == [True, False]
    assert json.loads(rows[1]["after"]) == {"party": "group:support", "mode": "closed", "chats_per_day": 50,
                                            "tasks_per_day": None}
    put_setting(api, "group:support", {"mode": "sometimes"}, expected=422)
    put_setting(api, "group:support", {"chats_per_day": 100001}, expected=422)
    put_setting(api, "group:nowhere", {"mode": "open"}, expected=404)
    put_setting(api, "human:nobody", {"mode": "open"}, expected=404)
    put_setting(api, "ana", {"mode": "open"}, expected=422)                    # a party says what it is


def test_the_map_has_the_contract_shape(api):
    org(api)
    put_setting(api, "group:support", {"tasks_per_day": 20})
    made = add_line(api, {"sender": "bot:finance", "receiver": "human:eve", "per_day": 3})["line"]
    found = reach(api)
    assert {"defaults", "settings", "effective", "lines", "may_edit"} <= set(found)
    assert found["defaults"] == {"mode": "open", "chats_per_day": 999, "tasks_per_day": 999}
    assert {"party", "mode", "chats_per_day", "tasks_per_day"} <= set(found["settings"][0])
    assert {"mode", "chats_per_day", "tasks_per_day", "from"} <= set(found["effective"]["*"])
    line = next(row for row in found["lines"] if row["id"] == made["id"])
    assert {"id", "sender", "receiver", "per_day", "rule", "may_edit"} <= set(line)
    assert (line["sender"], line["receiver"], line["per_day"], line["rule"], line["may_edit"]) == \
        ("bot:finance", "human:eve", 3, None, True)
    assert {"*", "group:support", "human:cara", "bot:finance"} <= set(found["may_edit"])
    assert "human:ana" not in found["may_edit"]                                # the owner is never limited


def test_a_lines_rule_is_an_internal_org_rule_made_edited_and_retired_with_it(api):
    org(api)
    line = add_line(api, {"sender": "bot:finance", "receiver": "group:support", "per_day": 0,
                          "rule": "Send Support only refunds over $500."})["line"]
    rule = rule_row(api, line["rule"]["id"])
    assert line["rule"]["text"] == "Send Support only refunds over $500."
    assert (rule["kind"], rule["if_broken"], rule["status"]) == ("internal", "warn", "active")
    assert json.loads(rule["who"])["bots"] == ["finance"] and json.loads(rule["with_scope"])["groups"] == ["support"]
    # One line per sender and receiver, and a line runs between two parties.
    add_line(api, {"sender": "bot:finance", "receiver": "group:support"}, expected=409)
    add_line(api, {"sender": "bot:finance", "receiver": "bot:finance"}, expected=422)
    edited = edit_line(api, line["id"], {"rule": "Send Support only refunds over $900."})["line"]
    assert edited["rule"]["id"] == line["rule"]["id"] and edited["per_day"] == 0      # per_day not sent: kept
    assert rule_row(api, line["rule"]["id"])["text"] == "Send Support only refunds over $900."
    dropped = edit_line(api, line["id"], {"rule": None, "per_day": 4})["line"]
    assert dropped["rule"] is None and dropped["per_day"] == 4
    assert rule_row(api, line["rule"]["id"])["status"] == "retired"
    # A new rule on the line is a new org rule; removing the line retires it.
    again = edit_line(api, line["id"], {"rule": "Copy Cara on refunds."})["line"]["rule"]["id"]
    r = api.delete(f"/api/v2/reach/lines/{line['id']}", headers=headers())
    assert r.status_code == 200 and r.json() == {"id": line["id"], "removed": True}
    assert rule_row(api, again)["status"] == "retired"
    assert reach(api)["lines"] == []
    rows = audit(api, "reach_line:" + line["id"])
    assert rows[0]["before"] is None and rows[-1]["after"] is None and len(rows) == 5
    assert json.loads(rows[1]["before"])["rule"]["text"] == "Send Support only refunds over $500."
    assert json.loads(rows[1]["after"])["rule"]["text"] == "Send Support only refunds over $900."
    assert json.loads(rows[-1]["before"])["rule"]["text"] == "Copy Cara on refunds."     # what was removed, rule and all
    assert api.delete(f"/api/v2/reach/lines/{line['id']}", headers=headers("ana-test", "again")).status_code == 404


def test_everyone_as_sender_or_receiver(api):
    org(api)
    line = add_line(api, {"sender": "*", "receiver": "human:cara", "per_day": 5, "rule": "Ask Cara before noon."})
    rule = rule_row(api, line["line"]["rule"]["id"])
    assert json.loads(rule["who"])["everyone"] is True
    to_all = add_line(api, {"sender": "human:eve", "receiver": "*", "rule": "Keep it short."})["line"]
    with_scope = json.loads(rule_row(api, to_all["rule"]["id"])["with_scope"])
    assert not (with_scope["people"] or with_scope["groups"] or with_scope["bots"])     # With empty: anyone


def test_a_lead_changes_their_teams_and_their_people_and_bots_but_never_their_own(api):
    org(api)
    put_setting(api, "group:support-tier-2", {"mode": "closed"}, token="cara-test")
    put_setting(api, "human:fay", {"chats_per_day": 30}, token="cara-test")
    put_setting(api, "bot:finance", {"tasks_per_day": 3}, token="cara-test")
    add_line(api, {"sender": "bot:finance", "receiver": "human:ana", "per_day": 2, "rule": "Only escalations."},
             token="cara-test")
    put_setting(api, "human:cara", {"chats_per_day": 1000}, token="cara-test", expected=403)   # their own
    put_setting(api, "human:eve", {"chats_per_day": 1}, token="cara-test", expected=403)       # not on their team
    put_setting(api, "*", {"mode": "closed"}, token="cara-test", expected=403)                 # Everyone: admins
    add_line(api, {"sender": "human:cara", "receiver": "human:ana"}, token="cara-test", expected=403)
    add_line(api, {"sender": "human:eve", "receiver": "human:fay"}, token="cara-test", expected=403)
    mine = reach(api, "cara-test")
    assert set(mine["may_edit"]) == {"group:support", "group:support-tier-2", "human:fay", "bot:finance"}
    assert all(line["may_edit"] for line in mine["lines"])
    # The admin changes Everyone; nobody limits the owner.
    put_setting(api, "*", {"chats_per_day": 500}, token="ben-test")
    put_setting(api, "human:ana", {"chats_per_day": 5}, token="ben-test", expected=422)
    # A plain member reads the map but changes nothing in it.
    eve = reach(api, "eve-test")
    assert eve["lines"] and not any(line["may_edit"] for line in eve["lines"])
    line_id = eve["lines"][0]["id"]
    edit_line(api, line_id, {"per_day": 9}, token="eve-test", expected=403)
    assert api.delete(f"/api/v2/reach/lines/{line_id}", headers=headers("eve-test")).status_code == 403


def test_a_bots_manager_changes_that_bot(api):
    org(api)
    put_setting(api, "bot:helper", {"mode": "closed"}, token="eve-test")            # Eve owns Helper
    add_line(api, {"sender": "bot:helper", "receiver": "human:eve", "per_day": 10}, token="eve-test")
    assert "bot:helper" in reach(api, "eve-test")["may_edit"]
    put_setting(api, "bot:finance", {"mode": "closed"}, token="eve-test", expected=403)


def test_settings_and_lines_naming_someone_the_viewer_may_not_see_are_left_out(api):
    org(api)
    put_setting(api, "human:fay", {"chats_per_day": 30})
    add_line(api, {"sender": "bot:finance", "receiver": "human:fay", "per_day": 1})
    add_line(api, {"sender": "bot:finance", "receiver": "human:eve", "per_day": 2})
    r = api.put("/api/v2/humans/fay/access", headers=headers(), json={
        "see": {"people": ["cara"]}, "read": {"people": ["cara"]}, "write": {"people": ["cara"]}, "revision": 0})
    assert r.status_code == 200, r.text
    eve = reach(api, "eve-test")
    named = {s["party"] for s in eve["settings"]} | set(eve["effective"]) | set(eve["may_edit"])
    named |= {p for line in eve["lines"] for p in (line["sender"], line["receiver"])}
    assert "human:fay" not in named
    assert [(line["sender"], line["receiver"]) for line in eve["lines"]] == [("bot:finance", "human:eve")]
    cara = reach(api, "cara-test")
    assert "human:fay" in cara["effective"] and len(cara["lines"]) == 2


def test_an_edit_may_flip_a_line_and_its_rule_follows_but_never_onto_a_pair_that_has_one(api):
    org(api)
    line = add_line(api, {"sender": "bot:finance", "receiver": "human:eve", "per_day": 2, "rule": "Be brief."})["line"]
    flipped = edit_line(api, line["id"], {"sender": "human:eve", "receiver": "bot:finance", "per_day": 2,
                                          "rule": "Be brief."})["line"]
    assert (flipped["id"], flipped["sender"], flipped["receiver"]) == (line["id"], "human:eve", "bot:finance")
    rule = rule_row(api, flipped["rule"]["id"])
    assert json.loads(rule["who"])["people"] == ["eve"] and json.loads(rule["with_scope"])["bots"] == ["finance"]
    other = add_line(api, {"sender": "bot:finance", "receiver": "human:eve"})["line"]
    edit_line(api, other["id"], {"sender": "human:eve", "receiver": "bot:finance"}, expected=409)
    # A lead may not move a line onto a sender that is not theirs to change.
    mine = add_line(api, {"sender": "human:fay", "receiver": "human:ana"}, token="cara-test")["line"]
    edit_line(api, mine["id"], {"sender": "human:eve"}, token="cara-test", expected=403)
    assert edit_line(api, flipped["id"], {"rule": ""})["line"]["rule"] is None             # "" retires it too
    assert rule_row(api, flipped["rule"]["id"])["status"] == "retired"


def events(api, target):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events WHERE target=? ORDER BY created_at, id", (target,))]


def hide_fay_from_all_but_cara(api):
    r = api.put("/api/v2/humans/fay/access", headers=headers(), json={
        "see": {"people": ["cara"]}, "read": {"people": ["cara"]}, "write": {"people": ["cara"]}, "revision": 0})
    assert r.status_code == 200, r.text


def test_a_lines_rule_is_left_out_of_every_rule_read_for_whoever_may_not_see_the_line(api):
    org(api)
    hidden = add_line(api, {"sender": "bot:finance", "receiver": "human:fay", "rule": "Copy Fay on refunds."})["line"]
    shown = add_line(api, {"sender": "bot:finance", "receiver": "human:eve", "rule": "Copy Eve on payroll."})["line"]
    before = get(api, "rules/counts", "eve-test")
    hide_fay_from_all_but_cara(api)
    rid, other = hidden["rule"]["id"], shown["rule"]["id"]

    def ids(rules):
        return {r["id"] for r in rules}
    listed = get(api, "rules?kind=internal", "eve-test")["rules"]
    assert other in ids(listed) and rid not in ids(listed)
    assert rid not in ids(get(api, "rules?status=any", "eve-test")["rules"])
    assert rid not in ids(get(api, "rules?bot=finance", "eve-test")["rules"])
    get(api, "rules/" + rid, "eve-test", expected=404)
    tab = get(api, "bots/finance/rules", "eve-test")["internal"]["rules"]
    assert other in ids(tab) and rid not in ids(tab)
    by = get(api, "rules/by-teammate", "eve-test")
    assert rid not in ids(by["rules"]) and all(rid not in b["who"] + b["with"] for b in by["bots"] + by["people"])
    assert get(api, "rules/counts", "eve-test")["internal"] == before["internal"] - 1
    # Nor is it changed by someone who may not read it: it is not there.
    r = api.post("/api/v2/rules/" + rid, json={"version": 1, "text": "Copy Eve instead."}, headers=headers("eve-test"))
    assert r.status_code == 404, r.text
    # Whoever sees the line reads its rule.
    assert rid in ids(get(api, "rules?kind=internal", "cara-test")["rules"])
    assert get(api, "rules/" + rid, "cara-test")["text"] == "Copy Fay on refunds."
    assert get(api, "rules/counts", "cara-test")["internal"] == before["internal"]


def test_a_lines_rule_takes_its_who_and_with_from_the_line_only(api):
    org(api)
    line = add_line(api, {"sender": "bot:finance", "receiver": "human:eve", "rule": "Be brief."})["line"]
    rid = line["rule"]["id"]
    for change in ({"who": {"everyone": True}}, {"with": {"people": ["fay"]}}):
        r = api.post("/api/v2/rules/" + rid, json={"version": 1, **change}, headers=headers("ben-test"))
        assert r.status_code == 422 and "change the line" in r.text, r.text
    row = rule_row(api, rid)
    assert json.loads(row["who"])["bots"] == ["finance"] and json.loads(row["with_scope"])["people"] == ["eve"]
    # Its sentence may still change through the rules API, and the line reads it.
    r = api.post("/api/v2/rules/" + rid, json={"version": 1, "text": "Be very brief."}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    assert next(x for x in reach(api)["lines"] if x["id"] == line["id"])["rule"]["text"] == "Be very brief."
    # Moving the line still moves its rule.
    edit_line(api, line["id"], {"receiver": "human:cara"})
    assert json.loads(rule_row(api, rid)["with_scope"])["people"] == ["cara"]


def test_a_bots_manager_is_found_by_the_bots_id_not_its_slug(api):
    org(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('0190-scout', 'scout', 'Scout', 'active')")
        c.execute("INSERT INTO bot_config(bot, config, team, owner_id) VALUES('0190-scout', '{}', '', 'eve')")
    put_setting(api, "bot:scout", {"mode": "closed"}, token="eve-test")              # Eve owns Scout
    assert "bot:0190-scout" in reach(api, "eve-test")["may_edit"]
    put_setting(api, "bot:scout", {"mode": "open"}, token="fay-test", expected=403)


def test_who_changed_a_setting_is_null_to_a_reader_who_may_not_see_them(api):
    org(api)
    put_setting(api, "bot:finance", {"tasks_per_day": 3}, token="cara-test")
    put_setting(api, "human:fay", {"chats_per_day": 30}, token="cara-test")
    r = api.put("/api/v2/humans/cara/access", headers=headers(), json={
        "see": {"people": ["fay"]}, "read": {"people": ["fay"]}, "write": {"people": ["fay"]}, "revision": 0})
    assert r.status_code == 200, r.text
    eve = {s["party"]: s for s in reach(api, "eve-test")["settings"]}
    assert eve["bot:finance"]["tasks_per_day"] == 3 and eve["bot:finance"]["updated_by"] is None
    ana = {s["party"]: s for s in reach(api)["settings"]}
    assert ana["bot:finance"]["updated_by"] == "human:cara"


def test_a_rule_sentence_changed_alone_is_a_change_to_the_line(api):
    org(api)
    line = add_line(api, {"sender": "bot:finance", "receiver": "human:eve", "per_day": 2, "rule": "Be brief."})["line"]
    with api.app.state.store.read() as c:
        made = dict(c.execute("SELECT * FROM reach_lines WHERE id=?", (line["id"],)).fetchone())
    edited = edit_line(api, line["id"], {"rule": "Be very brief."}, token="cara-test")["line"]
    assert edited["rule"] == {"id": line["rule"]["id"], "text": "Be very brief."}
    with api.app.state.store.read() as c:
        stored = dict(c.execute("SELECT * FROM reach_lines WHERE id=?", (line["id"],)).fetchone())
    assert stored["updated_by_id"] == "human:cara" and stored["updated_at"] >= made["updated_at"]
    assert stored["updated_at"] == edited["updated_at"]
    rows = audit(api, "reach_line:" + line["id"])
    assert len(rows) == 2 and json.loads(rows[1]["after"])["rule"]["text"] == "Be very brief."
    assert [e["action"] for e in events(api, "reach_line:" + line["id"])] == ["reach.line_added", "reach.line_changed"]
    # The same sentence again changes nothing.
    edit_line(api, line["id"], {"rule": "Be very brief.", "per_day": 2}, token="cara-test")
    assert len(audit(api, "reach_line:" + line["id"])) == 2
