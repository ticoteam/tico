# pyright: strict
"""Who fills each role the Product and Engineering presets name (docs/pipelines-v3.md "Default pipelines").

The presets name roles, not bots: `pipeline_settings.preset` fills a pipeline's manager, its stage owners and the
person on its people OKs from `resolve`, and `ensure_presets` fills a still-empty one once the role's bot appears.

- A role is found by its seat's template, never by a name (F23): an open seat (`seats.closed_at` NULL) with that
  `template` whose `filled_by` is a bot that is not archived. Reviewer 1 and Reviewer 2 share `pr-reviewer`: among its
  seats, ordered by title then `created_at`, the first is Reviewer 1 and the second Reviewer 2.
- With no such seat, the first active bot whose `bot_settings.template` is the role's, by slug.
  Reviewer 2 is then the second `pr-reviewer` bot.
- Release Manager is `release_schedule_routes.release_manager`: the same bot the release hour runs on.
- `ok_person` (D5): the person on a stage's people OK is the first person above the pipeline's manager bot on the org
  chart, else the org owner (C16). None only when no person can sign in.

Reads only; never writes.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

PRODUCT_MANAGER = "product_manager"
ENGINEERING_MANAGER = "engineering_manager"
BUILDER = "builder"
REVIEWER_1 = "reviewer_1"
REVIEWER_2 = "reviewer_2"
RELEASE_MANAGER = "release_manager"
ROLES = (PRODUCT_MANAGER, ENGINEERING_MANAGER, BUILDER, REVIEWER_1, REVIEWER_2, RELEASE_MANAGER)
# The template each role's seat is placed with (templates/catalog/<template>).
TEMPLATES = {
    PRODUCT_MANAGER: "product-manager",
    ENGINEERING_MANAGER: "engineering-manager",
    BUILDER: "builder",
    REVIEWER_1: "pr-reviewer",
    REVIEWER_2: "pr-reviewer",
    RELEASE_MANAGER: "release-owner",
}
# The default chart's row id for each role's seat (backend/company_file/chart_formula.json). A live seat's bot gets
# this slug at go-live (company_import._go_live).
CHART_IDS = {
    PRODUCT_MANAGER: "product-manager",
    ENGINEERING_MANAGER: "engineering-manager",
    BUILDER: "builder",
    REVIEWER_1: "reviewer-1",
    REVIEWER_2: "reviewer-2",
    RELEASE_MANAGER: "release-manager",
}


def resolve(conn: "sqlite3.Connection") -> dict[str, str | None]:
    """{role: the bot's actor (`bot:<id>`) or None} for every role in ROLES. Track A."""
    raise NotImplementedError("pipeline_seats.resolve: track A")


def ok_person(conn: "sqlite3.Connection", manager: str | None) -> str | None:
    """The person on a stage's people OK for a pipeline whose manager is `manager` (D5, C16). Track A."""
    raise NotImplementedError("pipeline_seats.ok_person: track A")
