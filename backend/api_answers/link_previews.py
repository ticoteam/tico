# pyright: strict
"""The answers of the Link previews routes (backend/link_preview.py)."""

from typing import TypedDict

from ._base import answer


@answer
class LinkPreview(TypedDict):
    url: str
    """The address asked about, without its #fragment"""
    title: str
    site_name: str
    """The site's own name (og:site_name), else its host"""
    description: str | None
    image: str | None
    """The page's picture made small, served by this server (never the outside address)"""
    image_width: int | None
    image_height: int | None
