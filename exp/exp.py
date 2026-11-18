"""Timing experiments on the seeded hub bench database (fictional data). argv: db, sql dir."""
import re, sqlite3, statistics, sys, time
db = sqlite3.connect(sys.argv[1])
D = sys.argv[2]
def sql(name):
    return open(f"{D}/{name}.sql").read().strip()
def close_paren(s, i):
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(": depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0: return j
    raise ValueError
def clause(s, start):
    """the parenthesised NOT EXISTS (...) starting at `start` (index of NOT)"""
    i = s.index("(", start)
    return s[start:close_paren(s, i) + 1]
def timed(label, q, runs=7, plan=False):
    times = []
    for _ in range(runs):
        t = time.perf_counter(); rows = db.execute(q).fetchall(); times.append((time.perf_counter() - t) * 1000)
    print(f"{statistics.median(times):8.2f} ms {len(rows):6d} rows  {label}")
    if plan:
        for r in db.execute("EXPLAIN QUERY PLAN " + q):
            if "SCAN" in r[3] or "INDEX" in r[3] and r[1] == 0: print("          ", r[3])
    return rows
TASK = "NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id=r.task_id"
AUD = "COALESCE(audience,CASE WHEN substr(COALESCE(room_key,''),1,13)='live-meeting:' THEN 'meeting' WHEN scope='personal' THEN 'personal' WHEN scope='shared' THEN 'home' WHEN kind='task' AND task_id IS NOT NULL THEN 'task' ELSE 'participants' END)"
RAUD = re.sub(r"\b(audience|room_key|scope|kind|task_id)\b", r"r.\1", AUD)
print("--- baseline (ben)")
for n in range(5):
    timed(f"ben-test-{n}", sql(f"ben-test-{n}"))
q = sql("ben-test-4")
i = q.index("(SELECT r.id FROM conversations r WHERE CASE")
readable_list = q[i + 1:close_paren(q, i)]
timed("readable root list alone", readable_list)
j = q.index("(SELECT lm.conversation_id")
timed("legacy home bound list alone", q[j + 1:close_paren(q, j)], plan=True)
timed("finished task ids", "SELECT id FROM tasks WHERE status IN ('done','closed')")
timed("all open roots, index scan only", "SELECT count(*) FROM conversations r WHERE r.parent_id IS NULL AND r.archived_at IS NULL AND r.closed_at IS NULL")
FIN = "COALESCE((SELECT MAX(h.created_at) FROM history_events h WHERE h.subject_type='task' AND h.subject_id=t.id AND h.kind='move' AND h.new IN (SELECT id FROM stages WHERE kind IN ('done','closed'))),t.closed_at,t.done_at,t.updated_at)"
c_any = ("SELECT r.id FROM tasks t JOIN conversations r ON r.task_id=t.id AND r.parent_id IS NULL WHERE t.status IN ('done','closed') "
         "AND r.archived_at IS NULL AND r.closed_at IS NULL AND EXISTS (SELECT 1 FROM conversations s JOIN messages m ON m.conversation_id=s.id "
         f"WHERE s.root_id=r.id AND m.kind<>'notice' AND m.deleted_at IS NULL AND m.created_at>{FIN})")
timed("C_any: finished roots with a post after the finish", c_any, plan=True)
timed("finished roots, join only", "SELECT r.id FROM tasks t JOIN conversations r ON r.task_id=t.id AND r.parent_id IS NULL WHERE t.status IN ('done','closed') AND r.archived_at IS NULL AND r.closed_at IS NULL", plan=True)
# a partial index of the open roots a thread list can hold (no task audience)
db.execute(f"CREATE INDEX IF NOT EXISTS x_open_threads ON conversations(CASE WHEN TRIM(COALESCE(subject,''))='' THEN 1 ELSE 0 END, lower(COALESCE(subject,'')), id) WHERE parent_id IS NULL AND archived_at IS NULL AND closed_at IS NULL AND {AUD} IN ('company','team','members','personal','participants')")
THREADS = f"r.parent_id IS NULL AND r.archived_at IS NULL AND r.closed_at IS NULL AND {RAUD} IN ('company','team','members','personal','participants')"
print("--- title pages: task check last, from the thread index")
for n in (2, 3):
    q = sql(f"ben-test-{n}")
    k = q.index(TASK); c = clause(q, k)
    moved = q.replace(c + " AND ", "")
    end = moved.index(") ORDER BY t_blank") if ") ORDER BY t_blank" in moved else None
    inner_end = moved.rindex(")", 0, moved.index("ORDER BY t_blank"))
    moved = moved[:inner_end] + " AND " + c + moved[inner_end:]
    timed(f"ben-test-{n} task check last", moved)
    idx = moved.replace("FROM conversations r WHERE r.parent_id IS NULL AND r.archived_at IS NULL AND r.closed_at IS NULL AND",
                        f"FROM conversations r INDEXED BY x_open_threads WHERE {THREADS} AND", 1)
    timed(f"ben-test-{n} task check last + thread index", idx, plan=True)
q = sql("ben-test-4")
idx = q.replace("SELECT r.id FROM conversations r WHERE r.parent_id IS NULL AND r.task_id IN",
                f"SELECT r.id FROM conversations r INDEXED BY x_open_threads WHERE {THREADS} AND r.task_id IN", 1)
timed("ben-test-4 rail revival from the thread index", idx, plan=True)
print("--- walk: task check last")
q = sql("ben-test-0")
k = q.index(TASK); c = clause(q, k)
moved = q.replace(c + " AND ", "")
inner_end = moved.rindex(")", 0, moved.index("ORDER BY activity"))
moved = moved[:inner_end] + " AND " + c + moved[inner_end:]
timed("ben-test-0 walk task check last", moved)
cany = [r[0] for r in db.execute(c_any)]
import json
fin_guard = f"(r.task_id IS NULL OR r.task_id NOT IN (SELECT id FROM tasks WHERE status IN ('done','closed')) OR r.id IN (SELECT value FROM json_each('{json.dumps(cany)}')))"
guarded = moved[:inner_end] + " AND " + fin_guard + moved[inner_end:]
timed("ben-test-0 walk + finished guard", guarded, plan=True)
q = sql("ben-test-1")
guard1 = q.replace("WHERE r.parent_id IS NULL AND r.task_id IN", f"WHERE r.id IN (SELECT value FROM json_each('{json.dumps(cany)}')) AND r.parent_id IS NULL AND r.task_id IN", 1)
timed("ben-test-1 walk revival limited to C_any", guard1, plan=True)
print("stats:", db.execute("SELECT count(*), sum(last_unread_seq>0), sum(last_seq>0) FROM conversations WHERE task_id IS NOT NULL").fetchone())
