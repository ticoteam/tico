-- A by-hand stage's setting: a person's request on its first visit moves on by itself when the next stage has room
-- (never_stuck._requests). Off by default: an idea waits until someone starts it. On for the Product preset's Ideas,
-- the one stage that did this before it was a setting, so Product keeps doing it.
ALTER TABLE stages ADD COLUMN requests_move_on INTEGER NOT NULL DEFAULT 0 CHECK (requests_move_on IN (0, 1));
UPDATE stages SET requests_move_on = 1
WHERE kind = 'work' AND by_hand = 1 AND pipeline_id IN (SELECT id FROM pipelines WHERE preset = 'product');
