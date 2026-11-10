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

# The old Marketing task type, by its id and names. Only the shipped migration 0035_growth_rethink reads them.
TYPE_ID = "idea"
TYPE_NAMES = ("Marketing", "Idea", "Growth idea")
