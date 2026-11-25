"""The Tools registry's reads on a big company, before and after (PR #5233 review S8): SQL plans and median timings.

    python3 -m backend.tests.tool_guides_bench        (RunsOn: python3 scripts/remote.py -- python3 -m ...)

300 API tools over 30 vendors, a key each, 200k proxy calls and a key read per tool and agent in the last 30 days; the
old module is the one at 326c39df03 (it built the whole Tools index), read with git."""

import importlib.util
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from backend import tool_guides as new
from backend import tool_services as TS
from backend.auth import Auth, Identity
from backend.store import H, Problem
from backend.tests.helpers.company import company_store

OLD = "326c39df03"
TOOLS, CALLS, AGENTS, REPEATS = 300, 200_000, 20, 5
ADMINS = ("ana@acme.example", "ben@acme.example")
SERVICES = ["stripe", *[s for s in TS.SERVICES if s != "stripe"][:29]]  # Stripe, a shipped guide, is the detail read


def old_module():
    """backend/tool_guides.py as it was at OLD, as a module of the backend package."""
    show = ["git", "show", f"{OLD}:backend/tool_guides.py"]
    found = subprocess.run(show, capture_output=True, text=True)
    if found.returncode:
        subprocess.run(["git", "fetch", "--depth=1", "origin", OLD], check=True, capture_output=True)
        found = subprocess.run(show, capture_output=True, text=True, check=True)
    spec = importlib.util.spec_from_loader("backend._tool_guides_old", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "backend"
    sys.modules[spec.name] = module
    exec(compile(found.stdout, "tool_guides@" + OLD, "exec"), module.__dict__)
    return module


def seed(c):
    now = H.now()
    services = SERVICES
    agents = [f"agent-{n}" for n in range(AGENTS)]
    tools, keys = [], []
    for n in range(TOOLS):
        service = services[n % len(services)]
        tid = f"{service}-{n}"
        tools.append((tid, f"{service} {n}", service, "proxy" if n % 2 else "env", "human:ana", now, now))
        keys.append((f"k{n}", f"{service} {n}", "api_key", f"KEY_{n}", b"\0", b"\0", now, now, "human:ana", tid))
    c.executemany(
        "INSERT INTO tools(id,name,service,delivery,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", tools
    )
    c.executemany(
        "INSERT INTO credentials(id,name,kind,env,ciphertext,nonce,created_at,updated_at,updated_by,tool_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        keys,
    )
    c.executemany(  # Cara holds a tenth of the keys
        "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES(?,?,'human:cara','human:ana',?)",
        [(f"g{n}", f"k{n}", now) for n in range(0, TOOLS, 10)],
    )
    c.executemany(
        "INSERT INTO tool_calls(id,tool_id,actor,bot,access_level,method,path,status_code,result,created_at) "
        "VALUES(?,?,?,?,'read','GET','/v1/x',200,'sent',?)",
        (
            (
                f"call{n}",
                tools[n % TOOLS][0],
                "bot:" + agents[n % AGENTS],
                agents[n % AGENTS],
                H.shift(now, minutes=-(n % (29 * 24 * 60))),
            )
            for n in range(CALLS)
        ),
    )
    c.executemany(
        "INSERT INTO credential_uses(credential_id,actor,bot,used_at) VALUES(?,?,?,?)",
        [(f"k{n}", "bot:" + a, a, now) for n in range(TOOLS) for a in agents],
    )


def median_ms(run):
    times = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        try:
            run()
        except Problem as e:  # the old module's detail is a 404 for a reader who sees none of the vendor's tools
            return e.status
        times.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(times), 1)


def main():
    old = old_module()
    with tempfile.TemporaryDirectory() as folder:
        store = company_store(Path(folder))
        with store.transaction() as c:
            seed(c)
        auth, settings = Auth(store), store.settings
        readers = {
            "ana (owner)": Identity("human:ana", "owner", "ana@acme.example"),
            "cara (member, 30 grants)": Identity("human:cara", "human", "cara@acme.example"),
            "ops (agent)": Identity("bot:ops", "bot"),
        }
        with store.read() as c:
            vendor = "stripe"
            print(f"{TOOLS} tools, {CALLS} calls; detail of `{vendor}`; median of {REPEATS} (ms)")
            print("| reader | list old | list new | detail old | detail new |")
            print("|---|---|---|---|---|")
            for label, who in readers.items():
                times = [
                    median_ms(lambda: old.list_page(c, who, auth, settings, ADMINS)),
                    median_ms(lambda: new.list_page(c, who, auth, ADMINS)),
                    median_ms(lambda: old.detail(c, who, auth, settings, ADMINS, vendor)),
                    median_ms(lambda: new.detail(c, who, auth, ADMINS, vendor)),
                ]
                print(f"| {label} | " + " | ".join(str(t) for t in times) + " |")
            ids = [f"{s}-{n}" for n, s in enumerate(SERVICES * 10)]
            marks = ",".join("?" * len(ids))
            since = H.shift(H.now(), days=-30)
            for name, sql in (
                (
                    "tool_calls",
                    "SELECT tool_id, bot, actor, count(*), max(created_at) FROM tool_calls "
                    f"WHERE tool_id IN ({marks}) AND created_at>=? GROUP BY tool_id, bot, actor",
                ),
                (
                    "credential_uses",
                    "SELECT k.tool_id, u.bot, u.actor, u.used_at FROM credential_uses u JOIN credentials k "
                    f"ON k.id=u.credential_id WHERE k.tool_id IN ({marks}) AND u.used_at>=?",
                ),
            ):
                print(f"\nEXPLAIN QUERY PLAN {name}:")
                for row in c.execute("EXPLAIN QUERY PLAN " + sql, [*ids, since]):
                    print("  " + row[3])


if __name__ == "__main__":
    main()
