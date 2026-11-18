# pyright: strict
"""Growth segments: who the team sells to, each worth different money (Consumer and Rental, say).

A segment has a slug and a name; its customer rule in plain words (when someone becomes a customer, and when they
churn); `churn_days`, the inactivity window churn waits on (0 when churn is a cancel date), which delays every retention
window of its customers (backend/growth_computed.py); and its own revenue per customer month, margin and minimum cohort,
each falling back to the settings when blank.

A customer belongs to the segment its `segment` label names, by slug or name, any case; a label that names none, or a
blank one, is No segment. A channel's spend is for one segment (`growth_channels.segment_id`) or company-wide (NULL).
The customer's own segment decides counts, retention and LTV; the channel's segment decides spend only. A team with no
segments reads its labels as before.

Who may: everyone signed in reads. The owner, admins and the Growth Manager add and change segments, as channels
(backend/growth_setup.py editor); retiring one stays a person's.
"""

import sqlite3
from collections.abc import Callable, Iterable
from typing import Any, cast

from fastapi import Request

from . import brand as _brand_untyped
from . import growth_setup as _setup_untyped
from . import hubdb as _hubdb_untyped
from . import models as M
from .store import Problem

H: Any = _hubdb_untyped
admin = cast("Callable[[Any, Any], bool]", _brand_untyped.admin)
may_edit = cast("Callable[[Any, Any, Any], bool]", _setup_untyped.editor)
Row = dict[str, Any]

NO_SEGMENT = "none"  # the cut's value for customers whose label names no segment; never a segment's slug
FIELDS = (
    "slug",
    "name",
    "customer_rule",
    "churn_days",
    "revenue_month",
    "margin_pct",
    "min_cohort",
    "sort",
    "owner_ref",
    "retired_at",
)


def view(row: Row, channels: Iterable[str] = ()) -> Row:
    return {**{k: row.get(k) for k in FIELDS}, "channels": list(channels)}


def find(c: sqlite3.Connection, slug: str) -> Row | None:
    return H._one(c, "SELECT * FROM growth_segments WHERE slug=?", (slug,))


def listed(c: sqlite3.Connection, include_retired: bool = False) -> list[Row]:
    """The segments by sort then name, each with the slugs of the live channels whose spend is for it."""
    rows: list[Row] = H._rows(
        c.execute(
            "SELECT * FROM growth_segments"
            + ("" if include_retired else " WHERE retired_at IS NULL")
            + " ORDER BY sort, name COLLATE NOCASE"
        )
    )
    channels: dict[str, list[str]] = {}
    for sid, slug in c.execute(
        "SELECT segment_id, slug FROM growth_channels WHERE segment_id IS NOT NULL AND retired_at IS NULL "
        "ORDER BY sort, name COLLATE NOCASE"
    ):
        channels.setdefault(str(sid), []).append(str(slug))
    return [view(r, channels.get(str(r["id"]), [])) for r in rows]


def ref(c: sqlite3.Connection, value: str | None) -> str | None:
    """A live segment's id from its slug or name, any case; None for blank (company-wide)."""
    text = str(value or "").strip()
    if not text:
        return None
    row: Row | None = H._one(
        c,
        "SELECT id FROM growth_segments WHERE retired_at IS NULL AND (lower(slug)=lower(?) OR lower(name)=lower(?))",
        (text, text),
    )
    if not row:
        raise Problem("not_found", f"No segment {text}: tico growth segment list names them", 404)
    return str(row["id"])


def _owner(c: sqlite3.Connection, value: str | None) -> str | None:
    if value in (None, ""):
        return None
    actor: str | None = H.resolve_actor(c, value)
    if not actor or actor == H.TICO_ACTOR:
        raise Problem("not_found", f"No person or bot {value}", 404)
    return actor


def _shown(c: sqlite3.Connection, slug: str) -> Row:
    return next(s for s in listed(c, include_retired=True) if s["slug"] == slug)


def _clash(c: sqlite3.Connection, slug: str, name: str | None, own_id: str | None = None) -> None:
    """Slugs and names share one namespace (a customer label may give either), and none is No segment."""
    said = (name or "").strip().lower()
    if said == NO_SEGMENT:
        raise Problem("validation", f"{NO_SEGMENT} is how customers with no segment read; pick another name", 422)
    for other in H._rows(c.execute("SELECT id, slug, name FROM growth_segments WHERE id<>?", (own_id or "",))):
        if said and said in (str(other["slug"]), str(other["name"]).strip().lower()):
            raise Problem("conflict", f"There is already a segment named or slugged {name}", 409)
        if not own_id and slug == str(other["name"]).strip().lower():
            raise Problem("conflict", f"There is already a segment named {slug}", 409)


def create(c: sqlite3.Connection, actor: str, body: M.GrowthSegmentCreate) -> Row:
    H._writer(c, actor)
    if body.slug == NO_SEGMENT:
        raise Problem("validation", f"{NO_SEGMENT} is how customers with no segment read; pick another slug", 422)
    if find(c, body.slug):
        raise Problem("conflict", f"There is already a segment {body.slug}", 409)
    _clash(c, body.slug, body.name)
    at: str = H.now()
    row: Row = {
        "id": H.new_id(),
        **{k: getattr(body, k) for k in FIELDS if k not in ("retired_at", "owner_ref")},
        "owner_ref": _owner(c, body.owner_ref),
        "created_at": at,
        "updated_at": at,
    }
    c.execute(
        f"INSERT INTO growth_segments ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
    )
    H.event(c, actor, "growth.segment.create", row["id"], {"slug": body.slug})
    return _shown(c, body.slug)


def update(c: sqlite3.Connection, actor: str, slug: str, fields: Row) -> Row:
    H._writer(c, actor)
    row = find(c, slug)
    if not row:
        raise Problem("not_found", f"No segment {slug}", 404)
    for required in ("name", "customer_rule", "churn_days", "sort"):
        if required in fields and fields[required] is None:
            raise Problem("validation", f"{required} cannot be cleared", 422)
    if fields.get("name"):
        _clash(c, slug, fields["name"], str(row["id"]))
    if "owner_ref" in fields:
        fields["owner_ref"] = _owner(c, fields["owner_ref"])
    changed = {k: v for k, v in fields.items() if row.get(k) != v}
    if changed:
        c.execute(
            f"UPDATE growth_segments SET {', '.join(k + '=?' for k in changed)}, updated_at=? WHERE id=?",
            [*changed.values(), H.now(), row["id"]],
        )
        old = str(row["name"]).strip().lower()
        if "name" in changed and old != slug:
            # customers loaded under the old name keep their segment: their label becomes the slug, which never changes
            c.execute("UPDATE growth_customers SET segment=? WHERE lower(trim(segment))=?", (slug, old))
        H.event(c, actor, "growth.segment.update", row["id"], {"slug": slug, **changed})
    return _shown(c, slug)


def retire(c: sqlite3.Connection, actor: str, slug: str) -> Row:
    """Its customers' labels still name it, so their figures stay under it; no channel can be given it any more."""
    H._writer(c, actor)
    row = find(c, slug)
    if not row:
        raise Problem("not_found", f"No segment {slug}", 404)
    if not row["retired_at"]:
        at: str = H.now()
        c.execute("UPDATE growth_segments SET retired_at=?, updated_at=? WHERE id=?", (at, at, row["id"]))
        H.event(c, actor, "growth.segment.retire", row["id"], {"slug": slug})
    return _shown(c, slug)


def install(app: Any, store: Any, auth: Any, mutate: Callable[..., Any]) -> None:
    def editor(c: sqlite3.Connection, who: Any, what: str) -> None:
        auth.domain(who)
        if not may_edit(auth, who, c):
            raise Problem("forbidden", f"Only the owner, an admin or the Growth Manager {what}", 403)

    def retirer(who: Any) -> None:
        auth.domain(who)
        if not admin(auth, who):
            raise Problem("forbidden", "Only the owner or an admin retires a segment", 403)

    @app.get("/api/v2/growth/segments")
    def growth_segments(request: Request, include_retired: bool = False) -> Row:  # pyright: ignore[reportUnusedFunction]
        """The segments by sort then name, each with its channels. `may_edit`: the caller adds and changes them."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return {"segments": listed(c, include_retired), "may_edit": may_edit(auth, who, c)}

    @app.post("/api/v2/growth/segments")
    def growth_segment_add(request: Request, body: M.GrowthSegmentCreate) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Owner, admin or the Growth Manager: a new segment."""
        who = request.state.identity

        def work(c: sqlite3.Connection) -> Row:
            editor(c, who, "adds a segment")
            return create(c, who.actor, body)

        return mutate(request, body, work)

    @app.patch("/api/v2/growth/segments/{slug}")
    def growth_segment_update(request: Request, slug: str, body: M.GrowthSegmentUpdate) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Owner, admin or the Growth Manager: change a segment, only the fields sent."""
        who = request.state.identity

        def work(c: sqlite3.Connection) -> Row:
            editor(c, who, "changes a segment")
            return update(c, who.actor, slug, {k: getattr(body, k) for k in body.model_fields_set})

        return mutate(request, body, work)

    @app.post("/api/v2/growth/segments/{slug}/retire")
    def growth_segment_retire(request: Request, slug: str, body: M.Empty) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Owner or admin: retire a segment; its customers keep counting under it."""
        who = request.state.identity

        def work(c: sqlite3.Connection) -> Row:
            retirer(who)
            return retire(c, who.actor, slug)

        return mutate(request, body, work)
