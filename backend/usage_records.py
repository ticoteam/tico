"""Immutable per-run accounting, including both sides of a fallback."""

# New reports retain each primary/fallback segment. Legacy rows remain visible, without guessing their login or effort.
RUNS = """(SELECT t.id,t.bot,t.started,t.finished,t.task_id,s.input_tokens,s.cached_tokens,s.output_tokens,
 s.model,s.provider,s.est_cost_usd,s.billing,s.runtime,s.harness,s.effort,s.profile,s.est_cost_low
 FROM turns t JOIN turn_usage_segments s ON s.turn_id=t.id
 UNION ALL SELECT t.id,t.bot,t.started,t.finished,t.task_id,t.input_tokens,t.cached_tokens,t.output_tokens,
 t.model,t.provider,t.est_cost_usd,t.billing,NULL,NULL,NULL,NULL,NULL FROM turns t
 WHERE NOT EXISTS (SELECT 1 FROM turn_usage_segments s WHERE s.turn_id=t.id))"""

# Providers that name a seller, not the model's maker: Cursor `auto` and OpenRouter pick the model themselves.
RESELLERS = {"cursor", "openrouter"}


def family(part):
    """The model maker behind one segment (`anthropic`, `openai`, ...), from Tico's model catalog only. A model the
    catalog does not list is unknown (None) whatever ran it: Claude Code or Codex can run another maker's model."""
    from . import providers
    maker = (providers.MODEL_BY_ID.get(part.get("model") or "") or {}).get("provider")
    return maker if maker and maker not in RESELLERS else None


def ran_on(c, turn_ids):
    """Per run, the model that actually did the work: each counted segment, the fallback named, and the
    one maker when every segment agrees. A run's bot config names what was asked for, not what ran."""
    ids = list(turn_ids)
    parts, fallbacks = {}, {}
    for start in range(0, len(ids), 500):       # SQLite caps the parameters of one statement
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(f"SELECT turn_id,model,provider,runtime,harness FROM turn_usage_segments "
                             f"WHERE turn_id IN ({marks}) ORDER BY turn_id,position", chunk):
            parts.setdefault(row["turn_id"], []).append(
                {k: row[k] for k in ("model", "provider", "runtime", "harness")})
        for row in c.execute(f"SELECT id,model,provider FROM turns WHERE id IN ({marks})", chunk):
            if row["id"] not in parts and (row["model"] or row["provider"]):
                parts[row["id"]] = [{"model": row["model"], "provider": row["provider"], "runtime": None, "harness": None}]
        fallbacks.update(c.execute(f"SELECT id,json_extract(result_json,'$.fallback') FROM attempts "
                                   f"WHERE id IN ({marks})", chunk).fetchall())
    out = {}
    for tid in ids:
        segments = [{**p, "family": family(p)} for p in parts.get(tid, [])]
        makers = {s["family"] for s in segments}
        out[tid] = {"segments": segments, "fallback": fallbacks.get(tid),
                    "family": makers.pop() if len(makers) == 1 else None}
    return out
