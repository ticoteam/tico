"""A run's measured segment, written as turn_finish records one (backend/usage_facts.py), for tests that build runs by
hand. The run's bot, task and times come from its turns row when it has one."""
from backend import usage_facts
from backend.store import H


def segment(c, run_id, position=0, **columns):
    row = c.execute("SELECT bot, task_id, started_at, finished_at FROM turns WHERE id=?", (run_id,)).fetchone()
    at = H.now()
    if "est_cost_usd" in columns:
        columns["cost_nano"] = usage_facts.nano(columns.pop("est_cost_usd"))
    values = {name: None for name in usage_facts.COLUMNS}
    values.update({"source": "estimate", "run_id": run_id, "position": position, "input_tokens": 0,
                   "cached_tokens": 0, "output_tokens": 0, "bot_id": row[0] if row else None,
                   "task_id": row[1] if row else None, "started_at": (row[2] if row else None) or at,
                   "metered_at": (row[3] if row else None) or at, "created_at": at, **columns})
    c.execute(usage_facts.INSERT, values)
