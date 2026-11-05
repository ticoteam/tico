"""A meeting can only send its words to people who already have them."""
from . import live_privacy, media, read_cache, task_rights
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
                continue
    if record is not None:
        allowed.update('bot:' + row[0] for row in c.execute(
            "SELECT b.bot FROM live_meeting_bots b JOIN live_meetings m ON m.id=b.meeting_id "
            "WHERE m.imported_meeting_id=? AND b.joined_by=m.owner_actor", (record['id'],)))
    return allowed


def check(c, auth, who, owner, *, record=None, live_id=None, roles=None, guest=None, requester=None):
    live = None
    if live_id:
        live = c.execute("SELECT * FROM live_meetings WHERE id=?", (live_id,)).fetchone()
        if not live or not live_privacy.member(c, who.actor, live):
            raise Problem('not_found', 'Meeting not found', 404)
    private = bool(record and record['metadata'].get('private')) or bool(live and live['visibility'] != 'team')
    if not private:
        return None
    allowed = audience(c, auth, record=record, live=live)
    requester = requester or who.actor
    readers = {owner, requester, *[H.resolve_actor(c, a) or a for role, values in (roles or {}).items()
                                  if role.strip().lower() == 'participant' for a in values]}
    if guest:
        readers.add(guest)
    parties = {owner, requester}
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
    """Record sent provenance in the same transaction when the links migration is installed."""
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meeting_task_links'").fetchone():
        c.execute("INSERT OR IGNORE INTO meeting_task_links(id,meeting_id,task_id,kind,created_by,created_at) "
                  "VALUES(?,?,?,'sent',?,?)", (H.new_id(), meeting_id, task_id, author, H.now()))


def saved_read_sql(actor_sql, *, human=None):
    # CASE keeps nesting shallow enough for SQLite inside the queue's permission predicate.
    reader = human or 'h'
    allowed = ("CASE "
               "WHEN EXISTS (SELECT 1 FROM media_control mc WHERE mc.meeting_id=m.id AND mc.deleted_at IS NOT NULL) THEN 0 "
               f"WHEN lower({reader}.email)=lower(m.owner) THEN 1 "
               "WHEN m.review_state<>'live' THEN 0 "
               "WHEN coalesce(json_extract(m.metadata,'$.audience'),'')<>'participants' AND "
               f"lower({reader}.email)=(SELECT lower(json_extract(value,'$.email')) FROM registry_metadata WHERE key='owner') THEN 1 "
               "WHEN coalesce(json_extract(m.metadata,'$.kind'),'')<>'meeting' THEN 0 "
               "WHEN json_extract(m.metadata,'$.audience')='participants' THEN "
               f"EXISTS (SELECT 1 FROM json_each(m.metadata,'$.audience_emails') a WHERE lower(trim(a.value))=lower({reader}.email)) "
               "WHEN NOT coalesce(json_extract(m.metadata,'$.private'),0) THEN 1 "
               f"WHEN EXISTS (SELECT 1 FROM json_each(m.metadata,'$.calendar.attendees') a WHERE lower(trim(a.value))=lower({reader}.email)) THEN 1 "
               f"ELSE EXISTS (SELECT 1 FROM json_each(m.metadata,'$.participants') a WHERE lower(trim(json_extract(a.value,'$.email')))=lower({reader}.email)) END")
    if human:
        return allowed
    return (f"EXISTS (SELECT 1 FROM humans h WHERE h.id=substr({actor_sql},7) AND {actor_sql} LIKE 'human:%' "
            f"AND h.email<>'' AND {allowed})")


def live_read_sql(actor_sql, table='m'):
    # Recorder grants come from ordinary live rooms, never thread-backed calls.
    return (f"{table}.discarded_at IS NULL AND {table}.thread_id IS NULL AND "
            f"({table}.visibility='team' OR {table}.owner_actor={actor_sql} OR "
            f"EXISTS (SELECT 1 FROM live_meeting_access a WHERE a.meeting_id={table}.id AND a.actor={actor_sql}))")


def participant_sql(c, actor_sql, table='tasks'):
    saved = saved_read_sql('r.actor', human='h')
    sent = (f"WHEN EXISTS (SELECT 1 FROM meeting_items i JOIN meetings m ON m.id=i.meeting_id "
            f"WHERE i.result_ref={table}.id AND i.status='pushed' AND {saved}) THEN 1 ")
    if read_cache.one(c, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meeting_task_links'"):
        sent += (f"WHEN EXISTS (SELECT 1 FROM meeting_task_links l JOIN meetings m ON m.id=l.meeting_id "
                 f"WHERE l.task_id={table}.id AND l.kind='sent' AND {saved}) THEN 1 ")
    sent += ("WHEN EXISTS (SELECT 1 FROM live_meetings lm LEFT JOIN meetings m ON m.id=lm.imported_meeting_id "
             f"WHERE lm.id={table}.live_meeting_id AND {live_read_sql('r.actor', 'lm')} "
             f"AND CASE WHEN lm.imported_meeting_id IS NULL THEN 1 ELSE {saved} END) THEN 1 ELSE 0 END")
    return ("EXISTS (SELECT 1 FROM task_roles r JOIN humans h ON h.id=substr(r.actor,7) "
            f"WHERE r.task_id={table}.id AND r.role='participant' AND r.actor={actor_sql} "
            f"AND r.actor LIKE 'human:%' AND h.email<>'' AND CASE {sent})")


def revoke_saved(c, rid):
    sources = "SELECT result_ref FROM meeting_items WHERE meeting_id=? AND status='pushed'"
    sources += " UNION SELECT t.id FROM tasks t JOIN live_meetings lm ON lm.id=t.live_meeting_id WHERE lm.imported_meeting_id=?"
    args = [rid, rid]
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='meeting_task_links'").fetchone():
        sources += " UNION SELECT task_id FROM meeting_task_links WHERE meeting_id=? AND kind='sent'"
        args.append(rid)
    c.execute("DELETE FROM task_roles AS r WHERE role='participant' AND task_id IN (" + sources + ") "
              "AND NOT EXISTS (SELECT 1 FROM meetings m WHERE m.id=? AND " + saved_read_sql('r.actor') + ")", (*args, rid))


def revoke_live(c, rid):
    c.execute("DELETE FROM task_roles AS r WHERE role='participant' AND task_id IN "
              "(SELECT id FROM tasks WHERE live_meeting_id=?) AND NOT EXISTS "
              "(SELECT 1 FROM live_meetings m WHERE m.id=? AND " + live_read_sql('r.actor') + ")", (rid, rid))


def participant_access_sql(c, actor_sql):
    # Look up this candidate by id so queue reads keep their covering index and never build a task list.
    return ("EXISTS (SELECT 1 FROM tasks participant_task WHERE participant_task.id=tasks.id AND ("
            + participant_sql(c, actor_sql, table="participant_task") + "))")


def participant_query(c, actor_sql):
    # Batch the participant-only fallback used by message and task detail reads.
    return ("SELECT tasks.id FROM task_roles audience_role JOIN tasks ON tasks.id=audience_role.task_id "
            f"WHERE audience_role.actor={actor_sql} AND audience_role.role='participant' AND ("
            + participant_sql(c, actor_sql) + ")")


def participant_tasks(c, actor):
    # One indexed actor lookup per snapshot, not one query for every task/message in a list. Writes clear it.
    def build():
        return frozenset(row[0] for row in c.execute(participant_query(c, '?'), (actor, actor)))
    return read_cache.value(c, ('meeting_participant_tasks', actor), build, shared=True)
