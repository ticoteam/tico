"""Recruit: the Recruiting seats on the team chart, the applicants for the install crew job, the cloud agent request in
flight and one seat filled from a template last week."""

from .. import hubdb as H
from .. import lifecycle
from ..auth import Identity

CONTENT = {
    # Recruiting seats, posted by Ana. `days` is when the seat was posted.
    "seats": {
        "install": {"title": "Install Crew Coordinator", "reports_to": "ops-manager", "days": 6.0,
                    "description": "For a person, not a bot. Schedules the field crews and technicians for each Facade "
                                   "install: which crew goes, on which day, with the skills, parts and site access each job "
                                   "needs. Keeps the site manager and the customer told, and re-routes a job to another crew "
                                   "when one falls through."},
        "warranty": {"title": "Warranty Claims", "reports_to": "support-lead", "days": 2.5,
                     "description": "Works each warranty claim from the customer's first message: checks the unit's serial "
                                    "number and purchase date, decides repair, replacement or refund inside the refund limit, "
                                    "and drafts the reply. Anything over the limit goes to Ben as a call."},
    },
    # One applicant for the human seat each: a recruiter task, owned by the recruiter, with its summary as the note.
    # `status` is the stage: open (new), doing (interview set), review (summary ready for Ana), waiting (outside reply).
    "applicants": [
        {"name": "Maya Osei", "applied": 5.6, "changed": 0.9, "status": "review",
         "note": "Ran install crews for a solar installer for eight years, up to thirty jobs a week. Strong on safety "
                 "sign-off and on keeping customers told. Asked about winter pay. Recommend an interview with Ben."},
        {"name": "Jordan Park", "applied": 5.2, "changed": 0.4, "status": "doing",
         "note": "Dispatches field crews for a regional HVAC firm and builds each day's routes from parts and skills. No "
                 "install time, but the scheduling is the job. Interview Thursday with Ben, then a ride-along on a "
                 "Storefront install."},
        {"name": "Luis Ortega", "applied": 4.0, "changed": 1.8, "status": "waiting",
         "note": "Reference call requested from a former employer. Waiting on their reply before the next step."},
        {"name": "Priya Nair", "applied": 0.1, "changed": None, "status": "open", "note": ""},
    ],
    # The cloud agent request in flight: a seat for the agent under Engineering, and the first task Ana gave it.
    "request": {"agent": "devin", "title": "Devin", "reports_to": "product-manager", "days": 1.9,
                "task": {"title": "Write a replay test for the scene that landed on the wrong window",
                         "body": "Two windows overlapped in the test house, and the scene landed on the wrong one. Build a "
                                 "replay test from the saved calibration that fails on that case."}},
    # One hire filled from a template last week: a seat posted, then filled by the bot hired for it.
    "hire": {"title": "Creator Partnerships", "reports_to": "marketing-lead", "posted": 6.3, "days": 5.9,
             "bot": "creators", "description": "Finds creators whose audience fits a holiday light show, agrees the terms "
                                              "(a free unit, a fee or both) and tracks each post's results against the "
                                              "season's goal."},
    "applicant_title": "Applicant for Install Crew Coordinator: {name}",
    "applicant_body": "Applied on the careers page.",
}

HUMAN = "human:ana"
RECRUITER = "bot:recruiter"


def seed(b):
    def post_seat(row):
        b.at(days=row["days"])
        return b.call("POST", "seats", {"title": row["title"], "description": row.get("description", ""),
                                        "reports_to": row["reports_to"]})["id"]

    # The hire: a seat posted, then filled by the bot, which closes it and moves whatever reported to it.
    hire = CONTENT["hire"]
    b.at(days=hire["posted"])
    seat = b.call("POST", "seats", {"title": hire["title"], "description": hire["description"],
                                    "reports_to": hire["reports_to"]})["id"]
    b.at(days=hire["days"])
    b.write(lambda c: lifecycle.fill_seat(c, Identity(HUMAN, "owner"), seat, H.resolve_bot(c, hire["bot"])["id"]))

    post_seat(CONTENT["seats"]["install"])

    # The applicants: one recruiter task each, carrying the stage as its status and the summary as its note.
    def applicants(c):
        for row in CONTENT["applicants"]:
            b.at(days=row["applied"])
            task = H.task_create(c, HUMAN, CONTENT["applicant_title"].format(name=row["name"]),
                                 CONTENT["applicant_body"], RECRUITER, deduplicate=False)
            if row["changed"] is not None:
                b.at(days=row["changed"])
                H.task_update(c, RECRUITER, task["id"], status=row["status"], note=row["note"])
    b.write(applicants)

    post_seat(CONTENT["seats"]["warranty"])

    # The cloud agent request in flight. The seat and its first task are made as the request makes them. Tico's
    # ticket cannot be filed here (the demo reaches no one), so the seat is written as requested, with no ticket.
    req = CONTENT["request"]
    b.at(days=req["days"])
    seat = b.call("POST", "seats", {"title": req["title"], "reports_to": req["reports_to"]})["id"]

    def request(c):
        task = H.task_create(c, HUMAN, req["task"]["title"], req["task"]["body"], HUMAN, deduplicate=False)
        c.execute("UPDATE seats SET agent=?,agent_name=?,request_state=?,first_task=? WHERE id=?",
                  (req["agent"], req["title"], lifecycle.REQUESTED, task["id"], seat))
        H.event(c, HUMAN, "seat.requested", seat, {"agent": req["agent"], "ticket": ""})
    b.write(request)
