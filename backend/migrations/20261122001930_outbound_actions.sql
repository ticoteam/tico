-- The outbound gate's one queue (backend/outbound_actions.py, docs/outbound-actions.md): every send and outside write a
-- channel adapter makes, decided by one policy and driven by the shared outbox leases (backend/outbox.py). `payload` is
-- the frozen content, wiped once the row ends; `metadata` is kept for later lookups. `provider_attempted_at` is set in
-- the one lease-checked write before the provider is called, so a row that lost its worker after it is `unknown`,
-- never sent again on its own. The state list is enforced by backend/status_values.py.
CREATE TABLE outbound_actions (
  id TEXT PRIMARY KEY,
  connection_id TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL,
  audience TEXT NOT NULL CHECK (audience IN ('internal', 'external')),
  operation TEXT NOT NULL,
  destination TEXT NOT NULL,
  occurrence_key TEXT NOT NULL UNIQUE,
  actor TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}',
  policy_decision TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  send_after TEXT NOT NULL,
  next_attempt_at TEXT NOT NULL,
  provider_attempted_at TEXT,
  claimed_at TEXT,
  claim_token TEXT,
  approval_id TEXT NOT NULL DEFAULT '',
  task_id TEXT,
  receipt TEXT NOT NULL DEFAULT '{}',
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX outbound_actions_due ON outbound_actions(state, next_attempt_at);
CREATE INDEX outbound_actions_actor ON outbound_actions(actor, created_at);
