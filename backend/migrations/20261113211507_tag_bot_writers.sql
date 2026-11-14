-- Bots a task mover lets attach and remove this one tag on any task they can read (hubdb._label_writer), as a JSON
-- list of bot actors. Only a mover changes it; the tag's owner cannot, so a bot never grants itself a tag.
ALTER TABLE tags ADD COLUMN bot_writers TEXT NOT NULL DEFAULT '[]';
