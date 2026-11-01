"""Settings and admission for Nightly memory tidy. No model calls or repository writes.

The execution ticket consumes eligible() inside its admission transaction, immediately
before filing work. A missing executor fails closed, including Run now.
"""
import json

from . import providers, system_routines, task_models
from .store import H, Problem

KEY = "memory_tidy"
PILOTS_KEY = "memory_tidy:pilots"
MAX_PILOTS = 3
# Product's v1.2 selection. A saved empty list remains empty; never overwrite an owner's edit.
DEFAULT_PILOTS = [
    {"bot": "pr-reviewer", "model": "codex:gpt-6.1-sol", "effort": "low"},
    {"bot": "botops", "model": "claude:claude-sonnet-5-5", "effort": "low"},
    {"bot": "builder-claude", "model": "claude:claude-sonnet-5-5", "effort": "low"},
]


def pilots(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (PILOTS_KEY,)).fetchone()
    return json.loads(row[0]) if row else [dict(p) for p in DEFAULT_PILOTS if H.bot(c, p["bot"])]


def model_floor(row):
    return "haiku" not in (row.get("id", "") + " " + row.get("label", "")).lower()


def model_options(c, settings):
    enabled = providers.load(c, settings)["enabled"]
    return [{"model": r["runtime"] + ":" + r["id"], "label": r["label"], "efforts": r.get("efforts") or []}
            for r in providers.MODEL_CATALOG if not r.get("deprecated") and not r.get("meeting")
            and "low" in (r.get("efforts") or ()) and model_floor(r) and providers.runtime_enabled(r["runtime"], enabled, r)]


def eligible(c, settings, bot, *, proposed=None):
    """One eligibility decision for setup and run admission.

    Saving checks membership in the proposed list even while off; running checks the
    saved list and switch. Setup raises the refusal for the editor; runs return None.
    Execution calls this inside its spend/admission transaction, never from a snapshot.
    """
    if proposed is None and not system_routines.config_of(c, KEY)["on"]:
        return None
    entry = next((p for p in (proposed if proposed is not None else pilots(c)) if p["bot"] == bot), None)
    if entry is None:
        return None
    try:
        if not H.bot(c, bot):
            raise Problem("invalid", "Choose an existing bot by its slug", 422)
        runtime, separator, model = entry["model"].partition(":")
        if not separator or not runtime or not model:
            raise Problem("model", "Choose a named model as runtime:model", 422)
        row = providers.MODEL_BY_ID.get(model) or {}
        if not model_floor(row):
            raise Problem("model_floor", "Nightly memory tidy cannot use a Haiku-tier model", 422)
        offered = {r["model"] for r in model_options(c, settings)}
        if entry["model"] not in offered:
            raise Problem("model", "Choose a current model enabled in Settings > Computers > Models", 422)
        if entry["effort"] != "low":
            raise Problem("effort", "Nightly memory tidy runs at low reasoning effort", 422)
        task_models.choice_of(model, "low")
        return dict(entry)
    except Problem:
        if proposed is not None:
            raise
        return None


def validate(c, settings, entries):
    if len(entries) > MAX_PILOTS:
        raise Problem("invalid", "The pilot includes at most three bots", 422)
    if len({p["bot"] for p in entries}) != len(entries):
        raise Problem("invalid", "Choose each bot once by its slug", 422)
    return [eligible(c, settings, p["bot"], proposed=entries) for p in entries]


def save(c, settings, actor, entries):
    entries = validate(c, settings, entries)
    before = pilots(c)
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (PILOTS_KEY, json.dumps(entries)))
    if before != entries:
        H.event(c, actor, "memory_tidy.pilots_updated", KEY, {"before": before, "after": entries})


UNAVAILABLE = "Nightly memory tidy passes are not available yet"


def nightly(store, auth, at):
    from . import memory_tidy_runs
    from .updates import ZONE
    with store.transaction() as c:
        # Withdrawal is maintenance, including while the switch is off. A pass
        # never depends on the bot having received a nightly learnings packet.
        memory_tidy_runs.sweep(c, at)
        cfg = system_routines.config_of(c, KEY)
        if not cfg['on']:
            return None
        local = at.astimezone(ZONE)
        hour, minute = map(int, (cfg.get('at') or '03:30').split(':'))
        if (local.hour, local.minute) < max((3, 0), (hour, minute)):
            return None
    # Revisit after each settlement to admit the next pilot. A log line from the
    # first pilot cannot suppress all remaining pilots for the rest of the night.
    return run_now(store, auth, H.KEEPER, at=at)


def run_now(store, auth, actor, *, at=None):
    from . import memory_tidy_runs
    with store.transaction() as c:
        if not system_routines.config_of(c, KEY)['on']:
            raise Problem('conflict', 'Nightly memory tidy is off. Switch it on to run it.', 409)
        rows = memory_tidy_runs.admit(c, store.settings, at or H.parse_ts(H.now()))
        return any(c.execute("SELECT status FROM memory_tidy_passes WHERE id=?", (ident,)).fetchone()[0] == 'filed'
                   for ident in rows)
