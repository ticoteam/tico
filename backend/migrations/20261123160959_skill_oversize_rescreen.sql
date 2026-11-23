-- A pending proposal left not screened as `oversize` is screened again, once: an update too big for the state with its
-- diff is now screened on the whole new skill without it (backend/skill_screening.py `screen`).
UPDATE skill_proposals SET screen_status = 'pending', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE status = 'pending' AND screen_status = 'not_screened'
  AND (SELECT s.reason FROM skill_screenings s WHERE s.proposal_id = skill_proposals.id
       ORDER BY s.created_at DESC, s.rowid DESC LIMIT 1) = 'oversize';
