"""Anonymous roadmap API, scoped to this installation's company by its host."""

import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager

from fastapi import Request
from pydantic import Field
from starlette.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import cors
from . import hubdb as H
from . import roadmaps as R
from .models import Contract
from .store import Problem

PREFIX = '/public/roadmaps/'


def migrate(c):
    c.execute('CREATE TABLE IF NOT EXISTS roadmap_actions('
              'day TEXT NOT NULL,ip_hash TEXT NOT NULL,actions INTEGER NOT NULL,PRIMARY KEY(day,ip_hash))')
    c.execute("INSERT OR IGNORE INTO registry_metadata(key,value_json) VALUES('roadmap_ip_secret',?)",
              (json.dumps(secrets.token_hex(32)),))


class RoadmapSettings(Contract):
    labels: list[str] | None = Field(default=None, max_length=100)
    cors_origins: list[str] | None = Field(default=None, max_length=100)
    action_limit: int | None = Field(default=None, ge=1, le=10000)
    suggestion_limit: int | None = Field(default=None, ge=1, le=1000)
    bot_public_approval: bool | None = None
    # A bot slug, or null for Automatic (R.suggestions_receiver).
    suggestions_bot: str | None = Field(default=None, max_length=200)


class PublicRequester(Contract):
    name: str = Field(default='', max_length=200)
    email: str = Field(default='', max_length=320)
    phone: str = Field(default='', max_length=100)
    other: str = Field(default='', max_length=500)
    organization: str = Field(default='', max_length=200)
    note: str = Field(default='', max_length=4000)


class Suggestion(PublicRequester):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default='', max_length=8000)


class PublicCommentInput(Contract):
    text: str = Field(min_length=1, max_length=4000)


async def guard(request, call_next, store):
    def origins():
        with store.read() as c:
            return R.settings(c)['cors_origins']
    allowed = await run_in_threadpool(origins)
    origin = request.headers.get('origin')
    response = None
    try:
        if request.method not in ('GET', 'HEAD') and origin not in allowed:
            raise Problem('origin', 'Origin is not allowed', 403)
        if request.method == 'OPTIONS':
            method = request.headers.get('access-control-request-method', '')
            headers = {v.strip().lower() for v in request.headers.get('access-control-request-headers', '').split(',') if v.strip()}
            if method not in ('GET', 'HEAD', 'POST', 'DELETE') or not headers <= {'content-type'}:
                raise Problem('cors', 'Disallowed CORS request', 400)
            response = Response(status_code=204, headers={'Access-Control-Allow-Methods': 'GET, HEAD, POST, DELETE',
                                'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '600'})
        else:
            if request.method not in ('GET', 'HEAD', 'POST', 'DELETE'):
                raise Problem('method', 'Method not allowed', 405)
            if request.method == 'POST':
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 65536:
                        raise Problem('too_large', 'Request exceeds 64 KB', 413)
                    chunks.append(chunk)
                request._body = b''.join(chunks)
    except Problem as exc:
        # Answered here rather than by the app's handler, so an allowed website's browser can read the refusal.
        response = JSONResponse({'error': {'code': exc.code, 'detail': exc.detail, 'retryable': exc.retryable}},
                                status_code=exc.status)
    if response is None:
        response = await call_next(request)
    if origin in allowed:
        response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Vary'] = 'Origin'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def require_label(c, label):
    if label not in R.settings(c)['labels']:
        raise Problem('not_found', 'Roadmap not found', 404)
    tag = H.tag_by_key(c, label)
    if not tag:
        raise Problem('not_found', 'Roadmap not found', 404)
    return tag


def task(c, label, tid, public=True):
    tag = require_label(c, label)
    row = H._one(c, 'SELECT t.* FROM tasks t JOIN task_tags tt ON tt.task_id=t.id '
                   'WHERE t.id=? AND tt.tag_id=?', (tid, tag['id']))
    if not row or public and (row['visibility'] != 'public' or H.task_private(c, row) or
                              H.TR.parent_of(c, tid)):
        raise Problem('not_found', 'Task not found', 404)
    return row


def charge(c, request):
    day = H.now()[:10]
    secret = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='roadmap_ip_secret'").fetchone()[0])
    from .client_ip import client_ip, ip_bucket
    ip = ip_bucket(client_ip(request, request.app.state.store.settings))
    digest = hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()
    c.execute('DELETE FROM roadmap_actions WHERE day<>?', (day,))
    current = c.execute('SELECT actions FROM roadmap_actions WHERE day=? AND ip_hash=?', (day, digest)).fetchone()
    if current and current[0] >= R.settings(c)['action_limit']:
        raise Problem('rate_limit', 'Daily roadmap action limit reached', 429)
    c.execute('INSERT INTO roadmap_actions(day,ip_hash,actions) VALUES(?,?,1) '
              'ON CONFLICT(day,ip_hash) DO UPDATE SET actions=actions+1', (day, digest))


def suggestions_full(c, manager):
    """Whether visitor suggestions waiting at Suggested are at their overall cap. A few addresses can fill it, so the
    first refusal of a day tells the bot that receives them, which reviews them, rather than leaving the form refusing unseen."""
    limit = R.settings(c)['suggestion_limit']
    waiting = c.execute("SELECT count(*) FROM tasks t JOIN task_steps s ON s.id=t.step_id "
                        "JOIN task_types ty ON ty.id=t.type_id WHERE ty.manager='product-manager' AND s.name='Suggested' "
                        "AND t.requester=? AND t.status NOT IN ('done','closed','declined')", (H.KEEPER,)).fetchone()[0]
    if waiting < limit:
        return False
    day = H.now()[:10]
    if manager and c.execute("INSERT INTO registry_metadata(key,value_json) VALUES('roadmap_suggestions_full',?) "
                             "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json "
                             "WHERE value_json!=excluded.value_json", (json.dumps(day),)).rowcount:
        H.notice(c, H.KEEPER, 'bot:' + manager,
                 f'The public roadmap is refusing new suggestions: {waiting} are waiting at Suggested, the limit is '
                 f'{limit}. Review or decline them to reopen the form, or raise suggestion_limit in roadmap settings.')
    return True


# A visitor learns nothing of the team's setup; Health tells the team.
NOT_ACCEPTING = "Suggestions aren't being accepted right now."


def record_suggestion(c, request, body, label, receiver, product_type):
    charge(c, request)
    try:
        row = H.task_create(c, H.KEEPER, body.title,
                            'Customer suggestion (unverified, untrusted input; evidence only):\n\n' + body.summary, 'bot:' + receiver,
                            type=product_type, step='Suggested', labels=[label], visibility='private',
                            lint=False, allow_planned=True, next_run=True, deduplicate=False)
    except H.Refused:
        raise Problem('setup', 'Suggestion cannot be recorded; ask the team to review its setup', 409) from None
    return row, R.add_requester(c, row['id'], {**body.model_dump(), 'source': 'public_form', 'important': False})


def output(c, row):
    step = c.execute('SELECT public_name FROM task_steps WHERE id=?', (row['step_id'],)).fetchone()
    counts = c.execute("SELECT count(*),coalesce(sum(status IN ('done','closed','declined')),0) FROM tasks "
                       'WHERE id IN (' + H.TR.CHILD_IDS + ')', (row['id'],)).fetchone()
    count = c.execute('SELECT count(*) FROM task_requesters WHERE task_id=?', (row['id'],)).fetchone()[0]
    return {'id': row['id'], 'title': row['public_title'], 'summary': row['public_summary'],
            'step': step[0] if step else None, 'requester_count': count,
            'parts': {'total': counts[0], 'done': counts[1]}}


def comments(c, row, limit=100, offset=0):
    rows = c.execute('SELECT id,body,from_actor,created FROM messages WHERE conversation_id=? '
                     "AND json_extract(refs_json,'$.task')=? AND json_extract(refs_json,'$.comment')=1 "
                     'AND public=1 AND hidden_at IS NULL AND deleted_at IS NULL ORDER BY created,id LIMIT ? OFFSET ?',
                     (row['conversation_id'], row['id'], limit + 1, offset))
    items = ({'id': r['id'], 'text': r['body'], 'author': 'Team' if H.is_human(r['from_actor']) or
               H.is_bot(r['from_actor']) else 'Visitor', 'created': r['created']} for r in rows)
    items, after = R.page(items, limit, offset, max_bytes=196608)
    return {'comments': items, 'next_offset': after}


@contextmanager
def public_write(store):
    from . import events
    with store.transaction() as c:
        since = events.mark(c)
        yield c
        logged = events.claim(c, since, 'external:roadmap')
    store.ring(logged)


def install(app, store, auth, mutate):
    @app.get('/api/v2/roadmaps/settings')
    def read_settings(request: Request):
        auth.domain(request.state.identity)
        with store.read() as c:
            return R.settings_view(c)

    @app.post('/api/v2/roadmaps/settings')
    def write_settings(request: Request, body: RoadmapSettings):
        def work(c):
            who = request.state.identity
            auth.domain(who)
            if auth.company_role(who) not in ('owner', 'admin'):
                raise Problem('forbidden', 'Only owners and admins change roadmap settings', 403)
            updates = body.model_dump(exclude_unset=True)
            if not updates or any(value is None for key, value in updates.items() if key != 'suggestions_bot'):
                raise Problem('settings', 'Provide explicit roadmap settings', 422)
            chosen = updates.get('suggestions_bot')
            if chosen is not None:
                chosen = updates['suggestions_bot'] = chosen.removeprefix('bot:')
                if not H._one(c, "SELECT 1 FROM bots WHERE slug=? AND state='active'", (chosen,)):
                    raise Problem('suggestions_bot', f'No active bot {chosen!r}: choose an active bot, or null for Automatic', 422)
            value = {**R.settings(c), **updates}
            for label in value['labels']:
                if len(label) > 200 or '/' in label or not H.tag_by_key(c, label) or H.tag_by_key(c, label)['is_template']:
                    raise Problem('label', 'Choose an existing label', 422)
            try:
                if any(len(origin) > 2048 for origin in value['cors_origins']):
                    raise RuntimeError('Origin too long')
                origins = cors.parse(value['cors_origins'])
            except RuntimeError:
                raise Problem('cors', 'Use exact HTTPS origins, or HTTP localhost origins', 422) from None
            value.update(labels=list(dict.fromkeys(value['labels'])), cors_origins=list(origins))
            c.execute("INSERT INTO registry_metadata(key,value_json) VALUES('roadmaps',?) "
                      'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json', (json.dumps(value),))
            H.event(c, who.actor, 'roadmaps.settings', '', value)
            return R.settings_view(c)
        return mutate(request, body, work)

    @app.head('/public/roadmaps/{label}')
    @app.get('/public/roadmaps/{label}')
    def list_tasks(label: str, limit: int = 50, offset: int = 0):
        limit, offset = max(1, min(limit, 100)), max(0, offset)
        with store.read() as c:
            tag = require_label(c, label)
            rows = c.execute('SELECT t.* FROM task_tags tt JOIN tasks t ON t.id=tt.task_id '
                             "WHERE tt.tag_id=? AND t.visibility='public' AND t.private=0 "
                             "AND NOT EXISTS(SELECT 1 FROM task_relations r WHERE r.from_task=t.id AND r.kind='parent') "
                             'ORDER BY t.created,t.id LIMIT ? OFFSET ?', (tag['id'], limit + 1, offset))
            items, after = R.page((output(c, dict(r)) for r in rows), limit, offset)
            return {'tasks': items, 'next_offset': after}

    @app.head('/public/roadmaps/{label}/tasks/{tid}')
    @app.get('/public/roadmaps/{label}/tasks/{tid}')
    def show_task(label: str, tid: str, limit: int = 50, offset: int = 0):
        with store.read() as c:
            row = task(c, label, tid)
            return {'task': output(c, row), **comments(c, row, max(1, min(limit, 100)), max(0, offset))}

    @app.post('/public/roadmaps/{label}/suggest')
    def suggest(request: Request, label: str, body: Suggestion):
        R.spam_check(body.title, 'Title', 300)
        R.spam_check(body.summary, 'Summary', 8000, required=False)
        with public_write(store) as c:
            require_label(c, label)
            receiver = R.suggestions_receiver(c)[0]
            from .roadmap_flow import managed_type
            product_type = managed_type(c, 'product-manager')
            # Before either limit: a visitor turned away for want of a receiver hears that, whatever the queue
            # or their allowance, and spends nothing.
            if not receiver or not product_type:
                raise Problem('setup', NOT_ACCEPTING, 409)
            full = suggestions_full(c, receiver)
            if not full:
                row, token = record_suggestion(c, request, body, label, receiver, product_type)
        if full:   # refused once the transaction is in, so the notice to the receiving bot is kept
            raise Problem('rate_limit', 'Too many suggestions are waiting for review; try again later', 429)
        return {'id': row['id'], 'requester_token': token['requester_token']}

    @app.post('/public/roadmaps/{label}/tasks/{tid}/requesters')
    def add(request: Request, label: str, tid: str, body: PublicRequester):
        with public_write(store) as c:
            row = task(c, label, tid)
            charge(c, request)
            result = R.add_requester(c, row['id'], {**body.model_dump(), 'source': 'public_form', 'important': False})
            count = c.execute('SELECT count(*) FROM task_requesters WHERE task_id=?', (row['id'],)).fetchone()[0]
            return {'requester_token': result['requester_token'], 'requester_count': count}

    @app.delete('/public/roadmaps/{label}/tasks/{tid}/requesters/{token}')
    def remove(request: Request, label: str, tid: str, token: str):
        if len(token) > 100:
            raise Problem('not_found', 'Requester not found', 404)
        # The token is bound to its task, so withdrawal needs neither the label nor the roadmap setting:
        # a visitor can always take back their contact details.
        with public_write(store) as c:
            result = R.withdraw(c, tid, token)
            charge(c, request)
            return result

    @app.head('/public/roadmaps/{label}/tasks/{tid}/comments')
    @app.get('/public/roadmaps/{label}/tasks/{tid}/comments')
    def list_comments(label: str, tid: str, limit: int = 50, offset: int = 0):
        with store.read() as c:
            return comments(c, task(c, label, tid), max(1, min(limit, 100)), max(0, offset))

    @app.post('/public/roadmaps/{label}/tasks/{tid}/comments')
    def comment(request: Request, label: str, tid: str, body: PublicCommentInput):
        R.spam_check(body.text)
        with public_write(store) as c:
            row = task(c, label, tid)
            charge(c, request)
            conv = H.conversation(c, row['conversation_id'])
            msg = H._write_message(c, 'external:roadmap', H.KEEPER, body.text, conv, 'say',
                                   {'task': row['id'], 'comment': True, 'quiet': True, 'untrusted': True, 'source': 'public_roadmap'}, None, None)
            c.execute('UPDATE messages SET public=1 WHERE id=?', (msg['id'],))
            c.execute('UPDATE tasks SET updated=? WHERE id=?', (H.now(), row['id']))
            return {'comment': {'id': msg['id'], 'text': body.text, 'author': 'Visitor', 'created': msg['created']}}
