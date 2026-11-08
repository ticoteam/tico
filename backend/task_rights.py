"""Task-local inheritance from the current mixed org chart. Never impersonate a report.

Only task parties lend their rights; bot access, approvals and credentials keep their
own checks. Inputs and results last no longer than the connection's read snapshot.
"""
from . import people as P, read_cache


def subjects(c, actor):
    """The caller and everyone below them, using the same parents as people.manages."""
    from . import hubdb as H, views

    def build():
        # The table check is inside the cached build: a SQL request asks once per private task, and each
        # ask was a sqlite_master read (184 per member request on a 6k-task test hub).
        if not (H.is_bot(actor) or H.is_human(actor)) or not H._has_table(c, "bot_config"):
            return {actor}
        # Read only below (people.org_parent never changes them), so the snapshot's own copies are shared, not copied.
        roster, entries = views.roster(c, shared=True), views.entries(c, shared=True)
        archived = {r['id'] for r in H.bots(c) if r.get('state') == 'archived'}
        nodes = {**{'b:' + slug: ('bot', slug) for slug in entries if slug not in archived},
                 **{'p:' + p['id']: ('person', p['id']) for p in roster.get('people', [])}}
        me = ('b:' if H.is_bot(actor) else 'p:') + H.actor_id(actor)
        out = {actor}
        if me not in nodes:
            return out
        bare = True                          # a bot's bare person id in reports_to is that person (people.py)
        parents = {node: P.org_parent(kind, ident, roster, entries, archived, bare)
                   for node, (kind, ident) in nodes.items()}
        parents.update({'g:' + gid: 'p:' + group['reports_to'] if group.get('reports_to') else ''
                        for gid, group in (roster.get('org_groups') or {}).items()})
        for node, (kind, ident) in nodes.items():
            at, seen = parents[node], {node}
            while at and at not in seen:
                seen.add(at)
                at = parents.get(at, '')
            # Malformed cycles cannot confer authority on their members.
            if not at and me in seen and node != me:
                out.add(('bot:' if kind == 'bot' else 'human:') + ident)
        return out

    # Read only: represents, private_subjects and task_sql take membership and sort copies. A copy per call cost more
    # than the answer (one call per task a visibility check reads).
    return read_cache.value(c, ('task_rights_subjects', actor), build, shared=True)


def represents(c, actor, party):
    return bool(party) and (actor == party or party in subjects(c, actor))


def participant(c, actor, row):
    return any(represents(c, actor, row[k]) for k in ('owner_id', 'requester_id'))


def mover(c, actor, row, current=None):
    """A report's mover rights apply only to a task on which that report is a party."""
    from . import hubdb as H
    if current or actor == H.KEEPER or current is None and H.can_move(c, actor):
        return True
    return any(H.is_human(row[k]) and represents(c, actor, row[k]) and H.can_move(c, row[k])
               for k in ('owner_id', 'requester_id'))


def private_subjects(c, actor):
    """Private inheritance currently follows bot reports; person-only tasks keep their parties."""
    return {actor, *(subject for subject in subjects(c, actor) if subject.startswith('bot:'))}
