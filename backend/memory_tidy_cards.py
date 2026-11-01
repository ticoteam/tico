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
from datetime import timezone
from typing import Literal

from fastapi import Request

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
"""


def stamp(at):
    return at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def sweep(c, at=None):
    """Hourly cleanup and filing cleanup; withdrawal never applies an edit."""
    at = at or H.now()
    return c.execute("UPDATE memory_tidy_cards SET status='withdrawn',reason='expired',decided=? "
                     "WHERE status='open' AND expires<=?", (at, at)).rowcount


SUMMARY_FIELDS = ('id', 'bot', 'reviewer', 'memory_commit', 'model', 'cost_usd', 'diary',
                  'created', 'expires', 'status', 'reason')


def view(row, *, detail=True):
    # The card's independent audience is reviewer, not the private task's audience.
    # Task ids and numbered edits remain internal provenance for Keep.
    return {**{k: row[k] for k in SUMMARY_FIELDS}, **({'diff': row['diff']} if detail else {}), 'kind': 'memory_tidy',
            'title': 'Nightly memory tidy: ' + row['bot'], 'requester': 'bot:' + row['bot'],
            'keep_available': False}


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
    return [view(r, detail=False) for r in c.execute("SELECT " + ",".join(SUMMARY_FIELDS) + " FROM memory_tidy_cards WHERE reviewer=? "
                                     "AND status='open' AND expires>? ORDER BY created,id", (actor, at or H.now()))]


def require_card(c, actor, ident):
    row = c.execute('SELECT * FROM memory_tidy_cards WHERE id=? AND reviewer=?', (ident, actor)).fetchone()
    if not row:
        raise Problem('not_found', 'Unknown Nightly memory tidy card', 404)
    return row


class MemoryTidyDecision(M.Contract):
    decision: Literal['keep', 'discard']


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
            card = view(row)
            if row['status'] == 'open' and row['expires'] <= H.now():
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
            if body.decision == 'keep':
                raise Problem('unavailable', 'Keep is not available yet; the proposal remains open', 409)
            c.execute("UPDATE memory_tidy_cards SET status='discarded',decided=?,decided_by=? WHERE id=?",
                      (H.now(), who.actor, ident))
            return {'card': view(require_card(c, who.actor, ident))}
        return mutate(request, body, write)
