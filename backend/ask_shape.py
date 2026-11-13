# pyright: strict
"""The shape of a bot's ask to a person (Tasks spec "Bots ask people a question with choices", AC-1 and AC-2): plain
code, read before the org rule check makes its model call, so a badly shaped ask costs nothing and nothing is written.

A bot's question to a person says why it matters (`why`). When it offers choices it offers 2 to 4, each with a line
on what happens on it (`details`, one per choice, in the same order), and names the one it recommends (`recommend`).
A question with no choices (a typed answer, such as the team's vision) needs only the why. What it shows is something
the person can open: a page, a file, a task, a web address (or a website's live browser, which the decision view
draws); never a path in a bot's repository.

"I don't know, explain" and "Give it back" are added by Tico to every bot ask and are not counted among the choices.
An OK (`kind` ok) is not a question: it shows the exact thing it is for, and keeps its own rules (task_asks).

Whether the words are plain (no Markdown, no file paths, a why that says why) is the team's rule "Asks to a person"
(org_rules.ASKS_TO_A_PERSON), judged by the rule check in the call it already makes.
"""

import re
from collections.abc import Sequence

MIN_CHOICES, MAX_CHOICES = 2, 4
WHY_MAX, DETAIL_MAX = 200, 160

NEEDS = (
    "An ask to a person needs: why it matters, 2 to 4 choices each saying what happens, and which one you recommend."
)
NO_WHY = f"{NEEDS} Add --why with one line on why it matters to them."
COUNT = f"{NEEDS} Give 2 to 4 choices, or none for a typed answer."
NO_DETAIL = f"{NEEDS} Give each choice a line on what happens: --choice \"Label|what happens\"."
NO_RECOMMEND = f"{NEEDS} Name the one you recommend with --recommend <choice>."
NOT_A_CHOICE = f"{NEEDS} --recommend names one of your choices."
STRAY = f"{NEEDS} Give the choices' lines only with choices, one per choice."

# What an ask may show: an id that opens in the app, or a web address.
OPENS = re.compile(r"^(?:page|file|task|browser):\S+$|^https?://\S+$", re.I)


def unopenable(person: str, ref: str) -> str:
    return f"{person} can't open {ref.strip()}. Publish it as a page and show that page."


def refusal(
    person: str,
    *,
    why: str | None,
    choices: Sequence[str] | None,
    details: Sequence[str] | None,
    recommend: str | None,
    shows: Sequence[str] | None,
) -> str | None:
    """What is wrong with a bot's question to `person` (their name), as the bot is told it, or None. Plain code: no
    model call, nothing read."""
    if not (why or "").strip():
        return NO_WHY
    labels = [str(c).strip() for c in choices or []]
    lines = [str(d).strip() for d in details or []]
    if labels:
        if not MIN_CHOICES <= len(labels) <= MAX_CHOICES:
            return COUNT
        if len(lines) != len(labels) or not all(lines):
            return NO_DETAIL
        if not (recommend or "").strip():
            return NO_RECOMMEND
        if str(recommend).strip().casefold() not in {label.casefold() for label in labels}:
            return NOT_A_CHOICE
    elif lines or (recommend or "").strip():
        return STRAY
    for ref in shows or []:
        if not OPENS.match(str(ref).strip()):
            return unopenable(person, str(ref))
    return None


def recommended(choices: Sequence[str] | None, recommend: str | None) -> str | None:
    """The choice `recommend` names, as the choice is spelled, or None."""
    want = (recommend or "").strip().casefold()
    for label in choices or []:
        if str(label).strip().casefold() == want:
            return str(label).strip()
    return None
