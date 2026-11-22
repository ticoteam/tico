# pyright: strict
"""Jira as a source (backend/item_sources): links only so far. A link to a Jira issue is stored typed (source jira, item
type issue, key `PROJ-123`) with the connection it came through, so a task can be found by its issue. Nothing here calls
Jira yet: no metadata is read, and no requirement can ask anything of an issue (CONDITIONS is empty).

The key is the issue key as Jira shows it (upper case). Jira keys are unique within one site, so an issue is always one
connection's: the tool whose base URL or sites are on the link's host (`site`, item_sources.outside_item). A link on a
site no connection serves stays a plain link.
"""

import re
from urllib.parse import urlsplit

from . import Condition, Item

SOURCE, ISSUE = "jira", "issue"
NAME = "Jira"
ITEMS = {ISSUE: Item(ISSUE, "Issue")}
CONDITIONS: dict[str, Condition] = {}

KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)$")
# Jira Cloud's issue page: https://<site>.atlassian.net/browse/PROJ-123, and the board view's ?selectedIssue=PROJ-123.
ISSUE_URL = re.compile(
    r"^https://[A-Za-z0-9-]+\.atlassian\.net/(?:browse/([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)(?:[/?#]\S*)?$"
    r"|\S*[?&]selectedIssue=([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)(?:[&#]\S*)?$)"
)


def match_key(item_type: str, text: str) -> str | None:
    """An issue key (`proj-123` or `PROJ-123`) as Jira spells it, `PROJ-123`; None for anything else."""
    found = KEY.match(text.strip()) if item_type == ISSUE else None
    return found.group(1).upper() if found else None


def match_link(url: str) -> tuple[str, str] | None:
    """(item type, item key) of a Jira Cloud issue link, or None."""
    found = ISSUE_URL.match(url.strip())
    if not found:
        return None
    return ISSUE, (found.group(1) or found.group(2)).upper()


def site(url: str) -> str | None:
    """The Jira site a link is on: its host, lower case."""
    return (urlsplit(url.strip()).hostname or "").lower() or None
