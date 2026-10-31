"""The one exception Finance raises for a request it will not carry out, with a stable code callers branch on."""

from collections.abc import Sequence
from dataclasses import dataclass

INVALID = "invalid"                 # the request is malformed or breaks an invariant
DENIED = "denied"                   # rights, view or a who-may rule says no
NOT_AVAILABLE = "not_available"     # an action kind a later phase delivers
NOT_FOUND = "not_found"
CONFLICT = "conflict"               # the state moved under the request (already reversed, already closed)
PERIOD_CLOSED = "period_closed"
UNBALANCED = "unbalanced"


@dataclass(frozen=True)
class Named:
    """The budgets a refusal names. `template` has {0}, {1}, ... where their names go; `fallback` is the same refusal
    without them, for an asker who may not see one. A refusal's record keeps `fallback` as its reason and the rest
    here, so a replay names the budgets again only where the asker may see them now (spec §7.5). `numbers` marks a
    template that carries amounts too: the asker then needs the budget's numbers, not only its name."""
    budgets: tuple[str, ...]
    template: str
    fallback: str
    numbers: bool = False
    cards: tuple[str, ...] = ()


class FinanceError(Exception):
    """A refusal with a code and a message safe to show the requester (it never names what they cannot see). `named`
    is set when the message names budgets: a record of the refusal then keeps `named.fallback` in place of it."""

    def __init__(self, code: str, message: str, named: Named | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.named = named


def invalid(message: str) -> FinanceError:
    return FinanceError(INVALID, message)


def denied(message: str) -> FinanceError:
    return FinanceError(DENIED, message)


def named_refusal(code: str, named: Named, names: Sequence[str], shown: bool) -> FinanceError:
    """A refusal naming budgets: their names go in the message only where `shown`, which the caller decides from
    what the asker may see. The record keeps `named`, never the names."""
    return FinanceError(code, named.template.format(*names) if shown else named.fallback, named)


def brace(text: str) -> str:
    """`text` as part of a template: its braces doubled, so `format` leaves them alone."""
    return text.replace("{", "{{").replace("}", "}}")


def not_found(message: str) -> FinanceError:
    return FinanceError(NOT_FOUND, message)
