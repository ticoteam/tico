-- Pipeline F0: the tables the pipeline engine reads and writes. None has a foreign key to tasks, task_steps or
-- task_types: a pipeline and its stages are named by id only, so a purge of a task keeps working (backend/task_delete.py).
-- Every table is created empty; no code writes to it until the engine tracks land.

-- A stage's requirement (a rule the work must meet before the ticket leaves the stage). Its versions are kept in
-- stage_requirement_versions; `version` is the one in force.
CREATE TABLE IF NOT EXISTS stage_requirements (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    pipeline_stage_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('attachment','field','approval','run')),
    needs TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(needs)),
    reviewed_by TEXT NOT NULL CHECK (reviewed_by IN ('nobody','bot','person','bot_then_person')),
    reviewer_id TEXT,
    backup_reviewer_id TEXT,
    rubric TEXT,
    applies_to_label TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    enforced_by TEXT CHECK (enforced_by IS NULL OR enforced_by = 'review_gate'),
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retired_at TEXT,
    retired_by TEXT,
    CHECK (kind <> 'approval' OR reviewed_by = 'person')
);
CREATE UNIQUE INDEX IF NOT EXISTS stage_requirements_name ON stage_requirements(pipeline_stage_id, name) WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS stage_requirements_position ON stage_requirements(pipeline_stage_id, position);

-- Every definition a stage requirement has had, one row per version. Append-only.
CREATE TABLE IF NOT EXISTS stage_requirement_versions (
    id INTEGER PRIMARY KEY,
    stage_requirement_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition TEXT NOT NULL CHECK (json_valid(definition)),
    changed_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (stage_requirement_id, version)
);
CREATE TRIGGER IF NOT EXISTS stage_requirement_versions_no_update BEFORE UPDATE ON stage_requirement_versions
BEGIN SELECT RAISE(ABORT, 'stage_requirement_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS stage_requirement_versions_no_delete BEFORE DELETE ON stage_requirement_versions
BEGIN SELECT RAISE(ABORT, 'stage_requirement_versions is append-only'); END;

-- Evidence put against a requirement on one ticket: a file, a link, a check run or a receipt. digest and
-- work_version are what it was attached to; a later push or edit makes it stale rather than changing it.
CREATE TABLE IF NOT EXISTS requirement_attachments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    stage_requirement_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('blob','file','link','check_run','receipt')),
    blob_id TEXT,
    file_id TEXT,
    file_version INTEGER,
    task_link_id TEXT,
    url TEXT,
    digest TEXT NOT NULL,
    work_version INTEGER NOT NULL,
    commit_sha TEXT,
    attached_by TEXT,
    created_at TEXT NOT NULL,
    removed_at TEXT,
    removed_by TEXT
);
CREATE INDEX IF NOT EXISTS requirement_attachments_task ON requirement_attachments(task_id, stage_requirement_id, created_at);

-- Each judgement the engine made about a ticket at a stage (a rule, the LLM judge, a person, or a bot). Append-only:
-- only the outcome and the undo columns change, once each. Rows go only when their task is purged (backend/task_delete.py
-- deletes them with it), so there is no delete trigger.
CREATE TABLE IF NOT EXISTS stage_decisions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    pipeline_stage_id TEXT NOT NULL,
    stage_visit_id TEXT,
    stage_requirement_id TEXT,
    requirement_version INTEGER,
    requirement_attachment_id TEXT,
    attachment_digest TEXT,
    work_version INTEGER,
    judge TEXT NOT NULL CHECK (judge IN ('rule','llm','person','bot')),
    judge_actor TEXT,
    verdict TEXT NOT NULL CHECK (verdict IN ('met','failed','unsure','sent_back','override','withdrawn')),
    confidence REAL,
    reason TEXT CHECK (reason IS NULL OR length(CAST(reason AS BLOB)) <= 500),
    route_next_to TEXT,
    inputs_digest TEXT,
    judge_call_id TEXT,
    cost_cents INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,
    outcome_at TEXT,
    undone_at TEXT,
    undone_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS stage_decisions_task ON stage_decisions(task_id, stage_requirement_id, created_at);
CREATE INDEX IF NOT EXISTS stage_decisions_requirement ON stage_decisions(stage_requirement_id, created_at);
CREATE TRIGGER IF NOT EXISTS stage_decisions_append_only BEFORE UPDATE ON stage_decisions
WHEN NEW.id IS NOT OLD.id OR NEW.task_id IS NOT OLD.task_id OR NEW.pipeline_stage_id IS NOT OLD.pipeline_stage_id
  OR NEW.stage_visit_id IS NOT OLD.stage_visit_id OR NEW.stage_requirement_id IS NOT OLD.stage_requirement_id
  OR NEW.requirement_version IS NOT OLD.requirement_version
  OR NEW.requirement_attachment_id IS NOT OLD.requirement_attachment_id
  OR NEW.attachment_digest IS NOT OLD.attachment_digest OR NEW.work_version IS NOT OLD.work_version
  OR NEW.judge IS NOT OLD.judge OR NEW.judge_actor IS NOT OLD.judge_actor OR NEW.verdict IS NOT OLD.verdict
  OR NEW.confidence IS NOT OLD.confidence OR NEW.reason IS NOT OLD.reason
  OR NEW.route_next_to IS NOT OLD.route_next_to OR NEW.inputs_digest IS NOT OLD.inputs_digest
  OR NEW.judge_call_id IS NOT OLD.judge_call_id OR NEW.cost_cents IS NOT OLD.cost_cents
  OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'stage_decisions is append-only: only its outcome and undo change'); END;

-- A claim by a reviewer on one requirement of one ticket while they review it. One open claim per requirement.
CREATE TABLE IF NOT EXISTS review_claims (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    stage_requirement_id TEXT NOT NULL,
    claimed_by TEXT NOT NULL,
    work_version INTEGER,
    attachment_digest TEXT,
    claimed_at TEXT NOT NULL,
    expires_at TEXT,
    released_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS review_claims_open ON review_claims(task_id, stage_requirement_id) WHERE released_at IS NULL;

-- A stage that sends its tickets on to another pipeline: `move` hands the ticket over, `spawn` makes a new one.
-- One live link per stage. A handoff records a spawn: the ticket it came from and the one it made.
CREATE TABLE IF NOT EXISTS pipeline_links (
    id TEXT PRIMARY KEY,
    from_pipeline_id TEXT NOT NULL,
    from_pipeline_stage_id TEXT NOT NULL,
    to_pipeline_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('move','spawn')),
    return_stage_id TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    retired_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_links_open ON pipeline_links(from_pipeline_stage_id) WHERE retired_at IS NULL;
CREATE TABLE IF NOT EXISTS pipeline_handoffs (
    id TEXT PRIMARY KEY,
    pipeline_link_id TEXT NOT NULL,
    source_task_id TEXT NOT NULL,
    target_task_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    returned_at TEXT
);

-- One pipeline manager pass, and what it did on each ticket it looked at.
CREATE TABLE IF NOT EXISTS pipeline_manager_runs (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('ok','failed','skipped')),
    exceptions_count INTEGER NOT NULL DEFAULT 0,
    actions_count INTEGER NOT NULL DEFAULT 0,
    model_called INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS pipeline_manager_runs_pipeline ON pipeline_manager_runs(pipeline_id, started_at);
CREATE TABLE IF NOT EXISTS pipeline_manager_actions (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    pipeline_manager_run_id TEXT NOT NULL,
    task_id TEXT,
    stage_visit_id TEXT,
    stage_requirement_id TEXT,
    action TEXT NOT NULL CHECK (action IN ('ask','ask_backup','refile','hold','release_hold','open_botops','forward','propose_rule')),
    target_actor TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_manager_actions_once ON pipeline_manager_actions(stage_visit_id, stage_requirement_id, action) WHERE action IN ('ask','ask_backup','refile');
