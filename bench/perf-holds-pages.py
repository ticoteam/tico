"""Measures the pages that ask holds.why of every bot (Needs you, the Floor, Health) on perf-hub-overload's fictional
company with the hosted TIDY company's hold shape (Nov 19): 165 bots, five in six assigned over 10 computers, 9 of them
given a Claude and a Codex plan, open usage-limit holds past their end on a plan and on a computer's own login, and 20
paused bots. While any login is held, every bot's usage limit is judged (holds._usage_limit, execution.cooling_off).

    nice -n 10 .venv/bin/python bench/perf-holds-pages.py

Env as bench/perf-hub-overload.py (OPEN, CLOSED, THREADS, RUNS, DB, PATHS, TOKENS, ANSWERS, STATEMENTS, PROFILE);
PATHS defaults to the three pages. Only fictional data. Never point it at a real hub.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PATHS", "/api/v2/needs-you,/api/v2/company/floor,/api/v2/health")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_spec = importlib.util.spec_from_file_location("overload", Path(__file__).with_name("perf-hub-overload.py"))
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)  # imports conftest: the fictional registry and isolated benchmark settings

from backend import holds, runner_states
from backend.store import H

PAST = "2026-11-16T20:00:00.000000Z"
_seed = B.seed


def seed(api):
    _seed(api)
    ready = {
        "schema_version": 1,
        "runtimes": {rt: {"installed": True, "authenticated": "ready"} for rt in ("claude", "codex")},
    }
    with api.app.state.store.transaction() as c:
        for n, slug in enumerate(B.BOTS):
            runner = f"r{n % 10}"
            c.execute(
                "INSERT OR IGNORE INTO runners(id,label,owner_id,token_hash,created_at) VALUES(?,?,'ana',?,?)",
                (runner, "box" + runner, "h" + runner, H.now()),
            )
            if n % 6 != 5:
                c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, runner, H.now()))
            else:
                c.execute("DELETE FROM assignments WHERE bot=?", (slug,))
        for sid, kind in (("plan-claude", "claude_token"), ("plan-codex", "codex_login")):
            c.execute(
                "INSERT OR IGNORE INTO model_subscriptions(id,name,kind,sealed,status,added_by,created_at,updated_at) "
                "VALUES(?,?,?,'','working','human:ana',?,?)",
                (sid, sid, kind, H.now(), H.now()),
            )
        for n in range(10):
            runner_states.save(c, f"r{n}", readiness=json.dumps(ready))
            for runtime, sid in (("claude", "plan-claude"), ("codex", "plan-codex"))[: 2 if n < 9 else 0]:
                c.execute(
                    "INSERT OR REPLACE INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated_at) "
                    "VALUES(?,?,?,'human:ana',?)",
                    (f"r{n}", runtime, sid, H.now()),
                )
        holds.put(c, "usage_limit", "login", "sub:plan-claude|claude", actor="human:ana", until_at=PAST)
        holds.put(c, "usage_limit", "login", "r0||claude", actor="human:ana", until_at=PAST)
        for slug in B.BOTS[100:120]:
            holds.put(c, "paused", "bot", slug, actor="human:ana")
            c.execute("UPDATE bots SET state='paused' WHERE id=?", (slug,))


B.seed = seed

if __name__ == "__main__":
    B.main()
