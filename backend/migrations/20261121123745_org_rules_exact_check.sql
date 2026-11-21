-- A rule's stateless typed check (backend/org_rules.py `exact_check_in`): an allow list or a dollar cap on one action,
-- as JSON, checked exactly by org_rule_check.exact_check. NULL for a rule the decision model reads.
ALTER TABLE org_rules ADD COLUMN exact_check TEXT;
