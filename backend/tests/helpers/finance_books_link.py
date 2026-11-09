"""Helpers that other tests share, moved out of test_finance_books_link.py so they import no test file."""

from typing import Any
from backend import hook_relay


COMPANY = "2f7c1d3e-5b8a-4c6d-9e0f-1a2b3c4d5e6f"  # a fictional company id


LINK = hook_relay.Link("gk_test", "https://updates.tico.team/v1/gateway", "fp")


class HQ:
    """HQ's books broker in place of the network: each endpoint answers from its list in order (an exception is
    raised), and every call is recorded as (path, body)."""

    def __init__(self, **answers: list[Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, link: object, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, dict(body)))
        answer = self.answers[path.rsplit("/", 1)[1]].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)


def tokens(access: str, refresh: str, expires: str = "2026-10-15T12:30:00Z") -> dict[str, Any]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": expires,
        "refresh_expires_at": None,
        "realm_id": "123",
        "tenant_id": None,
    }
