CREATE TABLE hq_help_tasks (
    task_id TEXT PRIMARY KEY NOT NULL REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE TABLE hq_company_refs (
    ref_id TEXT PRIMARY KEY NOT NULL REFERENCES entity_external_refs(id) ON DELETE CASCADE
);

INSERT INTO hq_help_tasks(task_id)
SELECT DISTINCT w.task_id FROM watcher_items w JOIN watcher_refs r ON r.bot=w.bot AND r.key=w.key
JOIN tasks t ON t.id=w.task_id
WHERE w.key GLOB 'hq:TK-*' AND r.ref GLOB 'hq-origin:*';
