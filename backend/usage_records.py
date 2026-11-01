"""Immutable per-run accounting, including both sides of a fallback."""
import json

# New reports retain each primary/fallback segment. Legacy rows remain visible, without guessing their login or effort.
RUNS = """(SELECT t.id,t.bot,t.started_at,t.finished_at,t.task_id,s.input_tokens,s.cached_tokens,s.output_tokens,
 s.model,s.provider,s.est_cost_usd,s.billing,s.runtime,s.harness,s.effort,s.profile,s.est_cost_low,
 NULL AS usage_reason
 FROM turns t JOIN turn_usage_segments s ON s.turn_id=t.id
 UNION ALL SELECT t.id,t.bot,t.started_at,t.finished_at,t.task_id,t.input_tokens,t.cached_tokens,t.output_tokens,
 t.model,t.provider,t.est_cost_usd,t.billing,NULL,NULL,NULL,NULL,NULL,t.usage_reason FROM turns t
 WHERE NOT EXISTS (SELECT 1 FROM turn_usage_segments s WHERE s.turn_id=t.id))"""

# Providers that name a seller, not the model's maker: Cursor `auto` and OpenRouter pick the model themselves.
RESELLERS = {"cursor", "openrouter"}


def family(part):
    """The model maker behind one segment (`anthropic`, `openai`, ...), from Tico's model catalog only. A model the
    catalog does not list is unknown (None) whatever ran it: Claude Code or Codex can run another maker's model."""
    from . import providers
    maker = (providers.MODEL_BY_ID.get(part.get("model") or "") or {}).get("provider")
    return maker if maker and maker not in RESELLERS else None


def _record(text):
    try:
        return json.loads(text) if text else None
    except (TypeError, ValueError):
        return None


def ran_on(c, turn_ids):
    """Per run, the model that actually did the work: each counted segment, the fallback named, and the
    one maker when every segment agrees. A run's bot config names what was asked for, not what ran."""
    ids = list(turn_ids)
    parts, fallbacks, chosen = {}, {}, {}
    for start in range(0, len(ids), 500):       # SQLite caps the parameters of one statement
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(f"SELECT turn_id,model,provider,runtime,harness,effort FROM turn_usage_segments "
                             f"WHERE turn_id IN ({marks}) ORDER BY turn_id,position", chunk):
            parts.setdefault(row["turn_id"], []).append(
                {k: row[k] for k in ("model", "provider", "runtime", "harness", "effort")})
        for row in c.execute(f"SELECT id,model,provider FROM turns WHERE id IN ({marks})", chunk):
            if row["id"] not in parts and (row["model"] or row["provider"]):
                parts[row["id"]] = [{"model": row["model"], "provider": row["provider"], "runtime": None, "harness": None,
                                     "effort": None}]
        for row in c.execute(f"SELECT id,json_extract(result,'$.fallback'),model FROM attempts "
                             f"WHERE id IN ({marks})", chunk):
            fallbacks[row[0]] = row[1]
            chosen[row[0]] = _record(row[2])
    out = {}
    for tid in ids:
        segments = [{**p, "family": family(p)} for p in parts.get(tid, [])]
        makers = {s["family"] for s in segments}
        # The bot's task model, the fallback from it, or a check kept over it, in plain words (backend/task_models.py).
        picked = chosen.get(tid) if isinstance(chosen.get(tid), dict) else None
        out[tid] = {"segments": segments, "fallback": fallbacks.get(tid),
                    "family": makers.pop() if len(makers) == 1 else None,
                    "task_model": {"source": picked.get("source"), "note": picked.get("note")} if picked else None}
    return out
