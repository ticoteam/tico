"""What a bot made from a catalog template comes with in Finance: the Finance rights and one company-wide view that its
card declares (`finance:` in card.yaml). settings_admin.create_bot is where every hire lands (a seat, a team build,
company import, the bot's own add), so actions.template_hire makes the grant there; actions.template_ensure makes the
grants still waiting for Finance, at start and on the scheduler's Finance tick.

Once per bot: each grant is one `bot.finance_granted` event, so a later revoke stays revoked and a restart grants
nothing. A hire before Finance is set up is recorded as `bot.finance_pending` and granted once Finance is set up. Rights
go through rights.grant_defaults, the mechanism the Bookkeeper's rights use. Only the Procurement Manager declares a
grant today: finance.ap and the expenses view."""
import json
from pathlib import Path

import yaml

from .. import hubdb as H
from .context import is_set_up
from .core import rights as R
from .core import view as VW
from .core.org import SYSTEM
from .rights import grant_defaults
from .store import access as SA
from .store import ledger as SL
from .store import tico

GRANTED = "bot.finance_granted"
PENDING = "bot.finance_pending"


def declared(catalog_dir, template: str) -> dict:
    """The Finance grant a template's card declares: {"rights": [finance.* names], "view": a view kind}, or {} when it
    declares none. finance.admin is never a template's to give, and an unknown name or view is dropped, not granted."""
    if not template:
        return {}
    try:
        card = yaml.safe_load((Path(catalog_dir) / template / "card.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    block = card.get("finance") if isinstance(card, dict) else None
    if not isinstance(block, dict):
        return {}
    names = block.get("rights") if isinstance(block.get("rights"), list) else []
    rights = list(dict.fromkeys(str(r) for r in names if r in R.RIGHTS and r != R.ADMIN))
    view = str(block.get("view") or "")
    view = view if view in VW.WHAT else ""
    return {"rights": rights, "view": view} if rights or view else {}


def hire(c, slug: str, template: str, catalog_dir) -> dict:
    """A bot just made from `template`: its grant now when Finance is set up, else recorded as pending. A template that
    declares no grant does nothing, and no hire fails because Finance is not set up yet."""
    grant = declared(catalog_dir, template)
    if not grant:
        return {}
    if not is_set_up(c):
        H.event(c, H.KEEPER, PENDING, slug, grant)
        return {}
    return _grant(c, slug, grant)


def ensure(c) -> list[str]:
    """Once Finance is set up: the pending grants of bots not archived, each once. The bots granted now."""
    if not is_set_up(c):
        return []
    made = []
    rows = c.execute("SELECT target, detail_json FROM events WHERE action=? ORDER BY ts, id", (PENDING,)).fetchall()
    for row in rows:
        bot = H.bot(c, row["target"])
        if bot is None or bot["state"] == "archived":
            continue
        if _grant(c, row["target"], json.loads(row["detail_json"] or "{}")):
            made.append(row["target"])
    return made


def _grant(c, slug: str, grant: dict) -> dict:
    """The grant, once: the rights the bot does not hold yet and the view, then one event that stops a second grant."""
    if c.execute("SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1", (GRANTED, slug)).fetchone():
        return {}
    actor, at = "bot:" + slug, tico.now()
    added = grant_defaults(c, actor, grant.get("rights") or [], at=at)
    view = grant.get("view") or ""
    if view:
        made = VW.Grant(id=SL.new_id(), subject_kind="teammate", subject=actor, what=view, whose="company",
                        granted_by=SYSTEM)
        SA.insert_grant(c, made, SYSTEM, at)
        SA.bump_view(c, f"hire {slug}", at)
    H.event(c, H.KEEPER, GRANTED, slug, {"rights": added, "view": view})
    return {"rights": added, "view": view}
