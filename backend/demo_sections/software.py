"""Product and Engineering: Acme builds software (a blank answer is yes), so it has the two default pipelines
(docs/pipelines-v3.md, Product and Engineering), each with a few tasks across its first stages.

Eli files them: two ideas wait in Ideas, one has moved on to Spec, and two Engineering tasks wait On deck. Acme has no
Builder yet, so nothing has reached Build.
"""

from .. import answers as A
from .. import hubdb
from .. import pipeline_settings as PS
from .. import task_engine as E

ELI = "human:eli"
# (days ago, pipeline, title, body, stages moved on by hand)
TASKS = (
    (3.2, "product", "Schedule shows from the app", "Customers ask to set a show to start at sunset on set days.", 0),
    (2.6, "product", "Calibrate with two phones", "Big houses need two angles; one phone misses the far wall.", 0),
    (2.1, "product", "Share a show with a link", "Families want to send a show to the grandparents' kit.", 1),
    (1.4, "engineering", "Wi-Fi drops on firmware 2.4", "Kits on 2.4 lose Wi-Fi after about an hour of play.", 0),
    (0.8, "engineering", "Faster scene upload", "A 40 MB scene takes four minutes to reach the kit.", 0),
)


def seed(b):
    def work(c):
        PS.ensure_presets(c)
        for days, pipeline, title, body, steps in TASKS:
            b.at(days=days)
            made = hubdb.task_create(c, ELI, title, body, ELI, pipeline=pipeline, allow_planned=True)
            b.tasks[f"software:{title}"] = made["id"]
            for _ in range(steps):  # out of Ideas by hand, saying done first where the stage asks it
                b.at(days=days - 0.1)
                stage = E.task(c, made["id"])["stage_id"]
                done = c.execute(
                    "SELECT id FROM requirements WHERE stage_id=? AND kind='owner_done' AND withdrawn_at IS NULL",
                    (stage,),
                ).fetchone()
                if done:
                    A.answer(c, made["id"], str(done[0]), ELI, "done")
                if E.task(c, made["id"])["stage_id"] == stage:
                    E.move(c, made["id"], E.NEXT, ELI, "Ready for a spec")

    b.write(work)
