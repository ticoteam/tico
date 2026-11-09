"""Helpers that other tests share, moved out of test_pages.py so they import no test file."""

from backend.tests.helpers.api import headers


HTML = (
    b"<html><head><title>Weekly</title><script>alert('x')</script></head><body><h1>Acme weekly review</h1>"
    b"<p>Pipeline grew to <b>42 deals</b>.</p><img src='https://cdn.acme.example/x.png'></body></html>"
)


def page(api, attempt, name="weekly.html", data=HTML, **query):
    r = api.post(
        "/api/v2/pages",
        params={"name": name, **query},
        content=data,
        headers={**headers(attempt["token"]), "Content-Type": "text/html"},
    )
    return r


def read(api, fid, who):
    return api.get("/api/v2/pages/" + fid, headers=headers(who))
