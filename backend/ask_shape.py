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
from collections.abc import Callable, Sequence

MIN_CHOICES, MAX_CHOICES = 2, 4
WHY_MAX, DETAIL_MAX = 200, 160

NEEDS = (
    "An ask to a person needs: why it matters, 2 to 4 choices each saying what happens, and which one you recommend."
)
# The fixes, each one sentence: a refusal names every one the ask needs, so one rewrite fixes it.
NO_WHY = "Add --why with one line on why it matters to them."
COUNT = "Give 2 to 4 choices, or none for a typed answer."
SAME = "Give each choice a different label."
NO_DETAIL = 'Give each choice a line on what happens: --choice "Label|what happens".'
NO_RECOMMEND = "Name the one you recommend with --recommend <choice>."
NOT_A_CHOICE = "--recommend names one of your choices."
STRAY = "Give the choices' lines only with choices, one per choice."

# What an ask may show: a page, file or task by its id (a page or file pinned at a version), a website's live browser,
# or a web address. A path (anything with a slash or a dot after page:, file: or task:) is not an id.
OPENS = re.compile(r"^(?:(page|file|task):([A-Za-z0-9_-]+)(?:@[1-9][0-9]*)?|browser:\S+|https?://\S+)$", re.I)


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
    exists: Callable[[str, str], bool] = lambda kind, ident: True,
) -> str | None:
    """What is wrong with a bot's question to `person` (their name), as the bot is told it, or None. Plain code: no
    model call. `exists(kind, id)` says whether the page, file or task a `page:`, `file:` or `task:` ref names is
    there (one indexed read each, made by the caller)."""
    fixes: list[str] = []
    if not (why or "").strip():
        fixes.append(NO_WHY)
    labels = [str(c).strip() for c in choices or []]
    lines = [str(d).strip() for d in details or []]
    if labels:
        if not MIN_CHOICES <= len(labels) <= MAX_CHOICES:
            fixes.append(COUNT)
        if len({label.casefold() for label in labels}) != len(labels):
            fixes.append(SAME)
        if len(lines) != len(labels) or not all(lines):
            fixes.append(NO_DETAIL)
        if not (recommend or "").strip():
            fixes.append(NO_RECOMMEND)
        elif str(recommend).strip().casefold() not in {label.casefold() for label in labels}:
            fixes.append(NOT_A_CHOICE)
    elif lines or (recommend or "").strip():
        fixes.append(STRAY)
    if fixes:
        return " ".join([NEEDS, *fixes])
    for ref in shows or []:
        found = OPENS.match(str(ref).strip())
        if not found or (found.group(1) and not exists(found.group(1).lower(), found.group(2))):
            return unopenable(person, str(ref))
    return None


def recommended(choices: Sequence[str] | None, recommend: str | None) -> str | None:
    """The choice `recommend` names, as the choice is spelled, or None."""
    want = (recommend or "").strip().casefold()
    for label in choices or []:
        if str(label).strip().casefold() == want:
            return str(label).strip()
    return None
