# pyright: strict
"""Company › Floor: work standing in the Growth, Success and Product factories' stages, so each hall has stacks of
different heights, a few teammates holding them, a rate in and out, and one that waits on Ana.

Leads come in all week and most stop at Targets; a few reach Outreach and Replies, one is Qualified, and three closed
out as Done. Bids talks to three venues and has two proposals out. Most customers sit in Active. Product has builds
in Build and Review 1, one in QA, and one release waiting on Ana's OK. Each task enters its pipeline on its day and is moved on by Ana with a reason, the way a
person moves work past a stage it has no check for yet.
"""

import sqlite3
from typing import Any

from .. import hubdb as _hubdb
from .. import task_engine as E

hubdb: Any = _hubdb
ANA = "human:ana"
WHY = "Picked up on the weekly review"
# (pipeline, stage it stands in, owner it is filed for, days ago it entered, title)
WORK = (
    ("lead", "lead-targets", "bot:growth-manager", 0.2, "Harbor Lane Theatre"),
    ("lead", "lead-targets", "bot:growth-manager", 0.4, "Northline Event Hall"),
    ("lead", "lead-targets", "bot:growth-manager", 1.3, "Calder Street Brewery"),
    ("lead", "lead-targets", "bot:growth-manager", 2.1, "Wren Gardens Venue"),
    ("lead", "lead-targets", "bot:growth-manager", 2.8, "Pine Hill Community Center"),
    ("lead", "lead-targets", "bot:growth-manager", 3.5, "Bayside Wedding Barn"),
    ("lead", "lead-targets", "bot:growth-manager", 4.6, "Old Mill Arts Space"),
    ("lead", "lead-targets", "bot:growth-manager", 5.2, "Lakeview Pavilion"),
    ("lead", "lead-outreach", "bot:sdr", 3.1, "Ridge Road Climbing Gym"),
    ("lead", "lead-outreach", "bot:sdr", 4.2, "Copper Kettle Cafe"),
    ("lead", "lead-outreach", "bot:sdr", 5.6, "Maple Court Hotel"),
    ("lead", "lead-replies", "bot:sdr", 5.9, "Fernwood School Hall"),
    ("lead", "lead-replies", "bot:sdr", 6.4, "Harborview Museum"),
    ("lead", "lead-qualified", "bot:sdr", 6.8, "Cedar Park Amphitheater"),
    ("opportunity", "opportunity-talking", "bot:bids", 2.5, "Proposal: Cedar Park lighting rig"),
    ("opportunity", "opportunity-talking", "bot:bids", 3.9, "Proposal: Maple Court ballroom"),
    ("opportunity", "opportunity-talking", "bot:bids", 4.8, "Proposal: Bayside summer season"),
    ("opportunity", "opportunity-proposal", "bot:bids", 6.1, "Proposal: Old Mill gallery nights"),
    ("opportunity", "opportunity-proposal", "bot:bids", 6.6, "Proposal: Fernwood winter show"),
    ("customer", "customer-onboarding", "bot:success-owner", 0.9, "Onboard Riverbend Theatre"),
    ("customer", "customer-onboarding", "bot:success-owner", 1.6, "Onboard Summit Climbing"),
    ("customer", "customer-active", "bot:success-owner", 2.2, "Grove Street Cinema"),
    ("customer", "customer-active", "bot:success-owner", 2.7, "Hollow Oak Brewing"),
    ("customer", "customer-active", "bot:success-owner", 3.4, "Saltmarsh Inn"),
    ("customer", "customer-active", "bot:success-owner", 4.0, "Kestrel Music Hall"),
    ("customer", "customer-active", "bot:success-owner", 4.9, "Brightwater Library"),
    ("customer", "customer-active", "bot:success-owner", 5.5, "Tamarack Lodge"),
    ("customer", "customer-referral-asked", "bot:success-owner", 6.3, "Referral ask: Grove Street Cinema"),
    ("product", "product-build", "bot:mobile", 1.2, "Scene preview on the phone app"),
    ("product", "product-build", "bot:firmware", 2.3, "Firmware 2.5: steady Wi-Fi"),
    ("product", "product-build", "bot:cloud", 3.0, "Scene upload retries on a weak signal"),
    ("product", "product-review-1", "bot:mobile", 4.1, "Share a show with a link"),
    ("product", "product-review-1", "bot:firmware", 4.7, "Two-phone calibration"),
    ("product", "product-qa", "bot:qa-owner", 5.3, "Sunset start for scheduled shows"),
    ("product", "product-ok-release", ANA, 6.0, "Release 2.4.1"),
)
# (pipeline, owner, days ago it entered, days ago it reached Done, title)
DONE = (
    ("lead", "bot:sdr", 6.9, 1.4, "Juniper Hall"),
    ("lead", "bot:sdr", 6.5, 3.2, "Stonebridge Arena"),
    ("lead", "bot:sdr", 5.8, 0.3, "Linden Street Theatre"),
    ("customer", "bot:success-owner", 6.7, 2.4, "Onboard Westfield Cinema"),
    ("opportunity", "bot:bids", 6.9, 0.6, "Proposal: Juniper Hall season lights"),
)


def _file(c: sqlite3.Connection, pipeline: str, owner: str, title: str) -> str:
    made: dict[str, Any] = hubdb.task_create(c, ANA, title, title + ".", owner, pipeline=pipeline, allow_planned=True)
    return str(made["id"])


def seed(b: Any) -> None:
    def work(c: sqlite3.Connection) -> None:
        # Every step on its own day, oldest first, so the clock only moves forward.
        steps: list[tuple[float, Any]] = []
        for pipeline, stage, owner, days, title in WORK:
            steps.append((days, ("file", pipeline, owner, title)))
            steps.append((days - 0.05, ("move", title, stage, owner)))
        for pipeline, owner, entered, done, title in DONE:
            steps.append((entered, ("file", pipeline, owner, title)))
            steps.append((done, ("move", title, E.DONE, owner)))
        for days, step in sorted(steps, key=lambda s: -s[0]):
            b.at(days=days)
            if step[0] == "file":
                _, pipeline, owner, title = step
                b.tasks[f"floor:{title}"] = _file(c, pipeline, owner, title)
                continue
            _, title, to, owner = step
            task_id = b.tasks[f"floor:{title}"]
            if (E.task(c, task_id) or {}).get("stage_id") != to:
                E.move(c, task_id, to, ANA, WHY)
            # A stage its task's assignee owns is held by nobody after a push past: its teammate takes it on.
            if to != E.DONE and not (E.task(c, task_id) or {}).get("owner_id"):
                E.reassign(c, task_id, owner, ANA, set_anyway=True)

    b.write(work)
