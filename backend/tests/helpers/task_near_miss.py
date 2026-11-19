"""The task-id resolver as it was before its near-miss search was prefiltered in SQL (backend/auth.py near_task_sql),
kept as the oracle for test_task_near_miss.py and bench/perf-task-near-miss.py, and the fictional tasks both seed."""

import re
import uuid

from backend.auth import TASK_PREFIX_MIN
from backend.store import H, Problem

HEX = "0123456789abcdef"


def old_edit_distance(a, b, limit):
    """Levenshtein distance when it is at most `limit`, else None (gives up as soon as it must exceed it)."""
    if abs(len(a) - len(b)) > limit:
        return None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(row) > limit:
            return None
        previous = row
    return previous[-1] if previous[-1] <= limit else None


def old_resolve_task(auth, c, who, ident, visible=None):
    """Auth.resolve_task before the change, line for line."""
    auth.task_snapshot(c)
    if not isinstance(ident, str) or not ident.strip():
        return ident
    ident = ident.strip()
    if H.task(c, ident):
        return ident
    auth.domain(who)
    visible = visible or auth.task_sql(c, who)

    def readable(rows):
        found = []
        for row in rows:
            try:
                auth.task_row(c, who, row)
            except Problem:
                continue
            found.append(row)
        return found

    number = re.fullmatch(r"#(\d{1,18})", ident)
    if number:
        rows = readable(
            H._rows(
                c.execute(
                    "SELECT id,title,owner_id,requester_id FROM tasks WHERE number=? AND (" + visible + ")",
                    (int(number.group(1)),),
                )
            )
        )
        if rows:
            return rows[0]["id"]
        raise Problem("not_found", "No task %s" % ident, 404)

    label = re.fullmatch(r"([A-Za-z]{1,2})-?(\d{1,18})", ident)
    if label:
        rows = readable(
            H._rows(
                c.execute(
                    "SELECT id,title,owner_id,requester_id FROM tasks WHERE number=? AND upper(COALESCE(prefix, "
                    "(SELECT p.prefix FROM pipelines p WHERE p.id = tasks.pipeline_id), ''))=? AND (" + visible + ")",
                    (int(label.group(2)), label.group(1).upper()),
                )
            )
        )
        if rows:
            return rows[0]["id"]

    if TASK_PREFIX_MIN <= len(ident) < 36:
        rows = readable(
            H._rows(
                c.execute(
                    "SELECT id,title,owner_id,requester_id FROM tasks WHERE substr(id,1,?)=? AND (" + visible + ") "
                    "ORDER BY created_at LIMIT 50",
                    (len(ident), ident.lower()),
                )
            )
        )
        if len(rows) == 1:
            return rows[0]["id"]
        if rows:
            raise Problem(
                "ambiguous_id",
                "Task id %s matches %d tasks: %s. Use more characters of the id."
                % (
                    ident,
                    len(rows),
                    "; ".join("%s (%s)" % (r["id"][:TASK_PREFIX_MIN], r["title"]) for r in rows[:10]),
                ),
                409,
            )
    if len(ident) >= 34:
        near = []
        for row in H._rows(
            c.execute(
                "SELECT id,title,owner_id,requester_id FROM tasks WHERE length(id) BETWEEN ? AND ? "
                "AND (" + visible + ")",
                (len(ident) - 2, len(ident) + 2),
            )
        ):
            distance = old_edit_distance(ident.lower(), row["id"], 2)
            if distance is not None:
                near.append((distance, row))
        if near:
            closest = min(d for d, _ in near)
            best = readable([row for d, row in near if d == closest])[:3]
            if best:
                raise Problem(
                    "not_found",
                    "No task %s. Did you mean %s?"
                    % (ident, " or ".join("%s (%s)" % (r["id"], r["title"]) for r in best)),
                    404,
                )
    raise Problem("not_found", "Task not found", 404)


def outcome(resolve, *args):
    """What a resolver answers: the id it resolved to, or its refusal."""
    try:
        return ("id", resolve(*args))
    except Problem as exc:
        return (exc.code, exc.status, exc.detail)


def edited(text, edits, rng):
    """`text` after (kind, position) edits, applied from the last position back so earlier positions hold: "sub"
    puts a different character at the position, "ins" adds one before it, "del" removes it."""
    out = list(text)
    for kind, at in sorted(edits, key=lambda e: e[1], reverse=True):
        if kind == "sub":
            out[at] = rng.choice([ch for ch in HEX + "-" if ch != out[at]])
        elif kind == "ins":
            out.insert(at, rng.choice(HEX))
        else:
            del out[at]
    return "".join(out)


def seed(c, ids, *, owners, private=0, created="2026-01-01T00:00:00Z"):
    """Tasks with these ids, owned and requested in turn by `owners`."""
    people(c)
    c.executemany(
        "INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
        "VALUES(?,?,?,?,'open',?,?,?)",
        [
            (tid, "Task %d" % n, owners[n % len(owners)], owners[(n + 1) % len(owners)], created, created, private)
            for n, tid in enumerate(ids)
        ],
    )


def people(c):
    """Eve and Cara, whom the api fixture's test identities name but its roster leaves out."""
    for slug in ("eve", "cara"):
        c.execute(
            "INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)", (slug, slug.title(), slug + "@acme.example")
        )


def random_ids(count, rng):
    return [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(count)]
