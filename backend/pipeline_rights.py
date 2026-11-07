# pyright: strict
"""Who may see a pipeline, and who may change its rules: its stage requirements, prefix, settings and links.

A pipeline is company-wide unless it is members-only (task_types.pipeline_visibility). A members-only pipeline shows to its
owner, its manager and its members (pipeline_members). Rules are a separate right: only people change them, the pipeline's
owner and the company's owner and Admins (access.owner_and_admin_emails); an Admin who is not one of those still does not
see a members-only pipeline. Bots never edit rules, whatever the owner column says.

The owner, the manager and the members are stored as references, and a reference may name a bot by its slug or its id, or
a person as `person:<id>`. `canonical` reads each one as the actor string a reader's identity carries, so the comparison
is the same whichever form was stored.
"""

from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

from . import access as Access


def canonical(c: "sqlite3.Connection", ref: object) -> str:
    """The actor string a stored reference names: `human:<id>` for a person (`person:<id>` too), `bot:<id>` for a bot named
    by its id or its slug, and "" for nothing."""
    text = str(ref or "").strip()
    if text.startswith("person:"):
        return "human:" + text[len("person:") :].strip()
    if text.startswith("human:"):
        return text
    value = (text[len("bot:") :] if text.startswith("bot:") else text).strip()
    if not value:
        return ""
    found = c.execute("SELECT id FROM bots WHERE id=? OR slug=? LIMIT 1", (value, value)).fetchone()
    return "bot:" + str(found[0] if found else value)


def _unique(ids: Sequence[object]) -> list[str]:
    return list(dict.fromkeys(str(i) for i in ids if i))


def editable_pipeline_ids(c: "sqlite3.Connection", actor: str, ids: Sequence[object]) -> set[str]:
    """The ids, of those given, whose rules `actor` may change: a person who owns the pipeline, or is an Admin."""
    who = str(actor or "")
    wanted = _unique(ids)
    if not wanted or not who.startswith("human:"):
        return set()
    marks = ",".join("?" * len(wanted))
    rows = c.execute(f"SELECT id, pipeline_owner_id FROM task_types WHERE id IN ({marks})", wanted).fetchall()
    found = c.execute("SELECT email FROM humans WHERE id=?", (who.split(":", 1)[1],)).fetchone()
    email = str((found[0] if found else "") or "").strip().lower()
    if email and email in set(Access.owner_and_admin_emails(c)):
        return {str(r[0]) for r in rows}
    return {str(r[0]) for r in rows if r[1] and r[1] == who}


def may_edit_rules(c: "sqlite3.Connection", actor: str, pipeline_id: str | None) -> bool:
    """True when `actor` may change the rules of pipeline `pipeline_id`."""
    return bool(pipeline_id) and str(pipeline_id) in editable_pipeline_ids(c, actor, [pipeline_id])


def visible_pipeline_ids(c: "sqlite3.Connection", actor: str, ids: Sequence[object]) -> set[str]:
    """The ids, of those given, the reader may see. A pipeline that is not there is not visible."""
    wanted = _unique(ids)
    if not wanted:
        return set()
    marks = ",".join("?" * len(wanted))
    rows = c.execute(
        f"SELECT id, pipeline_visibility, pipeline_owner_id, manager FROM task_types WHERE id IN ({marks})", wanted
    ).fetchall()
    visible = {str(r[0]) for r in rows if str(r[1] or "company") != "members"}
    private = [r for r in rows if str(r[1] or "company") == "members"]
    me = str(actor or "")
    if not private or not me:
        return visible
    private_ids = [str(r[0]) for r in private]
    marks = ",".join("?" * len(private_ids))
    members: defaultdict[str, set[str]] = defaultdict(set)
    for pipeline_id, member in c.execute(
        f"SELECT pipeline_id, actor_id FROM pipeline_members WHERE pipeline_id IN ({marks})", private_ids
    ).fetchall():
        members[str(pipeline_id)].add(canonical(c, member))
    for row in private:
        pipeline_id = str(row[0])
        owner, manager = canonical(c, row[2]), canonical(c, row[3])
        if me in (owner, manager) or me in members[pipeline_id]:
            visible.add(pipeline_id)
    return visible


def may_see(c: "sqlite3.Connection", actor: str, pipeline_id: str | None) -> bool:
    """True when `actor` may see pipeline `pipeline_id` (its name, stages, rules and tickets' counts)."""
    return bool(pipeline_id) and str(pipeline_id) in visible_pipeline_ids(c, actor, [pipeline_id])


def hidden_pipeline_ids(c: "sqlite3.Connection", actor: str) -> set[str]:
    """The members-only pipelines `actor` may not see. A list leaves out whatever joins one of them."""
    private = [str(r[0]) for r in c.execute("SELECT id FROM task_types WHERE pipeline_visibility='members'").fetchall()]
    return set(private) - visible_pipeline_ids(c, actor, private)
