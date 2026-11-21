-- A page approval may name several approvers (task_requirements.approvers): person_ids lists everyone who may approve,
-- and person_id, kept for one approver, holds the first of them. The first answer settles it for all of them.
ALTER TABLE requirements ADD COLUMN person_ids TEXT CHECK (person_ids IS NULL OR (kind = 'page'
  AND json_valid(person_ids) AND json_type(person_ids) = 'array' AND person_id IS json_extract(person_ids, '$[0]')));
CREATE INDEX requirements_person_ids ON requirements(id) WHERE person_ids IS NOT NULL AND withdrawn_at IS NULL;
