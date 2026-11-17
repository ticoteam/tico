# pyright: strict
"""A bot's question to a person that Tico can answer itself (Tasks spec "Tico answers what it already knows").

Bots asked people which repository a task is in, whether they could push to it, and whether they could view the app:
facts Tico holds. Before such a question reaches a person it is matched here, and the facts are read at that moment
(never at the start of the run, so a setting a person turned on a minute ago counts):

- every part of the question is a known topic and every fact is a yes: nobody is asked, the bot gets the answer
  (`answer`), with how to look it up next time where a bot can;
- a fact is a "no" that only a person can change (app view off, no write access): the question still goes to the
  person, with one plain "Tico checked:" sentence added to its why (`checked_line`);
- anything else (another topic, a question mixing one in, a fact Tico cannot read): the question goes on unchanged.

Matching is plain word patterns, no model call. It runs on a bot's ❓ to a person on a task
(task_comments_routes.task_question); a bot that was answered and still needs the person sends it again with
`not_known`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

REPO, ACCESS, APP_VIEW = "repository", "access", "app view"

# One question is cut into its parts at a question mark, a semicolon, or a joining "and"/"also"; each part must be a
# known topic, or the whole question goes to the person ("Which repo, and which of these designs?").
_PARTS = re.compile(r"[?;]+|,?\s+\b(?:and|also|plus)\b\s+|,\s+", re.I)
# What may be left of a part without making it a question of its own.
_FILLER = re.compile(r"^(?:please|thanks|thank you|quick question|sorry|hi|hello)?[.!\s]*$", re.I)
# Access and app view are known only when the part is about the asking bot itself ("Can I push", "for me"): "Should
# the access screen show dates?" is a product question, not one about this bot.
_ABOUT_ME = re.compile(r"\b(?:i|me|my|we|us|this bot)\b", re.I)
_SELF_TOPICS = ("access", "app view")
# A repository after "access to", "push to": the repo, the code, or a named owner/name.
_REPO_WORDS = (
    r"(?:push to |write to )?(?:the |this |our |your )?"
    r"(?:repo|repository|code|codebase|[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+)\b"
)
# A request to take access away is for a person, whatever Tico holds ("Can you remove my write access?").
_TAKE_AWAY = re.compile(r"\b(?:remove|revoke|take away|drop|turn off|disable|cut)\b", re.I)
_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        APP_VIEW,
        # The app itself, not a page of it ("Can I see the app's billing page?" is a question for the person).
        re.compile(
            r"\b(?:app[ -]?view|app[ -]?shot|(?:view|see|open|look at) (?:the |your )?app\b(?!'s|\s+page))", re.I
        ),
    ),
    (
        ACCESS,
        re.compile(
            # Repository access only: a bare "access", "permission" or "rights" ("permission to spend $40", "access
            # to the Stripe dashboard") is the person's to answer.
            # "Write access" counts unless it is to something other than a repository ("write access to the Stripe
            # account"); "merge rights" never does (merging is a person's step). "Can I push / write / commit / open
            # a PR" counts only alone or with a repository after it: "Can I push the release tag?", "Can I write a
            # reply to the customer?" and "Can we commit to the Friday date?" ask for a go-ahead, not a fact.
            rf"\b(?:write|push|commit|repo|repository|github) (?:access|permissions?|rights)\b"
            rf"(?! (?:to|on|for|in) (?!{_REPO_WORDS}))"
            rf"|\b(?:access|permissions?|rights) to {_REPO_WORDS}"
            rf"|\bcan (?:i|we|this bot|you let me) (?:push|write|commit|open (?:a )?prs?)(?: (?:to|in|into|on) {_REPO_WORDS})?[.!\s]*$",
            re.I,
        ),
    ),
    (
        REPO,
        re.compile(
            # The task's repository, not where something new should go ("Which repo should the new plugin live in?").
            r"\b(?:which|what)(?: github)? (?:repo|repository)(?: (?:is|are|am|do|does|should|did))? "
            r"(?:this|it|the task|the code|this task|i|we)\b(?! (?:put|add|create|start|move))"
            r"|\b(?:which|what)(?: github)? (?:repo|repository)$|\bwhere is the (?:code|repo|repository)\b"
            r"|\b(?:repo|repository) (?:is|for|should) (?:this|it|i)\b|^(?:the )?(?:repo|repository)$",
            re.I,
        ),
    ),
)
_NAMED_REPO = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100})\b")
# Slash words that name kinds of access, never a repository ("read/write access").
_ACCESS_WORDS = {"read", "write", "push", "pull", "r", "w", "rw", "commit", "merge", "admin"}


def _named_repositories(line: str) -> list[str]:
    return [
        name
        for name in _NAMED_REPO.findall(line or "")
        if not all(part.lower() in _ACCESS_WORDS for part in name.split("/"))
    ]


def topics(line: str) -> list[str] | None:
    """The known topics a question is about, in order, or None when any part of it is something else."""
    found: list[str] = []
    parts = [p.strip() for p in _PARTS.split(line or "") if p and p.strip()]
    for part in parts:
        if _FILLER.match(part):
            continue
        hit = next(
            (
                name
                for name, pattern in _TOPICS
                if pattern.search(part)
                and (name not in _SELF_TOPICS or (_ABOUT_ME.search(part) and not _TAKE_AWAY.search(part)))
            ),
            None,
        )
        if hit is None:
            return None
        if hit not in found:
            found.append(hit)
    return found or None


@dataclass
class Fact:
    topic: str
    yes: bool
    text: str  # a yes: the answer line the bot reads; a no: the plain "Tico checked:" sentence a person reads


@dataclass
class Known:
    """What Tico found for a question it matched: answered (every fact a yes) or checked (one or more a no)."""

    topics: list[str]
    facts: list[Fact]

    @property
    def answered(self) -> bool:
        return all(f.yes for f in self.facts)

    def answer(self, person: str, task_ref: object) -> str:
        lines = [f"Tico knows this, so {person} was not asked:"]
        lines += ["  " + f.text for f in self.facts]
        if REPO in self.topics and task_ref:
            lines.append(f"Next time: tico task show {task_ref} names the repository.")
        lines.append("Not your question? Ask again with --not-known.")
        return "\n".join(lines)

    def checked_line(self) -> str:
        return " ".join(f.text for f in self.facts if not f.yes)


def find(
    line: str,
    repositories: Callable[[], list[str]],
    repo_access: Callable[[str], str | None],
    app_view: Callable[[], dict[str, Any] | None],
    name_of: Callable[[str], str],
) -> Known | None:
    """The facts for a bot's question, read now, or None when it is not all known topics or a fact can't be read.
    `repositories()` is the task's (stage_inputs.repositories); `repo_access(repo)` is "write", "read", "" (none) or
    None (unknown: no GitHub App); `app_view()` is app_view.setting or None."""
    asked = topics(line)
    if not asked:
        return None
    repos = repositories()
    named = _named_repositories(line)
    if named:
        # A named repository counts only when Tico knows it (one of the task's, or one in the bot's access list);
        # any other name is left to the person rather than answered with a fact about a repository Tico can't see.
        known = [n for n in named if n.lower() in {r.lower() for r in repos} or repo_access(n)]
        if len(known) != len(named):
            return None
        repo: str | None = known[0]
    else:
        repo = repos[0] if len(repos) == 1 else None
    facts: list[Fact] = []
    for topic in asked:
        if topic == REPO:
            if len(repos) != 1:
                return None
            facts.append(Fact(REPO, True, f"Repository: {repos[0]} (tico task worktree add {repos[0]})."))
        elif topic == ACCESS:
            if not repo:
                return None
            level = repo_access(repo)
            if level is None:
                return None
            if level == "write":
                facts.append(Fact(ACCESS, True, f"Write access to {repo}: yes, through the GitHub App."))
            elif level == "read":
                facts.append(
                    Fact(
                        ACCESS,
                        False,
                        f"Tico checked: this bot can read {repo} but can't push to it. "
                        "You give it write access in the bot's Settings, under Repositories.",
                    )
                )
            else:
                facts.append(
                    Fact(
                        ACCESS,
                        False,
                        f"Tico checked: this bot can't reach {repo} yet. "
                        "You give it access in the bot's Settings, under Repositories.",
                    )
                )
        else:
            setting = app_view()
            if setting is None:
                return None
            if setting.get("on"):
                by = name_of(str(setting.get("turned_on_by") or "")) or "a person"
                when = str(setting.get("turned_on_at") or "")[:10]
                facts.append(
                    Fact(APP_VIEW, True, f"App view: on, turned on by {by}" + (f" on {when}." if when else "."))
                )
            else:
                facts.append(
                    Fact(
                        APP_VIEW,
                        False,
                        "Tico checked: this bot can't see the app yet. You turn that on in the bot's Settings.",
                    )
                )
    return Known(asked, facts)


def read_now(c: Any, caller: Any, auth: Any, task_id: str, line: str) -> Known | None:
    """`find` with the facts read in this connection, as `caller` (a bot) may see them: the task's repositories, the
    bot's GitHub access through the App, and its app view."""
    from . import app_view as _app_view
    from . import repositories as _repositories
    from . import stage_inputs as _stage_inputs
    from . import task_calls as _task_calls
    from .store import H as _H
    from .store import Problem

    H: Any = _H

    stage_inputs: Any = _stage_inputs
    repositories_module: Any = _repositories
    task_calls: Any = _task_calls
    bot: str = H.actor_id(caller.actor)
    visible = auth.task_sql(c, caller)

    def readable(other: str) -> bool:
        return bool(task_calls._readable(c, caller.actor, other, visible))

    def repositories() -> list[str]:
        return stage_inputs.repositories(c, task_id, readable)

    def repo_access(repo: str) -> str | None:
        app_row = (
            c.execute("SELECT org FROM github_app WHERE id='app'").fetchone() if H._has_table(c, "github_app") else None
        )
        if not app_row or not app_row["org"]:
            return None
        try:
            grants: list[dict[str, Any]] = repositories_module.access(c, bot, app_row["org"])["effective"]
        except Problem:
            return None
        return next((str(g["access"]) for g in grants if str(g["full_name"]).lower() == repo.lower()), "")

    return find(line, repositories, repo_access, lambda: _app_view.setting(c, bot), lambda a: H.actor_name(c, a) or "")
