"""Publication and requesters extend tasks; comments remain messages in their task thread.

The public roadmap is off in this release: it was built on task types and steps, and comes back with the Product
pipeline, on its stages. Its settings, a task's public fields and its requesters are kept."""

import hashlib
import json
import re
import secrets

from fastapi import Request
from pydantic import Field

from . import hubdb as H
from .models import Contract
from .store import Problem
from . import bot_settings as BS
from .api_contract import api_v2, error

# Anonymous suggestions waiting at Suggested, across every visitor: the per-address limit alone lets many
# addresses queue unbounded work for the bot that receives them (the same overall cap as waiting pairings).
SUGGESTIONS_WAITING_MAX = 20
NOT_YET = 'The public roadmap is not available yet.'


# Public text someone wrote leaves a task event for its field (publication(), task_create); the first version's copies
# left none.
def written(field):
    return (
        f"EXISTS(SELECT 1 FROM history_events e WHERE e.subject_type='task' AND e.subject_id=tasks.id "
        f"AND e.kind='{field}')"
    )


def settings(c):
    row = (
        c.execute("SELECT value FROM registry_metadata WHERE key='roadmaps'").fetchone()
        if H._has_table(c, 'registry_metadata')
        else None
    )
    return {
        'labels': [],
        'cors_origins': [],
        'action_limit': 10,
        'suggestion_limit': SUGGESTIONS_WAITING_MAX,
        'bot_public_approval': True,
        'suggestions_bot': None,
        **(json.loads(row[0]) if row else {}),
    }


def suggestions_receiver(c):
    """The bot that gets visitor suggestions and the suggestions-full notice, and why: the bot chosen in settings
    while it is active; otherwise Automatic, the first active or planned Feature Owner, then an active Product
    Lead. Returns (id or None, 'chosen' | 'automatic' | 'chosen_archived' | 'chosen_inactive'), the last two
    saying the chosen bot was passed over because it is archived (or gone), or paused, quarantined or planned."""
    chosen = settings(c)['suggestions_bot']
    source = 'automatic'
    if chosen:
        chosen = (H.resolve_bot(c, chosen) or {}).get('id', chosen)
        state = (H._one(c, 'SELECT state FROM bots WHERE id=?', (chosen,)) or {'state': 'archived'})['state']
        if state == 'active':
            return chosen, 'chosen'
        source = 'chosen_archived' if state == 'archived' else 'chosen_inactive'
    for template, states in (
        ('feature-owner', ('active', 'planned')),
        ('product-lead', ('active',)),
        ('product-manager', ('active',)),
    ):
        row = c.execute(
            f"SELECT bc.bot FROM bot_config bc {BS.JOIN} JOIN bots b ON b.id=bc.bot "
            f"WHERE s.template=? AND b.state IN (" + ','.join('?' * len(states)) + ") "
            "ORDER BY bc.bot LIMIT 1",
            (template, *states),
        ).fetchone()
        if row:
            return row[0], source
    return None, source


def settings_view(c):
    """The settings plus the bot suggestions go to right now, so a reader sees what Automatic means today."""
    bot, source = suggestions_receiver(c)
    name = H._one(c, 'SELECT display_name FROM bots WHERE id=?', (bot,)) if bot else None
    return {
        **settings(c),
        'suggestions_receiver': {'bot': bot, 'name': (name and name['display_name']) or bot, 'source': source},
    }


def publication_payload(task_id, operation, values):
    text = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return {
        'url': f'tico:roadmaps/{task_id}/{operation}',
        'text': text,
        'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
    }


def exact_approval(c, actor):
    """Whether `actor`'s public text needs an exact approved publish payload (approve): a bot's, or one acting for a
    person, when the team asks for approval; the Strategy Owner's whatever the setting."""
    if not H.is_bot(actor) and not H.VIA.get():
        return False
    from . import strategy_acts

    return strategy_acts.is_strategy_bot(actor) or settings(c)['bot_public_approval']


def approve(c, actor, task_id, operation, values, approval_id=None):
    if not exact_approval(c, actor):
        return
    from . import strategy_acts

    if strategy_acts.is_strategy_bot(actor) and not approval_id:
        # Publishing is the owner's at every level: the Strategy Owner publishes only what a person approved, whatever the
        # team's setting for other bots.
        strategy_acts.gate(c, actor, "publish", f"publish a change to a task on the public roadmap ({operation})")
    # From here the OK is checked exactly; for the Strategy Owner whatever the setting, so any id is not enough. The OK
    # (backend/task_asks.py) is the one this actor asked on this task for exactly this publication, given from its
    # preview by the person's own click, and it is used here, once.
    from . import task_asks

    expected = publication_payload(task_id, operation, values)
    subject = publication_subject(task_id)
    if not approval_id or not task_asks.passes(c, approval_id, subject, expected, actor):
        H.refuse(
            c,
            actor,
            'approval',
            'Public changes need an OK of exactly this publication. ' + NOT_YET,
        )
    task_asks.use(c, approval_id, subject, expected, actor)


def publication_subject(task_id):
    """The subject of an OK to publish a change to one task (backend/task_asks.py): asked with
    task_asks.ask_ok(..., subject=publication_subject(id), content=publication_payload(id, operation, values),
    task_id=id)."""
    return f'publish:{task_id}'


def publication(c, actor, row, visibility=None, public_title=None, public_summary=None, approval_id=None, allowance=0):
    """A task's public fields as a write sets them. `allowance` is the length of an org rule's Warn line the server put
    before the summary (backend/app.py, rule_warned_fields): the cap is the bot's own text's, the line past it."""
    fields = {
        k: v
        for k, v in (('visibility', visibility), ('public_title', public_title), ('public_summary', public_summary))
        if v is not None
    }
    if not fields:
        return
    if visibility is not None and visibility not in ('public', 'private'):
        H.refuse(c, actor, 'kind', 'visibility is public or private')
    if public_title is not None and (not public_title.strip() or len(public_title) > 300):
        H.refuse(c, actor, 'lint', 'Public title is 1 to 300 characters')
    if public_summary is not None and len(public_summary) > 8000 + allowance:
        H.refuse(c, actor, 'lint', 'Public summary is at most 8000 characters')
    if fields.get('visibility', row['visibility']) == 'public' and H.task_private(c, row):
        H.refuse(c, actor, 'private', 'A two-party private task cannot be published')
    if (
        fields.get('visibility', row['visibility']) == 'public'
        and not fields.get('public_title', row['public_title']).strip()
    ):
        H.refuse(c, actor, 'lint', 'Write a public title before publishing; the internal title and body stay private')
    changed = {k: v for k, v in fields.items() if v != row[k]}
    if changed and (any(k.startswith('public_') for k in changed) or changed.get('visibility') == 'public'):
        approve(c, actor, row['id'], 'text', changed, approval_id)
    for key, value in changed.items():
        c.execute(f'UPDATE tasks SET {key}=?,updated_at=? WHERE id=?', (value, H.now(), row['id']))
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
    c.execute(
        'INSERT INTO task_requesters(id,task_id,actor,' + ','.join(fields) + ',token_hash,created_at) '
        'VALUES(' + ','.join('?' * 13) + ')',
        (
            ident,
            task_id,
            actor,
            *(values.get(k, '') for k in fields),
            hashlib.sha256(token.encode()).hexdigest(),
            H.now(),
        ),
    )
    c.execute('UPDATE tasks SET updated_at=? WHERE id=?', (H.now(), task_id))
    H.event(c, actor or H.TICO_ACTOR, 'task.requester.add', task_id, {'requester_id': ident})
    return {'id': ident, 'requester_token': token}


def remove_requester(c, task_id, *, token=None, ident=None, actor=None):
    if token is not None:
        removed = c.execute(
            'DELETE FROM task_requesters WHERE task_id=? AND token_hash=?',
            (task_id, hashlib.sha256(token.encode()).hexdigest()),
        ).rowcount
    else:
        removed = c.execute('DELETE FROM task_requesters WHERE task_id=? AND id=?', (task_id, ident)).rowcount
    if not removed:
        raise Problem('not_found', 'Requester not found', 404)
    c.execute('UPDATE tasks SET updated_at=? WHERE id=?', (H.now(), task_id))
    H.event(c, actor or H.TICO_ACTOR, 'task.requester.remove', task_id, {})
    return {'removed': True}


def withdraw(c, task_id, token):
    """A visitor's token removes their request whatever became of the task since: unlabelled, off every
    enabled roadmap, private, or in the trash (where its snapshot would otherwise bring them back)."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    if c.execute('SELECT 1 FROM task_requesters WHERE task_id=? AND token_hash=?', (task_id, digest)).fetchone():
        return remove_requester(c, task_id, token=token)
    trash = (
        c.execute('SELECT rows FROM task_trash WHERE task_id=? AND purged_at IS NULL', (task_id,)).fetchone()
        if H._has_table(c, 'task_trash')
        else None
    )
    snapshot = json.loads(trash[0]) if trash else {}
    rows = snapshot.get('rows', {}).get('task_requesters') or []
    kept = [r for r in rows if r.get('token_hash') != digest]
    if len(kept) == len(rows):
        raise Problem('not_found', 'Requester not found', 404)
    snapshot['rows']['task_requesters'] = kept
    c.execute('UPDATE task_trash SET rows=? WHERE task_id=?', (json.dumps(snapshot, default=str), task_id))
    H.event(c, H.TICO_ACTOR, 'task.requester.remove', task_id, {'trashed': True})
    return {'removed': True}


# A task's requester tallies over its task_requesters rows: one aggregate for a task's requester list and the ranking.
STATS_COLUMNS = (
    'count(*) requester_count,sum(actor IS NULL) external_requesters,'
    'sum(actor IS NOT NULL) internal_requesters,sum(important) important_requesters,'
    "min(nullif(organization,'')) organization"
)


def stats(c, ids):
    if not ids:
        return {}
    rows = c.execute(
        'SELECT task_id,' + STATS_COLUMNS + ' FROM task_requesters '
        'WHERE task_id IN (SELECT value FROM json_each(?)) GROUP BY task_id',
        (json.dumps(ids),),
    )
    return {r['task_id']: {k: r[k] for k in r.keys() if k != 'task_id'} for r in rows}


EMPTY_STATS = {
    'requester_count': 0,
    'external_requesters': 0,
    'internal_requesters': 0,
    'important_requesters': 0,
    'organization': None,
}


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


# The Roadmap ranks the Product pipeline's tasks (pipeline_settings: the `product` preset, its pair's `product`
# Product, or the `features` preset that replaced it): each one the company has not retired.
PRODUCT_PIPELINES = (
    "SELECT id FROM pipelines WHERE retired_at IS NULL AND (preset IN ('product','features') OR id='product')"
)
NO_PRODUCT = 'The roadmap ranks the Product pipeline, and this company has none.'
# Each an expression, not an answer's alias: in an ORDER BY expression SQLite reads a bare name as r's column, which is
# null for a task nobody asked for.
RANK_SORTS = {
    'requester_count': 'coalesce(r.requester_count,0)',
    'external_requesters': 'coalesce(r.external_requesters,0)',
    'important': 'coalesce(r.important_requesters,0)',
    'organization': 'r.organization',
    'size': 't.size',
}


def ranking(c, visible, sort='requester_count', direction='desc', limit=100, offset=0):
    """A page of the Product pipeline's tasks that `visible` (Auth.task_sql, over the task's own columns) admits,
    with their requester tallies, by `sort` (RANK_SORTS) in `direction`; a task with no organization or size comes
    last either way. Returns (rows, next offset or None), or None when no Product pipeline is in use."""
    pipelines = [r[0] for r in c.execute(PRODUCT_PIPELINES)]
    if not pipelines:
        return None
    column = RANK_SORTS[sort]
    within = json.dumps(pipelines)
    rows = c.execute(
        'SELECT t.id,t.number,t.title,t.status,t.visibility,t.public_title,t.size,t.version,t.pipeline_id,t.stage_id,'
        's.name stage_name,coalesce(r.requester_count,0) requester_count,'
        'coalesce(r.external_requesters,0) external_requesters,coalesce(r.internal_requesters,0) internal_requesters,'
        'coalesce(r.important_requesters,0) important_requesters,r.organization '
        # The reader's rule reads the task's own columns, so it is applied where they are the only ones.
        'FROM (SELECT * FROM tasks WHERE pipeline_id IN (SELECT value FROM json_each(?)) AND (' + visible + ')) t '
        'LEFT JOIN stages s ON s.id=t.stage_id '
        'LEFT JOIN (SELECT task_id,' + STATS_COLUMNS + ' FROM task_requesters WHERE task_id IN '
        '(SELECT id FROM tasks WHERE pipeline_id IN (SELECT value FROM json_each(?))) GROUP BY task_id) r '
        'ON r.task_id=t.id '
        f'ORDER BY {column} IS NULL,{column} {direction},t.id LIMIT ? OFFSET ?',
        (within, within, limit + 1, offset),
    )
    return page((dict(r) for r in rows), limit, offset)


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

    @app.get(
        '/api/v2/tasks/{tid}/requesters',
        **api_v2(
            "Roadmaps", "listTaskRequesters", "Internal requester contacts, paged and byte-capped", "TaskRequesterList"
        ),
    )
    def requesters(request: Request, tid: str, limit: int = 100, offset: int = 0):
        with store.read() as c:
            row = task(c, request.state.identity, tid)
            limit, offset = max(1, min(limit, 100)), max(0, offset)
            columns = 'id,actor,name,email,phone,other,organization,note,source,important,created_at'
            rows = (
                dict(r)
                for r in c.execute(
                    'SELECT ' + columns + ' FROM task_requesters WHERE task_id=? '
                    'ORDER BY created_at,id LIMIT ? OFFSET ?',
                    (row['id'], limit + 1, offset),
                )
            )
            items, after = page(rows, limit, offset)
            for item in items:
                item['kind'] = 'teammate' if item['actor'] else 'external'
            own = c.execute(
                'SELECT id FROM task_requesters WHERE task_id=? AND actor=?', (row['id'], request.state.identity.actor)
            ).fetchone()
            return {
                'requesters': items,
                'next_offset': after,
                'my_requester_id': own[0] if own else None,
                **EMPTY_STATS,
                **stats(c, [row['id']]).get(row['id'], {}),
            }

    @app.post(
        '/api/v2/tasks/{tid}/requesters',
        **api_v2(
            "Roadmaps", "addTaskRequester", "Record a requester; returns a removal token once", "TaskRequesterAdded"
        ),
    )
    def create_requester(request: Request, tid: str, body: RequesterInput):
        def work(c):
            who = request.state.identity
            row = task(c, who, tid)
            actor = None
            if body.actor:
                actor = H.resolve_actor(c, body.actor)
                if not actor or not (H.is_human(actor) or H.is_bot(actor)) or not H._actor_exists(c, actor):
                    raise Problem('identity', 'No such person or agent', 422)
            if (actor != who.actor or not actor) and who.via == 'botops' and not who.confirmed:
                # BotOps acting for a person adds a teammate or an outside contact only on that person's own tap.
                made = getattr(request.app.state, 'botops_propose', None)
                if made:
                    name = H.human(c, H.actor_id(actor)) or {} if actor else {}
                    who_is = name.get('name') or actor or body.name or body.email or 'a contact'
                    return made(
                        c,
                        who,
                        'POST',
                        '/api/v2/tasks/' + tid + '/requesters',
                        body.model_dump(exclude_unset=True),
                        'Add ' + who_is + ' as a requester on “' + row['title'] + '”',
                    )
            if actor != who.actor:
                task(c, who, tid, True)
            return add_requester(c, row['id'], body.model_dump(), actor)

        return mutate(request, body, work)

    @app.post(
        '/api/v2/tasks/{tid}/requesters/{rid}/delete',
        **api_v2("Roadmaps", "deleteTaskRequester", "Remove a requester", "TaskRequesterRemoved"),
    )
    def delete_requester(request: Request, tid: str, rid: str, body: Contract):
        def work(c):
            who = request.state.identity
            row = task(c, who, tid)
            own = c.execute(
                'SELECT 1 FROM task_requesters WHERE task_id=? AND id=? AND actor=?', (row['id'], rid, who.actor)
            ).fetchone()
            if not own and who.via == 'botops' and not who.confirmed:
                return request.app.state.botops_propose(
                    c,
                    who,
                    'POST',
                    '/api/v2/tasks/' + tid + '/requesters/' + rid + '/delete',
                    body.model_dump(exclude_unset=True),
                    'Remove a requester from “' + row['title'] + '”',
                )
            if not own:
                task(c, who, tid, True)
            return remove_requester(c, row['id'], ident=rid, actor=who.actor)

        return mutate(request, body, work)

    @app.post(
        '/api/v2/tasks/{tid}/requesters/{rid}',
        **api_v2("Roadmaps", "updateTaskRequester", "Replace internal requester details", "TaskRequesterUpdated"),
    )
    def update_requester(request: Request, tid: str, rid: str, body: RequesterInput):
        def work(c):
            who = request.state.identity
            row = task(c, who, tid, True)
            if who.via == 'botops' and not who.confirmed:
                return request.app.state.botops_propose(
                    c,
                    who,
                    'POST',
                    '/api/v2/tasks/' + tid + '/requesters/' + rid,
                    body.model_dump(exclude_unset=True),
                    'Change a requester on “' + row['title'] + '”',
                )
            values = body.model_dump(exclude_unset=True, exclude={'actor'})
            if not values:
                raise Problem('requester', 'Provide at least one requester field', 422)
            changed = c.execute(
                'UPDATE task_requesters SET ' + ','.join(k + '=?' for k in values) + ' WHERE task_id=? AND id=?',
                (*values.values(), row['id'], rid),
            ).rowcount
            if not changed:
                raise Problem('not_found', 'Requester not found', 404)
            c.execute('UPDATE tasks SET updated_at=? WHERE id=?', (H.now(), row['id']))
            H.event(c, who.actor, 'task.requester.update', row['id'], {'requester_id': rid})
            return {'updated': True}

        return mutate(request, body, work)

    @app.post(
        '/api/v2/tasks/{tid}/publication-preview',
        **api_v2("Roadmaps", "previewTaskPublication", "Exact publish approval payload for public edits or replies"),
    )
    def preview(request: Request, tid: str, body: PublicationInput):
        """Not available yet, like the public routes: no route asks a publish OK (task_asks has no ask path for the
        `publish:` subject, and the generic OK route refuses it), so a preview would lead nowhere."""
        raise Problem('not_found', NOT_YET, 404)

    @app.post(
        '/api/v2/tasks/{tid}/comments/{mid}/moderate',
        **api_v2("Roadmaps", "moderateTaskComment", "Hide, restore or delete a public comment", "TaskCommentModerated"),
    )
    def moderate(request: Request, tid: str, mid: str, body: Moderate):
        def work(c):
            who = request.state.identity
            row = task(c, who, tid, True)
            msg = H.comment_on(c, row, mid)
            if not msg or not msg.get('public') or msg.get('deleted_at'):
                raise Problem('not_found', 'Public comment not found', 404)
            H._message_writer(c, who.actor, msg, H.conversation(c, msg['conversation_id']))
            if body.action == 'delete':
                c.execute("UPDATE messages SET body='',deleted_at=?,hidden_at=? WHERE id=?", (H.now(), H.now(), mid))
                H._withdraw_queued_comment_copies(c, mid)
                H._refresh_comment_replies(c, H.message(c, mid, include_deleted=True))
            else:
                c.execute(
                    'UPDATE messages SET hidden_at=? WHERE id=?', (H.now() if body.action == 'hide' else None, mid)
                )
            c.execute('UPDATE tasks SET updated_at=? WHERE id=?', (H.now(), row['id']))
            H.event(c, who.actor, 'task.comment.moderate', row['id'], {'comment': mid, 'action': body.action})
            return {'moderated': True}

        return mutate(request, body, work)

    @app.get(
        '/api/v2/roadmap-tasks',
        **api_v2(
            "Roadmaps",
            "listRoadmapTasks",
            "The Product pipeline's tasks the caller can read, ranked by who asked for them",
            "RoadmapTaskList",
            description="Every task of the company's Product pipeline the caller may read, in any stage and status, "
            "with its requester tallies, paged and byte-capped. sort is requester_count (the default), "
            "external_requesters, important, organization or size; direction is desc (the default) or asc. A task "
            "with no organization or size sorts last either way. 404 not_found when the company has no Product "
            "pipeline. Set a task's public text with updateTask (visibility, public_title, public_summary) and its "
            "stage with moveTask",
            parameters={"limit": "1 to 100 (100)", "offset": "From next_offset"},
            responses={"404": error("not_found: the company has no Product pipeline")},
        ),
    )
    def ranked(
        request: Request, sort: str = 'requester_count', direction: str = 'desc', limit: int = 100, offset: int = 0
    ):
        if sort not in RANK_SORTS or direction not in ('asc', 'desc'):
            raise Problem('sort', 'sort is ' + ', '.join(RANK_SORTS) + '; direction is asc or desc', 422)
        with store.read() as c:
            who = request.state.identity
            auth.domain(who)
            found = ranking(c, auth.task_sql(c, who), sort, direction, max(1, min(limit, 100)), max(0, offset))
            if found is None:
                raise Problem('not_found', NO_PRODUCT, 404)
            items, after = found
            return {'tasks': items, 'next_offset': after}
