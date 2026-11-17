# pyright: strict
"""Product: Acme builds software (a blank answer is yes), so it has the default Product pipeline (docs/dev/pipelines-v3.md,
"Product"), with a few tasks across its first stages.

Eli files them, each starting in Spec (a feature starts as a chat; its first approval is the Spec). Acme has no
feature owner yet, so nothing has reached Build.
"""

import sqlite3
from typing import Any

from .. import hubdb as _hubdb
from .. import pipeline_settings as PS

hubdb: Any = _hubdb
ELI = "human:eli"
# (days ago, title, what Eli asked for)
TASKS = (
    (3.2, "Schedule shows from the app", "Customers ask to set a show to start at sunset on set days."),
    (2.6, "Calibrate with two phones", "Big houses need two angles; one phone misses the far wall."),
    (2.1, "Share a show with a link", "Families want to send a show to the grandparents' kit."),
    (1.4, "Wi-Fi drops on firmware 2.4", "Kits on 2.4 lose Wi-Fi after about an hour of play."),
    (0.8, "Faster scene upload", "A 40 MB scene takes four minutes to reach the kit."),
)


def seed(b: Any) -> None:
    def work(c: sqlite3.Connection) -> None:
        PS.ensure_presets(c)
        for days, title, asked in TASKS:
            b.at(days=days)
            made: dict[str, Any] = hubdb.task_create(c, ELI, title, asked, ELI, pipeline="product", allow_planned=True)
            b.tasks[f"software:{title}"] = str(made["id"])

    b.write(work)
