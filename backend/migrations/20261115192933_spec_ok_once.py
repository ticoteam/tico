"""Product's spec is OK'd by a person once, in OK spec (docs/pipelines-v3.md, "Product"). Spec's page is Jev's to check.

The Product of the pair (pipeline_settings.LEGACY) names a person on its Spec page: there it was the spec's only OK
(ROLES["product"]["ok"]). A company that gave that pipeline the one-pipeline shape by hand, with Related input and an
OK spec stage whose people-only page is the same Spec page, kept that person on Spec: the spec waited on them in Spec
although Jev passed it, and they were asked to OK the same version again in OK spec.

The person comes off Spec's page only where the stage is still the default one: the built-in `product-spec` stage, its
one requirement the "Spec page" page, not people only, naming a person; and a later stage of the same pipeline still
has a people-only page with the same line and rubric. A stage a company changed in any other way keeps its person. The
pipeline's settings version goes up, so an editor open on the old record must read it again before it saves.

Running it again changes nothing.
"""

from datetime import datetime, timezone

SPEC_STAGE = "product-spec"
LINE = "Spec page"

# The default Spec page, alone on its stage, naming a person, with a later people-only page of the same line and
# rubric in the same pipeline (the OK spec stage).
FOUND = (
    "SELECT r.id, s.pipeline_id FROM requirements r JOIN stages s ON s.id = r.stage_id "
    "WHERE s.id = ? AND s.kind = 'work' AND s.retired_at IS NULL "
    "AND r.visit_id IS NULL AND r.withdrawn_at IS NULL AND r.kind = 'page' AND r.line = ? "
    "AND r.who_oks <> 'people_only' AND r.person_id LIKE 'human:%' AND json_valid(r.settings) "
    "AND (SELECT COUNT(*) FROM requirements o WHERE o.stage_id = s.id AND o.visit_id IS NULL "
    "AND o.withdrawn_at IS NULL) = 1 "
    "AND EXISTS (SELECT 1 FROM stages k JOIN requirements q ON q.stage_id = k.id "
    "WHERE k.pipeline_id = s.pipeline_id AND k.kind = 'work' AND k.retired_at IS NULL AND k.position > s.position "
    "AND q.visit_id IS NULL AND q.withdrawn_at IS NULL AND q.kind = 'page' AND q.who_oks = 'people_only' "
    "AND q.line = r.line AND json_valid(q.settings) "
    "AND json_extract(q.settings, '$.rubric') IS json_extract(r.settings, '$.rubric'))"
)


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def up(conn):
    if not all(_table(conn, name) for name in ("pipelines", "stages", "requirements")):
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    for requirement_id, pipeline_id in conn.execute(FOUND, (SPEC_STAGE, LINE)).fetchall():
        conn.execute("UPDATE requirements SET person_id = NULL WHERE id = ?", (requirement_id,))
        conn.execute(
            "UPDATE pipelines SET settings_version = settings_version + 1, updated_at = ? WHERE id = ?",
            (now, pipeline_id),
        )
