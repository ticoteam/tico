# pyright: strict
"""Product: Acme builds software (a blank answer is yes), so it has the default Product pipeline (docs/pipelines-v3.md,
"Product"), with a few tasks across its first stages.

Eli files them, each with its pitch as the body: four wait in Pitch for a person's OK, and one he OK'd has moved on to
Spec. Acme has no feature owner yet, so nothing has reached Build.
"""

import sqlite3
from typing import Any

from .. import answers as A
from .. import hubdb as _hubdb
from .. import pipeline_settings as PS
from .. import task_engine as E

hubdb: Any = _hubdb
ELI = "human:eli"
# (days ago, title, pitch, whether Eli OK'd the pitch)
TASKS = (
    (3.2, "Schedule shows from the app", "Customers ask to set a show to start at sunset on set days.", True),
    (2.6, "Calibrate with two phones", "Big houses need two angles; one phone misses the far wall.", False),
    (2.1, "Share a show with a link", "Families want to send a show to the grandparents' kit.", False),
    (1.4, "Wi-Fi drops on firmware 2.4", "Kits on 2.4 lose Wi-Fi after about an hour of play.", False),
    (0.8, "Faster scene upload", "A 40 MB scene takes four minutes to reach the kit.", False),
)


def _open_requirement(c: sqlite3.Connection, task_id: str, kind: str) -> str:
    stage = (E.task(c, task_id) or {}).get("stage_id")
    found = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind=? AND withdrawn_at IS NULL", (stage, kind)
    ).fetchone()
    return str(found[0])


def seed(b: Any) -> None:
    def work(c: sqlite3.Connection) -> None:
        PS.ensure_presets(c)
        for days, title, pitch, oked in TASKS:
            b.at(days=days)
            made: dict[str, Any] = hubdb.task_create(c, ELI, title, pitch, ELI, pipeline="product", allow_planned=True)
            task_id = str(made["id"])
            b.tasks[f"software:{title}"] = task_id
            if oked:  # any person OKs a pitch: on to Spec once its duplicate check is done
                b.at(days=days - 0.1)
                A.answer(c, task_id, _open_requirement(c, task_id, "ok"), ELI, "ok")

    b.write(work)
