"""Reach over the API (docs/reach.md, backend/reach.py): settings and their inheritance, lines and their rules, who may
change what, and what a teammate who may not See someone reads. Acme is the fictional company here.

The org: Ana owns it, Ben is an admin, Cara leads Support, Fay is in Support Tier 2 (under Support) with the Finance bot,
Eve is on no team and owns the Helper bot."""

import json

from backend import bot_settings as BS
from backend.reach import may_book
from backend.store import encode
from backend.tests.helpers.api import get, headers  # noqa: F401  (the api fixture)
import pytest


@pytest.fixture(autouse=True)
def nearby_default(monkeypatch):
    """conftest.py starts other tests' bots open; these test the shipped default."""
    monkeypatch.delenv("TICO_REACH_DEFAULT", raising=False)


def org(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE registry_metadata SET value=? WHERE key='people'",
            (
                encode(
                    {
                        "people": [
                            {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                            {"id": "ben", "email": "ben@acme.example"},
                            {"id": "cara", "email": "cara@acme.example", "team": "support", "name": "Cara"},
                            {"id": "eve", "email": "eve@acme.example", "name": "Eve"},
                            {"id": "fay", "email": "fay@acme.example", "team": "support-tier-2", "name": "Fay"},
                        ],
                        "org_groups": {
                            "support": {"name": "Support", "lead": "person:cara"},
                            "support-tier-2": {"name": "Support Tier 2", "parent": "support"},
                        },
                    }
                ),
            ),
        )
        for pid, name in (("ben", "Ben"), ("cara", "Cara"), ("eve", "Eve"), ("fay", "Fay")):
            c.execute("INSERT OR IGNORE INTO humans(id, name, email) VALUES(?,?,?)", (pid, name, pid + "@acme.example"))
        c.execute("UPDATE bot_config SET team='support-tier-2' WHERE bot='finance'")
        BS.update(c, "finance", {"team": "support-tier-2"})
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('helper', 'helper', 'Helper', 'active')")
        c.execute("INSERT INTO bot_config(bot, team, owner_id) VALUES('helper', '', 'eve')")


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
    assert found["bot:finance"] == {
        "mode": "closed",
        "chats_per_day": 50,
        "tasks_per_day": 5,
        "book": "anyone",
        "from": {"mode": "*", "chats_per_day": "group:support", "tasks_per_day": "bot:finance", "book": None},
    }
    # A team takes it from the teams above it; someone on no team from Everyone, then the default.
    assert found["group:support-tier-2"]["chats_per_day"] == 50
    assert found["group:support-tier-2"]["from"]["chats_per_day"] == "group:support"
    assert found["human:eve"]["chats_per_day"] == 999 and found["human:eve"]["from"]["chats_per_day"] is None
    assert found["human:eve"]["mode"] == "closed"
    # A nearer team wins over a farther one, and clearing a field (null) takes it from above again.
    put_setting(api, "group:support-tier-2", {"chats_per_day": 10})
    assert reach(api)["effective"]["human:fay"]["chats_per_day"] == 10
    cleared = put_setting(api, "bot:finance", {"tasks_per_day": None})
    assert cleared["setting"] == {
        "party": "bot:finance",
        "mode": None,
        "chats_per_day": None,
        "tasks_per_day": None,
        "book": None,
    }
    assert "bot:finance" not in {s["party"] for s in reach(api)["settings"]}  # nothing left set: the row goes
    assert reach(api)["effective"]["bot:finance"]["tasks_per_day"] == 999


def test_a_setting_change_is_audited_and_bad_values_and_unknown_parties_are_refused(api):
    org(api)
    put_setting(api, "group:support", {"chats_per_day": 50})
    put_setting(api, "group:support", {"mode": "closed"})  # a field not sent stays
    rows = audit(api, "reach_setting:group:support")
    assert [r["before"] is None for r in rows] == [True, False]
    assert json.loads(rows[1]["after"]) == {
        "party": "group:support",
        "mode": "closed",
        "chats_per_day": 50,
        "tasks_per_day": None,
        "book": None,
    }
    put_setting(api, "group:support", {"mode": "sometimes"}, expected=422)
    put_setting(api, "group:support", {"chats_per_day": 100001}, expected=422)
    put_setting(api, "group:nowhere", {"mode": "open"}, expected=404)
    put_setting(api, "human:nobody", {"mode": "open"}, expected=404)
    put_setting(api, "ana", {"mode": "open"}, expected=422)  # a party says what it is


def test_a_lines_rule_is_an_internal_org_rule_made_edited_and_retired_with_it(api):
    org(api)
    line = add_line(
        api,
        {
            "sender": "bot:finance",
            "receiver": "group:support",
            "per_day": 0,
            "rule": "Send Support only refunds over $500.",
        },
    )["line"]
    rule = rule_row(api, line["rule"]["id"])
    assert line["rule"]["text"] == "Send Support only refunds over $500."
    assert (rule["kind"], rule["if_broken"], rule["status"]) == ("internal", "warn", "active")
    assert json.loads(rule["who"])["bots"] == ["finance"] and json.loads(rule["with_scope"])["groups"] == ["support"]
    # One line per sender and receiver, and a line runs between two parties.
    add_line(api, {"sender": "bot:finance", "receiver": "group:support"}, expected=409)
    add_line(api, {"sender": "bot:finance", "receiver": "bot:finance"}, expected=422)
    edited = edit_line(api, line["id"], {"rule": "Send Support only refunds over $900."})["line"]
    assert edited["rule"]["id"] == line["rule"]["id"] and edited["per_day"] == 0  # per_day not sent: kept
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
    assert rows[0]["before"] is None and rows[-1]["after"] is None and len(rows) == 4
    assert api.delete(f"/api/v2/reach/lines/{line['id']}", headers=headers("ana-test", "again")).status_code == 404


def test_everyone_as_sender_or_receiver(api):
    org(api)
    line = add_line(api, {"sender": "*", "receiver": "human:cara", "per_day": 5, "rule": "Ask Cara before noon."})
    rule = rule_row(api, line["line"]["rule"]["id"])
    assert json.loads(rule["who"])["everyone"] is True
    to_all = add_line(api, {"sender": "human:eve", "receiver": "*", "rule": "Keep it short."})["line"]
    with_scope = json.loads(rule_row(api, to_all["rule"]["id"])["with_scope"])
    assert not (with_scope["people"] or with_scope["groups"] or with_scope["bots"])  # With empty: anyone


def test_a_lead_changes_their_teams_and_their_people_and_bots_but_never_their_own(api):
    org(api)
    put_setting(api, "group:support-tier-2", {"mode": "closed"}, token="cara-test")
    put_setting(api, "human:fay", {"chats_per_day": 30}, token="cara-test")
    put_setting(api, "bot:finance", {"tasks_per_day": 3}, token="cara-test")
    add_line(
        api,
        {"sender": "bot:finance", "receiver": "human:ana", "per_day": 2, "rule": "Only escalations."},
        token="cara-test",
    )
    put_setting(api, "human:cara", {"chats_per_day": 1000}, token="cara-test", expected=403)  # their own
    put_setting(api, "human:eve", {"chats_per_day": 1}, token="cara-test", expected=403)  # not on their team
    put_setting(api, "*", {"mode": "closed"}, token="cara-test", expected=403)  # Everyone: admins
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
    put_setting(api, "bot:helper", {"mode": "closed"}, token="eve-test")  # Eve owns Helper
    add_line(api, {"sender": "bot:helper", "receiver": "human:eve", "per_day": 10}, token="eve-test")
    assert "bot:helper" in reach(api, "eve-test")["may_edit"]
    put_setting(api, "bot:finance", {"mode": "closed"}, token="eve-test", expected=403)


def test_settings_and_lines_naming_someone_the_viewer_may_not_see_are_left_out(api):
    org(api)
    put_setting(api, "human:fay", {"chats_per_day": 30})
    add_line(api, {"sender": "bot:finance", "receiver": "human:fay", "per_day": 1})
    add_line(api, {"sender": "bot:finance", "receiver": "human:eve", "per_day": 2})
    r = api.put(
        "/api/v2/humans/fay/access",
        headers=headers(),
        json={"see": {"people": ["cara"]}, "read": {"people": ["cara"]}, "write": {"people": ["cara"]}, "revision": 0},
    )
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
    flipped = edit_line(
        api, line["id"], {"sender": "human:eve", "receiver": "bot:finance", "per_day": 2, "rule": "Be brief."}
    )["line"]
    assert (flipped["id"], flipped["sender"], flipped["receiver"]) == (line["id"], "human:eve", "bot:finance")
    rule = rule_row(api, flipped["rule"]["id"])
    assert json.loads(rule["who"])["people"] == ["eve"] and json.loads(rule["with_scope"])["bots"] == ["finance"]
    other = add_line(api, {"sender": "bot:finance", "receiver": "human:eve"})["line"]
    edit_line(api, other["id"], {"sender": "human:eve", "receiver": "bot:finance"}, expected=409)
    # A lead may not move a line onto a sender that is not theirs to change.
    mine = add_line(api, {"sender": "human:fay", "receiver": "human:ana"}, token="cara-test")["line"]
    edit_line(api, mine["id"], {"sender": "human:eve"}, token="cara-test", expected=403)
    assert edit_line(api, flipped["id"], {"rule": ""})["line"]["rule"] is None  # "" retires it too
    assert rule_row(api, flipped["rule"]["id"])["status"] == "retired"


# ----------------------------------------------------------------------------------------- who bots may book (may_book)
def may(api, actor, target):
    with api.app.state.store.read() as c:
        return may_book(c, actor, target)


def events(api, action, target):
    with api.app.state.store.read() as c:
        return [
            json.loads(r[0])
            for r in c.execute("SELECT detail FROM events WHERE action=? AND target=?", (action, target))
        ]


def test_who_bots_may_book_is_anyone_until_a_setting_narrows_it_and_a_team_passes_it_down(api):
    org(api)
    assert reach(api)["defaults"]["book"] == "anyone"
    assert may(api, "bot:finance", "human:cara")[0]  # the default: anyone
    put_setting(api, "group:support", {"book": "nearby"})
    found = reach(api)["effective"]
    # Finance is on Support Tier 2, under Support, so it takes the setting from Support; Helper is on no team.
    assert found["bot:finance"]["book"] == "nearby" and found["bot:finance"]["from"]["book"] == "group:support"
    assert found["bot:helper"]["book"] == "anyone" and found["bot:helper"]["from"]["book"] is None
    # A bot's own setting wins over its team's; clearing it takes the team's again.
    put_setting(api, "bot:finance", {"book": "none"})
    assert reach(api)["effective"]["bot:finance"]["from"]["book"] == "bot:finance"
    assert not may(api, "bot:finance", "human:fay")[0]
    put_setting(api, "bot:finance", {"book": None})
    assert reach(api)["effective"]["bot:finance"]["book"] == "nearby"


def test_nearby_books_only_who_the_nearby_mode_reaches_and_the_reason_says_where_it_was_set(api):
    org(api)
    put_setting(api, "group:support", {"book": "nearby"})
    assert may(api, "bot:finance", "human:ana")[0]  # its manager (the owner: it has none on the chart)
    assert may(api, "bot:finance", "human:fay")[0]  # on its team
    assert may(api, "bot:finance", "bot:helper")[0]  # a peer
    allowed, reason = may(api, "bot:finance", "human:cara")  # in Support, but off finance's part of the chart
    assert not allowed
    assert reason.startswith("Who bots may book is Nearby for Support: ") and "Nothing was booked." in reason
    # A line from it adds its receiver, as the nearby mode does, and a line that blocks the pair takes its neighbor away.
    add_line(api, {"sender": "bot:finance", "receiver": "human:cara"})
    assert may(api, "bot:finance", "human:cara")[0]
    add_line(api, {"sender": "bot:finance", "receiver": "human:fay", "per_day": 0})
    assert not may(api, "bot:finance", "human:fay")[0]


def test_none_books_no_one_but_a_person_the_hub_and_the_assistant_are_never_limited(api):
    org(api)
    put_setting(api, "*", {"book": "none"})
    allowed, reason = may(api, "bot:finance", "human:fay")
    assert not allowed and reason.startswith("Who bots may book is No one for Everyone, so ")
    assert not may(api, "bot:helper", "human:ana")[0]
    assert may(api, "human:ana", "human:cara")[0]
    assert may(api, "human:fay", "human:cara")[0]
    assert may(api, "keeper", "human:cara")[0]
    assert may(api, "bot:assistant", "human:cara")[0]  # the Assistant (TICO_ASSISTANT_BOT) is a built-in helper


def test_the_book_setting_is_validated_audited_and_its_change_is_an_event(api):
    org(api)
    put_setting(api, "group:support", {"book": "sometimes"}, expected=422)
    put_setting(api, "group:support", {"book": "nearby"})
    assert json.loads(audit(api, "reach_setting:group:support")[-1]["after"])["book"] == "nearby"
    assert events(api, "reach.setting_changed", "reach_setting:group:support")[-1]["fields"] == ["book"]
    assert reach(api)["settings"][0]["book"] == "nearby"
