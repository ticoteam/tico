-- Which delivery holds a claimed live-meeting turn (backend/live_meetings.py claim_runner_turn): `job:<job id>` for the
-- runner job whose lease claimed it, `direct:<claim id>` for a claim through the meeting API. Only that owner may answer
-- the turn, and a direct claim nobody answers within live_meetings.CLAIM_ABANDON_SECONDS ends as `claim_abandoned`.
-- NULL for turns claimed before this column existed and for unclaimed ones.
ALTER TABLE live_meeting_turns ADD COLUMN claim_owner TEXT;
