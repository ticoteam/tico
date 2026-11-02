"""Persistent Nightly memory tidy cards, independent of the pass task lifecycle.

Only trusted filing code calls file_card with a checked memory_tidy.Result and the
filing ledger's list-price estimate (including subscription usage). There is no
public offer endpoint: a child cannot turn its own unchecked output into a card.
No repository, model, approval, message or job writes happen here. Keep application
is installed by the separate Keep ticket; until then it fails closed.
"""
import json
import math
import re
import sqlite3
from datetime import timezone
from typing import Literal

from fastapi import Request
from pydantic import Field

from . import models as M
from .memory_tidy import Result
from .still_needed import crossed_at
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_tidy_cards (
 id TEXT PRIMARY KEY, bot TEXT NOT NULL, reviewer TEXT NOT NULL, pass_id TEXT NOT NULL UNIQUE,
 memory_commit TEXT NOT NULL, model TEXT NOT NULL, cost_usd REAL NOT NULL,
 diff TEXT NOT NULL, diary TEXT NOT NULL, edits_json TEXT NOT NULL,
 parent_task_id TEXT, check_task_id TEXT, started TEXT NOT NULL, created TEXT NOT NULL,
 expires TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', reason TEXT NOT NULL DEFAULT '',
 decided TEXT, decided_by TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS memory_tidy_cards_one_open ON memory_tidy_cards(bot) WHERE status='open';
CREATE INDEX IF NOT EXISTS memory_tidy_cards_reviewer ON memory_tidy_cards(reviewer,status,expires);
CREATE INDEX IF NOT EXISTS memory_tidy_cards_started ON memory_tidy_cards(bot,started);
CREATE TABLE IF NOT EXISTS memory_tidy_keeps (
 id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES memory_tidy_cards(id),
 runner_id TEXT NOT NULL, requested_by TEXT NOT NULL, created TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', commit_sha TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS memory_tidy_keeps_pending ON memory_tidy_keeps(card_id) WHERE status='queued';
"""


def stamp(at):
    return at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def sweep(c, at=None):
    """Hourly cleanup and filing cleanup; withdrawal never applies an edit."""
    at = at or H.now()
    return c.execute("UPDATE memory_tidy_cards SET status='withdrawn',reason='expired',decided=? "
                     "WHERE status='open' AND expires<=? AND id NOT IN (SELECT card_id FROM memory_tidy_keeps WHERE status='queued')", (at, at)).rowcount


SUMMARY_FIELDS = ('id', 'bot', 'reviewer', 'memory_commit', 'model', 'cost_usd', 'diary',
                  'created', 'expires', 'status', 'reason')


def ready_runner(c, bot):
    if c is None:
        return None
    try:
        row = c.execute("SELECT r.id,r.capabilities_json FROM assignments a JOIN runners r ON r.id=a.runner_id "
                        "WHERE a.bot=? AND r.revoked_at IS NULL", (bot,)).fetchone()
    except sqlite3.OperationalError:
        return None  # pure card fixtures have no computers
    return row['id'] if row and 'memory_tidy_keep_v1' in json.loads(row['capabilities_json']) else None


def pending(c, ident):
    return c is not None and c.execute("SELECT 1 FROM memory_tidy_keeps WHERE card_id=? AND status='queued'", (ident,)).fetchone() is not None


def view(row, *, detail=True, c=None):
    # The card's independent audience is reviewer, not the private task's audience.
    # Task ids and numbered edits remain internal provenance for Keep.
    return {**{k: row[k] for k in SUMMARY_FIELDS}, **({'diff': row['diff']} if detail else {}), 'kind': 'memory_tidy',
            'title': 'Nightly memory tidy: ' + row['bot'], 'requester': 'bot:' + row['bot'],
            'keep_available': bool(ready_runner(c, row['bot'])) and not pending(c, row['id']),
            'keep_pending': bool(pending(c, row['id']))}


def file_card(c, *, bot, reviewer, pass_id, memory_commit, model, cost_usd,
              result: Result, started_at=None, parent_task_id=None, check_task_id=None):
    """Called inside the filing transaction, before closing child/parent, with checked output.

    pass_id makes delivery idempotent even after Discard/withdrawal. started_at is
    the ledger's pass start, so a late older result cannot replace a newer pass.
    Only a successful offer supersedes the previous card, never a skip or flag.
    """
    if result.status != 'offer':
        return None
    previous = c.execute('SELECT * FROM memory_tidy_cards WHERE pass_id=?', (pass_id,)).fetchone()
    if previous:
        return view(previous)
    # The pilot forbids Haiku-tier passes. Reject a misconfigured filing before showing Keep.
    if (not bot or not pass_id or not re.fullmatch(r'(?:human|bot):[^\s:]+', reviewer)
            or not re.fullmatch(r'[0-9a-f]{40,64}', memory_commit)
            or not model or 'haiku' in model.lower()
            or isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float))
            or not math.isfinite(cost_usd) or cost_usd < 0
            or not result.diff or not result.edits_json or result.flags
            or not result.diary.strip() or len(result.diary.splitlines()) > 3):
        raise Problem('validation', 'Invalid checked Nightly memory tidy card', 422)
    # Validate serialized edit data, never apply it here.
    if not isinstance(json.loads(result.edits_json), dict):
        raise Problem('validation', 'Expected a numbered-edit object', 422)
    created = H.now()
    started = H.parse_ts(started_at or created)
    if started is None:
        raise Problem('validation', 'Invalid pass start', 422)
    started = stamp(started.replace(tzinfo=started.tzinfo or timezone.utc))
    newer = c.execute('SELECT 1 FROM memory_tidy_cards WHERE bot=? AND started>=? LIMIT 1', (bot, started)).fetchone()
    if newer:
        raise Problem('conflict', 'A newer Nightly memory tidy pass already has a card', 409)
    if c.execute("SELECT 1 FROM memory_tidy_keeps k JOIN memory_tidy_cards t ON t.id=k.card_id WHERE t.bot=? AND k.status='queued'", (bot,)).fetchone():
        raise Problem('conflict', 'A Keep is still being applied on this bot’s computer', 409)
    sweep(c, created)
    c.execute("UPDATE memory_tidy_cards SET status='withdrawn',reason='replaced',decided=? "
              "WHERE bot=? AND status='open'", (created, bot))
    ident = H.new_id()
    expires = stamp(crossed_at(created))
    c.execute('INSERT INTO memory_tidy_cards '
              '(id,bot,reviewer,pass_id,memory_commit,model,cost_usd,diff,diary,edits_json,'
              'parent_task_id,check_task_id,started,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (ident, bot, reviewer, pass_id, memory_commit, model, cost_usd, result.diff, result.diary,
               result.edits_json, parent_task_id, check_task_id, started, created, expires))
    return view(c.execute('SELECT * FROM memory_tidy_cards WHERE id=?', (ident,)).fetchone())


def cards_for(c, actor, at=None):
    return [view(r, detail=False, c=c) for r in c.execute("SELECT " + ",".join(SUMMARY_FIELDS) + " FROM memory_tidy_cards WHERE reviewer=? "
                                     "AND status='open' AND (expires>? OR id IN (SELECT card_id FROM memory_tidy_keeps WHERE status='queued')) ORDER BY created,id", (actor, at or H.now()))]


def require_card(c, actor, ident):
    row = c.execute('SELECT * FROM memory_tidy_cards WHERE id=? AND reviewer=?', (ident, actor)).fetchone()
    if not row:
        raise Problem('not_found', 'Unknown Nightly memory tidy card', 404)
    return row


class MemoryTidyDecision(M.Contract):
    decision: Literal['keep', 'discard']


class MemoryTidyCompletion(M.Contract):
    status: Literal['kept', 'refused']
    commit: str = ''
    reason: str = Field(default='', max_length=500)


def install(app, store, auth, mutate):
    @app.get('/api/v2/memory-tidy/cards')
    def list_cards(request: Request):
        """The named reviewer's cards, including after the pass tasks close. Bots may read their own cards."""
        who = request.state.identity
        auth.domain(who)
        with store.read_transaction() as c:
            return {'cards': cards_for(c, who.actor)}

    @app.get('/api/v2/memory-tidy/cards/{ident}')
    def read_card(request: Request, ident: str):
        who = request.state.identity
        auth.domain(who)
        with store.read_transaction() as c:
            row = require_card(c, who.actor, ident)
            card = view(row, c=c)
            if row['status'] == 'open' and row['expires'] <= H.now() and not pending(c, ident):
                card.update(status='withdrawn', reason='expired')
            return {'card': card}

    @app.post('/api/v2/memory-tidy/cards/{ident}/decide')
    def decide_card(request: Request, ident: str, body: MemoryTidyDecision):
        who = request.state.identity
        auth.domain(who)

        def write(c):
            row = require_card(c, who.actor, ident)
            if row['status'] != 'open' or row['expires'] <= H.now():
                raise Problem('conflict', 'This Nightly memory tidy card is no longer open', 409)
            if pending(c, ident):
                if body.decision == 'keep':
                    return {'card': view(row, c=c)}
                raise Problem('conflict', 'Keep is already being applied; wait for its result', 409)
            if body.decision == 'keep':
                runner = ready_runner(c, row['bot'])
                if not runner:
                    raise Problem('unavailable', 'Update this bot’s computer to enable Keep; the proposal remains open', 409)
                c.execute('INSERT INTO memory_tidy_keeps(id,card_id,runner_id,requested_by,created) VALUES(?,?,?,?,?)',
                          (H.new_id(), ident, runner, who.actor, H.now()))
                return {'card': view(row, c=c)}
            c.execute("UPDATE memory_tidy_cards SET status='discarded',decided=?,decided_by=? WHERE id=?",
                      (H.now(), who.actor, ident))
            return {'card': view(require_card(c, who.actor, ident), c=c)}
        return mutate(request, body, write)

    def computer(request):
        who = request.state.identity
        if who.role != 'runner':
            raise Problem('identity', 'A registered computer is required', 403)
        return who.runner_id

    @app.get('/api/v2/runners/me/memory-tidy-keeps')
    def computer_keeps(request: Request):
        rid = computer(request)
        with store.read_transaction() as c:
            rows = c.execute("SELECT k.id,k.card_id,t.bot,t.memory_commit,t.edits_json,t.diary,t.created,b.config_json "
                             "FROM memory_tidy_keeps k JOIN memory_tidy_cards t ON t.id=k.card_id "
                             "JOIN bot_config b ON b.bot=t.bot JOIN assignments a ON a.bot=t.bot AND a.runner_id=k.runner_id "
                             "WHERE k.runner_id=? AND k.status='queued' ORDER BY k.created", (rid,)).fetchall()
            return {'keeps': [{**{k: r[k] for k in r.keys() if k != 'config_json'}, 'config': json.loads(r['config_json']), 'date': r['created'][:10]} for r in rows]}

    @app.post('/api/v2/runners/me/memory-tidy-keeps/{ident}/complete')
    def complete_keep(request: Request, ident: str, body: MemoryTidyCompletion):
        rid = computer(request)

        def write(c):
            row = c.execute('SELECT * FROM memory_tidy_keeps WHERE id=? AND runner_id=?', (ident, rid)).fetchone()
            if not row:
                raise Problem('not_found', 'Unknown Keep operation', 404)
            if row['status'] != 'queued':
                return {'ok': True}  # repeated delivery cannot count twice
            if body.status == 'kept' and not re.fullmatch(r'[a-f0-9]{40,64}', body.commit):
                raise Problem('validation', 'A successful Keep needs its pushed commit', 422)
            c.execute('UPDATE memory_tidy_keeps SET status=?,commit_sha=?,reason=? WHERE id=?',
                      (body.status, body.commit, body.reason, ident))
            if body.status == 'kept':
                c.execute("UPDATE memory_tidy_cards SET status='kept',decided=?,decided_by=?,reason=? WHERE id=?",
                          (H.now(), row['requested_by'], body.commit, row['card_id']))
            else:
                c.execute('UPDATE memory_tidy_cards SET reason=? WHERE id=?', (body.reason, row['card_id']))
            return {'ok': True}
        return mutate(request, body, write)
