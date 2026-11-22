# pyright: strict
"""The answers of the Contacts routes in the stable contract (backend/entities/routes.py)."""

from __future__ import annotations

from typing import TypedDict

from ._base import answer


@answer
class ContactsNamesResult(TypedDict):
    names: dict[str, str]
    """Requested entity IDs visible to the caller, mapped to their current names"""


@answer
class ContactsRecordResult(TypedDict):
    id: str
    """The record's id in Tico"""
    entity: str
    """The entity it is a record of"""


class ContactsHistoryItem(TypedDict):
    id: str
    type: str
    """Either thread or activity"""
    kind: str
    occurred_at: str
    summary: str
    subject_id: str | None


@answer
class ContactsActivityHistory(TypedDict):
    rows: list[ContactsHistoryItem]
    next: str | None
    """Cursor for the next page"""
