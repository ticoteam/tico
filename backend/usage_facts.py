"""Every metered segment of usage, one append-only row each (docs/usage.md, Where the numbers come from).

A finished run writes one `estimate` fact per primary or fallback segment: its tokens, model and maker, how it was
billed and its list-price estimate in nanodollars. A run that counted nothing writes none (its `turns` row says why).
The Tico AI Gateway's meter is the amount of record for Gateway-billed spend: each read of it (backend/finance/usage.py)
adds a `meter` fact per model for what the meter moved since the last one. Both carry the same `meter_key`
(`gateway:<YYYY-MM>:<model>`), so a month's estimate and its meter sit side by side, and `reconciled` prefers the meter.

Rows are never changed or removed: a correction from the meter is a new row with a negative amount."""

NANO = 10**9


def nano(usd):
    """A USD amount as whole nanodollars; None stays None (a model with no list price)."""
    if usd is None:
        return None
    return int(round(float(usd) * NANO))


def gateway_billed(model, billing):
    """Whether a segment is on the Gateway's meter: a Tico model not run on a subscription (gateway_connection.is_tico)."""
    return str(model or "").startswith("tico-") and (billing or "api") != "subscription"


def meter_key(month, model):
    return f"gateway:{month}:{model}"


COLUMNS = ("source", "run_id", "position", "bot_id", "task_id", "provider", "model", "family", "runtime", "harness",
           "effort", "profile", "billing", "input_tokens", "cached_tokens", "output_tokens", "cost_nano", "low_estimate",
           "meter_key", "gateway_key_id", "install_id", "started_at", "metered_at", "created_at")
INSERT = (f"INSERT OR IGNORE INTO usage_facts({', '.join(COLUMNS)}) "
          f"VALUES({', '.join(':' + name for name in COLUMNS)})")


def record_run(c, run, usage, at):
    """The estimate facts of one finished run (`run`: its turns row; `usage`: what turn_finish was given). The first
    report stands: a run finished again (a late result for an attempt already finished) adds nothing once it has facts."""
    if not usage or not run:
        return 0
    if c.execute("SELECT 1 FROM usage_facts WHERE run_id=? AND source='estimate' LIMIT 1", (run["id"],)).fetchone():
        return 0
    from .usage_records import family
    parts = usage.get("segments") or [usage]
    for position, part in enumerate(parts):
        model, billing = part.get("model"), part.get("billing")
        low = part.get("est_cost_low")
        c.execute(INSERT, {
            "source": "estimate", "run_id": run["id"], "position": position, "bot_id": run["bot"],
            "task_id": run["task_id"], "provider": part.get("provider"), "model": model, "family": family(part),
            "runtime": part.get("runtime"), "harness": part.get("harness"), "effort": part.get("effort"),
            "profile": part.get("profile"), "billing": billing,
            "input_tokens": int(part.get("input_tokens") or 0), "cached_tokens": int(part.get("cached_tokens") or 0),
            "output_tokens": int(part.get("output_tokens") or 0), "cost_nano": nano(part.get("est_cost_usd")),
            "low_estimate": None if low is None else int(bool(low)),
            "meter_key": meter_key(at[:7], model) if gateway_billed(model, billing) else None,
            "gateway_key_id": part.get("gateway_key_id"), "install_id": part.get("install_id"),
            "started_at": run["started_at"] or at, "metered_at": at, "created_at": at})
    return len(parts)


def record_meter(c, month, as_of, charged, at):
    """One read of the Gateway's meter for `month` (YYYY-MM): {model: month-to-date charged nanodollars}. Each model
    gets a fact for what moved since the facts already kept; a read older than the newest kept for the month adds
    nothing, so an out-of-order fetch never walks the total back. The facts added."""
    newest = c.execute("SELECT max(metered_at) FROM usage_facts WHERE source='meter' AND meter_key LIKE ?",
                       (meter_key(month, "%"),)).fetchone()[0]
    if newest and as_of < newest:
        return 0
    kept = {row[0]: int(row[1] or 0) for row in c.execute(
        "SELECT meter_key, sum(cost_nano) FROM usage_facts WHERE source='meter' AND meter_key LIKE ? GROUP BY meter_key",
        (meter_key(month, "%"),))}
    from .usage_records import family
    added = 0
    for model in sorted(charged):
        key = meter_key(month, model)
        moved = int(charged[model]) - kept.get(key, 0)
        if not moved:
            continue
        c.execute(INSERT, {
            **{name: None for name in COLUMNS}, "source": "meter", "position": 0, "model": model,
            "family": family({"model": model}), "billing": "api", "input_tokens": 0, "cached_tokens": 0,
            "output_tokens": 0, "cost_nano": moved, "meter_key": key, "started_at": f"{month}-01T00:00:00.000000Z",
            "metered_at": as_of, "created_at": at})
        added += 1
    return added


def reconciled(c, month):
    """A month's Gateway-billed spend per model, estimate beside meter: {model: {estimated_nano, metered_nano,
    amount_nano}}. The amount is the meter's where it has read the model, else the estimate."""
    out = {}
    for model, estimated, metered in c.execute(
            "SELECT model, sum(CASE WHEN source='estimate' THEN coalesce(cost_nano, 0) END), "
            "sum(CASE WHEN source='meter' THEN cost_nano END) FROM usage_facts WHERE meter_key LIKE ? "
            "GROUP BY meter_key ORDER BY model", (meter_key(month, "%"),)):
        out[model] = {"estimated_nano": int(estimated or 0), "metered_nano": None if metered is None else int(metered),
                      "amount_nano": int(metered if metered is not None else estimated or 0)}
    return out
