"""The org chart is edited in place: a person or a bot moves under another person or bot, and
the one rule is that you may edit yourself and whatever reports up to you (backend/people.py
`manages`, Ana 2026-09-18)."""
import json

from backend.store import encode
from backend.tests.test_api import api, get, post  # noqa: F401


def roster(api, people):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode({"people": people}),))


def revision(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]


def tree(api, token="ana-test"):
    view = get(api, "org", token=token)
    people = {p["id"]: p["org_parent"] for p in view["people"]}
    bots = {b["id"]: b["org_parent"] for b in view["bots"]}
    return people, bots


def test_people_and_bots_move_under_people_and_bots_and_only_managers_move_them(api):
    roster(api, [{"id": "ana", "email": "ana@acme.example", "name": "Ana", "primary_for": ["*"]},
                 {"id": "ben", "email": "ben@acme.example", "name": "Ben", "reports_to": "ana", "primary_for": ["cpo", "product-design"]},
                 {"id": "cara", "email": "cara@acme.example", "name": "Cara", "reports_to": "ana"}])
    # Ben moves Cara under himself: Cara reports to Ana, so Ben may not.
    post(api, "people/cara", {"reports_to": "ben"}, token="ben-test", expected=403)
    post(api, "people/cara", {"reports_to": "ben"})                     # the owner may
    people, _ = tree(api)
    assert people["cara"] == "p:ben"
    # Now Cara is Ben's: Ben may edit his profile, and put a bot under him.
    post(api, "people/cara", {"goals": "Ship the mobile app"}, token="ben-test")
    post(api, "bots/cpo/definition", {"reports_to": "human:cara", "expected_revision": revision(api, "cpo")}, token="ben-test")
    _, bots = tree(api)
    assert bots["cpo"] == "p:cara"
    # A bot under a person is that person's to talk to; Cara may not touch Ben.
    post(api, "chat/cpo", {"text": "Hello"}, token="cara-test")
    post(api, "people/ben", {"goals": "Mine now"}, token="cara-test", expected=403)
    post(api, "people/cara", {"goals": "My own goals"}, token="cara-test")     # yourself, always
    # Nobody moves under their own report, and nobody reports to themselves.
    post(api, "people/ben", {"reports_to": "cara"}, expected=422)
    post(api, "people/ben", {"reports_to": "ben"}, expected=422)
    post(api, "people/ben", {"reports_to": "nobody"}, expected=404)
    # A bot moves back under a bot; a circle is refused as before.
    post(api, "bots/cpo/definition", {"reports_to": "coo", "expected_revision": revision(api, "cpo")})
    _, bots = tree(api)
    assert bots["cpo"] == "b:coo"
    post(api, "bots/coo/definition", {"reports_to": "cpo", "expected_revision": revision(api, "coo")}, expected=422)


def test_botops_applies_a_persons_bot_change_as_that_person(api):
    # Ana, 2026-09-25: "i told you to set scribe's reports to to ben and move it there".
    from backend.store import H
    from backend.tests.test_api import setup_attempt
    roster(api, [{"id": "ana", "email": "ana@acme.example", "name": "Ana", "primary_for": ["*"]},
                 {"id": "ben", "email": "ben@acme.example", "name": "Ben", "reports_to": "ana"},
                 {"id": "cara", "email": "cara@acme.example", "name": "Cara", "reports_to": "ana"}])
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('botops',?, 'ana')",
                  (encode({"name": "botops", "runtime": "fake", "status": "active"}),))
    _, _, botops = setup_attempt(api, "botops")
    _, _, ops = setup_attempt(api, "ops")
    ask = post(api, "chat/botops", {"text": "Move ops under Ben"})
    mid = ask.get("message", ask)["id"]
    body = lambda bot, **kw: {"reports_to": "human:ben", "expected_revision": revision(api, bot), **kw}
    # The default requester applies without opt-in; another bot may not cite a human request.
    post(api, "bots/ops/definition", body("ops"), token=botops["token"])
    post(api, "bots/ops/definition", body("ops", on_behalf_of=mid), token=ops["token"], expected=403)
    post(api, "bots/ops/definition", body("ops", on_behalf_of=mid), token=botops["token"])
    _, bots = tree(api)
    assert bots["ops"] == "p:ben"
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor,detail_json FROM events WHERE action='bot.definition_delegated'").fetchone()
        assert event["actor"] == "human:ana" and json.loads(event["detail_json"])["on_behalf_of"] == "human:ana"
    # The person's own limits hold: Cara may not move a bot he does not manage.
    theirs = post(api, "chat/botops", {"text": "Move ops under me"})     # rewritten as Cara's below
    theirs = theirs.get("message", theirs)["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET from_actor='human:cara' WHERE id=?", (theirs,))
    post(api, "bots/ops/definition", body("ops", reports_to="human:cara", on_behalf_of=theirs),
         token=botops["token"], expected=403)
    # A message to another bot, or one older than a week, is not a request to BotOps.
    other = post(api, "chat/cpo", {"text": "Move ops under Cara"})
    post(api, "bots/ops/definition", body("ops", on_behalf_of=other.get("message", other)["id"]), token=botops["token"], expected=403)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created=? WHERE id=?", (H.shift(H.now(), days=-8), mid))
    post(api, "bots/ops/definition", body("ops", reports_to="coo", on_behalf_of=mid), token=botops["token"], expected=403)
