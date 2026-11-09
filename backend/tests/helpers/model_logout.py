"""Helpers that other tests share, moved out of test_model_logout.py so they import no test file."""

from backend.tests.helpers.api import get, headers, post, runner


CAPABLE = ["model_sign_out_v1"]


def online(api, capabilities=CAPABLE, token=None):
    r = runner(api)
    beat(api, r["token"], capabilities)
    return r


def beat(api, token, capabilities=CAPABLE, auth=None):
    import uuid

    r = api.post(
        "/api/v2/runners/heartbeat",
        json={"version": "test", "platform": "test", "readiness": {}, "capabilities": capabilities},
        headers={**(auth or headers(token)), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text


def report(api, r, lid, **body):
    return post(api, f"runner-logins/{lid}/report", body, token=r["token"])


def pending(api, r):
    return get(api, "runner-logins", token=r["token"])["logins"]
