-- A 1:1 voice call's gathered turn keeps its joined question (backend/call_voice.py): its runner request, a resident
-- turn's fallback delivery and an empty reply's retry all ask it. NULL for every other turn.
ALTER TABLE live_meeting_turns ADD COLUMN question TEXT;
