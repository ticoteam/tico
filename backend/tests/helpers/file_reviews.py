"""Helpers that other tests share, moved out of test_file_reviews.py so they import no test file."""

from backend.tests.helpers.api import headers, post


def publish(api, token, text, name="cut.md", path=None):
    r = api.post(
        "/api/v2/files/uploads",
        json={"name": name, "text": text, **({"path": path} if path else {})},
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    return r.json()["file"]


def ask(api, token, review, on="ana", expected=200, question="Is this cut ready?", **body):
    r = api.post(
        "/api/v2/task-questions",
        json={"on": on, "question": question, "review": review, **body},
        headers=headers(token),
    )
    assert r.status_code == expected, r.text
    return r.json()


def answer(api, made, labels, other="", token="ana-test", expected=200):
    return post(
        api,
        f"tasks/{made['task']['id']}/answers",
        {
            "target": {"task": made["task"]["id"]},
            "answers": {"decision": labels},
            **({"other": other} if other else {}),
        },
        token,
        expected=expected,
    )


def approved(api, token, **query):
    r = api.get("/api/v2/file-approvals", params=query, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()
