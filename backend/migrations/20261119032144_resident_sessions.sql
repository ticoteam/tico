-- Resident sessions (backend/residents.py): a live provider process a runner keeps for one (bot, session key, login
-- binding) and reuses across that session's turns. A resident is not a turn: it holds no lane, start place, claim
-- limit or busy status, and never blocks a bot transfer or a cloud machine stop. Each turn it serves stays an ordinary
-- attempt, tied to it in `resident_turns`. `id` is the reservation's incarnation: a runner that restarts registers a new
-- one, so a late renew, release or bind from the old process never touches its replacement.
CREATE TABLE resident_sessions (
    id TEXT PRIMARY KEY NOT NULL,
    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    runner_id TEXT NOT NULL REFERENCES runners(id) ON DELETE CASCADE,
    generation INTEGER NOT NULL,
    session_key TEXT NOT NULL,
    binding TEXT NOT NULL DEFAULT '',
    provider_session_id TEXT NOT NULL DEFAULT '',
    runtime TEXT NOT NULL DEFAULT '',
    -- The company subscription the process signed in with ('' for the computer's own login): a turn handed another
    -- sign-in never joins it.
    subscription_id TEXT NOT NULL DEFAULT '',
    source_attempt_id TEXT REFERENCES attempts(id) ON DELETE SET NULL,
    state TEXT NOT NULL CHECK (state IN ('live', 'dormant', 'closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    stopped_at TEXT,
    stop_reason TEXT NOT NULL DEFAULT '',
    revoked_at TEXT,
    CHECK ((state = 'closed') = (stopped_at IS NOT NULL))
);
-- One writer per provider session: a second live or dormant reservation of the same key and binding is refused.
CREATE UNIQUE INDEX resident_sessions_one_writer ON resident_sessions(bot_id, session_key, binding)
    WHERE state <> 'closed';
CREATE INDEX resident_sessions_open_on_runner ON resident_sessions(runner_id) WHERE state <> 'closed';

-- The turns a resident served. `fenced_at`: the resident was stopped while this turn was bound to it, so the turn holds
-- no tool authority, credentials, start or new output from then on (auth.validate_identity, execution.started).
-- `conversation_id`: the conversation the turn answered, so a cleared session or a thread's revocation finds the
-- residents that read it without walking attempts, jobs and messages.
CREATE TABLE resident_turns (
    attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    resident_session_id TEXT NOT NULL REFERENCES resident_sessions(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    fenced_at TEXT,
    fence_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX resident_turns_resident ON resident_turns(resident_session_id);
CREATE INDEX resident_turns_conversation ON resident_turns(conversation_id);

-- When access last narrowed anywhere in the company (residents.revoke and the triggers below). A turn claimed before
-- then may hold what it no longer may, so it cannot register its process as a resident; it finishes as an ordinary turn.
CREATE TABLE resident_access_changes (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    changed_at TEXT NOT NULL
);

-- A bot that stops being active, a computer whose credential is revoked, and a bot moved to another computer or
-- generation end its residents in the same write, whichever code path makes the change. Each turn bound to one is fenced.
CREATE TRIGGER resident_sessions_bot_inactive AFTER UPDATE OF state ON bots
WHEN NEW.state IS NOT 'active' AND OLD.state IS NOT NEW.state
BEGIN
    INSERT INTO resident_access_changes(id,changed_at) VALUES(1,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        ON CONFLICT(id) DO UPDATE SET changed_at=excluded.changed_at;
    UPDATE resident_turns SET fenced_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), fence_reason='bot_' || NEW.state
    WHERE fenced_at IS NULL AND attempt_id IN (SELECT id FROM attempts WHERE state IN ('leased','running'))
    AND resident_session_id IN (
        SELECT id FROM resident_sessions WHERE bot_id=NEW.id AND state <> 'closed'
    );
    UPDATE resident_sessions SET state='closed', stopped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        revoked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        stop_reason='bot_' || NEW.state
    WHERE bot_id=NEW.id AND state <> 'closed';
END;

CREATE TRIGGER resident_sessions_runner_revoked AFTER UPDATE OF revoked_at ON runners
WHEN NEW.revoked_at IS NOT NULL AND OLD.revoked_at IS NULL
BEGIN
    INSERT INTO resident_access_changes(id,changed_at) VALUES(1,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        ON CONFLICT(id) DO UPDATE SET changed_at=excluded.changed_at;
    UPDATE resident_turns SET fenced_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), fence_reason='runner_revoked'
    WHERE fenced_at IS NULL AND attempt_id IN (SELECT id FROM attempts WHERE state IN ('leased','running'))
    AND resident_session_id IN (
        SELECT id FROM resident_sessions WHERE runner_id=NEW.id AND state <> 'closed'
    );
    UPDATE resident_sessions SET state='closed', stopped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        revoked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        stop_reason='runner_revoked'
    WHERE runner_id=NEW.id AND state <> 'closed';
END;

CREATE TRIGGER resident_sessions_assignment_moved AFTER UPDATE ON assignments
WHEN OLD.runner_id IS NOT NEW.runner_id OR OLD.generation IS NOT NEW.generation
BEGIN
    INSERT INTO resident_access_changes(id,changed_at) VALUES(1,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        ON CONFLICT(id) DO UPDATE SET changed_at=excluded.changed_at;
    UPDATE resident_turns SET fenced_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), fence_reason='bot_moved'
    WHERE fenced_at IS NULL AND attempt_id IN (SELECT id FROM attempts WHERE state IN ('leased','running'))
    AND resident_session_id IN (
        SELECT id FROM resident_sessions WHERE bot_id=NEW.bot AND state <> 'closed'
            AND (runner_id IS NOT NEW.runner_id OR generation IS NOT NEW.generation)
    );
    UPDATE resident_sessions SET state='closed', stopped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        revoked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        stop_reason='bot_moved'
    WHERE bot_id=NEW.bot AND state <> 'closed' AND (runner_id IS NOT NEW.runner_id OR generation IS NOT NEW.generation);
END;

CREATE TRIGGER resident_sessions_assignment_removed AFTER DELETE ON assignments
BEGIN
    INSERT INTO resident_access_changes(id,changed_at) VALUES(1,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        ON CONFLICT(id) DO UPDATE SET changed_at=excluded.changed_at;
    UPDATE resident_turns SET fenced_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), fence_reason='bot_unassigned'
    WHERE fenced_at IS NULL AND attempt_id IN (SELECT id FROM attempts WHERE state IN ('leased','running'))
    AND resident_session_id IN (
        SELECT id FROM resident_sessions WHERE bot_id=OLD.bot AND state <> 'closed'
    );
    UPDATE resident_sessions SET state='closed', stopped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        revoked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        stop_reason='bot_unassigned'
    WHERE bot_id=OLD.bot AND state <> 'closed';
END;

-- A cleared or rotated session (a new session epoch, backend/execution.py reset_bot_sessions) ends the residents that
-- keep it, so the next turn starts fresh. Not a revocation: a turn under way finishes and posts as before.
CREATE TRIGGER resident_sessions_bot_epoch AFTER INSERT ON bot_session_epochs
BEGIN
    UPDATE resident_sessions SET state='closed', stopped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), stop_reason='session_cleared'
    WHERE bot_id=NEW.bot AND state <> 'closed';
END;

CREATE TRIGGER resident_sessions_bot_epoch_bumped AFTER UPDATE ON bot_session_epochs
BEGIN
    UPDATE resident_sessions SET state='closed', stopped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), stop_reason='session_cleared'
    WHERE bot_id=NEW.bot AND state <> 'closed';
END;

CREATE TRIGGER resident_sessions_epoch AFTER INSERT ON session_epochs
BEGIN
    UPDATE resident_sessions SET state='closed', stopped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), stop_reason='session_cleared'
    WHERE state <> 'closed' AND (
        session_key IN ('conversation:' || NEW.conversation_id, 'root:' || NEW.conversation_id || ':private',
            'root:' || NEW.conversation_id || ':public')
        OR id IN (SELECT resident_session_id FROM resident_turns WHERE conversation_id=NEW.conversation_id)
    );
END;

CREATE TRIGGER resident_sessions_epoch_bumped AFTER UPDATE ON session_epochs
BEGIN
    UPDATE resident_sessions SET state='closed', stopped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), stop_reason='session_cleared'
    WHERE state <> 'closed' AND (
        session_key IN ('conversation:' || NEW.conversation_id, 'root:' || NEW.conversation_id || ':private',
            'root:' || NEW.conversation_id || ':public')
        OR id IN (SELECT resident_session_id FROM resident_turns WHERE conversation_id=NEW.conversation_id)
    );
END;
