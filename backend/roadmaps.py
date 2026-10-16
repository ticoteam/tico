"""Publication and requesters extend tasks; comments remain messages in their task thread."""

import hashlib
import json
import re
import secrets

from fastapi import Request
from pydantic import Field

from . import hubdb as H
from .models import Contract
from .store import Problem

# Anonymous suggestions waiting at Suggested, across every visitor: the per-address limit alone lets many
# addresses queue unbounded work for the Product Manager (the same overall cap as waiting pairings).
SUGGESTIONS_WAITING_MAX = 20
VISIBILITY = "TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('public','private'))"


def migrate(c):
    had_public_text = 'public_title' in {r[1] for r in c.execute('PRAGMA table_info(tasks)')}
    H.add_column(c, 'tasks', 'visibility', VISIBILITY)
    H.add_column(c, 'tasks', 'public_title', "TEXT NOT NULL DEFAULT ''")
    H.add_column(c, 'tasks', 'public_summary', "TEXT NOT NULL DEFAULT ''")
    H.add_column(c, 'tasks', 'size', 'REAL')
    steps_fresh = 'public_name' not in {r[1] for r in c.execute('PRAGMA table_info(task_steps)')}
    H.add_column(c, 'task_steps', 'public_name', "TEXT NOT NULL DEFAULT ''")
    if steps_fresh:
        c.execute('UPDATE task_steps SET public_name=name')
    H.add_column(c, 'task_types', 'default_visibility', "TEXT NOT NULL DEFAULT 'private'")
    H.add_column(c, 'messages', 'public', 'INTEGER NOT NULL DEFAULT 0')
    H.add_column(c, 'messages', 'hidden_at', 'TEXT')
    c.execute("CREATE INDEX IF NOT EXISTS messages_public_task ON messages(conversation_id,json_extract(refs_json,'$.task'),created,id) "
              "WHERE public=1 AND hidden_at IS NULL AND deleted_at IS NULL AND json_extract(refs_json,'$.comment')=1")
    # Once per database: an install that ran the first version of roadmaps had its Product roadmap type public by
    # default and every task's internal title and body copied into its public text. The type becomes private.
    # That version had public text columns but no marker, and its editor showed public text only on public tasks,
    # so any public text on a private task is such a copy, even when the title or body changed since. A public task
    # whose public text still equals its internal title and body was published by the copy, not by a person, so it
    # goes private with no public text. Publishing any of them later needs public text written for it.
    c.execute('CREATE TABLE IF NOT EXISTS registry_metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL)')
    if c.execute("INSERT OR IGNORE INTO registry_metadata(key,value_json) VALUES('roadmaps_explicit_public_text','true')").rowcount:
        c.execute("UPDATE task_types SET default_visibility='private' WHERE manager='product-manager'")
        if had_public_text:
            c.execute("UPDATE tasks SET public_title='',public_summary='' WHERE visibility='private' "
                      "AND (public_title<>'' OR public_summary<>'')")
            c.execute("UPDATE tasks SET visibility='private',public_title='',public_summary='' WHERE visibility='public' "
                      "AND public_title=substr(coalesce(title,''),1,300) AND public_summary=substr(coalesce(body,''),1,8000)")
    c.execute('CREATE TABLE IF NOT EXISTS task_requesters('
              'id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),actor TEXT,'
              "name TEXT NOT NULL DEFAULT '',email TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',"
              "other TEXT NOT NULL DEFAULT '',organization TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',"
              "source TEXT NOT NULL,important INTEGER NOT NULL DEFAULT 0,token_hash TEXT NOT NULL UNIQUE,created TEXT NOT NULL)")
    c.execute('CREATE INDEX IF NOT EXISTS task_requesters_task ON task_requesters(task_id)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS task_requesters_actor ON task_requesters(task_id,actor) '
              'WHERE actor IS NOT NULL')


def settings(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key='roadmaps'").fetchone() if H._has_table(c, 'registry_metadata') else None
    return {'labels': [], 'cors_origins': [], 'action_limit': 10, 'suggestion_limit': SUGGESTIONS_WAITING_MAX,
            'bot_public_approval': True,
            **(json.loads(row[0]) if row else {})}


def publication_payload(task_id, operation, values):
    text = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return {'url': f'tico:roadmaps/{task_id}/{operation}', 'text': text,
            'content_sha256': hashlib.sha256(text.encode()).hexdigest()}


def approve(c, actor, task_id, operation, values, approval_id=None):
    if not H.is_bot(actor) and not H.VIA.get():
        return
    if not settings(c)['bot_public_approval']:
        return
    expected = publication_payload(task_id, operation, values)
    row = H.approval(c, approval_id) if approval_id else None
    if not row or row['kind'] != 'publish' or row['requested_by'] != actor or row['task_id'] != task_id \
            or row['payload'] != expected or row['decision'] != 'approved' or row['consumed_at']:
        H.refuse(c, actor, 'approval', 'Public changes need an exact approved publish payload; '
                 'use POST /api/v2/tasks/{id}/publication-preview')
    H.approval_consume(c, actor, approval_id)


def publication(c, actor, row, visibility=None, public_title=None, public_summary=None, approval_id=None):
    fields = {k: v for k, v in (('visibility', visibility), ('public_title', public_title),
                                ('public_summary', public_summary)) if v is not None}
    if not fields:
        return
    if visibility is not None and visibility not in ('public', 'private'):
        H.refuse(c, actor, 'kind', 'visibility is public or private')
    if public_title is not None and (not public_title.strip() or len(public_title) > 300):
        H.refuse(c, actor, 'lint', 'Public title is 1 to 300 characters')
    if public_summary is not None and len(public_summary) > 8000:
        H.refuse(c, actor, 'lint', 'Public summary is at most 8000 characters')
    if fields.get('visibility', row['visibility']) == 'public' and H.task_private(c, row):
        H.refuse(c, actor, 'private', 'A two-party private task cannot be published')
    if fields.get('visibility', row['visibility']) == 'public' and not fields.get('public_title', row['public_title']).strip():
        H.refuse(c, actor, 'lint', 'Write a public title before publishing; the internal title and body stay private')
    changed = {k: v for k, v in fields.items() if v != row[k]}
    if changed and (any(k.startswith('public_') for k in changed) or changed.get('visibility') == 'public'):
        approve(c, actor, row['id'], 'text', changed, approval_id)
    for key, value in changed.items():
        c.execute(f'UPDATE tasks SET {key}=?,updated=? WHERE id=?', (value, H.now(), row['id']))
        H._task_event(c, row['id'], actor, key, row[key], value, '')


def spam_check(text, what='Comment', limit=4000, required=True):
    if required and not text.strip() or len(text) > limit:
        raise Problem(what.lower(), f'{what} is 1 to {limit} characters', 422)
    if len(re.findall(r'https?://|www\.', text, re.I)) > 2 or re.search(r'<\s*(?:a|script|iframe)\b', text, re.I):
        raise Problem('spam', 'Too many links or embedded HTML', 422)


def check_public_comment(c, actor, row, text, approval_id=None):
    if row['visibility'] != 'public' or H.task_private(c, row):
        H.refuse(c, actor, 'private', 'Public comments require a public task')
    spam_check(text)
    approve(c, actor, row['id'], 'comment', {'text': text.strip()}, approval_id)


class RequesterInput(Contract):
    actor: str | None = Field(default=None, max_length=200)
    name: str = Field(default='', max_length=200)
    email: str = Field(default='', max_length=320)
    phone: str = Field(default='', max_length=100)
    other: str = Field(default='', max_length=500)
    organization: str = Field(default='', max_length=200)
    note: str = Field(default='', max_length=4000)
    source: str = Field(default='manual', pattern='^(public_form|inbox|meeting|manual)$')
    important: bool = False


def add_requester(c, task_id, values, actor=None):
    values = dict(values)
    if actor and c.execute('SELECT 1 FROM task_requesters WHERE task_id=? AND actor=?', (task_id, actor)).fetchone():
        raise Problem('duplicate', 'Already requested by this teammate', 409)
    ident, token = H.new_id(), secrets.token_urlsafe(32)
    fields = ('name', 'email', 'phone', 'other', 'organization', 'note', 'source', 'important')
    c.execute('INSERT INTO task_requesters(id,task_id,actor,' + ','.join(fields) + ',token_hash,created) '
              'VALUES(' + ','.join('?' * 13) + ')',
              (ident, task_id, actor, *(values.get(k, '') for k in fields),
               hashlib.sha256(token.encode()).hexdigest(), H.now()))
    c.execute('UPDATE tasks SET updated=? WHERE id=?', (H.now(), task_id))
    H.event(c, actor or H.KEEPER, 'task.requester.add', task_id, {'requester_id': ident})
    return {'id': ident, 'requester_token': token}


def remove_requester(c, task_id, *, token=None, ident=None, actor=None):
    if token is not None:
        removed = c.execute('DELETE FROM task_requesters WHERE task_id=? AND token_hash=?',
                            (task_id, hashlib.sha256(token.encode()).hexdigest())).rowcount
    else:
        removed = c.execute('DELETE FROM task_requesters WHERE task_id=? AND id=?', (task_id, ident)).rowcount
    if not removed:
        raise Problem('not_found', 'Requester not found', 404)
    c.execute('UPDATE tasks SET updated=? WHERE id=?', (H.now(), task_id))
    H.event(c, actor or H.KEEPER, 'task.requester.remove', task_id, {})
    return {'removed': True}


def withdraw(c, task_id, token):
    """A visitor's token removes their request whatever became of the task since: unlabelled, off every
    enabled roadmap, private, or in the trash (where its snapshot would otherwise bring them back)."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    if c.execute('SELECT 1 FROM task_requesters WHERE task_id=? AND token_hash=?', (task_id, digest)).fetchone():
        return remove_requester(c, task_id, token=token)
    trash = c.execute('SELECT rows_json FROM task_trash WHERE task_id=? AND purged_at IS NULL',
                      (task_id,)).fetchone() if H._has_table(c, 'task_trash') else None
    snapshot = json.loads(trash[0]) if trash else {}
    rows = snapshot.get('rows', {}).get('task_requesters') or []
    kept = [r for r in rows if r.get('token_hash') != digest]
    if len(kept) == len(rows):
        raise Problem('not_found', 'Requester not found', 404)
    snapshot['rows']['task_requesters'] = kept
    c.execute('UPDATE task_trash SET rows_json=? WHERE task_id=?', (json.dumps(snapshot, default=str), task_id))
    H.event(c, H.KEEPER, 'task.requester.remove', task_id, {'trashed': True})
    return {'removed': True}


def stats(c, ids):
    if not ids:
        return {}
    rows = c.execute('SELECT task_id,count(*) requester_count,sum(actor IS NULL) external_requesters,'
                     'sum(actor IS NOT NULL) internal_requesters,sum(important) important_requesters,'
                     "min(nullif(organization,'')) organization FROM task_requesters "
                     'WHERE task_id IN (SELECT value FROM json_each(?)) GROUP BY task_id', (json.dumps(ids),))
    return {r['task_id']: {k: r[k] for k in r.keys() if k != 'task_id'} for r in rows}


EMPTY_STATS = {'requester_count': 0, 'external_requesters': 0, 'internal_requesters': 0,
               'important_requesters': 0, 'organization': None}


def page(rows, limit=100, offset=0, max_bytes=262144):
    result, used = [], 256
    more = False
    for row in rows:
        if len(result) >= limit:
            more = True
            break
        size = len(json.dumps(row, ensure_ascii=False, default=str).encode()) + 1
        if used + size > max_bytes:
            more = True
            break
        used += size
        result.append(row)
    return result, offset + len(result) if more else None


class PublicationInput(Contract):
    visibility: str | None = Field(default=None, pattern='^(public|private)$')
    public_title: str | None = Field(default=None, min_length=1, max_length=300)
    public_summary: str | None = Field(default=None, max_length=8000)
    labels: list[str] | None = Field(default=None, max_length=20)
    comment: str | None = Field(default=None, min_length=1, max_length=4000)


class Moderate(Contract):
    action: str = Field(pattern='^(hide|show|delete)$')


def install_internal(app, store, auth, mutate, mover):
    def task(c, who, tid, write=False):
        auth.domain(who)
        row = auth.task(c, who, auth.resolve_task(c, who, tid))
        if write:
            H._writer(c, who.actor)
            from .task_relations import _mine
            _mine(c, who.actor, row, mover(c, who))
        return row

    @app.get('/api/v2/tasks/{tid}/requesters')
    def requesters(request: Request, tid: str, limit: int = 100, offset: int = 0):
        with store.read() as c:
            row = task(c, request.state.identity, tid)
            limit, offset = max(1, min(limit, 100)), max(0, offset)
            columns = 'id,actor,name,email,phone,other,organization,note,source,important,created'
            rows = (dict(r) for r in c.execute('SELECT ' + columns + ' FROM task_requesters WHERE task_id=? '
                                              'ORDER BY created,id LIMIT ? OFFSET ?', (row['id'], limit + 1, offset)))
            items, after = page(rows, limit, offset)
            for item in items:
                item['kind'] = 'teammate' if item['actor'] else 'external'
            own = c.execute('SELECT id FROM task_requesters WHERE task_id=? AND actor=?',
                            (row['id'], request.state.identity.actor)).fetchone()
            return {'requesters': items, 'next_offset': after, 'my_requester_id': own[0] if own else None,
                    **EMPTY_STATS, **stats(c, [row['id']]).get(row['id'], {})}

    @app.post('/api/v2/tasks/{tid}/requesters')
    def create_requester(request: Request, tid: str, body: RequesterInput):
        def work(c):
            who = request.state.identity
            row = task(c, who, tid)
            actor = None
            if body.actor:
                actor = H.resolve_actor(c, body.actor)
                if not actor or not (H.is_human(actor) or H.is_bot(actor)) or not H._actor_exists(c, actor):
                    raise Problem('identity', 'Teammate not found', 422)
            if actor != who.actor:
                task(c, who, tid, True)
            return add_requester(c, row['id'], body.model_dump(), actor)
        return mutate(request, body, work)

    @app.post('/api/v2/tasks/{tid}/requesters/{rid}/delete')
    def delete_requester(request: Request, tid: str, rid: str, body: Contract):
        def work(c):
            who = request.state.identity
            row = task(c, who, tid)
            own = c.execute('SELECT 1 FROM task_requesters WHERE task_id=? AND id=? AND actor=?',
                            (row['id'], rid, who.actor)).fetchone()
            if not own:
                task(c, who, tid, True)
            return remove_requester(c, row['id'], ident=rid, actor=who.actor)
        return mutate(request, body, work)

    @app.post('/api/v2/tasks/{tid}/requesters/{rid}')
    def update_requester(request: Request, tid: str, rid: str, body: RequesterInput):
        def work(c):
            who = request.state.identity
            row = task(c, who, tid, True)
            values = body.model_dump(exclude_unset=True, exclude={'actor'})
            if not values:
                raise Problem('requester', 'Provide at least one requester field', 422)
            changed = c.execute('UPDATE task_requesters SET ' + ','.join(k + '=?' for k in values) +
                                ' WHERE task_id=? AND id=?', (*values.values(), row['id'], rid)).rowcount
            if not changed:
                raise Problem('not_found', 'Requester not found', 404)
            c.execute('UPDATE tasks SET updated=? WHERE id=?', (H.now(), row['id']))
            H.event(c, who.actor, 'task.requester.update', row['id'], {'requester_id': rid})
            return {'updated': True}
        return mutate(request, body, work)

    @app.post('/api/v2/tasks/{tid}/publication-preview')
    def preview(request: Request, tid: str, body: PublicationInput):
        with store.read() as c:
            row = task(c, request.state.identity, tid)
            if body.labels is not None:
                if body.model_fields_set != {'labels'}:
                    raise Problem('publication', 'Preview labels separately from public text', 422)
                return {'payload': publication_payload(row['id'], 'labels', {'labels': H._labels(body.labels)})}
            if body.comment is not None:
                return {'payload': publication_payload(row['id'], 'comment', {'text': body.comment.strip()})}
            values = {k: v for k, v in body.model_dump(exclude_none=True).items() if v != row.get(k)}
            return {'payload': publication_payload(row['id'], 'text', values)}

    @app.post('/api/v2/tasks/{tid}/comments/{mid}/moderate')
    def moderate(request: Request, tid: str, mid: str, body: Moderate):
        def work(c):
            who = request.state.identity
            row = task(c, who, tid, True)
            msg = H.comment_on(c, row, mid)
            if not msg or not msg.get('public') or msg.get('deleted_at'):
                raise Problem('not_found', 'Public comment not found', 404)
            if body.action == 'delete':
                c.execute("UPDATE messages SET body='',deleted_at=?,hidden_at=? WHERE id=?", (H.now(), H.now(), mid))
                H._withdraw_queued_comment_copies(c, mid)
                H._refresh_comment_replies(c, H.message(c, mid, include_deleted=True))
            else:
                c.execute('UPDATE messages SET hidden_at=? WHERE id=?', (H.now() if body.action == 'hide' else None, mid))
            c.execute('UPDATE tasks SET updated=? WHERE id=?', (H.now(), row['id']))
            H.event(c, who.actor, 'task.comment.moderate', row['id'], {'comment': mid, 'action': body.action})
            return {'moderated': True}
        return mutate(request, body, work)

    @app.get('/api/v2/roadmap-tasks')
    def ranked(request: Request, sort: str = 'requester_count', direction: str = 'desc', limit: int = 100,
               offset: int = 0):
        sorts = {'requester_count': 'requester_count', 'external_requesters': 'external_requesters',
                 'important': 'important_requesters', 'organization': 'organization', 'size': 't.size'}
        if sort not in sorts or direction not in ('asc', 'desc'):
            raise Problem('sort', 'Unknown roadmap sort', 422)
        with store.read() as c:
            who = request.state.identity
            auth.domain(who)
            visible = auth.task_sql(c, who)
            limit, offset = max(1, min(limit, 100)), max(0, offset)
            rows = c.execute(
                'SELECT t.id,t.title,t.visibility,t.size,t.version,t.status,'
                's.name step,coalesce(r.requester_count,0) requester_count,'
                'coalesce(r.external_requesters,0) external_requesters,coalesce(r.internal_requesters,0) internal_requesters,'
                'coalesce(r.important_requesters,0) important_requesters,r.organization '
                'FROM tasks t LEFT JOIN task_steps s ON s.id=t.step_id LEFT JOIN '
                '(SELECT task_id,count(*) requester_count,sum(actor IS NULL) external_requesters,'
                'sum(actor IS NOT NULL) internal_requesters,sum(important) important_requesters,'
                "min(nullif(organization,'')) organization FROM task_requesters GROUP BY task_id) r ON r.task_id=t.id "
                "WHERE t.type_id IN (SELECT id FROM task_types WHERE manager='product-manager') AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=t.id AND (" + visible + ')) '
                'ORDER BY ' + sorts[sort] + ' ' + direction + ',t.id LIMIT ? OFFSET ?', (limit + 1, offset))
            items, after = page((dict(r) for r in rows), limit, offset)
            return {'tasks': items, 'next_offset': after}
