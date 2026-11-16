"""Flow's "Ping your bots" (backend/flow_ping.py): who a ping reaches, the message each bot gets, and a double press.
Acme is the fictional company; the chart is helpers/reach_nearby's (cpo reports to Ben; product-design, finance and
doc-updater around it; ops two levels down)."""

from backend import reach
from backend.tests.helpers.api import get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.helpers.task_caps import as_bot
from backend.tests.helpers.reach_nearby import chart, nearby_default  # noqa: F401  (the shipped reach default)


def ping(api, token="ana-test", expected=200, **body):
    r = api.post("/api/v2/flow/ping", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def names(found):
    return sorted(b["id"] for b in found)


def pings(api, bot):
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT sender_id, body FROM messages WHERE recipient_id=? AND json_extract(refs, '$.get_aligned') IS NOT NULL",
            ("bot:" + bot,),
        ).fetchall()
    return [dict(r) for r in rows]


def test_a_person_pings_their_reports_once_and_the_message_names_them(api):
    chart(api)
    first = ping(api, "ben-test")
    assert names(first["pinged"]) == ["cpo"] and first["recent"] == []  # only cpo reports to Ben
    [sent] = pings(api, "cpo")
    assert sent["sender_id"] == "human:ben"
    assert "skills/get-aligned/SKILL.md" in sent["body"] and "Ben" in sent["body"]
    again = ping(api, "ben-test")  # a double press asks once
    assert again["pinged"] == [] and names(again["recent"]) == ["cpo"]
    assert len(pings(api, "cpo")) == 1
    with api.app.state.store.read() as c:  # and lets cpo ask Ben its questions
        assert reach.pinged(c, "bot:cpo", "human:ben")


def test_a_ping_reaches_one_bot_or_one_teams_bots(api):
    chart(api)
    assert names(ping(api, scope="bot", id="ops")["pinged"]) == ["ops"]
    team = ping(api, scope="group", id="product")["pinged"]
    assert names(team) == ["cpo", "doc-updater", "product-design"]
    assert ping(api, expected=404, scope="bot", id="nobody")["error"]["code"] == "not_found"
    assert ping(api, expected=404, scope="group", id="nowhere")["error"]["code"] == "not_found"
    assert ping(api, expected=422, scope="everyone")["error"]["code"] == "invalid"


def test_only_people_ping_and_only_bots_they_may_read(api):
    chart(api)
    as_bot(api, "finance", "finance-test")
    assert ping(api, "finance-test", expected=403, scope="bot", id="ops")["error"]["code"] == "identity"
    current = get(api, "bots/ops/access", "ana-test")
    r = api.put(
        "/api/v2/bots/ops/access",
        json={
            "see": {"everyone": True},
            "read": {"people": ["ana"]},
            "write": {"everyone": True},
            "revision": current["revision"],
        },
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    assert ping(api, "cara-test", expected=404, scope="bot", id="ops")["error"]["code"] == "not_found"
    assert pings(api, "ops") == []
    assert names(ping(api, scope="bot", id="ops")["pinged"]) == ["ops"]  # the owner reads every bot


def test_a_renamed_bot_the_person_may_not_read_is_not_pinged(api):
    """The unreadable set is bot ids: a bot whose slug is not its id (renamed, or made with a UUID id) is still kept
    out of a ping by someone who may not read it."""
    from backend.store import H
    from backend.tests.helpers.api import restrict

    with api.app.state.store.transaction() as c:
        H.rename_bot(c, "ops", "renamed-ops")
        restrict(c, "ops", people=["ana"])
    assert ping(api, "cara-test", expected=404, scope="bot", id="renamed-ops")["error"]["code"] == "not_found"
    assert pings(api, "ops") == []


def test_a_report_still_being_set_up_is_left_out_and_the_rest_are_pinged(api):
    """A planned bot cannot take a message (H.say refuses it), so it is not a target: one new report never stops the
    ping to the others."""
    chart(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='planned' WHERE id='scout'")
    got = ping(api)  # Ana's reports: Scout (planned) and the bots with no manager
    assert "scout" not in names(got["pinged"]) and got["pinged"]
