# pyright: strict
"""The answers of the Pages routes: pages and Library › Files (backend/pages.py, backend/file_library.py)."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from ..models import AccessAudience
from ._base import answer


@answer
class PageApproval(TypedDict):
    version: int


@answer
class PageLinkView(TypedDict):
    target: str
    role: str
    name: str
    by: str
    at: str
    approval: PageApproval


@answer
class PageWriter(TypedDict):
    actor: str
    name: str
    versions: int
    last_at: str


@answer
class Page(TypedDict):
    audience: NotRequired[AccessAudience | None]
    """Only returned to managers on a single page read or write; null inherits where it is filed"""
    restricted: NotRequired[bool]
    """Only returned to managers, including on lists and cards"""
    links: NotRequired[list[PageLinkView]]
    writers: NotRequired[list[PageWriter]]
    may_write: NotRequired[bool]
    file_id: str
    title: str
    bot_id: str
    bot_name: str
    version: int
    updated_at: str
    updated_by: str | None
    updated_by_name: NotRequired[str]
    cadence: Literal["none", "daily", "weekly", "monthly"]
    as_of: str | None
    """The date the data is from (YYYY-MM-DD)"""
    stale_days: int | None
    """Days since the last version once past 1.5x the cadence; null while current or without a cadence"""
    replaced_by_id: str | None
    """The newer page's file id, when the reader may see it"""
    replaced_by_title: NotRequired[str | None]
    archived: bool
    task_id: str | None
    src: str | None
    """Where to frame the page; null means open it (GET /api/v2/files/{fid}/versions/{number})"""


@answer
class PagePin(TypedDict):
    place: str
    label: str


@answer
class PageShown(TypedDict):
    page: Page
    can_manage: NotRequired[bool]
    can_delete: NotRequired[bool]
    pinned: NotRequired[list[PagePin]]
    """The places it is pinned that the caller may open"""


@answer
class PageUnlinked(TypedDict):
    page: Page | None
    """null after the caller took a page they may not read off a task whose pages they write"""
    can_manage: NotRequired[bool]
    can_delete: NotRequired[bool]
    pinned: NotRequired[list[PagePin]]
    """The places it is pinned that the caller may open"""


@answer
class PagePublished(TypedDict):
    page: Page
    links: NotRequired[list[PageLinkView]]
    unembedded: list[str]
    created: NotRequired[bool]


@answer
class PageList(TypedDict):
    pages: list[Page]
    has_more: NotRequired[bool]
    next_cursor: NotRequired[str | None]
