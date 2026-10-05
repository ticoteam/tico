"""What the demo company says: Acme, a fictional project-tracking software company.

Only words live here; backend/demo_seed.py decides when each thing happened. Names are the ones
the test fixtures already use (Ana Rivera, Ben Okafor, Cara Mendes, Dana Reyes, Northwind,
Brightline ...) and every address is acme.example or another .example domain.
"""

COMPANY = "Acme"
ANSWERS = {"what_we_do": "Acme sells project-tracking software to small studios that run a few client projects themselves.",
           "customers": "both", "team_size": "1-5", "work_arrives": ["email", "tickets"],
           "repetitive_work": "Support replies, trial follow-ups and the weekly ops checklist."}

PEOPLE = [
    {"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "title": "Founder and owner",
     "team": "leadership", "primary_for": ["*"], "reports_to": None},
    {"id": "ben", "name": "Ben Okafor", "email": "ben@acme.example", "title": "Head of support",
     "team": "leadership", "primary_for": ["support", "inbox"], "reports_to": "ana"},
    {"id": "cara", "name": "Cara Mendes", "email": "cara@acme.example", "title": "Marketing lead",
     "team": "leadership", "primary_for": ["content"], "reports_to": "ana"},
]

# slug, catalog template, reports to
BOTS = [("coo", "assistant", None), ("botops", "botops", "coo"), ("support", "support", "coo"),
        ("sales", "sales", "coo"), ("inbox", "inbox", "coo"), ("content", "content", "coo")]

# Six daily updates per bot and one for Friday's week in review. Each bullet is one plain line.
UPDATES = {
    "coo": {
        "daily": [
            "- Routed six new requests to Support, Sales and Content.\n- Asked Ana to set the refund limit; Support is holding two large refunds until she does.\n- Nothing else is blocked on me.",
            "- Filed the Northwind pricing research for Sales and the launch post outline for Content.\n- Reminded Ben that the vendor invoice question is still open.",
            "- Merged two duplicate tasks about the welcome email.\n- Checked every bot's open work; nothing has sat untouched for a day.\n- Still waiting on Ana for the refund limit.",
            "- Sent Ana this week's priorities to look over.\n- Moved the Brightline renewal brief up because the call is Thursday.",
            "- Routed the partner enquiry from Harborly to Sales.\n- Asked the Librarian to map the market from Ana's answers; it is running now.",
            "- Cleared stale reminders from the shared inbox.\n- Two items wait on people: the refund limit and the pricing page review.",
        ],
        "weekly": [
            "- Routed 31 requests this week; 28 were finished within a day.\n- The refund limit has waited four days and is the one open decision for Ana.\n- Next week I will keep Sales and Support unblocked and prepare the monthly review.",
        ]},
    "botops": {
        "daily": [
            "- Set up Content from the catalog: repository, instructions and a weekly routine.\n- Checked that all six bots report ready on both computers.",
            "- Fixed Sales failing to start: its repository was one commit behind on the office Mac.\n- Added a note to the set-up playbook so the next bot avoids it.",
            "- Turned on the Librarian's market curation and gave it the market questions Ana answered.\n- Both computers are signed in to the model.",
            "- Started updating Support's instructions with the refund policy.\n- Reviewed last week's failed runs; there were none.",
            "- Trimmed Inbox's instructions from 900 lines to 300 so it starts faster.\n- Waiting on Support's review of the new refund wording.",
            "- Confirmed every bot has a computer and a routine that matches its job.\n- No bot is paused or quarantined.",
        ],
        "weekly": [
            "- Built one bot this week (Content) and repaired one start-up failure.\n- All six bots are ready and no run failed after Wednesday.\n- Next week I will finish the refund policy in Support's instructions and add a Listening bot if Ana wants one.",
        ]},
    "support": {
        "daily": [
            "- Drafted replies for 14 new tickets; 11 are ready for Ben to send.\n- Tagged five tickets as invoice questions and wrote one standing answer for them.",
            "- Triaged 9 tickets; three were login problems with the same cause.\n- Asked Ana for a refund limit so I can settle two large refunds.",
            "- Answered the backlog from the weekend: 12 drafts ready.\n- Found that the CSV export button confuses new admins and told Ana.",
            "- Drafted replies for 8 tickets and closed 6 that were already resolved.\n- Held two refund requests over $200 until the limit is set.",
            "- Wrote the standing answer for where to find an invoice and added it to Docs.\n- Sent Ben the list of tickets that need a call, not an email.",
            "- Triaged 11 tickets; 9 drafts are waiting for review.\n- The refund limit is still open, so two refunds are on hold.",
        ],
        "weekly": [
            "- Handled 63 tickets this week; a person edited 9 of my drafts before sending.\n- The three most common problems: invoice location, CSV export, and adding a second admin.\n- Next week I will draft every ticket the same day and finish the refund wording once Ana decides the limit.",
        ]},
    "sales": {
        "daily": [
            "- Researched Northwind's pricing page and wrote up how it differs from ours.\n- Found four trial signups that have not been contacted yet.",
            "- Drafted follow-ups for the five trial signups; one email needs Ana's approval to send.\n- Updated the pipeline summary in Docs.",
            "- Prepared the first half of the Brightline renewal brief.\n- Noted that Brightline asked about the CSV export in its last two calls.",
            "- Finished the Brightline renewal brief and sent it to Ana.\n- Declined a request to email the whole newsletter list; that needs a reviewed draft and an approval.",
            "- Replied to the Harborly partner enquiry with a short question about their volume.\n- Logged three call notes from this week.",
            "- Refreshed the pipeline picture: two renewals due this month, five trials open.\n- The trial follow-up email is still waiting for Ana's approval.",
        ],
        "weekly": [
            "- Five trials are open, two renewals are due this month, and one partner enquiry came in.\n- One send waits on Ana's approval; nothing else is blocked.\n- Next week I will send the approved follow-ups and start the Fernwood renewal brief.",
        ]},
    "inbox": {
        "daily": [
            "- Sorted 42 emails: filed 30, flagged 6 that need a reply.\n- Drafted answers for the two vendor questions.",
            "- Sorted 37 emails and unsubscribed from 4 newsletters Ana never reads.\n- Flagged the accountant's request for the quarterly figures.",
            "- Sorted 51 emails after the weekend: 8 need a reply.\n- Drafted a reply to the vendor invoice question for Ben to check.",
            "- Sorted 29 emails; nothing urgent.\n- Moved the conference invitation to the tasks list.",
            "- Sorted 44 emails and filed the partner enquiry under Sales.\n- Flagged two messages from customers to Support.",
            "- Sorted 33 emails; 5 need a reply.\n- Reminded Ana about the calendar invite that has no answer yet.",
        ],
        "weekly": [
            "- Sorted 236 emails this week; 31 needed a reply and I drafted 22 of them.\n- The quarterly figures request is the one item older than two days.\n- Next week I will draft replies the same morning and keep the flagged list under five.",
        ]},
    "content": {
        "daily": [
            "- Outlined the spring release announcement and picked three customer quotes.\n- Read last month's posts to match the tone.",
            "- Drafted the first half of the launch post.\n- Asked Ben which two support questions should become help articles.",
            "- Finished the launch post draft and put it up for Ana's review.\n- Started the outline for three how-to posts.",
            "- Revised the pricing page copy after Cara's notes and asked Ben to check the plan names.\n- Nothing is published until a person approves it.",
            "- Drafted the how-to post about adding a second admin.\n- Listed the screenshots the post needs.",
            "- Wrote the newsletter intro for next week.\n- Two drafts are waiting for review, none are published.",
        ],
        "weekly": [
            "- Drafted the launch post, revised the pricing page and outlined three how-to posts.\n- Two drafts wait for a person to review; nothing has been published.\n- Next week I will finish the how-to posts and send the newsletter to Cara for approval.",
        ]},
}

# (slug, title, body, owner, requester, final status, days ago created, days ago last change, note)
# Human-owned titles start with a verb because that is the rule for anything a person has to read.
TASKS = [
    ("setup-content", "Set up the Content bot", "Create the Content bot from the catalog and give it a weekly routine.",
     "bot:botops", "human:ana", "closed", 6.2, 5.4,
     "Content is live: its repository exists, its instructions come from the catalog, and it has a weekly routine."),
    ("northwind", "Research Northwind's pricing and packaging",
     "Find how Northwind prices its plans and what each includes. Keep it to one page.",
     "bot:sales", "human:ana", "done", 5.1, 4.2,
     "Northwind sells three plans from $29 to $99 a month. Their starter plan dropped from $39 last month; the notes are in Docs."),
    ("tag-refunds", "Tag last week's refund requests by reason",
     "Read last week's refund tickets and tag each with the reason. Count the reasons.",
     "bot:support", "human:ana", "done", 4.4, 3.5,
     "Nine refund requests: five for a duplicate charge, three for an unused plan, one for a lost invoice. Two are over $200."),
    ("market-graph", "Add the six competitors to the market graph",
     "Add each competitor we listed, with its website as the source.",
     "bot:librarian", "human:ana", "done", 4.0, 3.2,
     "Added Northwind, Brightline, Pricewise, Harborly, Fernwood and Doorlark, each cited from its own site."),
    ("harborly", "Route the Harborly partner enquiry", "A partner enquiry came in by email. Send it to whoever should answer.",
     "bot:coo", "human:ana", "done", 2.2, 1.9,
     "Sent to Sales. Sales replied to Harborly with one question about their volume."),
    ("release-post", "Draft the spring release announcement",
     "Write a 400-word post about the spring release for the blog. Use two customer quotes.",
     "bot:content", "human:ana", "review", 3.1, 0.6,
     "The draft is ready on this task. It has not been published; it needs your read first."),
    ("trial-followups", "Draft follow-ups for this week's five trial signups",
     "Write a short, friendly follow-up for each trial signup. Do not send anything.",
     "bot:sales", "human:ana", "waiting", 2.6, 0.4,
     "Four drafts are ready. The email to Dana Reyes is waiting for your approval before it is sent."),
    ("ticket-replies", "Draft replies for the open support tickets",
     "Draft a reply for every open ticket and tag the ones that need a call. Send nothing.",
     "bot:support", "human:ana", "doing", 1.1, 0.1, "Eleven of fourteen drafted; three are login problems with one cause."),
    ("brightline", "Prepare the Brightline renewal brief",
     "Write a one-page brief for Thursday's renewal call: usage, open issues and what to offer.",
     "bot:sales", "human:ana", "open", 0.9, 0.9, ""),
    ("inbox-sort", "Sort Ana's inbox and flag what needs a reply",
     "Sort today's mail, file the obvious, and flag what needs Ana.", "bot:inbox", "human:ana", "doing", 0.5, 0.1,
     "Twenty-eight sorted so far; five need a reply."),
    ("refund-policy", "Update the Support instructions with the refund policy",
     "Put the refund policy in Support's instructions, with the limit Ana chooses.",
     "bot:botops", "human:ana", "doing", 1.6, 0.3, "Drafted the wording; waiting for the limit to fill in the number."),
    ("howtos", "Outline three how-to posts for the help centre",
     "Outline how to add a project, add a second admin and export a CSV.", "bot:content", "human:ben", "open", 0.8, 0.8, ""),
    ("declined", "Email the whole newsletter list about the spring release",
     "Send the release announcement to everyone on the newsletter list.",
     "bot:sales", "human:ana", "declined", 1.8, 1.7,
     "I have not sent anything. Mail to a whole list needs a reviewed draft and your approval first."),
]

# Asks to a person (rule 7 shapes them: the verb first, the reason in a line or two).
ASKS = [
    ("refund-limit", "Decide the refund limit Support can approve alone", "bot:support", "human:ana",
     "I recommend $200: Support refunds anything below it and asks you above it.\n"
     "Nine refund requests came in last week and two were over $200. Both are waiting on this.", 1.5, "open"),
    ("pricing-copy", "Review the pricing page copy before it goes live", "bot:content", "human:ben",
     "The revised copy is on the task. Please check that the plan names match what Support tells customers.", 0.7, "open"),
    ("approve-followup", "Approve the follow-up email to Dana Reyes", "bot:sales", "human:ana",
     "This is the one email in the batch that needs you. It goes from sales@acme.example only if you approve.\n\n"
     "> Hi Dana,\n>\n> Thanks for trying Acme this week. You added four projects, and I noticed you have not tried "
     "the CSV export yet. It takes about a minute, and I can walk you through it if you would like.\n>\n> Best,\n> Ana", 0.4, "open"),
]

APPROVAL = {"kind": "send", "task": "approve-followup", "by": "bot:sales", "days": 0.4,
            "payload": {"to": "dana.reyes@brightline.example", "cc": "", "subject": "Following up on your Acme trial",
                        "body_sha256": "3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b",
                        "mailbox": "sales@acme.example"}}


# (bot, [(from, text, hours ago at the first message)...])
CHATS = {
    "support": (30, [
        ("human:ana", "How many tickets are waiting on the refund decision?"),
        ("bot:support", "Four. Two are for orders over $200 (Dana Reyes at Brightline, and one from Fernwood). "
                        "I have drafted replies for all four and will hold the two large ones until you set a limit."),
        ("human:ana", "Send me the two large ones first."),
        ("bot:support", "They are quoted on the refund limit task. Once you decide, I will release both drafts for you to send."),
    ]),
    "sales": (52, [
        ("human:ana", "Can you compare our starter plan to Northwind's before Thursday?"),
        ("bot:sales", "Yes. Northwind charges $29 for one project and $59 for five; ours is $35 for up to three. "
                      "Their cut is recent, so I would not lower ours yet. The notes are in Docs under Pricing and plans."),
        ("human:ana", "Good. Add Brightline's mobile app to the brief too."),
        ("bot:sales", "Added. Brightline launched it last month and Dana asked about our own mobile plans on the last call."),
    ]),
    "content": (20, [
        ("human:cara", "Should the launch post lead with the new reports or the faster import?"),
        ("bot:content", "The faster import. Three of the five customer quotes mention it, and reports need a screenshot "
                        "we do not have yet. I will lead with import and mention reports in the second paragraph."),
        ("human:cara", "Works for me."),
        ("bot:content", "Updated the draft. It is on the release announcement task for review; nothing is published."),
    ]),
    "botops": (75, [
        ("human:ana", "Can you add a Listening bot so we hear what studios say about us?"),
        ("bot:botops", "Yes. It is in the catalog, and both computers are signed in to the model. "
                       "I need to know which subreddits and keywords to start with."),
        ("human:ana", "Start with r/projectmanagement and the words Acme, Northwind and Brightline."),
        ("bot:botops", "Noted. I will set it up once you confirm you want it; until then it stays in the catalog."),
    ]),
}

ROUTINES = [
    ("support", "triage", "Morning ticket triage", "0 8 * * 1-5",
     "Read every ticket that arrived since yesterday. Draft a reply for each and tag the ones that need a call."),
    ("sales", "pipeline", "Friday pipeline summary", "0 15 * * 5",
     "Summarise the pipeline: trials open, renewals due this month, and anything that stalled."),
    ("content", "newsletter", "Weekly newsletter draft", "0 10 * * 3",
     "Draft next week's newsletter from the posts we published. Send nothing; put the draft on a task for Cara."),
    ("inbox", "sweep", "Weekday inbox sweep", "30 7 * * 1-5",
     "Sort the mailbox, file the obvious mail and flag what needs a person."),
]

# The turns the runner would have recorded: (bot, hours ago, trigger, summary, tokens in, tokens out)
TURNS = [
    # Earlier in the week, so Usage has a few days to draw.
    ("support", 30, "routine", "Morning triage: 9 drafts ready.", 79200, 6400),
    ("support", 55, "message", "Answered a billing question.", 33800, 2500),
    ("sales", 52, "task", "Prepared the Brightline renewal brief.", 74500, 6900),
    ("content", 76, "task", "Outlined the launch post.", 48800, 5400),
    ("inbox", 27, "routine", "Sorted the inbox: 31 filed, 4 flagged.", 35100, 2000),
    ("inbox", 51, "routine", "Sorted the inbox: 26 filed, 6 flagged.", 37900, 2200),
    ("botops", 100, "task", "Added Content from the catalog.", 91000, 8800),
    ("coo", 78, "task", "Routed four requests.", 21400, 1400),
    ("librarian", 125, "task", "Summarised two competitor pages.", 58300, 4700),
    ("support", 1.5, "message", "Drafted replies for three tickets.", 41200, 3100),
    ("support", 5, "routine", "Morning triage: 11 drafts ready.", 88400, 7200),
    ("sales", 9, "task", "Drafted trial follow-ups; one waits for approval.", 61000, 4800),
    ("content", 14, "task", "Revised the launch post draft.", 52300, 6100),
    ("botops", 3, "task", "Drafted the refund policy wording.", 47100, 3900),
    ("inbox", 2, "routine", "Sorted the morning inbox: 28 filed, 5 flagged.", 36400, 2100),
    ("coo", 6, "task", "Routed six requests.", 22800, 1500),
    ("librarian", 20, "task", "Applied the Fernwood report.", 30900, 2300),
]

# What model each demo bot runs on and how it is billed, for the usage the demo turns carry: about two thirds
# of a turn's input is read from the model's cache.
USAGE = {"coo": ("claude-opus-5", "anthropic", "subscription"), "botops": ("claude-opus-5", "anthropic", "subscription"),
         "support": ("claude-opus-5-5", "anthropic", "api"), "sales": ("gpt-6-sol", "openai", "api"),
         "inbox": ("gpt-6-luna", "openai", "api"), "content": ("claude-fable-5-1", "anthropic", "api"),
         "librarian": ("gpt-6-sol", "openai", "subscription")}

FOCUS = {"support": "Drafting ticket replies", "sales": "Waiting on an approval", "content": "Launch post review",
         "botops": "Refund policy wording", "inbox": "Sorting today's mail", "coo": "Routing requests",
         "librarian": "Watching Northwind"}

DOCS = [
    {"id": "support-refund-policy", "title": "Refund policy", "category": "Internal / Support",
     "content": "# Refund policy\n\nAcme refunds a charge when it was a duplicate, when the plan was never used, or when we billed after a cancellation.\n\n## Who decides\n\n- Support may refund up to the limit Ana sets.\n- Anything above the limit goes to Ana as a task, with the ticket quoted.\n- Support never promises a credit or a discount.\n\n## What to tell the customer\n\nSay what happened, what we are doing, and when they will see the money (three to five business days).\n"},
    {"id": "support-tone", "title": "Support tone of voice", "category": "Internal / Support",
     "content": "# Support tone of voice\n\nPlain, warm and short. Start with what we did, not with an apology.\n\n- Use the customer's first name.\n- One idea per paragraph.\n- Never blame the customer.\n- If we do not know, say so and say when we will.\n"},
    {"id": "support-escalation", "title": "When to call, not write", "category": "Internal / Support",
     "content": "# When to call, not write\n\nSupport drafts by email, and marks a ticket **call** when:\n\n1. The customer has written twice about the same problem.\n2. Money above the refund limit is involved.\n3. The customer is on a renewal in the next 30 days.\n\nBen makes the call; the bot writes the notes on the ticket afterwards.\n"},
    {"id": "sales-pricing", "title": "Pricing and plans", "category": "Internal / Sales",
     "content": "# Pricing and plans\n\n| Plan | Price | Projects |\n|---|---|---|\n| Starter | $35 a month | up to 3 |\n| Growth | $79 a month | up to 15 |\n| Agency | $149 a month | unlimited |\n\n## How we compare\n\nNorthwind charges $29 for one project and $59 for five. Brightline starts at $45. We do not discount in the first year.\n"},
    {"id": "sales-followups", "title": "Trial follow-up playbook", "category": "Internal / Sales",
     "content": "# Trial follow-up playbook\n\nFollow up two days after signup, and only once.\n\n- Say one thing you noticed in their account.\n- Offer one concrete help: a walk-through, an import, a call.\n- Never send without an approval.\n"},
    {"id": "ops-weekly", "title": "Weekly ops checklist", "category": "Internal / Operations",
     "content": "# Weekly ops checklist\n\nEvery Monday, Ana and Ben go through:\n\n1. Open refunds and the limit.\n2. Tickets older than two days.\n3. Trials and renewals due this month.\n4. What is waiting for a person.\n"},
    {"id": "help-add-project", "title": "How to add a project", "category": "External / Help centre",
     "content": "# How to add a project\n\n1. Open **Projects** and choose **Add project**.\n2. Enter the client name and the number of seats.\n3. Invite a teammate if you share the project.\n\nYou can import many projects at once from a CSV file.\n"},
    {"id": "help-invoices", "title": "Where to find your invoices", "category": "External / Help centre",
     "content": "# Where to find your invoices\n\nOpen **Billing** in your account menu. Every invoice is listed with a download button. If an invoice is missing, write to support@acme.example and we will send it the same day.\n"},
    {"id": "notes-northwind", "title": "Northwind pricing page, notes", "category": "Notes / Research", "collection": "notes",
     "content": "# Northwind pricing page, notes\n\nSales read the page on Tuesday. Starter is now $29 (was $39). Their annual discount is 15 percent. Nothing on the page mentions a mobile app.\n"},
    {"id": "notes-rules", "title": "How the bots handle money and mail", "category": "Notes / Bot operating rules", "collection": "notes",
     "content": "# How the bots handle money and mail\n\nA bot never sends mail outside the company, spends money, or publishes without a person's approval. It files an approval with the exact action attached.\n"},
]

MEETINGS = [
    {"days": 5.2, "title": "Weekly ops sync", "source": "zoom", "external_id": "zoom-ops-1",
     "participants": [{"name": "Ana Rivera", "email": "ana@acme.example"}, {"name": "Ben Okafor", "email": "ben@acme.example"},
                      {"name": "Cara Mendes", "email": "cara@acme.example"}],
     "notes": "## Summary\n\n- Support handled 63 tickets last week; a person edited 9 of the drafts.\n- The refund limit is still undecided and holds two large refunds.\n- The launch post is drafted and needs Ana's read.\n\n## Decisions\n\n- Ana will set the refund limit this week.\n- Cara approves the newsletter before it goes out.\n",
     "turns": [
         ("Ana", "Let's start with support. Ben, how did last week go?"),
         ("Ben", "Sixty-three tickets. The bot drafted every one and I edited nine. The two things I keep changing are the tone on refunds and the invoice answer."),
         ("Ana", "Which refunds are stuck?"),
         ("Ben", "Two over two hundred dollars. The bot will not touch them until we set a limit."),
         ("Ana", "I think two hundred is right. Below that Support just refunds, above it asks me. Let me confirm that on the task this week."),
         ("Cara", "On content, the launch post is drafted. I want it to lead with the faster import."),
         ("Ana", "Fine. Send me the draft and I will read it tonight. Nothing gets published before then."),
         ("Cara", "And the newsletter intro is ready. I would like to approve it myself before it goes out."),
         ("Ana", "Agreed. Anything that goes to the whole list is yours to approve, Cara."),
         ("Ben", "One more thing: the CSV export confuses people. Three tickets last week."),
         ("Ana", "Add it to the how-to posts. Ben, can you list which questions become help articles?"),
         ("Ben", "Yes, I will send the list tomorrow."),
     ],
     "items": [
         ("task", "Decide the refund limit Support can approve alone", {"owner": "ana", "priority": "p1"},
          "I think two hundred is right. Below that Support just refunds, above it asks me.", 105000),
         ("task", "List which support questions become help articles", {"owner": "ben", "priority": "p2"},
          "Ben, can you list which questions become help articles?", 235000),
         ("doc", "Add the refund limit to the refund policy", {"document": "Refund policy", "change": "Add the limit and who decides above it.", "why": "The limit was agreed in the ops sync."},
          "Below that Support just refunds, above it asks me.", 110000),
         ("feature", "Make the CSV export easier to find", {"side": "F", "area": "Projects", "bug": False,
                                                                   "current": "The export button is inside the more menu.", "expected": "A visible Export button on the project list."},
          "The CSV export confuses people. Three tickets last week.", 200000),
     ],
     "comments": [("human:ben", "I will send the list of help article questions tomorrow morning.", 240000)]},
    {"days": 3.1, "title": "Brightline renewal call with Dana Reyes", "source": "zoom", "external_id": "zoom-brightline-1",
     "participants": [{"name": "Ana Rivera", "email": "ana@acme.example"}, {"name": "Dana Reyes", "email": "dana.reyes@brightline.example"}],
     "notes": "## Summary\n\n- Brightline uses Acme for 22 seats and wants to renew for a year.\n- Dana asked about the CSV export and a mobile app.\n- Ana offered a walk-through of the export.\n",
     "turns": [
         ("Ana", "Thanks for making time, Dana. Your renewal is next month, so I wanted to hear how it is going."),
         ("Dana", "Honestly it has been good. We run twenty-two seats now and the reports save me an afternoon a week."),
         ("Ana", "That is great to hear. Is there anything that gets in your way?"),
         ("Dana", "The CSV export. I can never find it, and my accountant asks for one every quarter."),
         ("Ana", "I will have someone walk you through it this week, and we are making it easier to find."),
         ("Dana", "Also, do you have a mobile app? Brightline just released one and my clients are asking."),
         ("Ana", "Not yet. It is on the plan, and I would love your input when we design it."),
         ("Dana", "Happy to. We would like to renew for a year if the price stays the same."),
         ("Ana", "It will. I will send the renewal on Thursday."),
     ],
     "items": [
         ("task", "Send Brightline the renewal for one year at the current price", {"owner": "ana", "priority": "p1"},
          "It will. I will send the renewal on Thursday.", 420000),
         ("feature", "Offer a mobile app to studios", {"side": "B/F", "area": "Mobile", "bug": False,
                                                            "current": "There is no mobile app.", "expected": "A simple client-facing app."},
          "Do you have a mobile app? Brightline just released one and my clients are asking.", 300000),
     ],
     "comments": []},
    {"days": 1.6, "title": "Support process review", "source": "upload", "external_id": "upload-support-1",
     "participants": [{"name": "Ana Rivera", "email": "ana@acme.example"}, {"name": "Ben Okafor", "email": "ben@acme.example"}],
     "notes": "## Summary\n\n- The bot drafts every ticket the same day; Ben edits about one in seven.\n- Calls beat emails for renewals and repeat problems.\n",
     "turns": [
         ("Ben", "The drafts are good. I edit about one in seven, mostly to soften the first line."),
         ("Ana", "Put that in the tone document so the bot does it without being told."),
         ("Ben", "Will do. I also want a rule for when we call instead of write."),
         ("Ana", "Repeat problems and renewals in the next thirty days. Write it down."),
         ("Ben", "Done. I will add both to the support docs this afternoon."),
     ],
     "items": [], "comments": []},
]

COMPANIES = [
    {"id": "company/self", "name": "Acme", "type": "company", "tier": None, "aliases": ["Acme", "acme.example"],
     "external_ids": {"domain": "acme.example"}, "summary": "Project-tracking software for small studios."},
    {"id": "company/northwind", "name": "Northwind", "tier": "core", "aliases": ["Northwind"],
     "external_ids": {"domain": "northwind.example"}, "summary": "A large suite vendor with a self-serve tool. Cheapest starter plan."},
    {"id": "company/brightline", "name": "Brightline", "tier": "core", "aliases": ["Brightline", "Brightline YC"],
     "external_ids": {"domain": "brightline.example"}, "summary": "A venture-backed competitor with a new mobile app."},
    {"id": "company/pricewise", "name": "Pricewise", "tier": "lookalike", "aliases": ["Pricewise"],
     "external_ids": {}, "summary": "Rent-pricing tool; overlaps on reports but not on bookkeeping."},
    {"id": "company/harborly", "name": "Harborly", "tier": "core", "aliases": ["Harborly"],
     "external_ids": {}, "summary": "A regional consultancy that also asked about a partnership."},
    {"id": "company/fernwood", "name": "Fernwood", "tier": "lookalike", "aliases": ["Fernwood"],
     "external_ids": {}, "summary": "Bookkeeping software for freelancers; a customer switched from it."},
    {"id": "company/doorlark", "name": "Doorlark", "tier": "core", "aliases": ["Doorlark"],
     "external_ids": {"domain": "doorlark.example"}, "summary": "Timesheet startup adding invoicing."},
]
SEGMENTS = [{"id": "segment/small-studios", "name": "Small studios",
             "summary": "Studios that run a few projects themselves, the people Acme is built for."}]
CHANNELS = [{"id": "channel/r-projectmanagement", "name": "r/projectmanagement"}]
EVIDENCE = [
    ("northwind-pricing", "site", "https://northwind.example/pricing", "Starter: $29 a month for one project.",
     "Northwind's starter plan is $29, down from $39."),
    ("brightline-app", "news", "https://news.example/brightline-launches-mobile-app", "Brightline launches a mobile app for studios.",
     "Brightline launched a mobile app for studios last month."),
]
INSIGHTS = [
    ("bot:sales", "edge", "Doorlark", "Doorlark says it will add rent collection this quarter, which overlaps with our payments plan.",
     "https://doorlark.example/blog/rent-collection", "Rent collection is coming this quarter.", "medium"),
    ("bot:support", "other", "Fernwood", "A customer told Support they switched from Fernwood because its reports were slow.",
     "", "We switched from Fernwood, the reports were too slow.", "low"),
]

# key, title, owner, parent key, colour a person set (None: the Goal Manager's colour from the KPIs), why,
# [(kpi name, unit, target, readings oldest first, days until the target is due)]
GOALS = [
    ("grow", "Grow to 200 paying studios by December", "company", None, None,
     "", [("Paying studios", "studios", 200, [131, 139, 148], 93)]),
    ("tickets", "Answer every support ticket the same day", "bot:support", "grow", None,
     "", [("Tickets answered the same day", "%", 95, [86, 89, 91], 30)]),
    ("trials", "Turn 30 percent of trials into paying studios", "bot:sales", "grow", None,
     "", [("Trial conversion", "%", 30, [21, 22.5, 23], 12)]),
    ("posts", "Publish two useful posts a month", "bot:content", "grow", "yellow",
     "One post is out; the launch post is in review.", [("Posts published this month", "posts", 2, [0, 1, 1], 20)]),
]

# The curator's own pages, written over the seed's outlines: id, title, category, body.
MARKET_PAGES = [
    ("market/overview", "Overview", "Market / Overview",
     "# Market\n\n"
     "Acme sells project-tracking software to **small studios**: teams that run a handful of client projects "
     "themselves and want reports and time tracking without hiring an operations manager.\n\n"
     "## Who we compete with\n\n"
     "- **Northwind** is the price setter. Its starter plan is now $29 a month.\n"
     "- **Brightline** is the fast follower. It launched a mobile app last month and is venture funded.\n"
     "- **Harborly** and **Doorlark** overlap on parts of the product but sell to enterprise teams, not small studios.\n"
     "- **Pricewise** and **Fernwood** are lookalikes: they solve one slice well.\n\n"
     "## What we hear from studios\n\n"
     "Studios on r/projectmanagement ask for one thing over and over: less time on paperwork. Reports and the CSV "
     "export matter more to them than a longer feature list.\n"),
    ("market/structure-and-size", "Structure and size", "Market / Structure",
     "# Structure and size\n\n"
     "The market has three layers: large suites, software for enterprise teams, and software for small studios that run themselves. "
     "Acme sits in the last one.\n\n"
     "| Segment | Who buys | Typical plan |\n|---|---|---|\n"
     "| Small studios | Studios with 1 to 15 seats | $29 to $79 a month |\n"
     "| Growing agencies | Agencies with 15 to 200 seats | $79 to $149 a month |\n\n"
     "We treat any size figure as a thesis with a source, not as a fact on a company.\n"),
    ("market/theses", "Theses", "Market / Theses",
     "# Theses\n\n"
     "1. **Price is not the fight.** Northwind cut to $29, but studios that leave for price come back within a quarter "
     "when reports break. Source: two lost-and-returned customers in Support tickets.\n"
     "2. **Mobile is the next gap.** Brightline shipped a client app and Dana Reyes asked us about one on a renewal call.\n"
     "3. **Rent collection is coming to everyone.** Doorlark says it will add it this quarter.\n"),
    ("market/people-who-matter", "People who matter", "Market / People",
     "# People who matter\n\n"
     "The graph does not track individual people yet. The names that come up most in customer conversations are "
     "Dana Reyes (Brightline, a customer whose renewal is due) and the moderators of r/projectmanagement.\n"),
]
