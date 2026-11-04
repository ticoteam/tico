-- The Strategy Bot's tables under their names before v0.5.0 (ceo_merges, ceo_actions), and the triggers that mirrored
-- every write to strategy_merges and strategy_actions into them for one release. Nothing reads or writes them; the new
-- tables hold every row.
DROP TRIGGER IF EXISTS strategy_merges_to_old_insert;
DROP TRIGGER IF EXISTS strategy_merges_to_old_update;
DROP TRIGGER IF EXISTS strategy_merges_to_old_delete;
DROP TRIGGER IF EXISTS strategy_actions_to_old_insert;
DROP TRIGGER IF EXISTS strategy_actions_to_old_update;
DROP TRIGGER IF EXISTS strategy_actions_to_old_delete;
DROP TABLE IF EXISTS ceo_merges;
DROP TABLE IF EXISTS ceo_actions;
