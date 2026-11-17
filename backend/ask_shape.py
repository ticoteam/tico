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

Every ask a bot puts to a person, a question or an OK, whichever way it is made, also has a shape the server checks
(`line_refusal`; H.task_ask runs it on every path, the asks routes before the rule check's call): its first line is
short and says what is asked (a list of questions may follow on the lines after it), it stands on its own (nothing
pointing at text the person cannot see from the ask, such as "the comment above"), it carries no file paths, hashes,
ids or code, and its why is one line. It is a check in code, not an instruction, so nothing a bot was told overrides
it. Whether the words are plain beyond that (no Markdown, a why that says why) is the team's rule "Asks to a person"
(org_rules.ASKS_TO_A_PERSON), judged by the rule check in the call it already makes.
"""

import re
from collections.abc import Callable, Sequence
from typing import Any

from . import hubdb_text as _hubdb_text

_text: Any = _hubdb_text  # untyped module, through Any as in stage_rules.py

MIN_CHOICES, MAX_CHOICES = 2, 4
WHY_MAX, DETAIL_MAX = 200, 160
# The ask's first line: the row in the person's Flow, read on its own.
LINE_MAX = 140

NEEDS = (
    "An ask to a person needs why it matters. Choices are optional; with them, give 2 to 4, each saying what happens, "
    "and the one you recommend."
)
# What a line or why that breaks the ask's shape is told, each one sentence the bot can act on.
SHORT_LINE = (
    f"Start with one short line, at most {LINE_MAX} characters, that says what you need from them; a list of "
    "questions may follow on the lines after it."
)
WHY_ONE_LINE = f"Keep --why to one line, at most {WHY_MAX} characters."
UNSEEN = 'Write what you ask in the ask itself: they can\'t see what "{phrase}" points at.'
PLAIN = "Say it in plain words, with detail in a page you show: {problems}."
# Phrases that point at text the person cannot see from the ask (the comment above, the thread, answering by number).
# Phrases, never single words: "Is a price above $50 OK?" is a question that stands on its own.
POINTS_ELSEWHERE = re.compile(
    r"\b(?:"
    r"(?:see|read|as|per|listed|noted|mentioned|described|written|posted)\s+(?:above|below)"
    r"|(?:comments?|messages?|questions?|summary|replies|notes)\s+(?:above|below)"
    r"|(?:above|below)\s+(?:comments?|messages?)"
    r"|(?:my|our)\s+(?:last|previous|earlier|latest)\s+(?:comments?|messages?|notes?|replies|reply)"
    r"|my\s+comments?"
    r"|on\s+this\s+task"
    r"|in\s+(?:the|this)\s+thread"
    r"|(?:answer|reply|respond)\w*(?:\s+\w+){0,2}\s+by\s+numbers?"
    r")\b",
    re.I,
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
OPENS = re.compile(r"^(?:(page|file|task):([A-Za-z0-9_-]+)(?:@([1-9][0-9]{0,8}))?|browser:\S+|https?://\S+)$", re.I)


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
    exists: Callable[[str, str, int | None], bool] = lambda kind, ident, version: True,
) -> str | None:
    """What is wrong with a bot's question to `person` (their name), as the bot is told it, or None. Plain code: no
    model call. `exists(kind, id, version)` says whether the page, file or task a `page:`, `file:` or `task:` ref names
    is there, at the version it pins if it pins one (one indexed read each, made by the caller)."""
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
        pinned = int(found.group(3)) if found and found.group(3) else None
        if not found or (found.group(1) and not exists(found.group(1).lower(), found.group(2), pinned)):
            return unopenable(person, str(ref))
    return None


def line_refusal(line: str | None, why: str | None = None) -> str | None:
    """What is wrong with the words of a bot's ask to a person, as the bot is told it, or None: a first line too long or
    missing, a why longer than one line, words pointing at text the person cannot see, and paths, hashes, ids or code
    (hubdb_text.lint_plain) in the line or the why. Every fix is named at once, so one rewrite fixes it."""
    text, said = str(line or "").strip(), str(why or "").strip()
    fixes: list[str] = []
    first = text.split("\n", 1)[0].strip()
    if not first or len(first) > LINE_MAX:
        fixes.append(SHORT_LINE)
    if len(said) > WHY_MAX or "\n" in said:
        fixes.append(WHY_ONE_LINE)
    pointer = POINTS_ELSEWHERE.search(text) or POINTS_ELSEWHERE.search(said)
    if pointer:
        fixes.append(UNSEEN.format(phrase=pointer.group(0)))
    problems = [f"the ask {p}" for p in _text.lint_plain(text)] + [f"--why {p}" for p in _text.lint_plain(said)]
    if problems:
        fixes.append(PLAIN.format(problems="; ".join(problems)))
    return " ".join(fixes) or None


def recommended(choices: Sequence[str] | None, recommend: str | None) -> str | None:
    """The choice `recommend` names, as the choice is spelled, or None."""
    want = (recommend or "").strip().casefold()
    for label in choices or []:
        if str(label).strip().casefold() == want:
            return str(label).strip()
    return None
