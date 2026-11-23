ALTER TABLE attempt_subscriptions ADD COLUMN delivery TEXT NOT NULL DEFAULT '';
ALTER TABLE attempt_subscriptions ADD COLUMN parent_grant TEXT;
