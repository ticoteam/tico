# pyright: strict
"""Who fills each role the software presets name: Product, and Product and Engineering before 0.6 (docs/pipelines-v3.md
"Default pipelines").

The presets name roles, not bots: `pipeline_settings.preset` fills a pipeline's manager, its stage owners and the
person on its people OKs from `resolve`, and `ensure_presets` fills a still-empty one once the role's bot appears.

- A role is found by its seat's template, never by a name (F23): a seat with that `template` whose `filled_by` is a bot
  that is not archived. A filled seat is closed (`lifecycle.fill_seat` sets `closed_at` with `filled_by`), so this is
  any seat, open or closed, that a bot fills. Reviewer 1 and Reviewer 2 share `reviewer`: among its filled seats,
  ordered by title then `created_at`, the first is Reviewer 1 and the second Reviewer 2. The Product Owner is the
  first `feature-owner` the same way (the first segment's, live at setup).
- With no such seat, the first active bot whose `bot_settings.template` is the role's, by slug.
  Reviewer 2 is then the second `reviewer` bot.
- A pipeline's manager is its team's Manager: the Product Manager (`product-manager`) and the Engineering Manager
  (`engineering-manager`), each found by its template like any other role.
- Release Owner is `release_schedule_routes.release_manager`: the same bot the release hour runs on. That one must be
  active, so until a Release Owner is set up (a live seat's bot starts planned) it is found like any other role.
- `ok_person` (D5): the person on a stage's people OK is the first person above the pipeline's manager bot on the org
  chart, else the org owner (C16). None only when no person can sign in.

Reads only; never writes.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

PRODUCT_MANAGER = "product_manager"
PRODUCT_OWNER = "product_owner"
ENGINEERING_MANAGER = "engineering_manager"
BUILDER = "builder"
REVIEWER_1 = "reviewer_1"
REVIEWER_2 = "reviewer_2"
RELEASE_MANAGER = "release_manager"
QA_OWNER = "qa_owner"
ROLES = (
    PRODUCT_MANAGER,
    PRODUCT_OWNER,
    ENGINEERING_MANAGER,
    BUILDER,
    REVIEWER_1,
    REVIEWER_2,
    RELEASE_MANAGER,
    QA_OWNER,
)
# The template each role's seat is placed with (templates/catalog/<template>).
TEMPLATES = {
    PRODUCT_MANAGER: "product-manager",
    PRODUCT_OWNER: "feature-owner",
    ENGINEERING_MANAGER: "engineering-manager",
    BUILDER: "builder",
    REVIEWER_1: "reviewer",
    REVIEWER_2: "reviewer",
    RELEASE_MANAGER: "release-owner",
    QA_OWNER: "qa-owner",
}
# The default chart's row id for each role's seat (backend/company_file/chart_formula.json). A live seat's bot gets
# this slug at go-live (company_import._go_live). The Product Owner's is the first segment's, `<segment>-feature-owner`,
# or `feature-owner` with no segment.
CHART_IDS = {
    PRODUCT_MANAGER: "product-manager",
    PRODUCT_OWNER: "feature-owner",
    ENGINEERING_MANAGER: "engineering-manager",
    BUILDER: "builder",
    REVIEWER_1: "reviewer-1",
    REVIEWER_2: "reviewer-2",
    RELEASE_MANAGER: "release-owner",
    QA_OWNER: "qa-owner",
}


_SEATED = (
    "SELECT s.filled_by FROM seats s JOIN bots b ON b.id = s.filled_by "
    "WHERE s.template=? AND s.filled_by <> '' AND COALESCE(b.state, 'active') <> 'archived' "
    "ORDER BY s.title, s.created_at, s.id"
)
_BY_TEMPLATE = (
    "SELECT b.id FROM bots b JOIN bot_settings s ON s.bot_id = b.id "
    "WHERE s.template=? AND COALESCE(b.state, 'active') <> 'archived' ORDER BY b.slug"
)


def _has_seats(conn: "sqlite3.Connection") -> bool:
    found = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seats'").fetchone()
    if not found:
        return False
    return any(row[1] == "template" for row in conn.execute("PRAGMA table_info(seats)").fetchall())


def _bots(conn: "sqlite3.Connection", template: str, seats: bool) -> list[str]:
    """The bots of a template, as ids: those that fill its seats first, in seat order, then any other bot made from
    it, by slug."""
    seated = [str(row[0]) for row in conn.execute(_SEATED, (template,)).fetchall()] if seats else []
    rest = [str(row[0]) for row in conn.execute(_BY_TEMPLATE, (template,)).fetchall()]
    return list(dict.fromkeys([*seated, *rest]))


def resolve(conn: "sqlite3.Connection") -> dict[str, str | None]:
    """{role: the bot's actor (`bot:<id>`) or None} for every role in ROLES."""
    from . import release_schedule_routes

    seats = _has_seats(conn)
    found: dict[str, str | None] = {}
    reviewers = _bots(conn, TEMPLATES[REVIEWER_1], seats)
    for role in ROLES:
        if role == RELEASE_MANAGER:
            rm: Any = release_schedule_routes
            others = _bots(conn, TEMPLATES[role], seats)
            bot: str | None = rm.release_manager(conn) or (others[0] if others else None)
        elif role in (REVIEWER_1, REVIEWER_2):
            at = 0 if role == REVIEWER_1 else 1
            bot = reviewers[at] if len(reviewers) > at else None
        else:
            bots = _bots(conn, TEMPLATES[role], seats)
            bot = bots[0] if bots else None
        found[role] = f"bot:{bot}" if bot else None
    return found


def ok_person(conn: "sqlite3.Connection", manager: str | None) -> str | None:
    """The person on a stage's people OK for a pipeline whose manager is `manager` (D5, C16): the first person above
    that bot on the org chart who can act, else the company's person (its owner while they can act)."""
    from . import waiting_on

    return waiting_on.person_above(conn, manager) or waiting_on.company_person(conn)
