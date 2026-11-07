# pyright: strict
"""The judges that decide a requirement without a person: the rules, and the LLM rubric judge with escalation. The
judge's model call goes through `ENGINE`, which tests replace. The tick runs on the scheduler's rest pass and never
inside a transaction. Track B owns this module. Nothing is configured in F0, so the tick returns at once."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

ENGINE = None


def tick(store: "Store", at: Any) -> None:
    """One judge pass over the requirements waiting for a decision. None in F0."""
    return None


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """Judges have no routes."""
    return None
