"""Helpers that other tests share, moved out of test_meeting_items.py so they import no test file."""

from backend.tests.helpers.api import headers


def record(api, body=None, token="ana-test"):
    """A finished meeting of the caller's, filed through the import API."""
    body = body or {}
    calendar = body.pop("calendar", None)
    r = api.post(
        "/api/v2/meetings/import",
        json={
            "title": "Pricing call",
            "transcript": "Dana: We agreed to ship on Friday.",
            "review": "live",
            **({"participants": calendar["attendees"]} if calendar else {}),
            **body,
        },
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def items(api, rid, token="ana-test", expected=200):
    r = api.get(f"/api/meetings/{rid}/items", headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def add(api, rid, body, token="ana-test", expected=200, key=None):
    r = api.post(f"/api/meetings/{rid}/items", json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()


def change(api, rid, iid, body, token="ana-test", expected=200, key=None):
    body = {
        "version": next(
            i["version"] for rows in items(api, rid, token)["sections"].values() for i in rows if i["id"] == iid
        ),
        **body,
    }
    r = api.post(f"/api/meetings/{rid}/items/{iid}", json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()


def push(api, rid, iid, body=None, token="ana-test", expected=200, key=None):
    body = {
        "version": next(
            i["version"] for rows in items(api, rid, token)["sections"].values() for i in rows if i["id"] == iid
        ),
        **(body or {}),
    }
    r = api.post(f"/api/meetings/{rid}/items/{iid}/push", json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()
