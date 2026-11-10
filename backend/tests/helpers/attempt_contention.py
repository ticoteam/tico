"""Helpers that other tests share, moved out of test_attempt_contention.py so they import no test file."""

from backend.store import H, encode
from backend.tests.helpers.api import assign, ready, runner
from backend.enqueue import enqueue_stored


def fleet(api, attempts=100, trees=50):
    computer = runner(api)
    assign(api, computer, "ops")
    ready(api, computer, ["ops"])
    store = api.app.state.store
    now = H.now()
    until = H.shift(now, seconds=store.settings.lease_seconds)
    with store.transaction() as c:
        generation = c.execute("SELECT generation FROM assignments WHERE bot='ops'").fetchone()[0]
        for i in range(attempts + trees):
            tid, cid, mid = f"task-{i}", f"room-{i}", f"message-{i}"
            c.execute(
                "INSERT INTO conversations(id,kind,participants,created_at,task_id) VALUES(?,'task',?,?,?)",
                (cid, encode(["human:ana", "bot:ops"]), now, tid),
            )
            c.execute(
                "INSERT INTO tasks(id,title,body,requester_id,owner_id,status,conversation_id,created_at,updated_at) "
                "VALUES(?,?,'Synthetic workload','human:ana','bot:ops','open',?,?,?)",
                (tid, tid, cid, now, now),
            )
            if i < attempts:
                c.execute(
                    "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
                    "VALUES(?,?,'human:ana','bot:ops','say','Synthetic work',?,?)",
                    (mid, cid, now, encode({"task": tid})),
                )
                enqueue_stored(c, mid)
                c.execute(
                    "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,lane) "
                    "VALUES(?,?,'ops',?,?,?,'running',?,?,?)",
                    (
                        f"attempt-{i}",
                        mid,
                        computer["runner_id"],
                        generation,
                        f"synthetic-{i}",
                        until,
                        now,
                        "task:" + tid,
                    ),
                )
                c.execute("UPDATE jobs SET state='running',attempt_id=? WHERE id=?", (f"attempt-{i}", mid))
            else:
                detail = {
                    "owner": "bot:ops",
                    "checkout_state": "ready",
                    "setup_pending": False,
                    "expected_head": "a" * 40,
                    "checkout_target": "refs/heads/main",
                    "expected_base": "b" * 64,
                }
                c.execute(
                    "INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,computer_id,path,detail) "
                    "VALUES(?,?,'worktree',?,'present','bot:ops',?,?,?,?)",
                    (
                        f"tree-{i}",
                        tid,
                        f"worktree:tree-{i}",
                        now,
                        computer["runner_id"],
                        f"tasks/{tid}/Acme__product",
                        encode(detail),
                    ),
                )
                c.execute(
                    "INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at) "
                    "VALUES(?,?,'pr',?,'merged','bot:ops',?)",
                    (f"pr-{i}", tid, f"https://github.com/Acme/product/pull/{i}", now),
                )
    return computer
