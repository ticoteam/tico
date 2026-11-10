"""The org builder's fixed departments, and which one a teammate is in.

Department tags come from the org builder's fixed catalog, not arbitrary group names. The outgoing webhooks, the
Task Manager and the routines list read them here.

The task rule that closed a done task after a day went with the old engine: a task that is Done stays Done.
"""

from . import groups, people
from .store import H


def departments(settings):
    from . import recruit, recruit_rank
    import yaml

    # Read the same fixed department source as the org builder without rebuilding its bot catalog.
    value = yaml.safe_load(recruit.departments_file(settings).read_text()) or {}
    return [
        {"id": d["id"], "name": d["name"]}
        for d in value.get("departments", [])
        if d.get("id") in recruit_rank.DEPARTMENT_IDS
    ]


def department_of(c, settings, actor):
    roster = groups._roster(c)
    if H.is_human(actor):
        person = people.person(H.actor_id(actor), roster) or {}
        gid = person.get("team") if not person.get("hidden") else None
    elif H.is_bot(actor):
        gid = people.team_of(H.actor_id(actor), groups._configs(c), roster)
    else:
        return None
    allowed = {d["id"] for d in departments(settings)}
    # A subgroup inherits its fixed department; a similarly named arbitrary group grants nothing.
    return next((g for g in people.group_chain(gid, roster) if g in allowed), None)
