"""Key end dates read from the vendor (backend/api_tools.py `vendor_end_date`) and the Renew task they open
(backend/reconnect_tasks.py): GitHub's token expiration header and Cloudflare's token verify fill a key's end date on
each check that works, replacing a typed one; from 14 days out the Tools page's owner gets one Renew task with a key
card, which closes when the key gets a new value.

Ana owns the company (a credential administrator). Ops stands in as the Tools page's owner."""

import json
from datetime import timedelta

from backend import api_tools, reconnect_tasks
from backend.store import H
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import setup

OLD = "acme-gh-synthetic-old-0011223344"
NEW = "acme-gh-synthetic-new-5566778899"


def day(n):
    return (api_tools._today() + timedelta(days=n)).isoformat()


def checks(monkeypatch):
    def answer(url, hdrs, params, read_body=False):
        answer.asked.append(read_body)
        return answer.reply

    answer.asked = []
    answer.reply = api_tools.Reply(200)
    monkeypatch.setattr(api_tools, "send_check", answer)
    return answer


def github(api, owner=True):
    setup(api)
    if owner:
        put(api, "page-owners/tools", {"bot": "ops"})
    tid = tool(api, "GitHub", service="github", base_url="https://api.github.com")["id"]
    kid = key(api, tid, OLD)["id"]
    level(api, tid, "bot:cpo", "read_write")
    return tid, kid


def stored(api, kid):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT expires_on, expires_source FROM credentials WHERE id=?", (kid,)).fetchone()
        return tuple(row)


def tasks(api, tid):
    with api.app.state.store.read() as c:
        return [
            dict(H.task(c, r[0]))
            for r in c.execute(
                "SELECT DISTINCT json_extract(detail,'$.task') FROM events WHERE action=? AND target=?",
                (reconnect_tasks.EVENT, tid),
            )
        ]


def typed(api, tid, kid, on):
    r = api.patch(f"/api/v2/api-tools/{tid}/keys/{kid}", json={"expires_on": on}, headers=headers())
    assert r.status_code == 200, r.text


def said(api, task):
    with api.app.state.store.read() as c:
        return " ".join(
            str(r[0] or "")
            for r in c.execute("SELECT body FROM messages WHERE conversation_id=?", (task["conversation_id"],))
        )


def the_key(api, tid):
    [k] = get(api, f"api-tools/{tid}")["keys"]
    return k


def test_utc_day_reads_the_vendors_formats_and_refuses_anything_else():
    assert api_tools._utc_day("2026-12-01 09:00:00 UTC") == "2026-12-01"
    assert api_tools._utc_day("2026-12-01T00:00:00Z") == "2026-12-01"
    assert api_tools._utc_day("2026-12-01 23:30:00 -0800") == "2026-12-02"  # 07:30 UTC the next day
    assert api_tools._utc_day("2026-12-01T01:00:00+02:00") == "2026-11-30"
    assert api_tools._utc_day("2026-12-01") == "2026-12-01"
    assert api_tools._utc_day("next Tuesday") is None
    assert api_tools._utc_day("2026-13-40 00:00:00 UTC") is None


def test_githubs_header_fills_the_end_date_and_replaces_a_typed_one(api, monkeypatch):
    tid, kid = github(api)
    answer = checks(monkeypatch)
    typed(api, tid, kid, day(60))
    assert stored(api, kid) == (day(60), "person")
    answer.reply = api_tools.Reply(200, ends_header=f"{day(40)} 09:00:00 UTC")
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Working"
    assert stored(api, kid) == (day(40), "vendor")  # AC-1, AC-3: the vendor is right
    shown = the_key(api, tid)
    assert (shown["expires_on"], shown["expires_source"]) == (day(40), "vendor")
    assert answer.asked == [False]  # GitHub's date is a header: no body is read


def test_cloudflares_verify_reply_fills_the_end_date(api, monkeypatch):
    setup(api)
    tid = tool(api, "Cloudflare", service="cloudflare", base_url="https://api.cloudflare.com/client/v4")["id"]
    kid = key(api, tid, OLD)["id"]
    answer = checks(monkeypatch)
    answer.reply = api_tools.Reply(
        200,
        body=json.dumps({"result": {"id": "t1", "status": "active", "expires_on": f"{day(40)}T00:00:00Z"}}).encode(),
    )
    post(api, f"api-tools/{tid}/check", {})
    assert stored(api, kid) == (day(40), "vendor")  # AC-2
    assert answer.asked == [True]
    answer.reply = api_tools.Reply(200, body=b"<html>not json")
    post(api, f"api-tools/{tid}/check", {})
    assert stored(api, kid) == (day(40), "vendor")  # an unreadable reply changes nothing


def test_a_reply_with_no_end_date_clears_the_vendors_date_and_keeps_a_typed_one(api, monkeypatch):
    tid, kid = github(api)
    answer = checks(monkeypatch)
    answer.reply = api_tools.Reply(200, ends_header=f"{day(40)} 09:00:00 UTC")
    post(api, f"api-tools/{tid}/check", {})
    answer.reply = api_tools.Reply(200)  # a token with no end date now
    post(api, f"api-tools/{tid}/check", {})
    assert stored(api, kid) == (None, None)  # AC-4
    typed(api, tid, kid, day(60))
    post(api, f"api-tools/{tid}/check", {})
    assert stored(api, kid) == (day(60), "person")


def test_a_failed_check_or_an_unreadable_header_keeps_the_date(api, monkeypatch):
    tid, kid = github(api)
    answer = checks(monkeypatch)
    answer.reply = api_tools.Reply(200, ends_header=f"{day(40)} 09:00:00 UTC")
    post(api, f"api-tools/{tid}/check", {})
    for reply in (api_tools.Reply(401), api_tools.Reply(500), api_tools.Reply(200, ends_header="soon")):
        answer.reply = reply
        post(api, f"api-tools/{tid}/check", {})
        assert stored(api, kid) == (day(40), "vendor")  # AC-5


def test_a_vendor_date_14_days_out_opens_one_renew_task_and_a_new_value_ends_it(api, monkeypatch):
    tid, kid = github(api)
    answer = checks(monkeypatch)
    answer.reply = api_tools.Reply(200, ends_header=f"{day(14)} 09:00:00 UTC")
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Working"
    page = get(api, f"api-tools/{tid}")
    assert page["status"] == {"tone": "wait", "text": "Expires in 14 days"}  # AC-6
    [task] = tasks(api, tid)  # AC-7
    assert (task["title"], task["owner_id"], task["status"]) == ("Renew GitHub key", "bot:ops", "open")
    assert day(14) in said(api, task) and "GitHub says" in said(api, task) and "Ops" not in task["title"]
    with api.app.state.store.read() as c:
        request = dict(
            c.execute(
                "SELECT * FROM credential_requests WHERE conversation_id=?", (task["conversation_id"],)
            ).fetchone()
        )
    assert (request["requester"], request["credential_id"]) == ("human:ana", kid)
    post(api, f"api-tools/{tid}/check", {})  # a second check adds nothing
    assert reconnect_tasks.sweep(api.app.state.store) == 0
    assert len(tasks(api, tid)) == 1

    # AC-8: Ana saves a new value on the card; the task is done and the old value's date is gone until the next check.
    post(api, f"credential-requests/{request['id']}/save", {"value": NEW})
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "done"
    assert stored(api, kid) == (None, None)
    answer.reply = api_tools.Reply(200, ends_header=f"{day(90)} 09:00:00 UTC")
    post(api, f"api-tools/{tid}/check", {})
    assert stored(api, kid) == (day(90), "vendor")
    assert len(tasks(api, tid)) == 1


def test_a_lapsed_key_keeps_its_renew_task(api, monkeypatch):
    tid, _ = github(api)
    answer = checks(monkeypatch)
    answer.reply = api_tools.Reply(200, ends_header=f"{day(3)} 09:00:00 UTC")
    post(api, f"api-tools/{tid}/check", {})
    [task] = tasks(api, tid)
    answer.reply = api_tools.Reply(401)  # the day came
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Key rejected"
    assert [t["id"] for t in tasks(api, tid)] == [task["id"]]


def test_the_hourly_sweep_opens_a_renew_task_for_a_typed_date_no_check_reaches(api):
    setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    tid = tool(api, "Acme internal")["id"]  # no address, no check path
    kid = key(api, tid, OLD)["id"]
    typed(api, tid, kid, day(10))
    assert reconnect_tasks.sweep(api.app.state.store) == 1  # AC-9
    [task] = tasks(api, tid)
    assert (
        task["title"].startswith("Renew") and day(10) in said(api, task) and "Acme internal says" not in said(api, task)
    )
    assert reconnect_tasks.sweep(api.app.state.store) == 0


def test_a_far_end_date_opens_nothing(api, monkeypatch):
    tid, _ = github(api)
    answer = checks(monkeypatch)
    answer.reply = api_tools.Reply(200, ends_header=f"{day(30)} 09:00:00 UTC")
    post(api, f"api-tools/{tid}/check", {})
    assert reconnect_tasks.sweep(api.app.state.store) == 0
    assert tasks(api, tid) == []


def test_nothing_of_the_reply_but_the_day_is_kept(api, monkeypatch):
    tid, _ = github(api)
    answer = checks(monkeypatch)
    header = f"{day(40)} 09:00:00 UTC"
    answer.reply = api_tools.Reply(200, ends_header=header, body=b'{"login":"acme-octocat-synthetic"}')
    post(api, f"api-tools/{tid}/check", {})
    with api.app.state.store.read() as c:  # AC-10
        checks_rows = json.dumps([dict(r) for r in c.execute("SELECT * FROM tool_checks")])
        events = json.dumps([dict(r) for r in c.execute("SELECT * FROM events")])
    for text in (checks_rows, events):
        assert "09:00:00" not in text and "acme-octocat-synthetic" not in text
