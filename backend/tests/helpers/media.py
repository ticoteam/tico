"""Helpers that other tests share, moved out of test_media.py so they import no test file."""

from backend.tests.helpers.api import headers


def media_post(api, path, body=None, token="ana-test", key=None, expected=200, **kwargs):
    response = api.post("/api/" + path, json=body, headers=headers(token, key), **kwargs)
    assert response.status_code == expected, response.text
    return response.json()


def import_meeting(api, token="ana-test", expected=200, **fields):
    """A finished meeting of the caller's, filed through the import API."""
    body = {"title": "Pricing call", "transcript": "Ana: We agreed to ship on Friday.", "review": "live", **fields}
    response = api.post("/api/v2/meetings/import", json=body, headers=headers(token))
    assert response.status_code == expected, response.text
    return response.json()
