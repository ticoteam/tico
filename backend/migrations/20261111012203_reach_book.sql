-- Who bots may book (decision 11, docs/reach.md): a setting's book, NULL takes it from above. Values anyone, nearby or
-- none; the default is anyone. The values are checked in backend/reach.py, as the other fields' are.
ALTER TABLE reach_settings ADD COLUMN book TEXT;
