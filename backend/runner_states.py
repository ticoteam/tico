"""A computer's runtime state: one `runner_states` row per runner, apart from its identity on `runners` (migration 0106).

Heartbeats, readiness changes and load samples write here, so a beat never rewrites the identity row. Readers that want
both read the `runners_with_state` view.
"""

COLUMNS = frozenset({"last_seen_at", "awake_since", "version", "capacity", "readiness", "readiness_at", "capabilities",
                     "checkout", "restart_since", "memory_hold", "last_sampled_at"})


def save(c, runner_id, **fields):
    """Set these state columns for one runner. A new runner gets its row from a trigger; one that somehow has none
    gets it here."""
    unknown = set(fields) - COLUMNS
    if unknown or not fields:
        raise ValueError(f"not runner state columns: {sorted(unknown) or 'none given'}")
    names = list(fields)
    c.execute(f"INSERT INTO runner_states(runner_id,{','.join(names)}) VALUES(?{',?' * len(names)}) "
              f"ON CONFLICT(runner_id) DO UPDATE SET {','.join(f'{n}=excluded.{n}' for n in names)}",
              (runner_id, *fields.values()))
