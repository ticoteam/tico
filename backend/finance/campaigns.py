"""Ad campaigns: each campaign an ad account reports, under its account, with the owner its spend counts for, its spend,
clicks and conversions for a month, and the cost per conversion.

A campaign's owner is a team or a teammate (a bot or a person), set by `campaign_owner_set`; with none set it is the
account's bot (`inherited`), and a campaign still with no owner counts for no one but full-detail readers. The owner
decides the platform share (platform_share.py): a platform charge counts for each owner in proportion to the spend of its
campaigns, and the budgets read the same owner (budgeting.ad_covers, via store/ads.live_spend). Changing an owner
re-judges the platform's charges whose windows hold that campaign's spend (attribution.refresh_platform), and only a
charge with no attribution or a platform share is touched.

Who sees a campaign (visible.py's rule for records): a full-detail reader; its owner (the teammate, or a member or lead of
the owning team); a manager of the owner (someone above a teammate, or a team's lead); and anyone who sees at least one
of the platform's charge lines attributed to the owner. Zero-spend campaigns follow the same rule.

Actions (need finance.attribution, never gated, audited): campaign_owner_set {campaign_id, team? | teammate?} (neither:
back to the account's owner) and campaign_owner_undo {campaign_id} (puts back the owner before the latest change).
Tico-side only: nothing here enqueues a provider write.

Reads: `rows` and `read` (GET /api/v2/finance/campaigns?month=YYYY-MM), `campaign_summary` (what a briefing says of a
reader's campaigns). The month's money is in the book's currency, as the ad spend import stores it. Conversions are the
platform's counts; a cost per conversion is null when there are none.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from . import attribution as ATTR
from . import platform_share as PS
from .context import Ctx, load
from .core import dates
from .core import kinds as K
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, invalid
from .core.org import manager_of
from .handlers import Plan, Result
from .store import ads as SAD
from .store import attribution as ATT
from .store import ledger as SL

MAX_TEXT = 80
Row = dict[str, Any]


def month_window(month: str) -> tuple[str, str]:
    """The first and last day of a YYYY-MM month (ISO days)."""
    first = date.fromisoformat(month + "-01")
    return first.isoformat(), dates.month_end(first).isoformat()


def campaign_owner(team_id: str, teammate: str, bot_slug: str) -> tuple[str | None, str | None]:
    """A campaign's effective owner as (team, teammate): its own owner when it has one, else its account's bot as
    "bot:<slug>", else none (None, None)."""
    if team_id or teammate:
        return (team_id or None, teammate or None)
    return (None, f"bot:{bot_slug}" if bot_slug else None)


def per_conversion(spend: int, conversions: float) -> int | None:
    """The spend per conversion in cents, half up (None with no conversions)."""
    if conversions <= 0:
        return None
    return int(spend / conversions + 0.5)


def _manages(ctx: Ctx, viewer: str, mate: str) -> bool:
    return bool(mate) and (mate in ctx.org.reports(viewer) or manager_of(ctx.org, mate) == viewer)


def _attributed(ctx: Ctx, viewer: str, platform: str, team: str, mate: str, first: str, last: str) -> bool:
    """Whether the viewer sees a platform charge line in the window whose platform share names this owner."""
    keys = PS.contact_keys(ctx, platform)
    if not keys:
        return False
    marks = ",".join("?" * len(keys))
    ids = [
        str(r[0])
        for r in ctx.c.execute(
            "SELECT l.id FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id JOIN fin_attributions a "
            "ON a.line_id=l.id AND a.superseded_at IS NULL WHERE l.book_id=? AND l.contact_id IN (" + marks + ") "
            "AND l.entry_date BETWEEN ? AND ? AND e.status='posted' AND e.reverses_id IS NULL AND a.source='platform'",
            [ctx.book.id, *keys, first, last],
        )
    ]
    if not ids:
        return False
    found = SL.lines(ctx.c, ids)
    in_force = ATT.in_force_rows(ctx.c, list(found))
    for line_id, (_, line) in found.items():
        row = in_force.get(line_id)
        if row is None:
            continue
        splits = ATT.parse_splits(str(row["splits"]))
        if not any((mate and s.teammate == mate) or (team and s.team == team) for s in splits):
            continue
        if ATTR.visible(ctx, viewer, line):
            return True
    return False


def sees(ctx: Ctx, viewer: str, team: str, mate: str, platform: str, first: str, last: str) -> bool:
    """The campaign rule (module docstring) for one owner: whether the viewer sees a campaign owned this way."""
    if ctx.view(viewer).everything:
        return True
    if mate and mate == viewer:
        return True
    if team and (team in ctx.org.led_by(viewer) or ctx.org.team_of.get(viewer, "") in ctx.org.below(team)):
        return True
    if _manages(ctx, viewer, mate):
        return True
    return _attributed(ctx, viewer, platform, team, mate, first, last)


def owner_json(team: str | None, mate: str | None, inherited: bool) -> Row:
    """A campaign's owner as the API sends it: its team and/or teammate, and whether it is the account's (inherited)."""
    out: Row = {"inherited": inherited}
    if team:
        out["team"] = team
    if mate:
        out["teammate"] = mate
    return out


def rows(ctx: Ctx, viewer: str, first: str, last: str) -> list[Row]:
    """The campaigns the viewer sees with their month's figures over [first, last], by spend (largest first)."""
    grouped: dict[str, Row] = {}
    for r in SAD.campaign_month(ctx.c, first, last):
        cid = str(r["campaign_id"])
        g = grouped.get(cid)
        if g is None:
            g = grouped[cid] = {
                "campaign": r,
                "spend": 0,
                "clicks": 0,
                "conversions": 0.0,
                "as_of": "",
            }
        g["spend"] += int(r["spend"] or 0)
        g["clicks"] += int(r["clicks"] or 0)
        g["conversions"] += float(r["conversions"] or 0)
        if r["fetched_at"]:
            g["as_of"] = max(str(g["as_of"]), str(r["fetched_at"]))
    out: list[Row] = []
    for g in grouped.values():
        r = g["campaign"]
        team, mate = campaign_owner(str(r["team_id"] or ""), str(r["teammate"] or ""), str(r["bot_slug"] or ""))
        if not sees(ctx, viewer, team or "", mate or "", str(r["platform"]), first, last):
            continue
        spend, conv = int(g["spend"]), round(float(g["conversions"]), 2)
        out.append(
            {
                "id": str(r["campaign_id"]),
                "name": str(r["name"]),
                "platform": str(r["platform"]),
                "ad_account": {"id": str(r["ad_account_id"]), "name": str(r["account_name"])},
                "owner": owner_json(team, mate, not (r["team_id"] or r["teammate"])),
                "month": {
                    "spend": spend,
                    "clicks": int(g["clicks"]),
                    "conversions": conv,
                    "per_conversion": per_conversion(spend, conv),
                },
                "as_of": str(g["as_of"] or ctx.at),
            }
        )
    out.sort(key=lambda x: (-int(x["month"]["spend"]), str(x["name"]), str(x["id"])))
    return out


def totals(found: Sequence[Row]) -> Row:
    spend = sum(int(x["month"]["spend"]) for x in found)
    clicks = sum(int(x["month"]["clicks"]) for x in found)
    conv = round(sum(float(x["month"]["conversions"]) for x in found), 2)
    return {"spend": spend, "clicks": clicks, "conversions": conv, "per_conversion": per_conversion(spend, conv)}


def read(c: Any, viewer: str, month: str, at: str | None = None) -> Row:
    """GET /api/v2/finance/campaigns: the viewer's campaigns for a month, with their totals."""
    ctx = load(c, at)
    first, last = month_window(month)
    found = rows(ctx, viewer, first, last)
    return {"month": month, "campaigns": found, "totals": totals(found), "as_of": ctx.at}


def ad_spend(ctx: Ctx, viewer: str, first: str, last: str) -> Row:
    """The metric ad_spend: the viewer's campaigns' spend over [first, last], with when it was worked out."""
    found = rows(ctx, viewer, first, last)
    return {"amount": totals(found)["spend"], "as_of": ctx.at}


def ad_per_conversion(ctx: Ctx, viewer: str, first: str, last: str) -> Row:
    """The metric per_conversion: the viewer's campaigns' spend per conversion over [first, last] (amount null with none)."""
    t = totals(rows(ctx, viewer, first, last))
    return {"amount": t["per_conversion"], "conversions": t["conversions"], "as_of": ctx.at}


def campaign_summary(c: Any, reader: str, month: str, at: str | None = None) -> Row:
    """What a briefing says of a reader's campaigns this month: how many are spending (`live`) and the one with the
    lowest cost per conversion (`best`: its name and cost, None when no visible campaign has a conversion)."""
    ctx = load(c, at)
    first, last = month_window(month)
    found = rows(ctx, reader, first, last)
    live = sum(1 for x in found if int(x["month"]["spend"]) > 0)
    priced = [x for x in found if x["month"]["per_conversion"] is not None]
    best = min(priced, key=lambda x: (int(x["month"]["per_conversion"]), str(x["name"])), default=None)
    return {
        "live": live,
        "best": {"name": best["name"], "per_conversion": best["month"]["per_conversion"]} if best else None,
    }


# ------------------------------------------------------------------------------------------------ actions
def _campaign(ctx: Ctx, campaign_id: str) -> SAD.Campaign:
    row = SAD.campaign(ctx.c, campaign_id)
    if row is None:
        raise FinanceError(NOT_FOUND, "no such campaign")
    return row


def _recompute(ctx: Ctx, who: str, row: SAD.Campaign) -> int:
    """Re-judge the platform's charges whose windows hold this campaign's spend, now its owner has changed."""
    span = ctx.c.execute(
        "SELECT MIN(s.day), MAX(s.day), a.platform FROM fin_ad_spends s JOIN fin_ad_accounts a ON a.id=s.ad_account_id "
        "WHERE s.ad_account_id=? AND s.campaign_external_id=?",
        (row.ad_account_id, row.external_id),
    ).fetchone()
    if span is None or span[0] is None:
        return 0
    return ATTR.refresh_platform(ctx, who, str(span[2]), str(span[0]), str(span[1]))


def _shown(ctx: Ctx, who: str, campaign_id: str) -> Result:
    row = _campaign(ctx, campaign_id)
    bot = ctx.c.execute(
        "SELECT COALESCE(bot_slug, '') FROM fin_ad_accounts WHERE id=?", (row.ad_account_id,)
    ).fetchone()
    team, mate = campaign_owner(row.team_id, row.teammate, str(bot[0]) if bot else "")
    return {
        "campaign_id": campaign_id,
        "owner": owner_json(team, mate, not (row.team_id or row.teammate)),
        "lines": _recompute(ctx, who, row),
    }


def campaign_owner_set(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Give a campaign's spend to a team or a teammate (neither: the account's owner again)."""
    V.only(p, ("campaign_id", "team", "teammate"))
    campaign_id = V.text(p, "campaign_id", 80)
    team = V.opt_text(p, "team", MAX_TEXT) or ""
    mate = V.opt_text(p, "teammate", MAX_TEXT) or ""
    if team and mate:
        raise invalid("a campaign has one owner: a team or a teammate")
    if team and team not in ctx.org.teams:
        raise invalid("no such team")
    if mate and mate not in ctx.org.humans and mate not in ctx.org.bots:
        raise invalid("no such teammate")
    row = _campaign(ctx, campaign_id)

    def apply(aid: str) -> Result:
        SAD.set_owner(ctx.c, campaign_id, team, mate, who, ctx.at)
        return _shown(ctx, who, campaign_id)

    if team or mate:
        summary = f"Give the campaign {row.name} to {team or mate}"
    else:
        summary = f"Give the campaign {row.name} back to its account's owner"
    return Plan(K.ALLOW, summary, apply)


def campaign_owner_undo(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Put back the owner a campaign had before its latest change."""
    V.only(p, ("campaign_id",))
    campaign_id = V.text(p, "campaign_id", 80)
    row = _campaign(ctx, campaign_id)
    if row.owner_set_at is None:
        raise FinanceError(CONFLICT, "nothing to undo on that campaign")

    def apply(aid: str) -> Result:
        SAD.set_owner(ctx.c, campaign_id, "", "", who, ctx.at, undo=True)
        return _shown(ctx, who, campaign_id)

    return Plan(K.ALLOW, f"Undo the last owner change on the campaign {row.name}", apply)


PLANNERS: dict[str, Any] = {
    "campaign_owner_set": campaign_owner_set,
    "campaign_owner_undo": campaign_owner_undo,
}

__all__ = [
    "PLANNERS",
    "ad_per_conversion",
    "ad_spend",
    "campaign_owner",
    "campaign_summary",
    "month_window",
    "owner_json",
    "per_conversion",
    "read",
    "rows",
    "sees",
    "totals",
]
