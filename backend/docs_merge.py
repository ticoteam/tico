# pyright: strict
"""A bot's merge request for a docs-only pull request is refused: docs merge with no approval (policies/approvals.md).

A pull request whose every changed file is under docs/ or ends in .md or .mdx needs no person's yes, and neither does
any pull request in the docs and marketing site repository. The refusal tells the bot to add a `Changelog:` line to the
body and to merge it itself once its checks are green. When GitHub will not say which files changed, the request goes
ahead as before, so a person still decides.
"""

import re
from typing import Any, cast
from urllib.parse import quote

from .store import Problem

REFUSAL = "docs_merge_no_approval"
# Repositories where every pull request is a docs or site change (lower-case owner/name).
DOCS_SITE_REPOS = frozenset({"ticoteam/tico-marketing-site"})
DOCS_SUFFIXES = (".md", ".mdx")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
PAGE_SIZE = 100
PAGE_LIMIT = 30  # the same ceiling backend/github.py reads a pull request's files to

DOCS_MESSAGE = (
    "This pull request changes only docs, so it needs no approval. Add a line that starts with "
    '"Changelog:" to its body, saying what changed for readers, then merge it yourself once its checks are green.'
)
SITE_MESSAGE = (
    "Pull requests in {repo} are docs and site changes, so they need no approval. Add a line that starts with "
    '"Changelog:" to its body, saying what changed for readers, then merge it yourself once its checks are green.'
)


def is_docs_path(path: object) -> bool:
    text = str(path or "")
    return text.startswith("docs/") or text.lower().endswith(DOCS_SUFFIXES)


def changed_files(service: Any, repo: str, number: int) -> list[str] | None:
    """Every file the pull request changes, renames' old names too; None when GitHub will not say."""
    if service is None:
        return None
    try:
        token = service.mint([repo], {"pull_requests": "read", "contents": "read"})[0]
        names: list[str] = []
        for page in range(1, PAGE_LIMIT + 1):
            response = service._call(
                "GET",
                f"/repos/{quote(repo, safe='/')}/pulls/{number}/files",
                headers={"Authorization": "Bearer " + token},
                params={"per_page": PAGE_SIZE, "page": page},
            )
            if response.status_code != 200:
                return None
            files = cast(list[dict[str, Any]], response.json())
            for item in files:
                names.append(str(item["filename"]))
                if item.get("previous_filename"):
                    names.append(str(item["previous_filename"]))
            if len(files) < PAGE_SIZE:
                return names or None
    except (Problem, RuntimeError, ValueError, KeyError, TypeError):  # unreadable: the request goes ahead
        return None
    return None


def refusal(service: Any, payload: object) -> str | None:
    """The message that refuses this merge request, or None when it goes ahead."""
    if not isinstance(payload, dict):
        return None
    payload = cast(dict[str, Any], payload)
    repo = str(payload.get("repo") or "").strip()
    number = str(payload.get("pr") or "").strip().lstrip("#")
    if not REPO.fullmatch(repo) or not number.isdigit():
        return None  # the approval's own checks say what is missing
    if repo.lower() in DOCS_SITE_REPOS:
        return SITE_MESSAGE.format(repo=repo)
    names = changed_files(service, repo, int(number))
    if names and all(is_docs_path(name) for name in names):
        return DOCS_MESSAGE
    return None


def refuse(service: Any, payload: object) -> None:
    message = refusal(service, payload)
    if message:
        raise Problem(REFUSAL, message, 422)
