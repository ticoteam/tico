# pyright: strict
"""An Engineering factory with the same post-merge QA order as the live board."""

import json
from datetime import datetime, timedelta
from typing import Any, Callable, cast

from ..store import H

NAME = "Engineering delivery"
FACTORY = "Engineering"
STAGES = (
    ("Building", "doing"),
    ("Review 1", "review"),
    ("In QA", "review"),
    ("Post Release QA", "review"),
)
MERGED_CARDS = (
    ("Release dashboard filters", 21, 20, 19.75, 18, 901),
    ("API pagination cursor", 12, 11, 10.8, 10, 902),
    ("Team permissions summary", 6.5, 5.5, 5.25, 5, 903),
)


def _stamp(now: datetime, hours_ago: float) -> str:
    return (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def seed(b: Any) -> None:
    factory_id = b.call("POST", "factories", {"name": FACTORY})["id"]
    pipeline = b.call(
        "POST",
        "task-types",
        {"name": NAME, "numbered": True, "steps": [{"name": name, "status": status} for name, status in STAGES]},
    )["type"]
    type_id = pipeline["id"]
    b.call("PATCH", f"pipelines/{type_id}/settings", {"factory_id": factory_id})
    with b.store.read() as c:
        steps = {
            row["name"]: row["id"] for row in c.execute("SELECT id, name FROM task_steps WHERE type_id=?", (type_id,))
        }

    def set_engineering_role(c: Any) -> None:
        c.execute("UPDATE task_types SET role='engineering' WHERE id=?", (type_id,))

    b.write(set_engineering_role)
    history_event = cast(Callable[..., str], H.history_event)

    now = b.now

    def stamp(hours_ago: float) -> str:
        return _stamp(now, hours_ago)

    for title, review_h, merged_h, queued_h, produced_h, number in MERGED_CARDS:
        task_id = b.call(
            "POST",
            "tasks",
            {
                "title": title,
                "body": title + ".",
                "owner": "devops",
                "type": type_id,
                "step": steps["Building"],
            },
        )["task"]["id"]
        with b.store.transaction() as c:
            history_event(
                c,
                "task",
                task_id,
                "human:ana",
                "step",
                old=steps["Building"],
                new=steps["Review 1"],
                created_at=stamp(review_h),
            )
            history_event(
                c,
                "task",
                task_id,
                "human:ana",
                "step",
                old=steps["Review 1"],
                new=steps["In QA"],
                created_at=stamp(queued_h),
            )
            history_event(
                c,
                "task",
                task_id,
                "human:ana",
                "step",
                old=steps["In QA"],
                new=steps["Post Release QA"],
                created_at=stamp(produced_h),
            )
            c.execute("UPDATE tasks SET step_id=?,status='review' WHERE id=?", (steps["Post Release QA"], task_id))
            c.execute(
                "INSERT INTO task_links (id,task_id,kind,url,state,created_at,pr_merged_at) VALUES (?,?, 'pr',?,'merged',?,?)",
                (H.new_id(), task_id, f"https://acme.example/pull/{number}", stamp(merged_h), stamp(merged_h)),
            )

    queued_id = b.call(
        "POST",
        "tasks",
        {
            "title": "Search result grouping",
            "body": "Search result grouping.",
            "owner": "devops",
            "type": type_id,
            "step": steps["Review 1"],
        },
    )["task"]["id"]
    head = "demo-scoreboard-head"
    detail = {
        "head_sha": head,
        "health_reviewed_head": head,
        "merge_queue": {"state": "queued", "head": head},
    }
    with b.store.transaction() as c:
        c.execute(
            "INSERT INTO task_links (id,task_id,kind,url,state,checks,review_state,detail,created_at) "
            "VALUES (?,?,'pr',?,'open','passing','approved',?,?)",
            (H.new_id(), queued_id, "https://acme.example/pull/904", json.dumps(detail), stamp(1)),
        )
