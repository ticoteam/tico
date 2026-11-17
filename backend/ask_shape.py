# pyright: strict
"""The shape of a bot's ask to a person: plain code, read before the org rule check makes its model call, so a badly
shaped ask costs nothing and nothing is written.

Every ask a bot puts to a person, a ❓ message or an OK, whichever way it is made, has a shape the server checks
(`line_refusal`; H.task_ask and question_marks.mark run it on every path, the routes before the rule check's call): its
first line is short and says what is asked (a list of questions may follow on the lines after it), it stands on its
own (nothing pointing at text the person cannot see from the ask, such as "the comment above"), it is words (a line
of one letter, a lone ❓ or a number is a placeholder that asks nothing), and it carries no file
paths, hashes, ids or code. It is a check in code, not an instruction, so nothing a bot was told overrides it. Whether
the words are plain beyond that (no Markdown) is the team's rule "Asks to a person" (org_rules.ASKS_TO_A_PERSON), judged
by the rule check in the call it already makes. What a bot's OK shows is the engine's to check: a page or file version
it may read (task_engine.shows_a_file).
"""

import re
from typing import Any

from . import hubdb_text as _hubdb_text

_text: Any = _hubdb_text  # untyped module, through Any as in stage_rules.py

# The ask's first line: the row in the person's Flow, read on its own.
LINE_MAX = 140

# The fewest letters a first line needs: a mark, a number or one letter ("x") asks nothing; "OK?" does.
LETTERS_MIN = 2
LETTER = re.compile(r"[^\W\d_]")

# What a line that breaks the ask's shape is told, each one sentence the bot can act on.
SHORT_LINE = (
    f"Start with one short line, at most {LINE_MAX} characters, that says what you need from them; a list of "
    "questions may follow on the lines after it."
)
PLACEHOLDER = (
    'Ask in words: "{line}" says nothing they can answer. The first line says what you need from them, in a few words.'
)
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


def line_refusal(line: str | None) -> str | None:
    """What is wrong with the words of a bot's ask to a person, as the bot is told it, or None: a first line too long or
    missing, words pointing at text the person cannot see, and paths, hashes, ids or code (hubdb_text.lint_plain).
    Every fix is named at once, so one rewrite fixes it."""
    text = str(line or "").strip()
    fixes: list[str] = []
    first = text.split("\n", 1)[0].strip()
    if not first or len(first) > LINE_MAX:
        fixes.append(SHORT_LINE)
    elif len(LETTER.findall(first)) < LETTERS_MIN:
        fixes.append(PLACEHOLDER.format(line=first))
    pointer = POINTS_ELSEWHERE.search(text)
    if pointer:
        fixes.append(UNSEEN.format(phrase=pointer.group(0)))
    problems = [f"the ask {p}" for p in _text.lint_plain(text)]
    if problems:
        fixes.append(PLAIN.format(problems="; ".join(problems)))
    return " ".join(fixes) or None
