"""Marketing and growth ideas. An idea is a General task with a label that says where it is. Channels are
`channel:<slug>` labels, and the department is the task's team. An idea that worked and stays is done; one that
failed, was inconclusive or was stopped is closed, with the note saying which. A Growth pipeline comes with the Product
pipeline.
"""

# Where an idea is, and the labels that say so, in the order an idea goes. Bots use either family (templates/catalog).
STAGES = (
    ("idea", ("growth:ideas", "idea:on-deck")),
    ("building", ("idea:in-development",)),
    ("testing", ("growth:running", "idea:live")),
    ("scaling", ("idea:working",)),
    ("measuring", ("growth:measuring",)),
    ("paused", ("idea:paused",)),
)
LABELS = {label: stage for stage, labels in STAGES for label in labels}
# A test starts when an idea takes one of these labels.
STARTED = ("growth:running", "idea:live")

# The same stages as the Growth pipelines name them (pipeline_presets_more): an open task in a pipeline made from one of
# these presets is a test wherever it stands, labelled or not, and its stage wins over its labels. A preset stage keeps
# its id `<preset>-<key>`; the 0.6.0-rc.3 Experiments also had On deck and Measuring. A stage a company added reads as
# the preset's first.
EXPERIMENTS, MONITORING = "growth-experiments", "growth-monitoring"
PIPELINE_STAGES = {
    EXPERIMENTS: (
        ("ideas", "idea"),
        ("on-deck", "idea"),
        ("building", "building"),
        ("running", "testing"),
        ("measuring", "measuring"),
    ),
    MONITORING: (("watching", "scaling"), ("slipping", "scaling")),
}
# A test starts when it moves into Running, and Monitoring's Slipping is marked on the board.
RUNNING, SLIPPING = "running", "slipping"
# What a finished Experiments task found (its Results page tags it).
RESULTS = ("result:failed", "result:worked-once", "result:watching")


def stage_key(preset, pipeline_id, stage_id):
    """The preset's key for a Growth pipeline's stage (`running`, `slipping`...), or None for one a company added."""
    for key, _stage in PIPELINE_STAGES.get(preset, ()):
        if stage_id in (f"{pipeline_id}-{key}", f"{preset}-{key}"):
            return key
    return None


def pipeline_stage(preset, pipeline_id, stage_id):
    """Where a task in a Growth pipeline's work stage is, as the labels say it (idea, building, testing...)."""
    key = stage_key(preset, pipeline_id, stage_id)
    pairs = PIPELINE_STAGES[preset]
    return next((stage for k, stage in pairs if k == key), pairs[0][1])


# The old Marketing task type, by its id and names. Only the shipped migration 0035_growth_rethink reads them.
TYPE_ID = "idea"
TYPE_NAMES = ("Marketing", "Idea", "Growth idea")
