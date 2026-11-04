"""Bounded, viewer-filtered links between saved meetings and tasks."""
import base64
import binascii
import json
from typing import Literal
from urllib.parse import quote

from fastapi import Query, Request
from pydantic import Field

from . import media, models as M, task_references
from .store import H, Problem
from .views import human_only

PAGE_BYTES = 64 * 1024


class Link(M.Contract):
    task_ids: list[str] = Field(min_length=1, max_length=20)
    kind: Literal['linked', 'sent'] = 'linked'


def cursor_for(scope, key):
    return base64.urlsafe_b64encode(json.dumps([scope, *key], separators=(',', ':')).encode()).decode().rstrip('=')


def after_cursor(cursor, scope):
    if cursor is None:
        return None
    try:
        if len(cursor) > 2048:
            raise ValueError()
        values = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        if not isinstance(values, list) or len(values) != 3 or values[0] != list(scope) or not all(isinstance(v, str) for v in values[1:]):
            raise ValueError()
        return values[1:]
    except (ValueError, UnicodeError, binascii.Error):
        raise Problem('cursor', 'Invalid link cursor', 422) from None


def page(rows, project, scope, limit):
    """Look ahead only through readable rows: hidden links never produce a cursor or a count."""
    items, last = [], None
    for row in rows:
        value = project(row)
        if value is None:
            continue
        candidate = [*items, value]
        key = (row['sort_at'], row['id'])
        # Reserve the actual continuation envelope, including UTF-8 and JSON escaping.
        result = {scope[0]: candidate, 'next_cursor': cursor_for(scope, key)}
        size = len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
        if len(items) == limit or size > PAGE_BYTES:
            if not items:
                raise Problem('payload', 'Link exceeds the response limit', 413)
            return {scope[0]: items, 'next_cursor': cursor_for(scope, last)}
        items, last = candidate, key
    return {scope[0]: items, 'next_cursor': None}


def meeting_tasks(c, auth, who, rid, limit=20, cursor=None):
    record = media.authorized(c, who, rid)
    media.require_live(record)
    scope = ('tasks', rid)
    after = after_cursor(cursor, scope)
    rows = c.execute('SELECT *,created_at AS sort_at FROM meeting_task_links WHERE meeting_id=? ' +
                     ('AND (created_at,id)>(?,?) ' if after else '') + 'ORDER BY created_at,id',
                     (rid, *(after or ())))

    def project(row):
        task = task_references.readable(c, auth, who, row['task_id'])
        if not task:
            return None
        return {'task_id': task['id'], 'number': task['number'], 'title': task['title'][:200],
                'status': task['status'], 'kind': row['kind'], 'created_by': row['created_by']}
    return page(rows, project, scope, limit)


def task_meetings(c, auth, who, tid, limit=20, cursor=None):
    task = auth.task(c, who, auth.resolve_task(c, who, tid))
    scope = ('meetings', task['id'])
    after = after_cursor(cursor, scope)
    rows = c.execute("SELECT l.*,coalesce(nullif(json_extract(m.metadata,'$.started'),''),m.created_at) AS sort_at "
                     'FROM meeting_task_links l JOIN meetings m ON m.id=l.meeting_id WHERE l.task_id=? ' +
                     ("AND (coalesce(nullif(json_extract(m.metadata,'$.started'),''),m.created_at),l.id)<(?,?) " if after else '') +
                     'ORDER BY sort_at DESC,l.id DESC', (task['id'], *(after or ())))

    def project(row):
        try:
            record = media.authorized(c, who, row['meeting_id'])
        except Problem as exc:
            if exc.status not in (403, 404):
                raise
            return None
        if record.get('review_state', 'live') != 'live':
            return None
        return {'meeting_id': record['id'], 'title': record['title'][:200], 'started_at': row['sort_at'],
                'url': '#/meetings?meeting=' + quote(record['id'], safe=''), 'kind': row['kind']}
    return page(rows, project, scope, limit)


def install(app, store, auth, mutate):
    @app.get('/api/v2/meetings/{rid}/tasks')
    def listing(request: Request, rid: str, limit: int = Query(20, ge=1, le=50), cursor: str | None = None):
        who = request.state.identity
        human_only(who)
        with store.read_transaction() as c:
            return meeting_tasks(c, auth, who, rid, limit, cursor)

    @app.post('/api/v2/meetings/{rid}/tasks')
    def link(request: Request, rid: str, body: Link):
        who = request.state.identity
        human_only(who)
        def check(c):
            media.require_live(media.authorized(c, who, rid))
        def work(c):
            check(c)
            tasks = [auth.task(c, who, auth.resolve_task(c, who, tid)) for tid in body.task_ids]
            if body.kind == 'sent' and any(t['requester_id'] != who.actor for t in tasks):
                raise Problem('forbidden', 'Only the requester may link a sent task', 403)
            for task in tasks:
                c.execute('INSERT OR IGNORE INTO meeting_task_links(id,meeting_id,task_id,kind,created_by,created_at) '
                          'VALUES(?,?,?,?,?,?)', (H.new_id(), rid, task['id'], body.kind, who.actor, H.now()))
            # Receipts carry no task data; every answer is projected with current access below.
            return {'ok': True}
        mutate(request, body, work, check=check)
        with store.read_transaction() as c:
            return meeting_tasks(c, auth, who, rid)

    @app.delete('/api/v2/meetings/{rid}/tasks/{tid}')
    def unlink(request: Request, rid: str, tid: str):
        who = request.state.identity
        human_only(who)
        def check(c):
            media.require_live(media.authorized(c, who, rid))
            auth.task(c, who, auth.resolve_task(c, who, tid))
        def work(c):
            record = media.authorized(c, who, rid)
            task = auth.task(c, who, auth.resolve_task(c, who, tid))
            row = c.execute('SELECT * FROM meeting_task_links WHERE meeting_id=? AND task_id=?', (rid, task['id'])).fetchone()
            if row and not (row['created_by'] == who.actor or media.may_write(who, record)
                            or who.actor in (task['owner_id'], task['requester_id'])):
                raise Problem('forbidden', 'You cannot remove this link', 403)
            c.execute('DELETE FROM meeting_task_links WHERE meeting_id=? AND task_id=?', (rid, task['id']))
            return {'ok': True}
        return mutate(request, M.Empty(), work, check=check)

    @app.get('/api/v2/tasks/{tid}/meetings')
    def meetings(request: Request, tid: str, limit: int = Query(20, ge=1, le=50), cursor: str | None = None):
        who = request.state.identity
        human_only(who)
        with store.read_transaction() as c:
            return task_meetings(c, auth, who, tid, limit, cursor)
