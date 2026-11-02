"""The company's sidebar: which optional sections it hides, and its business type (Operations, plan §5).

Hiding is display only. A hidden section keeps its data, its rights, and its answers from the API and MCP, and a bookmark
to it still opens. The choice is one company-wide JSON document, `company-sidebar` in registry_metadata, read by every
client through `/api/v2/config` and changed by the owner or an admin (`PUT /api/v2/company/sidebar`). A company's
document lives in its own database: a hosted company runs its own server, so one company's choices never reach another.

- `hidden_sections` lists keys from KEYS. Unset (None) is not the same as empty ([], show everything). A write replaces
  the whole list, and a child's key stays in it while its parent is hidden, so showing the parent again keeps the child
  hidden.
- `business_type` is one of BUSINESS_TYPES. Choosing one derives `hidden_sections` from the table in BUSINESS_TYPES, but
  only while nothing is stored yet, or while setup is still open and the stored list is still the default the earlier
  answer made. Nothing else re-derives it, so a choice someone made is never overwritten.
- Defaults apply once. Setup derives them for a new company (`onboarding.save`, and `settle_setup` when setup completes
  without an answer). A company set up before business types existed gets the upgrade default the first time a server
  starts after the change (`ensure`): the Operations tabs its records and books or Stripe connections need stay on.

Every write leaves a `settings.sidebar` event with the before and after.
"""

import json
import sqlite3

from pydantic import Field

from .models import Contract
from .store import H, Problem

KEY = "company-sidebar"
OPERATIONS = "operations"
SECTIONS = ("strategy", "growth", "finance", OPERATIONS, "meetings")
# The four Operations tabs of the first go-live (plan §7.1). Their key is the tab's name in the API, so the tab that says
# "Products & services" is `operations.products`.
OPERATIONS_TABS = ("operations.locations", "operations.products", "operations.projects", "operations.equipment")
# Tabs that appear only once a connected system provides them (plan §5); they can be hidden like any other tab.
CONNECTED_TABS = ("operations.stock", "operations.jobs", "operations.production", "operations.business_systems")
KEYS = SECTIONS + OPERATIONS_TABS + CONNECTED_TABS
# Each Operations tab and the table behind it (plan §5, upgrade): a tab whose table holds a row is shown at the upgrade
# and at a setup with no answer. Books and Stripe connections count too (`_shown`), since their sync fills these tables
# after the upgrade. A CSV preview is stateless, and no other connected Operations system exists until O3, so nothing
# else counts here.
RECORD_TABS = (("operations.locations", "locations"), ("operations.products", "items"),
               ("operations.projects", "projects"), ("operations.equipment", "equipment"))

# The Operations tabs each business type shows at setup (plan §5). None hides the whole section.
BUSINESS_TYPES = {
    "field_service": OPERATIONS_TABS,
    "restaurant": ("operations.locations", "operations.products", "operations.equipment"),
    "retail": None,
    "manufacturer": OPERATIONS_TABS,
    "construction": OPERATIONS_TABS,
    "multi_location_services": ("operations.locations", "operations.products", "operations.equipment"),
    "software": ("operations.products", "operations.projects"),
    "other": None,
}


def defaults(business_type):
    """The keys a company of this business type hides at setup: the Operations section for a type with no use for it
    yet, else the Operations tabs its type does not show. Nothing outside Operations is hidden by default."""
    shown = BUSINESS_TYPES[business_type]
    if shown is None:
        return [OPERATIONS]
    return [tab for tab in OPERATIONS_TABS if tab not in shown]


def hidden_now(hidden):
    """What the sidebar hides: the stored keys, and Operations as well when all four of its base tabs are hidden (a
    section with every tab hidden is hidden). Unset hides nothing."""
    if not hidden:
        return []
    keys = set(hidden)
    if all(tab in keys for tab in OPERATIONS_TABS):
        keys.add(OPERATIONS)
    return [key for key in KEYS if key in keys]


def _normal(state):
    """The stored document as the rest of the module reads it: unknown values dropped, `hidden_sections` in KEYS order."""
    hidden = state.get("hidden_sections")
    kind = state.get("business_type")
    return {"business_type": kind if kind in BUSINESS_TYPES else None,
            "hidden_sections": [key for key in KEYS if key in hidden] if isinstance(hidden, list) else None}


def stored(c):
    """The company's sidebar as stored: `business_type` and `hidden_sections`, each None when nothing is stored."""
    from .repositories import metadata
    return _normal(metadata(c, KEY))


def config(c):
    """What `/api/v2/config` carries: null where nothing is stored, so a client can tell unset from empty."""
    return stored(c)


def check_business_type(value):
    if value not in BUSINESS_TYPES:
        raise Problem("validation", "Business type must be one of: " + ", ".join(BUSINESS_TYPES), 422)
    return value


def check_keys(keys):
    """The keys as stored: each one from KEYS, once, in KEYS order. Any other key is refused by name."""
    unknown = sorted({key for key in keys if key not in KEYS})
    if unknown:
        raise Problem("validation", "Unknown sidebar sections: " + ", ".join(unknown), 422)
    wanted = set(keys)
    return [key for key in KEYS if key in wanted]


def _apply_business_type(state, value, during_setup):
    """Record the business type, and derive the defaults where the rule in the module docstring allows it."""
    previous, hidden = state.get("business_type"), state.get("hidden_sections")
    state["business_type"] = value
    if hidden is None or (during_setup and previous and hidden == defaults(previous)):
        state["hidden_sections"] = defaults(value)


def _write(c, actor, change):
    """Apply `change` to the stored state, save it, and leave the audit event when anything moved."""
    from .repositories import save_metadata
    before = stored(c)
    state = dict(before)
    change(state)
    after = _normal(state)
    save_metadata(c, KEY, {key: value for key, value in after.items() if value is not None})
    if after != before:
        H.event(c, actor, "settings.sidebar", "company", {"before": before, "after": after})
    return after


def set_business_type(c, actor, value, during_setup=False):
    """Setup's business type (and Settings' later change to it). `during_setup` is set while the owner is still in setup,
    where a change of answer may still re-derive the defaults."""
    value = check_business_type(value)
    return _write(c, actor, lambda state: _apply_business_type(state, value, during_setup))


def settle_setup(c, actor):
    """Setup is complete without a business type: the company is Other, and its sidebar is what its records and
    connections need (`_upgrade_hidden`), which hides Operations when nothing is there (plan §5)."""
    if stored(c)["business_type"]:
        return stored(c)

    def change(state):
        state["business_type"] = "other"
        if state["hidden_sections"] is None:
            state["hidden_sections"] = _upgrade_hidden(c)
    return _write(c, actor, change)


def set_settings(c, actor, body):
    """Save what the request names and leave the rest. `hidden_sections` replaces the whole list."""
    def change(state):
        if "business_type" in body.model_fields_set:
            if body.business_type is None:
                raise Problem("validation", "Give a business type; it can't be cleared", 422)
            _apply_business_type(state, check_business_type(body.business_type), False)
        if "hidden_sections" in body.model_fields_set:
            if body.hidden_sections is None:
                raise Problem("validation", "Give a list of sections; [] shows everything", 422)
            state["hidden_sections"] = check_keys(body.hidden_sections)
    return _write(c, actor, change)


def _has_rows(c, table):
    """Whether a records table holds a row. A table this install has not created yet holds none."""
    try:
        return c.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        return False


def _past_setup(c):
    """A company that finished setup, or already runs an active bot, was set up before business types existed
    (backend/onboarding.py `needed`)."""
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key='onboarding'").fetchone()
    try:
        completed = bool((json.loads(row[0]) or {}).get("completed")) if row else False
    except (TypeError, ValueError):
        completed = False
    return completed or c.execute("SELECT 1 FROM bots WHERE state='active' LIMIT 1").fetchone() is not None


def _connections(c):
    """Whether a books connection (QuickBooks or Xero) exists, whether one still has provider records waiting to come in,
    and whether a Stripe connection exists. Disconnected connections count for nothing; a table this install has not
    created yet holds none. The waiting read is the books mirror's `pending` (finance/reads.py books_connected)."""
    from .finance import reads as FR
    try:
        books, waiting = FR.books_connected(c)
        stripe = c.execute("SELECT 1 FROM fin_connections WHERE provider='stripe' AND status!='disconnected' LIMIT 1"
                           ).fetchone() is not None
    except sqlite3.OperationalError:
        return False, False, False
    return books, waiting, stripe


def _shown(c):
    """The Operations tabs the company's records and connections need: a tab whose table holds a row; a books connection,
    or one with records waiting to come in, shows Locations and Products & services; a Stripe connection shows Products &
    services."""
    shown = {tab for tab, table in RECORD_TABS if _has_rows(c, table)}
    books, waiting, stripe = _connections(c)
    if books or waiting:
        shown |= {"operations.locations", "operations.products"}
    if stripe:
        shown.add("operations.products")
    return shown


def _upgrade_hidden(c):
    """What the upgrade and a setup with no answer hide: the Operations tabs `_shown` does not need, or the whole section
    when it needs none of them."""
    shown = _shown(c)
    if not shown:
        return [OPERATIONS]
    return [tab for tab in OPERATIONS_TABS if tab not in shown]


def ensure(c):
    """At every start, once per install: a company set up before this change, with nothing chosen yet, gets the upgrade
    default (`_upgrade_hidden`). A company still in setup is left for setup to answer."""
    current = stored(c)
    if current["business_type"] or current["hidden_sections"] is not None or not _past_setup(c):
        return
    hidden = _upgrade_hidden(c)
    _write(c, "system", lambda state: state.update(hidden_sections=hidden))


class SidebarSettings(Contract):
    """Fields left out stay as they are. `hidden_sections` replaces the whole list; [] shows everything."""
    business_type: str | None = Field(default=None, max_length=40)
    hidden_sections: list[str] | None = Field(default=None, max_length=len(KEYS) * 2)


def install(app, store, auth, mutate):
    from fastapi import Request

    from .settings_catalog import WHO

    def admin_check(c, who):
        """The caller as the access list has them now, so a change of admins counts from the next request."""
        auth.sync_access(c)
        current = auth.identity_for_actor(c, who.actor) if who.role in ("owner", "human") and not who.support else who
        if not auth.bot_admin(current):
            raise Problem("forbidden", "Only the owner or an admin changes the sidebar", 403)

    @app.get("/api/v2/company/sidebar")
    def sidebar_show(request: Request):
        """The sidebar: what is hidden, the business type, and who may change it. Everyone signed in reads it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            current = stored(c)
        return {**current, "hidden_now": hidden_now(current["hidden_sections"]), "sections": list(SECTIONS),
                "tabs": list(OPERATIONS_TABS + CONNECTED_TABS), "business_types": list(BUSINESS_TYPES),
                "may_change": bool(auth.bot_admin(who)), "who_may_change": WHO["admin"]}

    @app.put("/api/v2/company/sidebar")
    def sidebar_set(request: Request, body: SidebarSettings):
        """Set the business type and which sections and Operations tabs are hidden. Hiding changes no permission and no
        data; the owner and admins may do it, and each change is kept with who and when."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: set_settings(c, who.actor, body),
                      check=lambda c: admin_check(c, who))
