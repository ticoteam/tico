# pyright: strict
"""How Decide judges a piece of evidence against a requirement: one attachment, one rubric, one model.

`judge_once` is the seam. A check Tico ships (the Spec, Review, QA and Checked in production pages: `CHECKS`) is asked as
its named parts, each a yes/no question (a probability) in the same one call; the check's bar (`BARS`) reads each part as
yes, no or can't tell. Met when every part is yes, not met when any part is no, unsure when none is no and one can't be
told. Any other rubric is asked as one question, read against the default bar. Every verdict carries a reason in one line
per part that is missing (at most three), such as "No out-of-scope list found." (`reason_for`, `_part_lines`): never a
probability, a band or the check's full wording, which helped nobody (task #351: 27% of verdicts said "could not tell"
with the whole rubric after it). A call that
did not give an answer (an error, a timeout, an answer that cannot be read, no model, the team's Decide cap) is not a
verdict at all: it is `failed`, with what went wrong as its reason, and the caller retries it (page_verdicts). Nothing
unsure or failed passes work.

Pipeline judges use Decide Low (`tico-decide-low`) through judge.server_engine, so their spend counts against the
team's Decide cap. The gateway chooses the current model.

A page requirement's verdicts call it (backend/page_verdicts.py, through `judge_many`), and the eval
(evals/pipeline_judge). The model is never asked while a write transaction is open.

`judge_many` asks about several (requirement, attachment) pairs at once: one page attached to several requirements is
read once, one call with a `met` question per requirement (the same input asked every question once, as the rule check
does), and up to GROUP pages share one call (backend/decide_groups.py). Each pair gets back only its own answer, read
exactly as `judge_once` reads one.
"""

import hashlib
import html
import json
import logging
import math
import re
import sqlite3
from collections import Counter
from typing import Any, TYPE_CHECKING, cast

from clients import judge as clients_judge

from . import decide_groups as DG
from . import file_library
from . import hubdb
from . import judge as server_judge

# These modules are untyped: through Any, their calls are Any here, not partially unknown (as in stranded_resume.py).
H: Any = hubdb
J: Any = clients_judge
judge: Any = server_judge
files: Any = file_library
JudgeError = clients_judge.JudgeError

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .config import Settings
    from .store import Store

LOG = logging.getLogger("tico.pipeline_judges")

# The pipeline's historical judge identifier maps to the Gateway's Decide Low tier.
MODELS: dict[str, str] = {"jev": "tico-decide-low"}
DEFAULT_MODEL = "jev"
QUESTION_SET = "pipeline-requirement"
# The bar: met at 0.75 or more, not met at 0.2 or less. It lives here and nowhere else (the eval imports it), and no
# company or pipeline stores its own. Was 0.8 to Nov 11: at 0.75 the whole eval set had no false met, good pages unsure
# went from 4 of 30 to 1 of 30, and the highest not-met case was 0.43 (Chris, Nov 11).
MET_AT, NOT_MET_AT = 0.75, 0.2
REASON_BYTES = 500
LINES_AT_MOST = 3  # a verdict names at most this many missing parts, one line each


# Task #3451: the no line of a part that asks whether something is there at all (a second review, its model, the
# commit, a reviewer's findings), stricter than its check's. Pages with it cut out scored 0.2 to 0.4 on it (a Review
# page with every commit hash taken out 0.08 to 0.33 on the commit; a QA page without Reviewer 2's lines 0.22 to 0.32),
# and the real pages of Nov 18 to 25 that scored as low lacked it (a second review whose maker was not named). Parts
# that ask how well a thing is done (what was checked, how findings are written) keep the check's no line: real pages
# that scored them 0.2 to 0.4 had them, and stay unsure.
PRESENT_NO_AT = 0.4


class Part:
    """One named part of a check: its question id, what Jev is asked (`ask`, completing "The attachment ..."), the line a
    verdict says when it is missing (`missing`) and when Jev can't tell (`unclear`), and the short line an OK that shows
    a passed page lists as one of its check rows (`found`; ui/app/decide.ts decideAskBubbleHTML), where it has one.
    `needs` is the key of a part this one only makes sense after: while that part reads no, this one is not named in
    the reason (a page with one review is told it lacks the second, not also that the two models don't differ). `at`
    is the part's own yes line where it is stricter than its check's bar (BARS), else None; `no_at` its own no line
    where that is stricter, else None."""

    __slots__ = ("key", "ask", "missing", "unclear", "found", "needs", "at", "no_at")

    def __init__(
        self,
        key: str,
        ask: str,
        missing: str,
        unclear: str,
        found: str = "",
        needs: str = "",
        at: float | None = None,
        no_at: float | None = None,
    ) -> None:
        self.key, self.ask, self.missing, self.unclear, self.found = key, ask, missing, unclear, found
        self.needs, self.at, self.no_at = needs, at, no_at


# The checks Tico ships, each as its parts (the Spec page's "What changes" table, task #351). A rubric is one of these
# when it reads as the preset's line, now or as it shipped before (`check_of`); a company that rewrote its rubric is
# asked it whole.
CHECKS: dict[str, tuple[Part, ...]] = {
    "spec": (
        Part(
            "goal",
            "states the goal: what the work is for, or the problem it solves",
            "No goal found.",
            "Can't tell if the goal is stated.",
        ),
        Part(
            "in_scope",
            "says what is in scope: what the work covers",
            "No in-scope list found.",
            "Can't tell what is in scope.",
        ),
        Part(
            "out_of_scope",
            "says what is out of scope: what the work leaves out, under any heading (out of scope, not doing, non-goals)",
            "No out-of-scope list found.",
            "Can't tell what is out of scope.",
        ),
        Part(
            "criteria",
            "says what done looks like as acceptance criteria someone can check (each a result that can be seen to "
            "hold or not)",
            "No acceptance criteria someone can check found.",
            "Can't tell if the acceptance criteria can be checked.",
        ),
    ),
    "review": (
        Part(
            "commit",
            "names the commit it reviewed for every linked PR, and that commit is the PR's newest (`work.context.heads`, "
            "in full or its first 7 or more characters)",
            "The newest commit of each linked PR is not named.",
            "Can't tell if the newest commit of each linked PR is named.",
        ),
        Part(
            "read_run",
            "says what the reviewer read and ran or checked (files, tests, commands); a summary of the change with "
            "nothing read, run or checked does not count",
            "Doesn't say what was read or run.",
            "Can't tell what was read or run.",
        ),
        Part(
            "blocks",
            "says what blocks and what to fix, or says that nothing blocks",
            "Doesn't say what blocks, or that nothing blocks.",
            "Can't tell if it says what blocks.",
        ),
    ),
    "qa": (
        Part(
            "built",
            "says what changed; the work's title and spec say what was asked, so naming the work or its PR is enough",
            "Doesn't say what was built.",
            "Can't tell what was built.",
            "Says what was built.",
        ),
        Part(
            "output",
            "shows proof it works: screenshots of the running app, or output from how it was checked (test counts, "
            "command output, a log line, numbers before and after); a line that only says it passed is not proof",
            "No check output found.",
            "Can't tell if check output is shown.",
            "Shows the check output.",
        ),
        Part(
            "checks",
            "shows the checks at the newest commit and none of them failed: passing, or still running or re-running "
            "when the page was written, is fine; a page that reports a check failed at the newest commit does not "
            "have this part",
            "The checks at the newest commit are not shown, or one failed.",
            "Can't tell if the checks at the newest commit passed.",
            "Checks pass at the newest commit.",
            at=0.75,  # the page with an older commit's checks scored 0.57 to 0.71 (BARS, "raised_by_eval")
        ),
        Part(
            "reviewer_1",
            "says what Reviewer 1 found on the commit it reviewed, or that it found nothing to fix (commits after the "
            "review are fine)",
            "Reviewer 1's findings are missing.",
            "Can't tell what Reviewer 1 found.",
            "Has Reviewer 1's findings.",
            no_at=PRESENT_NO_AT,
        ),
        Part(
            "reviewer_2",
            "says what Reviewer 2 found on the commit it reviewed, or that it found nothing to fix (commits after the "
            "review are fine)",
            "Reviewer 2's findings are missing.",
            "Can't tell what Reviewer 2 found.",
            "Has Reviewer 2's findings.",
            no_at=PRESENT_NO_AT,
        ),
        # Task #389 (Chris, Nov 13: the QA review "should look really, really clear", with architecture diagrams): what
        # a person reads first, how the change works, and how to undo it. Nov 14's five QA pages had none of the three.
        Part(
            "strip",
            "opens with a verdict a person reads first, near the top and before the details: whether the change is "
            "ready, with how many acceptance criteria passed and the checks, and the open findings and the risk there "
            "or just below (tiles, a table, a list or a line)",
            "No verdict strip to read first (criteria passed, checks, findings, risk).",
            "Can't tell if there is a verdict to read first.",
        ),
        Part(
            "how",
            "shows how the change works: a diagram of the parts it touches, or a list or table of the files or parts "
            "it changes saying what changed in each; a change to one file may name that file",
            "No diagram of how it works, or no list of the files changed.",
            "Can't tell if it shows how the change works.",
        ),
        Part(
            "rollback",
            "says the risk (what could go wrong, and for whom) and the rollback: how to undo the change, and whether "
            "stored data changes",
            "No risk and rollback (how to undo it, and whether stored data changes).",
            "Can't tell if it says how to undo the change.",
        ),
    ),
    # Tico's Review bar (REVIEW_RUBRIC: two reviews on one page, since Nov 18). Until task #3451 it was asked whole and
    # these parts only explained it: on the default bar of 0.75 for the whole page, no page passed (0 of 182 verdicts,
    # Nov 18 to 25). Asked as its parts on the single review's bar it replaced, as Spec, QA and Validate are.
    "two_reviews": (
        Part(
            "two",
            "holds two reviews of the work, and each names the model that made it",
            "No second review that names its model.",
            "Can't tell if there are two reviews that each name their model.",
            "Has two reviews, each naming its model.",
            no_at=PRESENT_NO_AT,
        ),
        # Task #3970 (Chris's builder rule, Nov 26): was "models", the two reviews' models differ. The second review
        # needs only a maker other than the builder's; the first may share it. A page that names no builder cannot
        # show that, so it does not have this part.
        Part(
            "maker",
            "names the model that built the change, and the second review's model (Reviewer 2's, the second opinion) "
            "is by a different maker than the builder's (a model's maker is the company that makes it, such as "
            "Anthropic for Claude or OpenAI for GPT and Codex); the first review may share the builder's maker; a page "
            "that does not name the model that built the change, or whose second review is by the builder's maker, "
            "does not have this part",
            "The page doesn't name the model that built the change, or the second review is by the builder's maker.",
            "Can't tell if the second review is by a different maker than the model that built the change (the "
            "builder).",
            "The second review is by a different maker than the builder's.",
            needs="two",
            no_at=PRESENT_NO_AT,
        ),
        Part(
            "commit",
            "names the commit each review was of for every linked PR, and that commit is the PR's newest "
            "(`work.context.heads`, in full or its first 7 or more characters); a second review that says it was of "
            "the same commit counts",
            "A review doesn't name the PR's newest commit.",
            "Can't tell if each review names the PR's newest commit.",
            "Each review names the newest commit.",
            no_at=PRESENT_NO_AT,
        ),
        Part(
            "checked",
            "says what each review checked: files read, tests or commands run",
            "Doesn't say what each review checked (files read, tests or commands run).",
            "Can't tell what each review checked.",
            "Says what each review checked.",
        ),
        Part(
            "findings",
            "gives each blocking finding its own file:line, a severity and why it matters; a page with no blocking "
            "finding meets this when it says so or lists only non-blocking notes, and those notes need no file:line, "
            "but a page that says nothing about what the reviews found does not; a finding on the PR description may "
            "cite the guideline line it breaks as its file:line",
            "A blocking finding lacks file:line, a severity or why it matters, or the page does not say what the "
            "reviews found.",
            "Can't tell if each blocking finding has file:line, a severity and why it matters.",
            "Each blocking finding has file:line, a severity and why, or none block.",
        ),
    ),
    "validate": (
        Part(
            "version",
            "names the version production runs",
            "The version production runs is not named.",
            "Can't tell if the version production runs is named.",
            "Names the version production runs.",
        ),
        Part(
            "holds",
            "shows that the version production runs holds this work: it is not older than the release that holds the "
            "work (`work.context.release`, when given)",
            "The version production runs doesn't hold this work yet.",
            "Can't tell if the version production runs holds this work.",
            "Production runs a version that holds this work.",
        ),
        Part(
            "shown",
            "says what was run or looked at in production and what it gave; a page that only says it works does not "
            "have this part",
            "Doesn't say what was run or looked at in production and what it gave.",
            "Can't tell what was run or looked at in production.",
            "Says what was run in production and what it gave.",
        ),
    ),
}
# Each check's bar: a part is yes at the first number or more, no at the second or less, can't tell between. Set by
# evals/pipeline_judge/rebuild.py from the 826 real verdicts of Nov 11 to 13 and what happened next (the result is
# evals/pipeline_judge/results/bars-2026-11-13.md): the lowest bar that keeps met-then-sent-back no higher than before
# and unsure under 10%. A send-back no bar could hold with unsure under 10% (the person sent back the work, every part
# plainly there) is out of Jev's reach and does not count. QA's checks part stays at 0.75 (`Part.at`) because the
# eval's page with the checks of an older commit scored 0.57 to 0.71 over nine real runs: met at 0.5, and once at 0.7
# (the result's "raised_by_eval"). Task #3451 re-ran it on the 250 real Review and QA pages of Nov 18 to 25
# (results/bars-2026-11-26.md): the two reviews at a yes of 0.5 left 18% unsure (75% before, none met), and QA's other
# parts at 0.6 left 9% (24% at 0.75); with the parts' own no lines (`PRESENT_NO_AT`) every page cut to one review, no
# commit, no rollback or one reviewer's findings was not met. A no line of 0.4 on every Review part left 13% unsure,
# but by sending back pages whose findings and checks were there. Rerun them when what Jev is shown, or a check's parts, change: `rebuild.py --out` rewrites
# the result without "raised_by_eval" or "set_by_eval", so carry each over by hand
# (test_the_committed_rebuild_result_is_the_bar_the_server_uses fails until it agrees with BARS).
BARS: dict[str, tuple[float, float]] = {
    "spec": (0.5, NOT_MET_AT),
    "review": (0.5, NOT_MET_AT),
    "two_reviews": (0.5, NOT_MET_AT),
    "qa": (0.6, NOT_MET_AT),
    "validate": (0.5, NOT_MET_AT),
}
# Rubrics asked whole are explained by their own sentences (`explain_parts`): asked beside the whole question in the
# same call, only to say which one is missing (task #614). The verdict stays the whole question's, read on the default
# bar: these parts never move a page from met to not met. When the verdict is not met or unsure, the reason names the
# sentence the model scored lowest, followed by the page line the model says its answer rests on: the same call asks it
# to pick one numbered line of the page, or none (READ, `_read_question`), so the line quoted is the model's own pick,
# never one Tico chose. A rubric of one sentence is its own only item, quoted (`_one_item`).


def _words(text: object) -> str:
    return " ".join(str(text or "").split())


def check_of(rubric: object) -> str | None:
    """Which of CHECKS a rubric is: the preset's line for it, now or as it shipped before (pipeline_settings.EARLIER), or
    None for a rubric of a company's own."""
    from . import pipeline_settings as PS

    S: Any = PS
    said = _words(rubric)
    if not said:
        return None
    known: dict[str, tuple[str, ...]] = {
        "spec": (S.SPEC_RUBRIC, *S.EARLIER.get("product-spec/page/rubric", ())),
        # The single review the presets shipped before the one Review stage, and the two reviews on one page since
        # (Tico's text, and its text before the PR-description line).
        "review": tuple(
            text
            for text in S.EARLIER.get("engineering-review-1/page/rubric", ())
            if text not in S.COMBINED_REVIEW_RUBRICS
        ),
        "two_reviews": S.COMBINED_REVIEW_RUBRICS,
        "qa": (S.QA_RUBRIC, *S.EARLIER.get("engineering-qa/page/rubric", ())),
        "validate": (S.VALIDATE_RUBRIC, *S.EARLIER.get("product-validate/page/rubric", ())),
    }
    for check, texts in known.items():
        if any(_words(text) == said for text in texts):
            return check
    return None


# A rubric a company edited to be close to one of Tico's bars, but not the same, is a company's own: asked whole on the
# default bar, not as the bar's calibrated parts. The save says so (pipeline_settings.near_bar_notes) and changes
# nothing. Close is at least NEAR_SHARE of the rubric's own words being in the bar (each word counted as often as it is
# in both, case and punctuation aside), so a bar made by cutting sentences from Tico's is close; and, so that a line or
# two lifted from it is not, the rubric holds at least NEAR_COVERS of the bar's words.
NEAR_SHARE = 0.8
NEAR_COVERS = 0.5
# What a person calls each check's bar.
BAR_NAMES: dict[str, str] = {
    "spec": "Spec",
    "review": "single review",
    "two_reviews": "Review",
    "qa": "QA",
    "validate": "Checked in production",
}


def _bag(text: object) -> "Counter[str]":
    return Counter(re.findall(r"[a-z0-9]+(?:[:'][a-z0-9]+)*", str(text or "").lower()))


def shared_words(one: object, other: object) -> float:
    """The share of `one`'s words that are in `other`: each word as often as it is in both, out of `one`'s words."""
    a, b = _bag(one), _bag(other)
    total = sum(a.values())
    return sum((a & b).values()) / total if total else 0.0


def near_bar(rubric: object) -> tuple[str, str] | None:
    """(check, Tico's text) when a rubric is close to one of Tico's bars as it ships now (NEAR_SHARE) but is none of
    them (`check_of` is None); else None."""
    from . import pipeline_settings as PS

    S: Any = PS
    if not _words(rubric) or check_of(rubric) is not None:
        return None
    shipped = {
        "spec": S.SPEC_RUBRIC,
        "two_reviews": S.REVIEW_RUBRIC,
        "qa": S.QA_RUBRIC,
        "validate": S.VALIDATE_RUBRIC,
    }
    close = [
        (shared_words(rubric, text), check)
        for check, text in shipped.items()
        if shared_words(rubric, text) >= NEAR_SHARE and shared_words(text, rubric) >= NEAR_COVERS
    ]
    return (max(close)[1], shipped[max(close)[1]]) if close else None


# A rubric of a company's own is explained by its sentences in at most this many parts (a longer rubric's sentences are
# grouped, none dropped), each quoted in at most SENTENCE_CHARS.
SENTENCES_AT_MOST = 8
SENTENCE_CHARS = 160
# The page line the model's answer rests on (task #614): asked in the same call as the verdict, as a choice of the
# page's first LINES_OFFERED lines (`page_lines`), each offered up to LINE_CHARS, or NO_LINE.
READ = "read"
NO_LINE = "none"
LINES_OFFERED = 80
LINE_CHARS = 140


def _sentences(said: str) -> list[str]:
    """A rubric's sentences: split after a full stop, question or exclamation mark that a capital, a quote, a bracket or
    a backtick follows, so "file:line" and "e.g. x" stay whole."""
    return [one for one in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(`])", said) if one.strip()]


# Sentences of a rubric that name nothing a page shows: they say what the page is, which items count, or that one
# without them fails. Explained sentence by sentence, such a line was quoted as if the page lacked it ("Can't tell if
# it meets: \"Only these seven count.\"", 27 of 36 QA reasons on Nov 18 and 19, task #3451), so it is never asked
# as a part. Each is matched on a whole sentence, case aside.
# A framing sentence's tail ("before the change ships") names no evidence: one that does ("only if it shows test
# output", "with the screenshots") adds a condition a page can miss, and is a part.
_NO_EVIDENCE = r"(?![^:;]*\b(if|unless|only|shows?|has|have|with|names?|includes?|gives?|lists?|says?)\b)"
FRAMING = (
    # "Only these seven count." / "Only these three count: a page with all three meets it ..."
    re.compile(r"only (these|those|the above)( \w+)? (count|matter)\b.*", re.I),
    # "A page that leaves any of them out, such as ..., does not meet it." Only a page that leaves out "them" (the items
    # the bar lists) is framing: "A page without a rollback plan does not meet it." or "A page that omits any of the
    # screenshots does not meet it." names an item, and is a part.
    re.compile(
        r"(a|an|any|the) ([\w-]+ ){0,3}(page|report) (that|which) (leaves|misses|lacks|omits|skips) (any|one|some|all)"
        r"( \w+)? of (them|these|those)( \w+)?( out)?\b.*\b(does not|doesn't|do not|don't|never) (meet|pass)(es|s)?"
        r"( it| this( bar| check)?| the (bar|check))?[.!]?",
        re.I,
    ),
    # "A QA page that must meet this bar before the change ships."
    re.compile(
        r"(a|an|the|this) ([\w-]+ ){0,3}(page|report)( that)? (must|should|has to|needs to) (meet|pass)( it| this( bar| check)?| the (bar|check))?\b"
        + _NO_EVIDENCE
        + r"[^:;]*[.!]?",
        re.I,
    ),
    # "A page with all seven meets it even when it reports a target missed, ..."
    re.compile(r"(a|any) ([\w-]+ ){0,3}(page|report) with all( \w+)? (meets|passes) (it|this|the bar)\b.*", re.I),
    # "A QA page a person approves before the change ships."
    re.compile(
        r"(a|an|the|this) ([\w-]+ ){0,3}(page|report)( that)? (a person|someone|people|the owner) (approves|oks|reads|signs off)\b"
        + _NO_EVIDENCE
        + r"[^:;,]*[.!]?",
        re.I,
    ),
)


def framing(sentence: str) -> bool:
    """Whether a rubric's sentence only frames it (FRAMING): what the page is, which items count, or that a page
    without them fails. Such a sentence is never explained as a part a page may lack."""
    said = _words(sentence)
    return any(pattern.fullmatch(said) for pattern in FRAMING)


def explain_parts(rubric: object) -> tuple[Part, ...]:
    """The parts asked beside a rubric asked whole, only to name what is missing (task #614): for a rubric of two or more
    sentences, one part per sentence that names something a page shows (FRAMING lines are left out; grouped into at
    most SENTENCES_AT_MOST parts, so none is left out), the sentence quoted. Empty for one of CHECKS (asked as its
    parts) and for a rubric of one sentence: that sentence is the only item, and the whole question's verdict is about
    it, so the reason quotes it (`_one_item`) without asking it again."""
    said = _words(rubric)
    if not said or check_of(rubric) is not None:
        return ()
    every = _sentences(said)
    if len(every) < 2:
        return ()
    sentences = [one for one in every if not framing(one)] or every
    size = -(-len(sentences) // SENTENCES_AT_MOST)  # sentences per part, so at most SENTENCES_AT_MOST parts
    groups = [" ".join(sentences[i : i + size]) for i in range(0, len(sentences), size)]
    parts: list[Part] = []
    for n, sentence in enumerate(groups, start=1):
        quoted = sentence if len(sentence) <= SENTENCE_CHARS else sentence[: SENTENCE_CHARS - 1].rstrip() + "…"
        parts.append(
            Part(
                f"s{n}",
                f"meets this part of `requirement.rubric`: {json.dumps(sentence)}",
                f"Not met: {json.dumps(quoted)}",
                f"Can't tell if it meets: {json.dumps(quoted)}",
            )
        )
    return tuple(parts)


def page_lines(text: object) -> list[str]:
    """The lines of a page's text the model may pick as what its answer rests on: an HTML page's text without its tags,
    one line per block, each with eight characters or more, the first LINES_OFFERED, each cut to LINE_CHARS. The same
    text gives the same lines, so the pick is read back against the lines it was offered."""
    if not isinstance(text, str) or not text.strip():
        return []
    if "<" in text and ">" in text:
        text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    out: list[str] = []
    for raw in text.splitlines():
        line = _words(raw)
        if len(line) < 8:
            continue
        out.append(line if len(line) <= LINE_CHARS else line[: LINE_CHARS - 1].rstrip() + "…")
        if len(out) == LINES_OFFERED:
            break
    return out


def _read_question(lines: list[str]) -> dict[str, Any]:
    """The choice asked beside a rubric asked whole: which one line of the page the model's answer rests on (L1 the
    first of `page_lines`), or NO_LINE."""
    return {
        "type": "choice",
        "instructions": (
            "Pick the one line of `attachment.text` your answer on whether the `attachment` meets "
            "`requirement.rubric` rests on most. When it does not meet the rubric, or you can't tell, pick the line "
            "where the part of the rubric least met falls short, or should be and isn't clear; when it meets it, the "
            f"line that shows that best. Each option quotes one line of the attachment. Pick `{NO_LINE}` when no line "
            "bears on it."
        ),
        "criteria": {
            **{f"L{n}": line for n, line in enumerate(lines, start=1)},
            NO_LINE: "No line of the attachment bears on it.",
        },
    }


def _seen(pick: object, text: object) -> str:
    """The line a reason gives for what the model read: the page line it picked, quoted, or that it found no line
    bearing on it. Empty when it made no valid pick: then nothing is quoted rather than a line Tico guessed."""
    if pick == NO_LINE:
        return "Read: no line of the page bears on it."
    lines = page_lines(text)
    if isinstance(pick, str) and re.fullmatch(r"L[1-9][0-9]*", pick) and int(pick[1:]) <= len(lines):
        return f"Read: {json.dumps(lines[int(pick[1:]) - 1], ensure_ascii=False)}"
    return ""


def _one_item(verdict: str, rubric: object) -> str:
    """A rubric of one sentence is its own only item: the line naming it, quoted up to SENTENCE_CHARS, under a not
    met or an unsure verdict on the whole question."""
    said = _words(rubric)
    if not said:
        return ""
    quoted = json.dumps(said if len(said) <= SENTENCE_CHARS else said[: SENTENCE_CHARS - 1].rstrip() + "…")
    return f"Not met: {quoted}" if verdict == "not_met" else f"Can't tell if it meets: {quoted}"


def _explain_lines(explain: tuple[Part, ...], parts: dict[str, float]) -> list[str]:
    """The reason a not met or unsure verdict on a rubric asked whole gives from the parts asked beside it: the parts
    that read no on the default bar with their `missing` line, else the ones that can't be told, lowest first, with
    their `unclear` line: a part never reads as missing unless it was scored no. When every part read as there on its
    own, the lowest is named as the least clear. Empty only when no part was answered (then the caller keeps today's
    line)."""
    bar = (MET_AT, NOT_MET_AT)
    known = sorted((part for part in explain if part.key in parts), key=lambda p: parts[p.key])
    if not known:
        return []
    no = [part for part in known if _band(parts[part.key], bar) == "no"]
    unclear = [part for part in known if _band(parts[part.key], bar) == "unsure"]
    if no:
        lines = [part.missing for part in no]
    elif unclear:
        lines = [part.unclear for part in unclear]
    else:
        lines = [f"Each item reads as there on its own; least clear: {known[0].unclear}"]
    return lines[:LINES_AT_MOST]


def _band(p: float, bar: tuple[float, float]) -> str:
    """yes, no or unsure for one part's probability against a check's bar."""
    if p >= bar[0]:
        return "yes"
    if p <= bar[1]:
        return "no"
    return "unsure"


def part_bar(part: Part, bar: tuple[float, float]) -> tuple[float, float]:
    """One part's (yes, no) lines: its check's bar, with the part's own lines where they are stricter (`Part.at`,
    `Part.no_at`). The no line never reaches the yes line."""
    yes = max(bar[0], part.at) if part.at is not None else bar[0]
    return yes, min(max(bar[1], part.no_at), yes) if part.no_at is not None else bar[1]


def _part_lines(check: str, parts: dict[str, float]) -> tuple[str, float, str]:
    """(verdict, p, reason) from each part's probability: not met naming the parts that are no, else unsure naming the
    parts Jev can't tell, else met. p is the lowest part's, so a check's bands read the same on p alone. Each part is
    read against the check's bar."""
    bar = BARS.get(check, (MET_AT, NOT_MET_AT))
    said = {part.key: _band(parts[part.key], part_bar(part, bar)) for part in CHECKS[check] if part.key in parts}
    # A part that only makes sense after another (`needs`) is not named while that one is missing.
    named = [part for part in CHECKS[check] if not (part.needs and said.get(part.needs) == "no")]
    no = [part.missing for part in named if said.get(part.key) == "no"]
    unclear = [part.unclear for part in named if said.get(part.key) in ("unsure", None)]
    p = min(parts.values()) if parts else 0.5
    if no:
        return "not_met", p, " ".join(no[:LINES_AT_MOST])
    if unclear:
        return "unsure", p, " ".join(unclear[:LINES_AT_MOST])
    return "met", p, "Every part is there."


# How much of the evidence's text and the task's spec Jev is sent. Jev reads 32k tokens of state (clients/judge.py
# MAX_STATE_CHARS: 120,000 characters of JSON, about four a token), and the two share it with the rest of the state
# (the rubric, the goal, the title, the commits and releases: a few thousand). 40,000 + 12,000 is under half of it, so
# even dense text (three characters a token) fits with room, and a page a bot writes (5,000 to 15,000 characters) is
# read whole. A longer text is cut and flagged truncated; `_state` cuts further if escaping would pass Jev's limit.
# Was 6,000 each (Nov 7, never measured): a normal 6,500-character spec came back unsure (rc.5, Nov 11).
TEXT_CHARS = 40_000
SPEC_CHARS = 12_000
STATE_CHARS = clients_judge.MAX_STATE_CHARS * 3 // 4  # the most JSON `_fit` lets through: a quarter of Jev's to spare
NO_EVIDENCE = "No evidence is attached."
# What a person sees as the reason when the judge was not shown all of the evidence (Fix L2-2).
CANNOT_SEE = "Tico could not read all of this evidence, so a person decides."
# What the owner is told when the text was too long for Jev: what to do, so it shortens it rather than attaching it again.
TOO_LONG = "This {what} is too long for the Decision model to read: keep it under {chars:,} characters."
# What a person sees when the judge said met on a spec it was shown only the start of: the rest may hold what is missing.
SPEC_CUT = "The judge saw only the start of this work's spec, so a person decides."
# The engine a test or a harness may set: (model, state, questions, label) -> {p, reason, provider, model, cost_usd,
# latency_ms}. None, with no installed store, asks no one, so every call fails until a caller sets one.
ENGINE: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None
# The raw engine a test may set for `judge_many`: (state, questions, label) -> the decision answer ({answers, cost_usd,
# provider, model, ms}), as judge.server_engine gives it. None asks the installed store's Jev.
ASK: "Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None
# Pages asked about in one call (backend/decide_groups.py). One until the replay through real Jev shows grouped verdicts
# match the ones given alone (no more than 2 in 100 changed); then DG.MOST.
GROUP = 1
# A page attached to several requirements read once, with one question per requirement. Off until the same replay shows
# the merged call gives each requirement the verdict it gets alone: the state and question wording differ from today's,
# and page_verdicts stores a merged verdict under the one-requirement key (rubric_hash), so compare merged and alone
# verdicts for the same pages before turning it on.
SAME_PAGE = False
# The store the server installed (install): judge_once asks the production engine on it when no engine is given.
_installed_store: Any = None


def judge_model_of(conn: sqlite3.Connection, pipeline_id: str | None) -> str:
    """The pipeline judge's identifier. The Gateway chooses the underlying Decide Low model."""
    return DEFAULT_MODEL


def verdict_of(p: float) -> str:
    """The band a probability falls in: met at MET_AT (0.75) or more, not_met at NOT_MET_AT (0.2) or less, unsure between."""
    if p >= MET_AT:
        return "met"
    if p <= NOT_MET_AT:
        return "not_met"
    return "unsure"


def reason_for(verdict: str, name: str) -> str:
    """What a verdict on a check asked whole says when the model gave no reason (Jev never does): one line naming the
    requirement (its short name, never its full rubric) and no probability. Never empty."""
    what = f'"{_words(name)}"' if _words(name) else "the check"
    if verdict == "met":
        return f"Meets {what}."
    if verdict == "not_met":
        return f"Doesn't meet {what}."
    return f"Can't tell if it meets {what}."


def _probability(value: object) -> float | None:
    """A probability from a model's answer, clamped to 0..1; None when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        score = float(value)
    except ValueError:
        return None
    if not math.isfinite(score):
        return None
    return min(1.0, max(0.0, score))


def excerpt(text: str | None) -> tuple[str | None, bool]:
    """The first TEXT_CHARS characters of an attachment's text, and whether that cut any. None (no text) stays None."""
    if text is None:
        return None, False
    return text[:TEXT_CHARS], len(text) > TEXT_CHARS


def _reads_text(mime: str) -> bool:
    """Whether Tico reads text from this type: a text, a table or an HTML page (a Tico page). Other bytes are not loaded."""
    return mime in file_library.TEXT_TYPES or mime in file_library.TABLE_TYPES or mime == "text/html"


def pr_work(link: dict[str, Any] | None) -> dict[str, Any]:
    """The work's repo, PR and author from the PR link it is judged against, as task_repo, task_pr and
    task_author. The author is the GitHub login that opened the PR (`detail.author_login`, kept from GitHub's webhook by
    backend/github.py), or None when Tico does not know it. Never the task's owner: that is whoever holds the task now,
    who may be its reviewer. The server's read and the eval adapter both use this, so the judge is told the same."""
    link = link or {}
    raw: object = link.get("detail")
    detail: object = raw
    if isinstance(raw, (str, bytes)):
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = None
    found = cast("dict[str, Any]", detail).get("author_login") if isinstance(detail, dict) else None
    author = str(found or "").strip()
    return {"task_repo": link.get("repo"), "task_pr": link.get("number"), "task_author": author or None}


def _state(requirement: dict[str, Any], attachment: dict[str, Any]) -> dict[str, Any]:
    """What the judge is told: the requirement and its rubric, the work as it is now (the task's work_revision, which
    the attachment row does not carry; its title, its spec (the description, its first SPEC_CHARS characters with
    `spec_truncated` when cut), and its repo, PR and PR author when Tico knows them), and the evidence: the version it was
    made for, what it is (a description), and its text, the first TEXT_CHARS characters with `truncated` when cut. `text`
    is null when Tico holds no text for it (an image, a video, a PDF or a link); the question set answers that as
    unreadable."""
    text, cut = excerpt(attachment.get("text"))
    spec = str(attachment.get("task_spec") or "")
    work: dict[str, Any] = {
        "work_version": int(attachment.get("task_work_version") or 0),
        "title": str(attachment.get("task_title") or ""),
        "spec": spec[:SPEC_CHARS],
    }
    if len(spec) > SPEC_CHARS:
        work["spec_truncated"] = True
    for key in ("repo", "pr", "author"):
        value = attachment.get("task_" + key)
        if value not in (None, ""):
            work[key] = value
    if attachment.get("context"):
        # What the page is judged against besides the rubric: the commits a review is for, the release it went out in.
        work["context"] = attachment["context"]
    return _fit(
        {
            "requirement": {
                "name": requirement.get("name") or "",
                "rubric": requirement.get("rubric") or "",
                "goal": requirement.get("goal") or "",  # the pipeline's goal (D58)
                # Present (true) only on a requirement that does not need evidence newer than the work's last change.
                **({"older_evidence_ok": True} if attachment.get("older_evidence_ok") else {}),
            },
            "work": work,
            "attachment": {
                "work_version": int(attachment.get("work_version") or 0),
                "source": attachment.get("source") or "",
                "digest": attachment.get("digest") or "",
                "url": attachment.get("url") or "",
                "file_version": attachment.get("file_version") or 0,
                "text": text,
                "truncated": cut or bool(attachment.get("truncated")),
            },
        }
    )


def _fit(state: dict[str, Any]) -> dict[str, Any]:
    """`state`, cut to STATE_CHARS of JSON (three quarters of Jev's limit, clients/judge.py MAX_STATE_CHARS): text that
    escapes long in JSON (quotes, new lines, letters outside ASCII, up to six characters each) can pass it under
    TEXT_CHARS. The evidence's text is cut first, then the spec, each to the longest start that fits, and flagged."""

    def size() -> int:
        return len(json.dumps(state, default=str))

    for part, key, flag in (("attachment", "text", "truncated"), ("work", "spec", "spec_truncated")):
        whole = state[part].get(key)
        if size() <= STATE_CHARS:
            break
        if not whole:
            continue
        low, high = 0, len(whole)  # the longest start of `whole` that fits: low fits (or is 0), high + 1 does not
        while low < high:
            mid = (low + high + 1) // 2
            state[part][key] = whole[:mid]
            low, high = (mid, high) if size() <= STATE_CHARS else (low, mid - 1)
        state[part][key] = whole[:low]
        state[part][flag] = True
    return state


def _questions(
    check: str | None = None, explain: tuple[Part, ...] = (), lines: list[str] | None = None
) -> dict[str, Any]:
    """The question set's questions (questions/pipeline-requirement.json): its one `met` question, or for one of CHECKS a
    yes/no question per part (`part_<key>`), each with the set's rules for reading the work and the attachment. A rubric
    asked whole with parts that explain it (`explain_parts`) is asked `met` and those parts beside it, and a rubric asked
    whole on a page with text also which line of it the answer rests on (READ, from `page_lines`)."""
    asked: dict[str, Any] = J.load_set(QUESTION_SET)["questions"]
    met: dict[str, Any] = asked["met"]
    rules = str(met["instructions"])
    # The set's rules for reading the work start at `work.title`; its first sentence (about the whole rubric) is not
    # this question's. Without that anchor (the set reworded), every rule is kept rather than the call failing.
    start = rules.find("`work.title`")
    rules = rules[start:] if start >= 0 else rules
    if check is None or check not in CHECKS:
        read = {READ: _read_question(lines)} if lines else {}
        if explain:
            # The whole question decides; each part beside it only says which one is missing (explain_parts).
            return {
                "met": met,
                **{
                    "part_" + part.key: {
                        **met,
                        "instructions": (
                            f"This question is about one part of `requirement.rubric` alone: the `attachment` "
                            f"{part.ask}, for the work as it is now. Answer high when it does, low when it does not. "
                            f"{rules}"
                        ),
                    }
                    for part in explain
                },
                **read,
            }
        return {**asked, **read}
    return {
        **{
            "part_" + part.key: {
                **met,
                "instructions": (
                    f"This question is about one part of `requirement.rubric` alone: the `attachment` {part.ask}, for "
                    f"the work as it is now. Answer high when it does, low when it does not. {rules}"
                ),
            }
            for part in CHECKS[check]
        },
    }


def _asked(rubric: object, text: object = None) -> dict[str, Any]:
    """The questions a requirement with this rubric is asked about a page with this text: its check's parts, or its
    whole question with the parts that explain it and the line it rests on."""
    return _questions(check_of(rubric), explain_parts(rubric), page_lines(text))


def _default_engine() -> "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None":
    """The engine a judge call uses when none is given: ENGINE when a test or harness set it, else the production engine on
    the installed store, else None (every call fails)."""
    if ENGINE is not None:
        return ENGINE
    return live_engine(_installed_store) if _installed_store is not None else None


def raw_engine(store: "Store") -> "Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]]":
    """Jev through judge.server_engine, answering the decision as it comes (`answers` by question id)."""

    db: Any = store

    def ask(state: dict[str, Any], questions: dict[str, Any], label: str) -> dict[str, Any]:
        with db.read() as c:
            asks = judge.server_engine(store, c, judge.primary_engine(store.settings, store))
        if asks is None:
            raise JudgeError("unconfigured", "no decision model is configured on this server")
        return cast("dict[str, Any]", asks(state, questions, label))

    return ask


def _noul(answers: Any, qid: str) -> float | None:
    answer: Any = cast("dict[str, Any]", answers).get(qid) if isinstance(answers, dict) else None
    return _probability(cast("dict[str, Any]", answer).get("noul") if isinstance(answer, dict) else None)


def _pick(answers: Any) -> str:
    """The option the model chose on READ, or empty when it was not asked or chose none of the options."""
    answer: Any = cast("dict[str, Any]", answers).get(READ) if isinstance(answers, dict) else None
    choice: object = cast("dict[str, Any]", answer).get("choice") if isinstance(answer, dict) else None
    return choice if isinstance(choice, str) else ""


def _read(result: dict[str, Any], qid: str | list[str], model: str, cost: object = None) -> dict[str, Any]:
    """The engine's answer to `qid` as judge_once's engines give it: {p, reason, provider, model, cost_usd,
    latency_ms}, and with a list of part question ids (`part_<key>`), `parts` (each part's probability) and p the lowest,
    or `met`'s when it was asked beside them.
    Raises JudgeError("shape") when any asked question has no probability."""
    answers: Any = result.get("answers")
    asked = [one for one in ([qid] if isinstance(qid, str) else qid) if one != READ]
    got = {one: _noul(answers, one) for one in asked}
    if any(p is None for p in got.values()) or not got:
        raise JudgeError("shape", "the decision model's answer has no probability")
    ps = cast("dict[str, float]", got)
    parts = {one.removeprefix("part_"): p for one, p in ps.items() if one.startswith("part_")}
    return {
        # Asked whole with parts beside it (explain_parts), the whole question is the verdict; the parts only explain it.
        "p": ps["met"] if "met" in ps else min(ps.values()),
        **({"parts": parts} if parts else {}),
        # The page line the model picked as what its answer rests on (READ), when it was asked one.
        **({"read": _pick(answers)} if _pick(answers) else {}),
        "reason": "",
        "provider": result.get("provider") or "",
        "model": result.get("model") or MODELS[model],
        "cost_usd": (result.get("cost_usd") if cost is None else cost) or 0.0,
        "latency_ms": result.get("ms") or 0,
    }


def live_engine(store: "Store") -> "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]]":
    """The decision engine the server asks through judge.server_engine."""

    ask = raw_engine(store)

    def engine(model: str, state: dict[str, Any], questions: dict[str, Any], label: str) -> dict[str, Any]:
        return _read(ask(state, questions, label), list(questions), model)

    return engine


def _base(requirement: dict[str, Any], attachment: dict[str, Any], chosen: str) -> dict[str, Any]:
    """The result judge_once starts from, before any answer."""
    return {
        "requirement_id": requirement.get("id") or requirement.get("stage_requirement_id") or "",
        "attachment_id": attachment.get("id") or "",
        "model": chosen,
        "provider": "",
        "cost_usd": 0.0,
        "latency_ms": 0,
        "reason": "",
    }


def judge_once(
    c: sqlite3.Connection,
    requirement: dict[str, Any],
    attachment: dict[str, Any],
    *,
    settings: "Settings | None" = None,
    engine: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None,
) -> dict[str, Any]:
    """Judge one attachment against one requirement's rubric with one model.

    Returns {"verdict": "met" | "not_met" | "unsure" | "failed", "p": float | None, "model", "cost_usd", "latency_ms",
    "provider", "reason", "requirement_id", "attachment_id"}; the reason is never empty. The model is Jev
    (judge_model_of). `engine` (or the module's ENGINE) makes the call; with neither, the call fails. An attachment with
    no id is no evidence: it is not met with p 0 and no model is asked. A failed call, an unreadable answer, or a call
    over the team's Decide cap (checked when `settings` is given) is `failed` (and `failed: True`), p None, never a pass.
    The caller's `c` is a read; no write transaction is held during the model call."""
    chosen = judge_model_of(c, requirement.get("pipeline_id"))
    base = _base(requirement, attachment, chosen)

    if not attachment.get("id"):
        return _ask(base, requirement, attachment, None)  # no evidence is not met, whatever the model or the cap
    if settings is not None and judge.over_spend_cap(c, settings) is not None:
        return _failed(base, "Decide has reached the team's monthly spend cap")
    return _ask(base, requirement, attachment, engine or _default_engine())


def _failed(base: dict[str, Any], why: str) -> dict[str, Any]:
    """The call itself failed (no model, an error, an unreadable answer, over the Decide cap): a judge failure, never a
    verdict and never a pass, with what went wrong as its reason, so a caller retries it (page_verdicts) and a person who
    sees it is told it was Jev that failed, not the page."""
    return {**base, "verdict": "failed", "p": None, "reason": why or "The judge call failed", "failed": True}


def judge_outside(
    store: Any,
    requirement: dict[str, Any],
    attachment: dict[str, Any],
    *,
    engine: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None,
) -> dict[str, Any]:
    """`judge_once` with no snapshot held during the model call: the model and the Decide cap are read in a short read,
    then the model is asked with no connection (the Jev step, page_verdicts.run)."""
    with store.read() as c:
        chosen = judge_model_of(c, requirement.get("pipeline_id"))
        capped = judge.over_spend_cap(c, store.settings) is not None
    base = _base(requirement, attachment, chosen)
    if capped and attachment.get("id"):
        return _failed(base, "Decide has reached the team's monthly spend cap")
    return _ask(base, requirement, attachment, engine or _default_engine())


def _ask(
    base: dict[str, Any],
    requirement: dict[str, Any],
    attachment: dict[str, Any],
    engine: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None",
) -> dict[str, Any]:
    """The model call and its answer, with no connection: `base` is judge_once's starting result (model chosen and the
    cap already checked). Safe to run while no database snapshot is held."""
    chosen = base["model"]

    if not attachment.get("id"):
        # No evidence row: nothing to read, so the answer is not met and no model is asked (it cost nothing and needs no
        # person to fix what is missing).
        return {**base, "verdict": "not_met", "p": 0.0, "reason": NO_EVIDENCE}

    ask = engine
    if ask is None:
        return _failed(base, "No judge is configured on this server")
    state = _state(requirement, attachment)
    try:
        result = ask(chosen, state, _asked(requirement.get("rubric"), state["attachment"].get("text")), QUESTION_SET)
    except Exception as exc:  # noqa: BLE001 - any error in the call is a judge failure, retried, never a verdict
        return _call_failed(base, exc)
    return _finish(base, requirement, attachment, state, result)


def _call_failed(base: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """The pair's result when its call ended in `exc`."""
    if isinstance(exc, JudgeError):
        failed: Any = exc
        return _failed(base, judge.FAILED.get(J.error_code(exc), failed.detail))
    LOG.warning("The judge call failed: %s", type(exc).__name__)
    return _failed(base, "The judge call failed")


def _finish(
    base: dict[str, Any],
    requirement: dict[str, Any],
    attachment: dict[str, Any],
    state: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """The verdict from the engine's answer about one pair, `state` being what one call about it alone is shown: from
    its parts when it was asked as one of CHECKS (`_part_lines`), else from its one probability."""
    chosen = base["model"]
    p = _probability(result.get("p"))
    check = check_of(requirement.get("rubric"))
    raw: object = result.get("parts")
    parts: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in cast("dict[str, object]", raw).items():
            got = _probability(value)
            if got is not None:
                parts[str(key)] = got
    if check is not None and parts:
        verdict, p, reason = _part_lines(check, parts)
    elif p is None:
        return _failed(base, "The judge's answer could not be read")
    else:
        verdict = verdict_of(p)
        reason = _words(result.get("reason")) or reason_for(verdict, str(requirement.get("name") or ""))
        explain = explain_parts(requirement.get("rubric"))
        text = state["attachment"].get("text")
        if verdict != "met" and check is None and not _words(result.get("reason")):
            # Name the item that is missing and the page line the model says its answer rests on (task #614). A met
            # keeps its line, and a model that gave its own words keeps them. A rubric of one sentence is its own only
            # item, quoted.
            named = _explain_lines(explain, parts) if explain and parts else []
            if not named and not explain:
                one = _one_item(verdict, requirement.get("rubric"))
                named = [one] if one else []
            seen = _seen(result.get("read"), text)
            if named and seen:
                named.insert(1, seen)
            elif seen:
                named = [seen]
            if named:
                reason = f"{reason_for(verdict, str(requirement.get('name') or ''))} {' '.join(named)}"
    reason = reason[:REASON_BYTES]
    if _cannot_see(attachment, state):
        # Enforced here, not only in the prompt: a model that says met on text it was not all shown is still unsure.
        # The call was made and is paid for, so its cost and latency are kept. A text that was cut tells the owner how
        # long it may be, so it is shortened rather than attached again.
        verdict, p, reason = "unsure", 0.5, _cannot_see_why(attachment, state)
    elif verdict == "met" and state["work"].get("spec_truncated"):
        # The rubric may need the whole spec, and the judge saw only its start: its met is not trusted. A not met stands.
        verdict, p, reason = "unsure", 0.5, SPEC_CUT
    return {
        **base,
        "verdict": verdict,
        "p": p,
        "model": str(result.get("model") or chosen),
        "provider": str(result.get("provider") or ""),
        "cost_usd": float(result.get("cost_usd") or 0.0),
        "latency_ms": int(result.get("latency_ms") or 0),
        "reason": reason,
    }


NAME = "requirement{}"
ONE_OF = (
    "Here `requirement` means `requirements.{name}`: this question is about that one requirement alone, and the other "
    "requirements are not part of it."
)


def _page_item(states: "list[dict[str, Any]]") -> "tuple[dict[str, Any], dict[str, Any], list[str]] | None":
    """One call about one page and several requirements: (state, questions, the question id of each requirement), the
    page and the work shown once and each requirement's `met` asked about its own. None when it does not fit one call;
    then each requirement is its own item."""
    met = _questions()["met"]
    first = states[0]
    held: dict[str, Any] = {}
    questions: dict[str, Any] = {}
    qids: list[str] = []
    for n, one in enumerate(states, start=1):
        name = NAME.format(n)
        held[name] = one["requirement"]
        qid = f"met{n}"
        questions[qid] = {**met, "instructions": ONE_OF.format(name=name) + " " + str(met["instructions"])}
        qids.append(qid)
    state = {"requirements": held, "work": first["work"], "attachment": first["attachment"]}
    return (state, questions, qids) if J.fits(state, questions) else None


def judge_many(store: Any, pairs: "list[tuple[dict[str, Any], dict[str, Any]]]") -> list[dict[str, Any]]:
    """`judge_outside` for each (requirement, attachment) pair, in order, in as few calls as fit: the pairs that show
    Jev the same page and work (one page attached to several requirements) are one item, read once with a question per
    requirement; up to GROUP items share a call (backend/decide_groups.py). Each pair's result is judge_once's, read
    from its own answer only; a pair whose answer is missing is asked again alone, and a failed call fails each pair
    in it as its own call failing would (`failed`, retried by page_verdicts). With ENGINE set (a test or harness's
    judge_once engine) each pair is asked on its own, as before."""
    if ENGINE is not None or (ASK is None and _installed_store is None):
        return [judge_outside(store, requirement, attachment) for requirement, attachment in pairs]
    ask = ASK or raw_engine(_installed_store if _installed_store is not None else store)
    with store.read() as c:
        capped = judge.over_spend_cap(c, store.settings) is not None
        bases = [_base(r, a, judge_model_of(c, r.get("pipeline_id"))) for r, a in pairs]
    out: list[dict[str, Any] | None] = [None] * len(pairs)
    states: dict[int, dict[str, Any]] = {}
    pages: dict[str, list[int]] = {}
    for n, (requirement, attachment) in enumerate(pairs):
        if not attachment.get("id"):
            out[n] = _ask(bases[n], requirement, attachment, None)
        elif capped:
            out[n] = _failed(bases[n], "Decide has reached the team's monthly spend cap")
        else:
            states[n] = _state(requirement, attachment)
            seen = json.dumps([states[n]["work"], states[n]["attachment"]], sort_keys=True, default=str)
            pages.setdefault(seen, []).append(n)
    # Each item: (state, questions) and, for each pair it holds, (its place, its question id).
    items: list[DG.Item] = []
    holds: list[list[tuple[int, str | list[str]]]] = []  # each pair's question id, or its parts' ids
    for members in pages.values():
        # A check's parts are never merged.
        whole = all(
            check_of(pairs[n][0].get("rubric")) is None and not explain_parts(pairs[n][0].get("rubric"))
            for n in members
        )
        merged = _page_item([states[n] for n in members]) if SAME_PAGE and whole and len(members) > 1 else None
        if merged is not None:
            items.append((merged[0], merged[1]))
            holds.append([(n, qid) for n, qid in zip(members, merged[2], strict=True)])
            continue
        for n in members:
            asked = _asked(pairs[n][0].get("rubric"), states[n]["attachment"].get("text"))
            items.append((states[n], asked))
            holds.append([(n, "met" if list(asked) == ["met"] else list(asked))])
    for k, run in enumerate(DG.packs(items, GROUP)):
        if k:  # the cap is read again before every call after the first, as judge_outside read it before each pair
            with store.read() as c:
                capped = judge.over_spend_cap(c, store.settings) is not None
        if capped:
            for i in run:
                for n, _qid in holds[i]:
                    out[n] = _failed(bases[n], "Decide has reached the team's monthly spend cap")
            continue
        replies = DG.ask_run(ask, [items[i] for i in run], QUESTION_SET, None, run)
        for i, reply in zip(run, replies, strict=True):
            share = None
            if isinstance(reply, dict):
                share = round(float(reply.get("cost_usd") or 0) / len(holds[i]), 6)
            for n, qid in holds[i]:
                requirement, attachment = pairs[n]
                if not isinstance(reply, dict):
                    out[n] = _call_failed(bases[n], reply)
                    continue
                try:
                    result = _read(reply, qid, bases[n]["model"], share)
                except JudgeError as exc:
                    out[n] = _call_failed(bases[n], exc)
                    continue
                out[n] = _finish(bases[n], requirement, attachment, states[n], result)
    return [r if r is not None else _failed(bases[n], "The judge call failed") for n, r in enumerate(out)]


def _cannot_see_why(attachment: dict[str, Any], state: dict[str, Any]) -> str:
    """Why Jev could not see all of the evidence: too long (with the length Jev was shown, in thousands, as the most it
    may be), or no text Tico can read."""
    evidence = state["attachment"]
    if evidence["text"] is None:
        return CANNOT_SEE
    what = "page" if str(attachment.get("source") or "").startswith("page:") else "evidence"
    return TOO_LONG.format(what=what, chars=max(1000, len(evidence["text"]) // 1000 * 1000))


def _cannot_see(attachment: dict[str, Any], state: dict[str, Any]) -> bool:
    """Whether the judge was not shown all of the evidence: Tico holds no text for an attachment that is there (an image, a
    video, a PDF, a link, or a text it could not read), or the text was cut. Such evidence never passes on the model's word
    (D60). An attachment with no id is no evidence row (the eval's missing-evidence cases): a rule judge decides that, so
    it is not treated as unreadable."""
    if not attachment.get("id"):
        return False
    evidence = state["attachment"]
    return evidence["text"] is None or bool(evidence["truncated"])


def read_prefix(blobs: Any, digest: str) -> bytes:
    """The first READ_MAX + 1 bytes of a blob, by a range read: a large upload is never loaded whole. The extra byte says
    the bytes go on (text_of_bytes sets truncated from it)."""
    return b"".join(blobs.open_range(digest, 0, files.READ_MAX))


def text_of_bytes(mime: str, data: bytes, size: int) -> tuple[str | None, bool]:
    """The text the judge is sent for evidence of this type, from its bytes: the words of the first READ_MAX bytes, and
    whether the bytes were cut (the extra byte in `data`, or a stored `size` past the cap). (None, False) for a type Tico
    reads no text from. The server's read and the eval adapter both use this, so they see the same text."""
    if not _reads_text(mime):
        return None, False
    cut = size > files.READ_MAX or len(data) > files.READ_MAX
    return files.extract(mime, data[: files.READ_MAX], full=True), cut


def _intact(data: bytes, digest: str, size: int) -> bool:
    """Whether the bytes a range read gave are the blob's bytes as far as the judge reads them. A blob within the cap must
    come back whole: its length is the stored size and its sha256 is the stored digest. A larger one must give exactly
    READ_MAX + 1 bytes, the cap and the extra byte that says more follows. Anything else is a damaged or cut read."""
    if size > files.READ_MAX:
        return len(data) == files.READ_MAX + 1
    if len(data) != size:
        return False
    return hashlib.sha256(data).hexdigest() == digest


def text_of(c: sqlite3.Connection, job: dict[str, Any]) -> tuple[str | None, bool]:
    """The text Tico holds for a piece of evidence, read up to file_library.READ_MAX bytes: a Tico page, a company file or
    an uploaded blob of a text type. (None, False) when Tico holds none (an image, a video, a PDF or a link), or the bytes
    cannot be read: the judge then answers unreadable, and the pass goes on. `truncated` is set when the bytes were cut."""
    blobs = getattr(c, "blobs", None)
    mime = str(job.get("mime") or "").split(";", 1)[0].strip().lower()
    digest = job.get("blob_digest")
    if blobs is None or not digest or not _reads_text(mime):
        return None, False
    size = int(job.get("blob_size") or 0)
    try:
        data = read_prefix(blobs, digest)
    except Exception as exc:  # noqa: BLE001 - an unreadable file is judged as unreadable; one bad blob never stops the pass
        LOG.warning("An attachment's bytes could not be read for the judge: %s", type(exc).__name__)
        return None, False
    if not _intact(data, str(digest), size):
        LOG.warning("An attachment's bytes came back short or altered for the judge; it is judged as unreadable")
        return None, False
    return text_of_bytes(mime, data, size)


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """Judges have no routes: a decision is read through the task routes. The installed store is what
    judge_once asks the production engine on when no engine is given."""
    global _installed_store
    _installed_store = store
    return None
