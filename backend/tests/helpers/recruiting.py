"""Helpers that other tests share, moved out of test_recruiting.py so they import no test file."""

from backend.tests.helpers.api import get, headers


def providers(api, *enabled):
    current = get(api, "providers")
    assert (
        api.put(
            "/api/v2/providers",
            headers=headers(),
            json={"enabled": list(enabled), "expected_revision": current["revision"]},
        ).status_code
        == 200
    )
