"""Reach: who each teammate may reach and how often, set the way an owner sets it (Rules › Internal). Seven lines with
a count, a rule or both (a count of 0 blocks the pair; no count leaves it open), a line from Everyone to Ana, and two
settings: Bookkeeper is closed (it reaches only who is listed) and Ben Okafor may send 20 tasks a day. Every write goes
through the API as Ana, after the bots and people it names exist.
"""

# (sender, receiver, per_day, rule). A party is `*`, `group:<id>`, `human:<id>` or `bot:<id>`.
LINES = [
    ("group:marketing", "bot:support", 0, "Customer quotes go to Sales."),
    ("bot:support", "bot:firmware", None, "Calibration bugs only, with the unit's log."),
    ("bot:support", "human:ben", 1, None),
    ("bot:support", "bot:pricing", 0, "Price questions go to the Sales Manager."),
    ("*", "human:ana", 3, "Decisions only. Put the rest in the daily note."),
    ("bot:content", "bot:legal-review", 2, "Claims about brightness and safety."),
    ("bot:bookkeeper", "group:finance", None, None),
    ("bot:bookkeeper", "human:ana", 1, "Month-end close only."),
]

# (party, fields): Bookkeeper's mode, and Ben Okafor's own task count a day.
SETTINGS = [
    ("bot:bookkeeper", {"mode": "closed"}),
    ("human:ben", {"tasks_per_day": 20}),
]


def seed(b):
    for sender, receiver, per_day, rule in LINES:
        b.call("POST", "reach/lines", {"sender": sender, "receiver": receiver, "per_day": per_day, "rule": rule})
    for party, fields in SETTINGS:
        b.call("PUT", f"reach/settings/{party}", fields)
