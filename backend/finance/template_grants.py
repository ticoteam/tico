"""What a bot made from a catalog template comes with in Finance: the Finance rights and one company-wide view that its
card declares (`finance:` in card.yaml). settings_admin.create_bot is where every hire lands (a seat, a team build,
company import, the bot's own add), so actions.template_hire makes the grant there; actions.template_ensure makes the
grants still waiting for Finance, at start and on the scheduler's Finance tick.

Only an owner's or an admin's hire is granted: create_bot passes its `privileged` as `allowed`. A member's hire, or a
bot's, records `bot.finance_not_granted` with its creator and no grant, and nothing grants it later (ensure reads only
the pending ones). The owner can give the rights by hand; the bot is hired and works without them.

Once per bot: each grant is one `bot.finance_granted` event, so a later revoke stays revoked and a restart grants
nothing. A hire before Finance is set up is recorded as `bot.finance_pending` and granted once Finance is set up. Rights
go through rights.grant_defaults, the mechanism the Bookkeeper's rights use. Only the Procurement Manager declares a
grant today: finance.ap and the expenses view."""
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from .. import hubdb as _hubdb
from .context import is_set_up
from .core import rights as R
from .core import view as VW
from .core.org import SYSTEM
from .rights import grant_defaults
from .store import access as SA
from .store import ledger as SL
from .store import tico

# Tico's hub is untyped: this module reaches it through this alias, typed at each use.
_H: Any = _hubdb

GRANTED = "bot.finance_granted"
PENDING = "bot.finance_pending"
NOT_GRANTED = "bot.finance_not_granted"
KEEPER: str = _H.KEEPER


def declared(catalog_dir: str | Path, template: str) -> dict[str, Any]:
    """The Finance grant a template's card declares: {"rights": [finance.* names], "view": a view kind}, or {} when it
    declares none. finance.admin is never a template's to give, and an unknown name or view is dropped, not granted."""
    if not template:
        return {}
    try:
        loaded: object = yaml.safe_load((Path(catalog_dir) / template / "card.yaml").read_text())
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    block: object = cast(dict[str, Any], loaded).get("finance")
    if not isinstance(block, dict):
        return {}
    finance = cast(dict[str, Any], block)
    listed: object = finance.get("rights")
    names: list[object] = cast(list[object], listed) if isinstance(listed, list) else []
    rights = list(dict.fromkeys(str(r) for r in names if r in R.RIGHTS and r != R.ADMIN))
    view = str(finance.get("view") or "")
    view = view if view in VW.WHAT else ""
    return {"rights": rights, "view": view} if rights or view else {}


def hire(c: sqlite3.Connection, slug: str, template: str, catalog_dir: str | Path, *, allowed: bool,
         creator: str) -> dict[str, Any]:
    """A bot just made from `template` by `creator`. An owner's or an admin's hire (`allowed`) gets its grant now when
    Finance is set up, else it is recorded as pending. Anyone else's hire records bot.finance_not_granted with the
    creator and no grant. A template that declares no grant does nothing, and no hire fails because Finance is not set
    up yet."""
    grant = declared(catalog_dir, template)
    if not grant:
        return {}
    if not allowed:
        reason = "hired by someone who is not an owner or an admin"
        tico.audit(c, creator, NOT_GRANTED, slug, {**grant, "creator": creator, "reason": reason})
        return {}
    if not is_set_up(c):
        tico.audit(c, KEEPER, PENDING, slug, grant)
        return {}
    return _grant(c, slug, grant)


def ensure(c: sqlite3.Connection) -> list[str]:
    """Once Finance is set up: the pending grants of bots not archived, each once. The bots granted now."""
    if not is_set_up(c):
        return []
    made: list[str] = []
    rows = c.execute("SELECT target, detail_json FROM events WHERE action=? ORDER BY ts, id", (PENDING,)).fetchall()
    for row in rows:
        slug = str(row["target"])
        bot: dict[str, Any] | None = _H.bot(c, slug)
        if bot is None or bot["state"] == "archived":
            continue
        if _grant(c, slug, json.loads(row["detail_json"] or "{}")):
            made.append(slug)
    return made


def _grant(c: sqlite3.Connection, slug: str, grant: Mapping[str, Any]) -> dict[str, Any]:
    """The grant, once: the rights the bot does not hold yet and the view, then one event that stops a second grant."""
    if c.execute("SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1", (GRANTED, slug)).fetchone():
        return {}
    actor, at = "bot:" + slug, tico.now()
    names = cast(list[object], grant.get("rights") or [])
    added = grant_defaults(c, actor, [str(r) for r in names], at=at)
    view = str(grant.get("view") or "")
    if view:
        made = VW.Grant(id=SL.new_id(), subject_kind="teammate", subject=actor, what=view, whose="company",
                        granted_by=SYSTEM)
        SA.insert_grant(c, made, SYSTEM, at)
        SA.bump_view(c, f"hire {slug}", at)
    tico.audit(c, KEEPER, GRANTED, slug, {"rights": added, "view": view})
    return {"rights": added, "view": view}
