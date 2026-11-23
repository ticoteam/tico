-- Legacy upkeep has no exit binding. A routed job keeps the selection it was issued under.
ALTER TABLE claude_login_leases ADD COLUMN exit_generation INTEGER;
ALTER TABLE attempt_subscriptions ADD COLUMN exit_generation INTEGER;
ALTER TABLE subscription_connects ADD COLUMN exit_generation INTEGER;
