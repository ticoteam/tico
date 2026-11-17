-- Company › Floor's in and out rates (backend/company_floor.py) read the visits of the last week: without this they
-- scanned every visit. Covering, so the rates read the index alone.
CREATE INDEX IF NOT EXISTS visits_entered ON visits(entered_at, stage_id, entered_how, task_id);
