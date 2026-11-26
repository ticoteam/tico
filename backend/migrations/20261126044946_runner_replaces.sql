-- A cloud machine made again: the computer its new one replaced, whose turns its Performance still lists.
ALTER TABLE runners ADD COLUMN replaces TEXT;
-- An overload moved there from the computer it replaced: it ended where that computer stopped.
ALTER TABLE perf_overloads ADD COLUMN carried_from TEXT;
