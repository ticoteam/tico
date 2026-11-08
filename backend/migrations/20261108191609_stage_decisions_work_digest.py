"""The work a judge's verdict was judged against (backend/pipeline_judges.py work_digest): a digest of the ticket's
title, spec, and the repo, PR and PR author it was told. A judge's verdict stands only while the work still has that
digest, so a verdict made for another PR or campaign is judged again. NULL on rows the judge did not write.
"""

TRIGGER = """
CREATE TRIGGER stage_decisions_append_only BEFORE UPDATE ON stage_decisions
WHEN NEW.id IS NOT OLD.id OR NEW.task_id IS NOT OLD.task_id OR NEW.pipeline_stage_id IS NOT OLD.pipeline_stage_id
  OR NEW.stage_visit_id IS NOT OLD.stage_visit_id OR NEW.stage_requirement_id IS NOT OLD.stage_requirement_id
  OR NEW.requirement_version IS NOT OLD.requirement_version
  OR NEW.requirement_attachment_id IS NOT OLD.requirement_attachment_id
  OR NEW.attachment_digest IS NOT OLD.attachment_digest OR NEW.work_version IS NOT OLD.work_version
  OR NEW.judge IS NOT OLD.judge OR NEW.judge_actor IS NOT OLD.judge_actor OR NEW.verdict IS NOT OLD.verdict
  OR NEW.confidence IS NOT OLD.confidence OR NEW.reason IS NOT OLD.reason
  OR NEW.route_next_to IS NOT OLD.route_next_to OR NEW.inputs_digest IS NOT OLD.inputs_digest
  OR NEW.judge_call_id IS NOT OLD.judge_call_id OR NEW.cost_cents IS NOT OLD.cost_cents
  OR NEW.created_at IS NOT OLD.created_at OR NEW.work_digest IS NOT OLD.work_digest
BEGIN SELECT RAISE(ABORT, 'stage_decisions is append-only: only its outcome and undo change'); END;
"""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(stage_decisions)")}
    if not columns:
        return
    if "work_digest" not in columns:
        conn.execute("ALTER TABLE stage_decisions ADD COLUMN work_digest TEXT")
    conn.execute("DROP TRIGGER IF EXISTS stage_decisions_append_only")
    conn.execute(TRIGGER.strip().removesuffix(";"))
    # The judge looks up its latest answer on an attachment every pass (pipeline_judges._waiting).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS stage_decisions_attachment ON stage_decisions(requirement_attachment_id, created_at)"
    )
