"""Reach: who each teammate may reach and how often, set the way an owner sets it (Rules › Internal).

A company default under the built-in one, team settings (Finance, Legal and HR only reach who's listed; Support chats
more, Marketing sends fewer tasks), and bots and people set apart from their team: Finance's lead is open again, the
Tax bot sends 5 tasks a day, the Video Producer has no chats, the SDR is capped. Ana's limit on the SEO Specialist (1 a day, after it kept asking her small things). Lines with a count, a rule or both (a
count of 0 blocks the pair; no count leaves it open): who a closed team may reach, hand-offs between departments and
the limits on the busiest pairs. Every write goes through the API as Ana, after the bots and people it names exist.
"""

# (sender, receiver, per_day, rule). A party is `*`, `group:<id>`, `human:<id>` or `bot:<id>`.
LINES = [
    # Everyone
    ("*", "human:ana", 3, "Decisions only. Put the rest in the daily note."),
    ("*", "bot:legal-review", None, "Contracts and claims only, with the draft attached."),
    ("*", "human:eli", 10, None),

    # Finance: the team reaches only who's listed.
    ("group:finance", "human:ana", 2, "Spend over $5k and month-end only."),
    ("group:finance", "bot:finance-lead", None, None),
    ("group:finance", "bot:ops-manager", None, "Purchase orders and vendor terms."),
    ("bot:bookkeeper", "group:finance", None, None),
    ("bot:bookkeeper", "human:ana", 1, "Month-end close only."),
    ("bot:accounts-payable", "bot:procurement", 5, "Invoice matches against POs."),
    ("bot:tax", "bot:bookkeeper", None, None),
    ("bot:pricing", "group:sales", 3, "New price sheets go through the Sales Manager."),
    ("bot:ar-followup", "bot:account-manager", 2, "Past-due accounts over 30 days."),
    ("bot:spend-watcher", "group:marketing", 1, "Ad spend over plan, once a day."),

    # Legal: reaches only who's listed.
    ("group:legal", "human:ana", None, None),
    ("group:legal", "group:marketing", 4, "Send back marked-up copy, not a rewrite."),
    ("group:legal", "bot:sales", 2, "Contract redlines."),

    # HR: the Recruiter reaches only who's listed.
    ("bot:recruiter", "human:ana", 1, "Offers only."),
    ("bot:recruiter", "human:eli", 2, "Engineering candidates."),
    ("bot:recruiter", "human:cara", 2, "Sales and marketing candidates."),

    # Product and engineering
    ("bot:pr-reviewer", "group:product", None, None),
    ("bot:pr-reviewer", "human:eli", 2, "Blocking reviews only."),
    ("bot:issue-triage", "human:eli", 3, "Severity 1 and 2 only."),
    ("bot:health-monitor", "bot:devops", None, "Alerts with the dashboard link."),
    ("bot:release-notes", "group:support", None, "Send notes the day before a release."),
    ("group:support", "group:product", 5, "Bugs need repro steps and the unit's log."),
    ("bot:support", "bot:firmware", None, "Calibration bugs only, with the unit's log."),
    ("group:marketing", "bot:firmware", 0, "Ask the Product Manager instead."),

    # Marketing
    ("group:marketing", "bot:support", 0, "Customer quotes go to Sales."),
    ("bot:content", "bot:legal-review", 2, "Claims about brightness and safety."),
    ("bot:paid-media", "bot:finance-lead", 1, "Budget changes over $1k."),
    ("bot:creators", "human:cara", 1, "Creator contracts only."),
    ("bot:seo", "bot:content", None, "Keyword briefs, one per post."),
    ("bot:seo", "human:ana", 1, "Small questions wait for one daily note. Message me sooner only if it costs money or affects customers."),
    ("bot:community", "bot:support", 10, "Hand off any owner with a broken unit."),
    ("bot:email-marketing", "bot:sales-ops", 2, "List pulls on Mondays."),
    ("bot:reputation", "group:marketing", 1, "Review trends, weekly."),

    # Sales
    ("bot:sdr", "human:cara", 0, "Send qualified leads to the Sales Manager."),
    ("bot:sdr", "bot:sales-engineer", 3, "Demos for accounts over 50 units."),
    ("group:sales", "bot:pricing", 2, "Discounts over 15%."),
    ("bot:bids", "group:legal", 1, "Tender terms before submission."),
    ("bot:retail", "bot:inventory", 4, "Store reset counts."),

    # Operations
    ("group:operations", "human:ben", 5, None),
    ("bot:logistics", "bot:support", None, "Carrier delays over two days."),
    ("bot:quality", "bot:firmware", 2, "Field failures with batch numbers."),
    ("bot:compliance", "group:legal", None, None),
    ("bot:returns", "bot:accounts-payable", 3, "Refunds over $200."),

    # Support
    ("bot:support", "human:ben", 1, None),
    ("bot:support", "bot:pricing", 0, "Price questions go to the Sales Manager."),
    ("bot:escalations", "human:ben", 2, "Angry retailers and safety reports."),
    ("bot:support-qa", "bot:support", None, None),
]

# (party, fields). A field left out is taken from above: the party's team, the teams above it, Everyone, the default.
SETTINGS = [
    ("*", {"chats_per_day": 200, "tasks_per_day": 60}),
    ("group:finance", {"mode": "closed", "tasks_per_day": 30}),
    ("group:legal", {"mode": "closed"}),
    ("group:hr", {"mode": "closed"}),
    ("group:support", {"chats_per_day": 400}),
    ("group:marketing", {"tasks_per_day": 40}),
    ("bot:finance-lead", {"mode": "open"}),
    ("bot:bookkeeper", {"mode": "closed"}),
    ("bot:recruiter", {"mode": "closed"}),
    ("bot:pr-reviewer", {"mode": "closed"}),
    ("bot:pricing", {"mode": "closed"}),
    ("bot:tax", {"tasks_per_day": 5}),
    ("bot:spend-watcher", {"chats_per_day": 20}),
    ("bot:sdr", {"chats_per_day": 50, "tasks_per_day": 10}),
    ("bot:paid-media", {"tasks_per_day": 8}),
    ("bot:video-producer", {"chats_per_day": 0}),
    ("bot:health-monitor", {"chats_per_day": 30}),
    ("bot:inbox", {"tasks_per_day": 100}),
    ("human:ben", {"tasks_per_day": 20}),
    ("human:cara", {"chats_per_day": 100}),
]


def seed(b):
    for sender, receiver, per_day, rule in LINES:
        b.call("POST", "reach/lines", {"sender": sender, "receiver": receiver, "per_day": per_day, "rule": rule})
    for party, fields in SETTINGS:
        b.call("PUT", f"reach/settings/{party}", fields)
