"""The public roadmap, scoped to this installation's company by its host. It is off in this release: it was built on
task types and steps, and comes back with the Product pipeline, on its stages. Its pages and API answer "not available
yet"; its settings are kept and stay editable. A visitor's token still takes their contact details back."""

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
    c.execute(
        'CREATE TABLE IF NOT EXISTS roadmap_actions('
        'day TEXT NOT NULL,ip_hash TEXT NOT NULL,actions INTEGER NOT NULL,PRIMARY KEY(day,ip_hash))'
    )
    c.execute(
        "INSERT OR IGNORE INTO registry_metadata(key,value) VALUES('roadmap_ip_secret',?)",
        (json.dumps(secrets.token_hex(32)),),
    )


class RoadmapSettings(Contract):
    labels: list[str] | None = Field(default=None, max_length=100)
    cors_origins: list[str] | None = Field(default=None, max_length=100)
    action_limit: int | None = Field(default=None, ge=1, le=10000)
    suggestion_limit: int | None = Field(default=None, ge=1, le=1000)
    bot_public_approval: bool | None = None
    # A bot id (a slug is accepted and stored as its id), or null for Automatic (R.suggestions_receiver).
    suggestions_bot: str | None = Field(default=None, max_length=200)


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
            headers = {
                v.strip().lower()
                for v in request.headers.get('access-control-request-headers', '').split(',')
                if v.strip()
            }
            if method not in ('GET', 'HEAD', 'POST', 'DELETE') or not headers <= {'content-type'}:
                raise Problem('cors', 'Disallowed CORS request', 400)
            response = Response(
                status_code=204,
                headers={
                    'Access-Control-Allow-Methods': 'GET, HEAD, POST, DELETE',
                    'Access-Control-Allow-Headers': 'Content-Type',
                    'Access-Control-Max-Age': '600',
                },
            )
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
        response = JSONResponse(
            {'error': {'code': exc.code, 'detail': exc.detail, 'retryable': exc.retryable}}, status_code=exc.status
        )
    if response is None:
        response = await call_next(request)
    if origin in allowed:
        response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Vary'] = 'Origin'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def allowance(c, request):
    """(day, the visitor's address digest, whether today's action limit is already reached)."""
    day = H.now()[:10]
    secret = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='roadmap_ip_secret'").fetchone()[0])
    from .client_ip import client_ip, ip_bucket

    ip = ip_bucket(client_ip(request, request.app.state.store.settings))
    digest = hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()
    current = c.execute('SELECT actions FROM roadmap_actions WHERE day=? AND ip_hash=?', (day, digest)).fetchone()
    return day, digest, bool(current and current[0] >= R.settings(c)['action_limit'])


def charge(c, request):
    day, digest, spent = allowance(c, request)
    c.execute('DELETE FROM roadmap_actions WHERE day<>?', (day,))
    if spent:
        raise Problem('rate_limit', 'Daily roadmap action limit reached', 429)
    c.execute(
        'INSERT INTO roadmap_actions(day,ip_hash,actions) VALUES(?,?,1) '
        'ON CONFLICT(day,ip_hash) DO UPDATE SET actions=actions+1',
        (day, digest),
    )


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
                chosen = chosen.removeprefix('bot:')
                chosen = updates['suggestions_bot'] = (H.resolve_bot(c, chosen) or {}).get('id', chosen)
                if not H._one(c, "SELECT 1 FROM bots WHERE id=? AND state='active'", (chosen,)):
                    raise Problem(
                        'suggestions_bot', f'No active bot {chosen!r}: choose an active bot, or null for Automatic', 422
                    )
            value = {**R.settings(c), **updates}
            for label in value['labels']:
                if (
                    len(label) > 200
                    or '/' in label
                    or not H.tag_by_key(c, label)
                    or H.tag_by_key(c, label)['is_template']
                ):
                    raise Problem('label', 'Choose an existing label', 422)
            try:
                if any(len(origin) > 2048 for origin in value['cors_origins']):
                    raise RuntimeError('Origin too long')
                origins = cors.parse(value['cors_origins'])
            except RuntimeError:
                raise Problem('cors', 'Use exact HTTPS origins, or HTTP localhost origins', 422) from None
            value.update(labels=list(dict.fromkeys(value['labels'])), cors_origins=list(origins))
            c.execute(
                "INSERT INTO registry_metadata(key,value) VALUES('roadmaps',?) "
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (json.dumps(value),),
            )
            H.event(c, who.actor, 'roadmaps.settings', '', value)
            return R.settings_view(c)

        return mutate(request, body, work)

    def not_yet():
        raise Problem('not_found', R.NOT_YET, 404)

    @app.head('/public/roadmaps/{label}')
    @app.get('/public/roadmaps/{label}')
    def list_tasks(label: str):
        not_yet()

    @app.head('/public/roadmaps/{label}/tasks/{tid}')
    @app.get('/public/roadmaps/{label}/tasks/{tid}')
    def show_task(label: str, tid: str):
        not_yet()

    @app.post('/public/roadmaps/{label}/suggest')
    def suggest(label: str):
        not_yet()

    @app.post('/public/roadmaps/{label}/tasks/{tid}/requesters')
    def add(label: str, tid: str):
        not_yet()

    @app.delete('/public/roadmaps/{label}/tasks/{tid}/requesters/{token}')
    def remove(request: Request, label: str, tid: str, token: str):
        if len(token) > 100:
            raise Problem('not_found', 'Requester not found', 404)
        # The token is bound to its task, so withdrawal needs neither the label nor the roadmap: a visitor can always
        # take back their contact details.
        with public_write(store) as c:
            result = R.withdraw(c, tid, token)
            charge(c, request)
            return result

    @app.head('/public/roadmaps/{label}/tasks/{tid}/comments')
    @app.get('/public/roadmaps/{label}/tasks/{tid}/comments')
    def list_comments(label: str, tid: str):
        not_yet()

    @app.post('/public/roadmaps/{label}/tasks/{tid}/comments')
    def comment(label: str, tid: str):
        not_yet()
