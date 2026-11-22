# pyright: strict
"""The answers of the Contacts routes in the stable contract (backend/entities/routes.py)."""

from __future__ import annotations

from typing import TypedDict

from ._base import answer


@answer
class ContactsRecordResult(TypedDict):
    id: str
    """The record's id in Tico"""
    entity: str
    """The entity it is a record of"""
