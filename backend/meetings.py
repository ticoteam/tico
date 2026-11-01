"""Durable meeting sources and delivery requests. Text stays in SQLite; binary files stay on disk/S3."""
import hashlib
import json
import re
from contextlib import closing

from clients.transcript import readable_transcript

from . import hubdb as H


def snapshot(meta, transcript='', notes='', conn=None, readable=None):
    """One meeting row, and one immutable version of it.

    `readable` is the transcript formatted for reading; without it the deterministic formatter
    (`clients/transcript.py`) writes it from `transcript`.
    """
    if conn is None:
        with closing(H.connect()) as db:
            return snapshot(meta, transcript, notes, db, readable)
    rid = meta['id']
    old = conn.execute('SELECT * FROM meetings WHERE id=?', (rid,)).fetchone()
    meta = {**meta, 'review_state': meta.get('review_state') or (old['review_state'] if old else 'live')}
    transcript = transcript or (old['transcript_original'] if old else '')
    notes = notes or (old['notes'] if old else '')
    recorded = meta.get('recorded_by') or (old['recorded_by_id'] if old else None)
    uploaded = meta.get('uploaded_by') or (old['uploaded_by_id'] if old else None)
    readable = readable if readable is not None else (readable_transcript(transcript) if transcript else '')
    metadata = json.dumps({**meta, 'recorded_by': recorded, 'uploaded_by': uploaded,
                           'readability_version': 'paragraphs-v1'}, sort_keys=True)
    digest = hashlib.sha256((metadata + transcript + notes).encode()).hexdigest()
    ts = H.now()
    conn.execute('''INSERT INTO meetings
        (id,title,owner_id,recorded_by_id,uploaded_by_id,metadata,transcript_original,
         transcript_readable,notes,content_hash,created_at,updated_at,review_state)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, owner_id=excluded.owner_id,
        recorded_by_id=excluded.recorded_by_id, uploaded_by_id=excluded.uploaded_by_id,
        metadata=excluded.metadata, transcript_original=excluded.transcript_original,
        transcript_readable=excluded.transcript_readable, notes=excluded.notes,
        content_hash=excluded.content_hash, updated_at=excluded.updated_at, review_state=excluded.review_state''',
        (rid, meta.get('title') or 'Meeting', meta.get('owner') or '', recorded, uploaded,
         metadata, transcript, readable, notes, digest, old['created_at'] if old else ts, ts, meta['review_state']))
    if transcript or notes:
        conn.execute('''INSERT OR IGNORE INTO meeting_versions
          (meeting_id,content_hash,metadata,transcript_original,transcript_readable,notes,created_at)
          VALUES (?,?,?,?,?,?,?)''', (rid,digest,metadata,transcript,readable,notes,ts))
    return rid


def get(rid, conn=None):
    if conn is None:
        with closing(H.connect()) as db:
            return get(rid, db)
    row = conn.execute('SELECT * FROM meetings WHERE id=?', (rid,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result['metadata'] = json.loads(result.pop('metadata'))
    result['metadata']['review_state'] = result['review_state']
    delivery = conn.execute('SELECT * FROM meeting_deliveries WHERE meeting_id=?', (rid,)).fetchone()
    result['delivery'] = dict(delivery) if delivery else None
    if result['delivery']:
        result['delivery']['attachments'] = json.loads(result['delivery'].pop('attachments'))
    return result


def filed_for(record, who):
    return who.role in ('owner', 'human') and bool(who.email) and record['owner_id'].lower() == who.email.lower()


def pending_count(c, who):
    if who.role not in ('owner', 'human') or not who.email:
        return 0
    return c.execute("SELECT count(*) FROM meetings m LEFT JOIN media_control mc ON mc.meeting_id=m.id "
                     "WHERE m.review_state='pending' AND m.owner_id=? COLLATE NOCASE AND mc.deleted_at IS NULL",
                     (who.email.lower(),)).fetchone()[0]


def review_settings(c, who):
    personal = c.execute("SELECT value FROM preferences WHERE actor=? AND key='meetings.auto_share'",
                         (who.actor,)).fetchone()
    team = c.execute("SELECT value FROM registry_metadata WHERE key='meetings.review_default'").fetchone()
    default = json.loads(team[0]) if team else 'review'
    choice = json.loads(personal[0]) if personal else None
    return {'auto_share': choice, 'review_default': default,
            'effective_auto_share': choice if isinstance(choice, bool) else default == 'auto'}


def initial_review(c, who):
    return 'live' if review_settings(c, who)['effective_auto_share'] else 'pending'


def announce(c, auth, rid, meta):
    """Publish a shared transcript once, in the transaction that makes it live."""
    if meta.get('review_state', 'live') != 'live' or meta.get('private') or meta.get('ready_announced'):
        return
    done = get(rid, c)
    if not done['transcript_original'] and not meta.get('turns'):
        return
    meta['ready_announced'] = True
    snapshot(meta, conn=c, readable=done['transcript_readable'])
    H.hook(c, 'meeting.ready', rid, lambda: {'id': rid, 'title': meta.get('title') or rid, 'source': meta.get('source'),
                                              'ended': meta.get('ended')}, 'keeper', revision=0)


def request_send(rid, email, sender, destination, instructions, attachments):
    with closing(H.connect()) as db:
        db.execute('''INSERT INTO meeting_deliveries
          (meeting_id,requested_by,sender_name,destination,instructions,attachments,requested_at)
          VALUES (?,?,?,?,?,?,?) ON CONFLICT(meeting_id) DO NOTHING''',
          (rid,email,sender,destination,instructions,json.dumps(attachments),H.now()))
        # Repeated clicks retry errors without changing the original attribution or payload.
        db.execute("UPDATE meeting_deliveries SET status='pending',error=NULL WHERE meeting_id=? AND status='error'", (rid,))
        return get(rid, db)['delivery']


SOURCE_SUMMARY = ('lead_name', 'direction', 'phone', 'lead_url')


def source(meta):
    """Where an imported meeting came from; nothing at all for a typed note.

    A Close call reads as one sentence the notes can use ("Close call with Dana Reyes, outbound,
    +1…, lead https://…") followed by whatever else the importer carried (`backend/imports.py`).
    """
    kind = meta.get('source')
    if not kind:
        return []
    ctx = {k: v for k, v in (meta.get('source_context') or {}).items() if v}
    if kind == 'close':
        head = ', '.join(part for part in [
            'Close call with ' + (ctx.get('lead_name') or 'an unnamed lead'),
            ctx.get('direction'), ctx.get('phone'),
            'lead ' + ctx['lead_url'] if ctx.get('lead_url') else ''] if part)
    else:
        head = kind
    rest = ', '.join(f"{k.replace('_', ' ')}: {v}" for k, v in sorted(ctx.items()) if k not in SOURCE_SUMMARY)
    return ['Source: ' + head] + ([f'Source details: {rest}'] if rest else [])


def named(participant):
    """A participant as one line: the name, and the address when there is one."""
    name, email = participant.get('name') or '', participant.get('email') or ''
    return f'{name} <{email}>' if name and email else name or email


def context(record):
    meta = record['metadata']; cal = meta.get('calendar') or {}; delivery = record.get('delivery') or {}
    attachments = delivery.get('attachments') or []
    listed = [named(p) for p in meta.get('participants') or [] if isinstance(p, dict)]
    return '## Meeting context\n\n' + '\n'.join('- ' + line for line in [ f"Meeting record: {record['id']}",
        f"Meeting: {record['title']}",
        f"Calendar event: {cal.get('event_id') or 'Not linked'}",
        f"Scheduled: {cal.get('start') or 'Unknown'} — {cal.get('end') or 'Unknown'}",
        f"Held: {meta.get('started') or 'Unknown'} — {meta.get('ended') or 'Unknown'}",
        f"Filed by: {record.get('recorded_by_id') or 'Unknown'}",
        f"Sent by: {delivery.get('requested_by') or 'Unknown'} at {delivery.get('requested_at') or 'Unknown'}",
        f"Calendar invitees (attendance unverified): {', '.join(cal.get('attendees') or []) or 'Unknown'}",
        f"Participants (as the source listed them): {', '.join(listed or meta.get('confirmed_attendees') or []) or 'Not listed'}",
        f"Notes on the record: {meta.get('note') or '(none)'}",
        f"Send instructions: {delivery.get('instructions') or '(none)'}",
        f"User attachments: {', '.join(a['name'] for a in attachments) or '(none)'}",
        *([f"Media: {meta['media_url']}"] if meta.get('media_url') else []),
        *source(meta),
        'Speaker names are as the source wrote them; do not infer identity from invitees.',
    ])


def retire_capture(c):
    """Nothing records or transcribes audio any more, so a meeting still waiting on either is a
    finished one with what it has. Idempotent: it touches only meetings in a capture state."""
    live = ('recording', 'finishing', 'transcribing', 'notes')
    marks = ','.join('?' * len(live))
    for row in c.execute(f"SELECT id,metadata,transcript_original FROM meetings "
                         f"WHERE json_extract(metadata,'$.status') IN ({marks})", live).fetchall():
        meta = json.loads(row['metadata'])
        meta.update(status='done', ended=meta.get('ended') or H.now(), error=None,
                    warning=None if row['transcript_original'] else 'Tico no longer records; this meeting has no transcript')
        c.execute('UPDATE meetings SET metadata=? WHERE id=?', (json.dumps(meta, sort_keys=True), row['id']))
    c.execute("UPDATE service_jobs SET state='cancelled',updated_at=? "
              "WHERE kind='recording.process' AND state IN ('queued','running')", (H.now(),))
