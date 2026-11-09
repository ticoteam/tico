"""Helpers that other tests share, moved out of test_page_audience.py so they import no test file."""

from backend.tests.helpers.api import headers
from backend.tests.helpers.files import turn
from backend.tests.helpers.pages import page, read


def edit(api, fid, audience, token="ben-test", expected=200):
    r = api.patch('/api/v2/pages/' + fid, json={'audience': audience}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def promoted(api):
    _, attempt = turn(api)
    made = page(api, attempt, title="Audience secret")
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    r = api.patch('/api/v2/files/' + fid, json={'promote': True}, headers=headers('ben-test'))
    assert r.status_code == 200, r.text
    return fid, attempt
