"""A meeting can only send its words to people who already have them."""
from . import live_privacy, media, task_rights
from .store import H, Problem

REFUSAL = "Someone who would see this isn't in the meeting"


def actors(c, auth):
    for row in c.execute("SELECT id FROM humans"):
        actor = 'human:' + row['id']
        try:
            yield actor, auth.identity_for_actor(c, actor)
        except Problem:
            continue
    for row in c.execute("SELECT id FROM bots WHERE state!='archived'"):
        yield 'bot:' + row['id'], None


def audience(c, auth, *, record=None, live=None):
    allowed = set()
    for actor, identity in actors(c, auth):
        if live is not None:
            if live_privacy.member(c, actor, live):
                allowed.add(actor)
        elif identity:
            try:
                media.authorized(c, identity, record['id'])
                allowed.add(actor)
            except Problem:
                pass
    if record is not None:
        allowed.update('bot:' + row[0] for row in c.execute(
            "SELECT b.bot FROM live_meeting_bots b JOIN live_meetings m ON m.id=b.meeting_id "
            "WHERE m.imported_meeting_id=? AND b.joined_by=m.owner_actor", (record['id'],)))
    return allowed


def check(c, auth, who, owner, *, record=None, live_id=None, roles=None, guest=None):
    live = None
    if live_id:
        live = c.execute("SELECT * FROM live_meetings WHERE id=?", (live_id,)).fetchone()
        if not live or not live_privacy.member(c, who.actor, live):
            raise Problem('not_found', 'Meeting not found', 404)
    private = bool(record and record['metadata'].get('private')) or bool(live and live['visibility'] != 'team')
    if not private:
        return None
    allowed = audience(c, auth, record=record, live=live)
    readers = {owner, who.actor, *[a for role, values in (roles or {}).items()
                                  if role.strip().lower() == 'participant' for a in values]}
    if guest:
        readers.add(guest)
    parties = {owner, who.actor}
    readers.update(actor for actor, _ in actors(c, auth)
                   if task_rights.private_subjects(c, actor) & parties)
    if not readers <= allowed:
        raise Problem('meeting_audience', REFUSAL, 409)
    return sorted(a for a in allowed if H.is_human(a))


def grant(c, task_id, participants, author):
    # These rows are granted only after the complete audience has passed containment.
    for actor in participants or []:
        c.execute("INSERT OR IGNORE INTO task_roles(task_id,role,actor,added_by,created_at) "
                  "VALUES(?,'participant',?,?,?)", (task_id, actor, author, H.now()))


def record_sent_link(c, meeting_id, task_id, author):
    """Integration hook: step (a) records the sent meeting_task_links row in this transaction."""
    pass


def participant_sql(c, actor_sql, table='tasks'):
    # Before the link migration lands, the pushed item is the same durable sent provenance.
    sent = (f"EXISTS (SELECT 1 FROM meeting_items i JOIN meetings m ON m.id=i.meeting_id "
            f"WHERE i.result_ref={table}.id AND i.status='pushed' AND json_extract(m.metadata,'$.private')=1)")
    if H._has_table(c, 'meeting_task_links'):
        sent += (f" OR EXISTS (SELECT 1 FROM meeting_task_links l JOIN meetings m ON m.id=l.meeting_id "
                 f"WHERE l.task_id={table}.id AND l.kind='sent' AND json_extract(m.metadata,'$.private')=1)")
    sent += (f" OR EXISTS (SELECT 1 FROM live_meetings m WHERE m.id={table}.live_meeting_id "
             "AND m.visibility!='team')")
    return (f"EXISTS (SELECT 1 FROM task_roles r WHERE r.task_id={table}.id AND r.role='participant' "
            f"AND r.actor={actor_sql}) AND ({sent})")
