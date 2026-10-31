"""The one exception Finance raises for a request it will not carry out, with a stable code callers branch on."""

INVALID = "invalid"                 # the request is malformed or breaks an invariant
DENIED = "denied"                   # rights, view or a who-may rule says no
NOT_AVAILABLE = "not_available"     # an action kind a later phase delivers
NOT_FOUND = "not_found"
CONFLICT = "conflict"               # the state moved under the request (already reversed, already closed)
PERIOD_CLOSED = "period_closed"
UNBALANCED = "unbalanced"


FIT_REASON = "it does not fit: not enough left"     # a set-aside that does not fit, as a record keeps it (no figure)
LIMIT_REASON = "the card has used more than that this period; its limit can't go below that"   # likewise, a card limit


class FinanceError(Exception):
    """A refusal with a code and a message safe to show the requester (it never names what they cannot see).
    `fit_budget` is set on a set-aside that does not fit, and `fit_card` on a card limit below what the card used: the
    record keeps that id and a sentence with no figure, and reads render the sentence per reader (record_of)."""

    def __init__(self, code: str, message: str, fit_budget: str = "", fit_card: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fit_budget = fit_budget
        self.fit_card = fit_card


def invalid(message: str) -> FinanceError:
    return FinanceError(INVALID, message)


def record_of(e: FinanceError) -> tuple[dict[str, object], str]:
    """What a refusal's record keeps: the id of the budget or card its figure is about, and a sentence with no figure.
    A refusal with no figure keeps its message."""
    if e.fit_budget:
        return {"fit": {"budget_id": e.fit_budget}}, FIT_REASON
    if e.fit_card:
        return {"limit": {"card_id": e.fit_card}}, LIMIT_REASON
    return {}, e.message


def denied(message: str) -> FinanceError:
    return FinanceError(DENIED, message)


def not_found(message: str) -> FinanceError:
    return FinanceError(NOT_FOUND, message)
