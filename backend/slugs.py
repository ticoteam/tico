# pyright: strict
"""The one slug rule for ids and file names made from free text: `Customer Theft Claims!` -> `customer-theft-claims`.

Many of these slugs are stored (stage, entity, tool, KPI and trigger ids, doc paths), so the rule never changes:
test_slugs.py pins its output for each caller's style.
"""

import re

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, limit: int | None = None, fallback: str = "") -> str:
    """`text` lower-cased, each run of anything but ASCII letters and digits one hyphen, none at either end; cut to
    `limit` characters (no hyphen left at the cut) when given, and `fallback` when nothing is left. The caller turns its
    value into a string first (`str(x)` or `str(x or "")`), as each did before they shared this."""
    slug = _NOT_SLUG.sub("-", text.lower()).strip("-")
    if limit is not None:
        slug = slug[:limit].strip("-")
    return slug or fallback
