"""Finance's webhook triggers (spec §3.4): a Mercury trigger's kept calls are bank payloads, so the ordinary trigger
routes refuse them and they are read here alone, by a finance admin who also sees sources (§7.5). Re-exported by
finance/reads, the only door."""
import sqlite3

from .context import is_set_up, load
from .core import rights as R
from .core.errors import denied
from .store import tico

Row = dict[str, object]
MAX_CALLS = 100


def hook_admin(c: sqlite3.Connection, viewer: str) -> bool:
    """Whether `viewer` manages Finance's triggers (a finance admin); no one does before Finance is set up."""
    if not is_set_up(c):
        return False
    ctx = load(c)
    return R.is_admin(ctx.org, viewer, ctx.rights(viewer))


def webhook_deliveries(c: sqlite3.Connection, viewer: str, *, limit: int = 20, at: str | None = None) -> Row:
    """Calls to Finance's triggers still kept, newest first, each with its body as it came."""
    ctx = load(c, at)
    if not R.is_admin(ctx.org, viewer, ctx.rights(viewer)):
        raise denied("webhook deliveries are for finance admins")
    view = ctx.view(viewer)
    if not (view.everything or view.sources):
        raise denied("webhook deliveries need the sources view")
    rows = tico.finance_hook_calls(c, ctx.at, max(1, min(int(limit), MAX_CALLS)))
    return {"deliveries": [{
        "trigger": str(r["trigger_id"]), "delivery": str(r["delivery"]), "received_at": str(r["received_at"]),
        "type": str(r["type"] or ""), "via": str(r["via"] or ""), "outcome": str(r["outcome"] or ""),
        "body": bytes(r["body"]).decode("utf-8", "replace")} for r in rows]}
