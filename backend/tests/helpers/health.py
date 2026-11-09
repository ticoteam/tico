"""Helpers that other tests share, moved out of test_health.py so they import no test file."""

from backend.tests.helpers.getting_started import add_bot
from backend.tests.helpers.onboarding import environment, signed_in


def health_of(api, headers=None):
    body = api.get("/api/v2/health", headers=headers or signed_in()).json()
    return body, {row["id"]: row for row in body["checks"]}
