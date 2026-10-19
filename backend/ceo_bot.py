"""The CEO Bot rollout switch: one per install, off by default (docs/goals-and-kpis.md; spec: Head of Product, CEO Bot).

The CEO Bot is the Goal Manager under another name and in another place on the org chart. Its slug stays
`goal-manager` (actor `bot:goal-manager`), so its KPI readings, goal events and old messages stay attached.

Off, nothing changes: the bot is the Goal Manager, built in and off the chart. On:
- its display name is "CEO Bot" (`bots.display_name`); the name it had before is kept here and given back when the
  switch goes off, unless someone renamed it since;
- the org chart (`chart_entries`) shows it under the owner, and under it the bots that would hang straight under the
  owner and are a Head or have no manager, and (`chart_groups`) the top-level groups those Heads are in. Built-in
  bots, helpers and message bots stay where they are.

The chart is the only thing that moves: `bot_config.reports_to` is never written, so who manages whom (the access
chain in backend/auth.py, goal rights in backend/goals.py, `bot_contact`) is exactly what it was. Turning the switch off
gives the chart back as it was. The switch is one `registry_metadata` row, flipped at runtime by an owner or admin
(`POST /api/v2/ceo-bot`); a bot never flips it.
"""
import json

from . import hubdb as H
from . import people as P


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

KEY = "ceo-bot"
SLUG = "goal-manager"
NAME = "CEO Bot"
OLD_NAME = "Goal Manager"


def _record(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def on(c):
    """Whether this install has the CEO Bot switch on."""
    return _record(c).get("on") is True


def name(c):
    """The name the built-in Goal Manager is given when it is (re)created: "CEO Bot" while the switch is on."""
    return NAME if on(c) else OLD_NAME


def view(c):
    record = _record(c)
    return {"on": record.get("on") is True, "name": NAME if record.get("on") is True else OLD_NAME,
            "changed_by": record.get("by") or "", "changed_at": record.get("at") or ""}


def _rename(c, actor, display_name):
    """The name in both places `SettingsAdmin.update_bot` keeps it, with a new revision so a form opened before
    cannot save the old name back over it."""
    c.execute("UPDATE bots SET display_name=? WHERE slug=?", (display_name, SLUG))
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (SLUG,)).fetchone()
    if not row:
        return
    try:
        declared = json.loads(row["config_json"] or "{}")
    except ValueError:
        declared = None
    if isinstance(declared, dict):
        declared["display_name"] = display_name
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (encode(declared), SLUG))
    c.execute("UPDATE bot_config SET revision=revision+1,definition_updated=?,definition_updated_by=? WHERE bot=?",
              (H.now(), actor, SLUG))


def set_on(c, actor, value):
    """Turn the switch on or off for this install. Idempotent: the same value again changes nothing."""
    record = _record(c)
    value = bool(value)
    if (record.get("on") is True) == value:
        return view(c)
    bot = H.bot(c, SLUG)
    current = (bot or {}).get("display_name") or ""
    if value:
        record["name_before"] = current if current and current != NAME else OLD_NAME
        if bot:
            _rename(c, actor, NAME)
    else:
        # A name a person gave it while the switch was on is theirs: only "CEO Bot" goes back.
        if bot and current == NAME:
            _rename(c, actor, record.get("name_before") or OLD_NAME)
    record.update(on=value, by=actor, at=H.now())
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, encode(record)))
    H.event(c, actor, "ceo_bot.switched", SLUG, {"on": value})
    return view(c)


def _outside(slug, entry, assistant, helpers):
    return slug in ("assistant", "botops", "librarian", SLUG, "health-monitor") or slug == assistant \
        or str((entry or {}).get("template") or "") in helpers


def chart_entries(c, settings, roster, entries, archived=()):
    """`entries` as the org chart draws them. With the switch on, the CEO Bot reports to the owner, and so do on the
    chart the bots that hang straight under the owner and are a Head (a `lead: true` card) or have no manager at all.
    Built-in bots, helpers and message bots stay where they are, and so does anyone under a group or another person.
    A copy; with the switch off, `entries` itself. Only for drawing the chart: permission checks read the stored
    `reports_to`."""
    archived = set(archived or ())
    if not on(c) or SLUG not in (entries or {}) or SLUG in archived:
        return entries
    owner = P.person((roster or {}).get("default_user"), roster)
    if not owner or owner.get("hidden"):
        return entries
    from . import onboarding as O
    helpers, leads = O.helper_templates(settings), O.lead_templates(settings)
    inboxes = {p.get("inbox_bot") for p in (roster or {}).get("people") or [] if p.get("inbox_bot")}
    top = "p:" + owner["id"]
    out = dict(entries)
    for slug, entry in entries.items():
        if slug in archived or slug in inboxes or _outside(slug, entry, settings.assistant_bot, helpers):
            continue
        entry = entry or {}
        if (P._clean(entry.get("reports_to")) and str(entry.get("template") or "") not in leads) \
                or P.org_parent("bot", slug, roster, entries, archived) != top:
            continue
        out[slug] = {**entry, "reports_to": SLUG}
    out[SLUG] = {**(entries[SLUG] or {}), "reports_to": "human:" + owner["id"]}
    return out


def chart_groups(roster, entries, chart, groups, archived=()):
    """`groups` (`org_view` rows) as the chart draws them. A Head is usually in a group of its own department, which
    sits at the top of the chart: with the switch on, a top-level group that holds a bot the CEO Bot heads on the
    chart (`chart`, from `chart_entries`) hangs under the CEO Bot, so the department sits in its subtree. Never the
    group the owner is in, since the CEO Bot hangs under the owner. With the switch off, `groups` itself."""
    if chart is entries:
        return groups
    archived = set(archived or ())
    owner = P.person((roster or {}).get("default_user"), roster) or {}
    mine = set(P.group_chain(owner.get("team"), roster))
    led = set()
    for slug, entry in chart.items():
        if slug != SLUG and slug not in archived and P._clean((entry or {}).get("reports_to")) == SLUG:
            chain = P.group_chain(P.team_of(slug, entries, roster), roster)
            if chain:
                led.add(chain[-1])
    return [{**g, "org_parent": "b:" + SLUG} if g["id"] in led and not g.get("parent") and g["id"] not in mine else g
            for g in groups]
