-- An Antigravity plan joins a person's pool: the pool read (backend/agent_subscriptions.py) names every plan kind
-- (model_subscriptions.POOL_KINDS), so the partial index it reads through names the same ones. A new name, because
-- 20261122220441_agent_subscriptions creates model_subscriptions_pool unconditionally and pending migrations run in
-- any order; the older three-kind index stays for plan_exits.PLAN_OWNERS.
CREATE INDEX model_subscriptions_plan_pool ON model_subscriptions(owner_id,kind,id)
  WHERE kind IN ('claude_token','claude_login','codex_login','antigravity_login');
