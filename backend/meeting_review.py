"""A person's quiet queue of imported meetings, before sharing with the Team."""
from typing import Literal

from fastapi import Request
from pydantic import Field, StrictBool

from . import media, meetings, models as M
from .store import H, Problem, encode
from .views import human_only


class Review(M.Contract):
    action: Literal['approve', 'dismiss', 'restore']
    private: StrictBool | None = None
    # A bot to hand the meeting to once it is shared, as Send does ("auto" routes it).
    send_to: str | None = Field(default=None, min_length=1, max_length=200)


class ReviewMany(M.Contract):
    action: Literal['approve_all', 'dismiss_all']
    ids: list[str] | None = None


class ReviewSettings(M.Contract):
    auto_share: StrictBool | None = None
    review_default: Literal['review', 'auto'] | None = None


def install_meeting_review(app, store, auth, mutate):
    def validate(c, who, rid, action):
        human_only(who)
        record = media.authorized(c, who, rid)
        if not meetings.filed_for(record, who):
            raise Problem('forbidden', 'Only the person this meeting is filed for may review it', 403)
        meta = record['metadata']
        target = {'approve': 'live', 'dismiss': 'dismissed', 'restore': 'pending'}[action]
        if meta['review_state'] == target:
            return record
        if action == 'approve' and meta['review_state'] != 'pending':
            raise Problem('review', 'Restore this meeting to Pending before sharing it', 409)
        if action == 'restore' and meta['review_state'] != 'dismissed':
            raise Problem('review', 'Only a dismissed meeting can be restored', 409)
        if action == 'dismiss' and meta['review_state'] != 'pending':
            raise Problem('review', 'Only a pending meeting can be dismissed', 409)
        return record

    def apply(c, who, rid, action, private=None, send_to=None):
        record = validate(c, who, rid, action)
        meta = record['metadata']
        if action == 'approve' and send_to and meta['review_state'] == 'pending':
            meta['review_send_to'] = send_to
        target = {'approve': 'live', 'dismiss': 'dismissed', 'restore': 'pending'}[action]
        if meta['review_state'] == target:
            return media.view(c, who, rid)
        meta['review_state'] = target
        if action == 'approve':
            meta['reviewed_at'] = H.now()
        if action == 'approve' and private is not None:
            meta['private'] = private
        media.save(c, meta, readable=record['transcript_readable'])
        c.execute('UPDATE media_control SET version=version+1 WHERE meeting_id=?', (rid,))
        if action == 'approve':
            meetings.announce(c, auth, rid, meta)
            destination = meta.pop('review_send_to', None)
            if destination:
                media.save(c, meta, readable=record['transcript_readable'])
                media.deliver(c, auth, who, rid, media.Send(slug=destination), [])
        H.event(c, who.actor, 'meeting.reviewed', rid, {'action': action})
        return media.view(c, who, rid)

    @app.get('/api/v2/meetings/settings')
    def settings(request: Request):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            return meetings.review_settings(c, who)

    @app.post('/api/v2/meetings/settings')
    def set_settings(request: Request, body: ReviewSettings):
        who = request.state.identity
        human_only(who)
        if 'review_default' in body.model_fields_set and who.role != 'owner':
            raise Problem('forbidden', 'Only the Team owner may set the meeting review default', 403)
        def work(c):
            if 'auto_share' in body.model_fields_set:
                if body.auto_share is None:
                    c.execute("DELETE FROM preferences WHERE actor=? AND key='meetings.auto_share'", (who.actor,))
                else:
                    c.execute('INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) '
                              'ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                              (who.actor, 'meetings.auto_share', encode(body.auto_share), H.now()))
            if body.review_default is not None:
                c.execute('INSERT INTO registry_metadata(key,value) VALUES(?,?) '
                          'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                          ('meetings.review_default', encode(body.review_default)))
            return meetings.review_settings(c, who)
        return mutate(request, body, work)

    @app.get('/api/v2/meetings')
    def listing(request: Request, review: Literal['pending', 'live', 'dismissed'] = 'live', q: str = ''):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            rows = []
            for row in c.execute('SELECT id FROM meetings WHERE review_state=? '
                                 'AND (?=\'live\' OR owner=? COLLATE NOCASE) ORDER BY created_at DESC',
                                 (review, review, who.email)):
                try:
                    item = media.view(c, who, row[0])
                except Problem as exc:
                    if exc.status not in (403, 404):
                        raise
                    continue
                if not q or q.casefold() in encode(item).casefold():
                    rows.append(item)
            return {'meetings': rows, 'count': len(rows), 'pending_count': meetings.pending_count(c, who)}

    @app.post('/api/v2/meetings/review')
    def review_many(request: Request, body: ReviewMany):
        who = request.state.identity
        human_only(who)
        def work(c):
            ids = list(dict.fromkeys(body.ids)) if body.ids is not None else [row[0] for row in c.execute(
                "SELECT m.id FROM meetings m LEFT JOIN media_control mc ON mc.meeting_id=m.id "
                "WHERE m.review_state='pending' AND lower(m.owner)=? AND mc.deleted_at IS NULL ORDER BY m.created_at,m.id",
                (who.email.lower(),))]
            # Validate the whole selection before publishing any of it.
            action = 'approve' if body.action == 'approve_all' else 'dismiss'
            for rid in ids:
                validate(c, who, rid, action)
            rows = [apply(c, who, rid, action) for rid in ids]
            return {'meetings': rows, 'count': len(rows), 'pending_count': meetings.pending_count(c, who)}
        return mutate(request, body, work)

    @app.post('/api/v2/meetings/{id}/review')
    def review_one(request: Request, id: str, body: Review):
        return mutate(request, body, lambda c: apply(c, request.state.identity, id, body.action, body.private, body.send_to))

    @app.get('/api/v2/meetings/{id}')
    def detail(request: Request, id: str):
        with store.read() as c:
            return media.view(c, request.state.identity, id, include_transcripts=True)
