"""The one exception Finance raises for a request it will not carry out, with a stable code callers branch on."""

INVALID = "invalid"                 # the request is malformed or breaks an invariant
DENIED = "denied"                   # rights, view or a who-may rule says no
NOT_AVAILABLE = "not_available"     # an action kind a later phase delivers
NOT_FOUND = "not_found"
CONFLICT = "conflict"               # the state moved under the request (already reversed, already closed)
PERIOD_CLOSED = "period_closed"
UNBALANCED = "unbalanced"


FIT_REASON = "it does not fit: not enough left"     # a set-aside that does not fit, as a record keeps it (no figure)


class FinanceError(Exception):
    """A refusal with a code and a message safe to show the requester (it never names what they cannot see).
    `fit_budget` is set on a set-aside that does not fit: its record keeps that budget's id and FIT_REASON, and reads
    render the sentence per reader (handlers.fit_reason)."""

    def __init__(self, code: str, message: str, fit_budget: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fit_budget = fit_budget


def invalid(message: str) -> FinanceError:
    return FinanceError(INVALID, message)


def denied(message: str) -> FinanceError:
    return FinanceError(DENIED, message)


def not_found(message: str) -> FinanceError:
    return FinanceError(NOT_FOUND, message)
