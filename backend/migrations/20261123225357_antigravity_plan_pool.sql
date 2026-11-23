-- An Antigravity plan joins a person's pool: the pool read (backend/agent_subscriptions.py) names every plan kind
-- (model_subscriptions.POOL_KINDS), so the partial index it reads through names the same ones.
DROP INDEX IF EXISTS model_subscriptions_pool;
CREATE INDEX model_subscriptions_pool ON model_subscriptions(owner_id,kind,id)
  WHERE kind IN ('claude_token','claude_login','codex_login','antigravity_login');
