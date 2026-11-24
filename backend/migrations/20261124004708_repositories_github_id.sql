-- GitHub's own id for a repository, filled by the repository sync: a rename or a move keeps it, so the row it was
-- (and whether it is In use) follows the repository instead of counting as first sight under its new name.
ALTER TABLE repositories ADD COLUMN github_id INTEGER;
