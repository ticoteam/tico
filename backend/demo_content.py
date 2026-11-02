"""What the demo company says: Acme Lights, a fictional maker of holiday projector kits and installs.

Only words live here; backend/demo_seed.py decides when each thing happened. Every person is at
acme.example and every other company at a .example domain. Nothing here is a real person or company.
"""

COMPANY = "Acme Lights"
ANSWERS = {"what_we_do": "Acme Lights makes holiday projector kits that turn a house into a light show: set it up in an afternoon, "
                         "calibrate it with your phone, and play scenes rendered for your home. Families buy kits online; shops, "
                         "hotels, malls and towns buy installs with a yearly scene license.",
           "customers": "both", "team_size": "11-50", "work_arrives": ["email", "tickets", "chat"],
           "repetitive_work": "Customer emails, calibration help, orders and shipping, business quotes and site surveys, "
                              "parts buying, invoices and the weekly numbers."}

PEOPLE = [
    {"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "title": "Founder and CEO",
     "team": "leadership", "primary_for": ["*", "finance-lead", "bookkeeper", "ar-followup", "accounts-payable",
                                           "spend-watcher", "tax", "pricing", "recruiter", "legal-review"],
     "reports_to": None},
    {"id": "ben", "name": "Ben Okafor", "email": "ben@acme.example", "title": "Head of operations and customer care",
     "team": "leadership", "primary_for": ["ops-manager", "procurement", "inventory", "logistics", "quality",
                                           "compliance", "returns", "support-lead", "support", "support-qa",
                                           "technical-support", "escalations", "reputation", "inbox"],
     "reports_to": "ana"},
    {"id": "cara", "name": "Cara Mendes", "email": "cara@acme.example", "title": "Head of growth",
     "team": "leadership", "primary_for": ["marketing-lead", "content", "scene-designer", "video-producer",
                                           "paid-media", "email-marketing", "seo", "community", "creators",
                                           "marketplace", "sales-lead", "sales", "sdr", "account-manager",
                                           "customer-success", "sales-engineer", "sales-ops", "retail", "partners",
                                           "bids"],
     "reports_to": "ana"},
    {"id": "eli", "name": "Eli Navarro", "email": "eli@acme.example", "title": "Head of engineering",
     "team": "leadership", "primary_for": ["product-manager", "pr-reviewer", "issue-triage", "devops", "firmware",
                                           "mobile", "cloud", "product-analyst", "release-notes", "health-monitor",
                                           "botops"],
     "reports_to": "ana"},
]

# slug, catalog template, reports to. Each department has a lead bot under the person who runs it (a `human:` parent),
# and the Assistant (coo) hangs under Ana.
BOTS = [("coo", "assistant", None), ("botops", "botops", "coo"),
        # Operations and supply chain
        ("ops-manager", "ops-manager", "human:ben"), ("procurement", "procurement", "ops-manager"),
        ("inventory", "inventory", "ops-manager"), ("logistics", "logistics", "ops-manager"),
        ("quality", "quality-inspections", "ops-manager"), ("compliance", "compliance", "ops-manager"),
        ("returns", "returns", "ops-manager"),
        # Engineering: calibration cloud, player, phone app
        ("product-manager", "product-manager", "human:eli"), ("pr-reviewer", "pr-reviewer", "product-manager"),
        ("issue-triage", "issue-triage", "product-manager"), ("devops", "devops-engineer", "product-manager"),
        ("release-notes", "release-notes", "product-manager"), ("health-monitor", "health-monitor", "product-manager"),
        ("firmware", "software-architect", "product-manager"), ("mobile", "software-architect", "product-manager"),
        ("cloud", "software-architect", "product-manager"), ("product-analyst", "product-analyst", "product-manager"),
        # Marketing and content (consumer)
        ("marketing-lead", "marketing-lead", "human:cara"), ("content", "content", "marketing-lead"),
        ("scene-designer", "content", "marketing-lead"), ("creators", "partnerships", "marketing-lead"),
        ("video-producer", "video-producer", "marketing-lead"), ("paid-media", "paid-media", "marketing-lead"),
        ("email-marketing", "email-marketing", "marketing-lead"), ("seo", "seo-visibility", "marketing-lead"),
        ("community", "community", "marketing-lead"), ("marketplace", "partnerships", "marketing-lead"),
        # Customer care
        ("support-lead", "support-lead", "human:ben"), ("support", "support", "support-lead"),
        ("support-qa", "support-qa", "support-lead"), ("technical-support", "technical-support", "support-lead"),
        ("escalations", "escalations", "support-lead"),
        ("reputation", "reputation", "support-lead"), ("inbox", "inbox", "support-lead"),
        # Business sales (B2B)
        ("sales-lead", "sales-lead", "human:cara"), ("sales", "sales", "sales-lead"), ("sdr", "sdr-research", "sales-lead"),
        ("account-manager", "account-manager", "sales-lead"),
        ("customer-success", "customer-success", "sales-lead"), ("sales-engineer", "sales-engineer", "sales-lead"),
        ("sales-ops", "sales-ops", "sales-lead"), ("retail", "partnerships", "sales-lead"),
        ("partners", "partnerships", "sales-lead"), ("bids", "sales", "sales-lead"),
        # Finance
        ("finance-lead", "finance-lead", "human:ana"), ("bookkeeper", "bookkeeper", "finance-lead"),
        ("ar-followup", "ar-followup", "finance-lead"), ("spend-watcher", "spend-watcher", "finance-lead"),
        ("tax", "tax", "finance-lead"), ("accounts-payable", "accounts-payable", "finance-lead"),
        ("pricing", "pricing", "finance-lead"),
        # People and legal
        ("recruiter", "recruiting", "coo"), ("legal-review", "legal-review", "coo")]

# Job titles for bots whose catalog template is only a starting point.
BOT_NAMES = {"firmware": "Firmware Engineer", "mobile": "Mobile App Engineer", "marketplace": "Marketplace Manager",
             "retail": "Retail Partners Manager", "partners": "Installer Partners Manager", "bids": "Public Bids Manager",
             "cloud": "Render Cloud Engineer", "scene-designer": "Scene Designer", "creators": "Creator Partnerships"}

# The two computers: Ana's Mac runs most bots; the Linux box hosts Inbox alone. (label, platform, runtimes)
RUNNERS = (("Ana's MacBook", "darwin", ("claude", "codex")), ("acme-runner-1", "linux", ("claude",)))

# The seven department leads and the bots that post a week's numbers beside their goal.
WEEK_KPIS = {
    "coo": [{"name": "Requests routed", "value": "31", "series": [22, 25, 27, 24, 31]}],
    "ops-manager": [{"name": "Orders shipped this week", "value": "2,310", "series": [1900, 2050, 2310]}],
    "product-manager": [{"name": "First-try calibration", "value": "84", "unit": "%", "series": [88, 87, 84],
                         "note": "Target 95. Off track."}],
    "marketing-lead": [{"name": "Home Pro share of home sales", "value": "38", "unit": "%", "series": [33, 35, 38]}],
    "support-lead": [{"name": "Tickets handled", "value": "63", "series": [48, 55, 51, 60, 63]}],
    "sales-lead": [{"name": "Quotes sent this week", "value": "9", "series": [4, 6, 5, 8, 9]}],
    "finance-lead": [{"name": "Bills paid on time", "value": "100", "unit": "%", "series": [96, 98, 100]}],
}

# Each bot's week, as (week's goal, Day A bullets, Day B bullets, next week's focus, blockers). A daily update
# is one of the two Day lists, alternating by day and by roster position, so no two bots read alike. Friday's
# week in review is built from the same lists: done is Day A plus the first line of Day B.
# Leads summarise what their reports did. Bullets are plain lines with no labels.
UPDATES = {
    "coo": ("Keep every bot moving and every decision with the right person; three decisions are open.",
            ("Routed five requests: two to Support, one to Sales, one to Finance and one to Eli's engineers.",
             "Asked Ana to decide the December Home Pro price; it is on her list with the numbers."),
            ("Checked every department lead's open work; nothing has sat untouched for a day.",
             "Two items wait on people: the Home Pro price and the Larkford bid price."),
            ("Keep Sales and Support unblocked this week.", "Prepare the monthly review for Ana."),
            ("Three decisions wait on Ana or Ben: the Home Pro price, the Larkford bid and the port fee.",)),
    "botops": ("Every bot ready on both computers with a routine that matches its job; none failed after Wednesday.",
               ("Checked that all 55 bots report ready on both computers.",
                "Fixed the render bot's repository, which was one commit behind on Ana's Mac."),
               ("Trimmed Support's instructions from 800 lines to 300 so it starts faster.",
                "Set up the Scene Designer from the catalog with a weekly routine."),
               ("Finish the holiday instructions for Marketplace.", "Add the Listening bot if Cara wants one."), ()),
    "ops-manager": ("Ship 6,000 units this season; 4,450 are out, and the port hold is the one risk.",
                    ("Orders are at 94 percent shipped within two days; the target is 95.",
                     "Port customs is holding 300 units; logistics is chasing the broker."),
                    ("Home Pro is down to 220 kits at the warehouse; inventory has a restock plan.",
                     "Lens rejects from batch 14 are at 3 percent; quality has the supplier report."),
                    ("Clear the port hold before the next sailing.", "Lock the December restock order."),
                    ("The port fee question waits on Ben's decision.",)),
    "procurement": ("Keep lenses and LED modules on order at least three weeks ahead of the peak.",
                    ("Compared two lens suppliers; the second is 4 percent cheaper with the same spec.",
                     "Drafted a purchase order for 1,200 lens sets for December."),
                    ("Confirmed the LED module delivery for Thursday; 800 modules are on the truck.",
                     "The lens order waits for Ops to approve its size before it goes out."),
                    ("Send the lens order once Ops approves it.", "Check the second supplier's sample lenses."), ()),
    "inventory": ("Stock matches open orders within three units every day through the season.",
                  ("Counted the warehouse: 1,140 Home Starter kits and 220 Home Pro kits ready to ship.",
                   "Flagged Home Pro as the first kit to run short in December."),
                  ("Reconciled stock against orders; the gap is three units, all of them in returns.",
                   "Set the reorder point for Storefront mounts at 40."),
                  ("Confirm the December restock with Procurement.", "Move the Home Pro hold to the marketplace count."), ()),
    "logistics": ("Every shipment arrives with its paperwork and inside the promised window.",
                  ("Booked the Thursday truck for 800 LED modules to the warehouse.",
                   "The port is holding 300 units until the broker sends the customs form."),
                  ("The $310 port fee was charged twice on the last sailing; the broker is checking.",
                   "Asked Accounts Payable to hold the freight invoice until the fee is settled."),
                  ("Get the customs form to the port.", "Confirm the holiday courier pickup times."),
                  ("The port fee question waits on Ben.",)),
    "quality": ("Lens rejects stay under 3 percent through the season.",
                ("Inspected 200 units from batch 14; six had a loose lens mount.",
                 "Logged the loose-mount failures for the supplier report."),
                ("Lens rejects are at 3 percent, down from 4 last week.",
                 "Re-tested the fix on 100 units; they all passed."),
                ("Send the supplier report on batch 14.", "Repeat the 100-unit test on the December batch."), ()),
    "compliance": ("Every product and scene pack carries the paperwork and warning wording the rules require.",
                   ("Checked the Home Pro 2 radio paperwork against the FCC rules; one signature is missing.",
                    "Prepared the filing summary for Ana to sign."),
                   ("Reviewed the holiday scene labels for the required safety wording.",
                    "Two scene packs need the warning moved onto the first screen."),
                   ("Get Ana's signature on the FCC filing.", "Check the Storefront sign safety notes."),
                   ("The FCC filing waits on Ana's signature.",)),
    "returns": ("Returns are back in stock within three days of arriving.",
                ("Processed 38 returns; 27 were duplicate orders and went back into stock.",
                 "Replaced four lenses under the warranty rule."),
                ("Opened a return for a Storefront projector damaged on arrival; the courier will repay it.",
                 "Sent the return labels for the holiday wave."),
                ("Keep the three-day restock promise.", "Write the lens replacement rule into Docs once Ben decides it."),
                ()),
    "product-manager": ("Calibration works first time for 95 percent of owners; it is at 84 percent, our one off-track goal.",
                        ("Calibration first-try is 84 percent; the target is 95 and the fix is in ring 3.",
                         "Pulled the failed calibrations into one list for the cloud and mobile teams."),
                        ("Firmware 2.4 on ring 2 shows no new issues across 1,100 devices.",
                         "Holding ring 3 until Eli signs off on the lens-mount fix."),
                        ("Get the lens-mount fix into ring 3.", "Review the calibration cohort with Eli on Monday."),
                        ("Ring 3 waits on Eli's sign-off.",)),
    "pr-reviewer": ("Every pull request gets a review within a day, with tests that run on the build machines.",
                    ("Reviewed nine pull requests; two calibration changes need a retry fix.",
                     "Approved the mobile change that adds the lens-mount guide."),
                    ("Checked the render service change; a missing timeout would have stalled jobs.",
                     "One test fails only on Linux and is waiting on its author."),
                    ("Clear the two open calibration reviews.", "Review the ring 3 release branch."), ()),
    "issue-triage": ("Every bug report is linked, tagged and in the right queue within a day.",
                     ("Triaged 14 bug reports; six are calibration failures with the same lens-mount cause.",
                      "Linked the duplicates to one issue for Firmware."),
                     ("Closed three duplicate reports about the last phone app version.",
                      "Asked Support for two repro steps on the Storefront pairing bug."),
                     ("Confirm the lens-mount cause with Firmware.", "Get repro steps on the pairing bug."), ()),
    "devops": ("Deploys go out without a rollback and the render queue never waits more than five minutes.",
               ("Deployed the cloud release at 10:00 with no rollback; render jobs are steady.",
                "Scaled the render workers from four to six for the holiday load."),
               ("Patched the build machines; every suite now runs in under 12 minutes.",
                "Rotated the cloud keys on the security calendar."),
               ("Watch the render queue during Friday's scene launch.", "Add a second build machine for the release branch."),
               ()),
    "release-notes": ("Every release ships with plain notes that Support can repeat.",
                      ("Drafted the firmware 2.4 notes; the lens-mount fix is in them.",
                       "Sent the notes to Support so its replies match."),
                      ("Wrote the mobile notes for the calibration screen change.",
                       "Waiting on Firmware to confirm the ring 3 date."),
                      ("Finalise the firmware 2.4 notes once ring 3 has a date.", "Send the mobile notes with the next build."),
                      ()),
    "health-monitor": ("Any device or service problem reaches a person within the hour.",
                       ("Checked 2,640 projectors online; 31 went offline overnight and came back by morning.",
                        "Found one render stall on the cloud service; the cause is a full disk."),
                       ("Telemetry shows the lens-mount temperature warning on 12 units from batch 14.",
                        "Sent the batch warning to Quality."),
                       ("Set the alert for render stalls over five minutes.", "Add batch 14 to the watch list."), ()),
    "firmware": ("Firmware ships in rings with no crashes, and calibration works first time for most owners.",
                 ("Rolled firmware 2.4 to ring 2, 1,100 devices, with no crashes.",
                  "Fixed the lens-mount calibration bug in the test build."),
                 ("Measured calibration retries on the test build: 3 in 100, down from 16.",
                  "Ring 3 starts once Eli signs off."),
                 ("Start ring 3 after Eli's sign-off.", "Write the calibration fallback for older lenses."),
                 ("Ring 3 waits on Eli's sign-off.",)),
    "mobile": ("The phone app calibrates a projector in under three minutes with no crashes.",
               ("Shipped the calibration screen update to 200 beta testers.",
                "The new lens-mount guide cut stuck taps by a third in the beta."),
               ("Fixed a crash when a phone switches networks during a scene upload.",
                "The next build adds an offline scene library."),
               ("Release the calibration screen to every owner once Eli says go.", "Test the offline scene library."),
               ()),
    "cloud": ("Every scene renders in under a minute and no render stalls for more than five minutes.",
              ("Rendered 1,840 scenes overnight; the average wait was 40 seconds.",
               "A render stalled on a full disk; the clean-up job now runs hourly."),
              ("Added a retry for scenes that fail on the first render.",
               "Holiday scene load is double last week's, so the workers are scaled."),
              ("Check the holiday scene load each morning.", "Move render logs to the 30-day store."), ()),
    "product-analyst": ("Each product question gets a cohort answer with the data and its caveats.",
                        ("Owners who calibrate in the first hour keep using scenes; 78 percent of them do.",
                         "Pulled calibration retries by lens batch for Quality."),
                        ("Home Pro owners add a scene twice as fast as Home Starter owners.",
                         "Drafted the calibration retry chart for Friday."),
                        ("Finish the calibration cohort for Eli.", "Set up the holiday scene usage report."), ()),
    "marketing-lead": ("Home Pro reaches 45 percent of home sales by the first week of December.",
                       ("Home Pro is 38 percent of home sales, up from 35; the target is 45.",
                        "Holiday emails and forum posts go out this week, but the Home Pro price is still open."),
                       ("Reviewed the Facade campaign with Sales; the cost per quote is down 12 percent.",
                        "Ad budget stays at $4,000 a week until Ana rules on the cap."),
                       ("Launch the December Home Pro offer once the price is set.", "Review the holiday email results."),
                       ("The December Home Pro price waits on Ana.",)),
    "content": ("Publish two useful holiday guides a month; one is out and the second is in review.",
                ("Drafted the holiday setup guide; it is in review with Cara.",
                 "Listed the five screenshots the guide still needs."),
                ("Published the first of the two holiday posts on the store blog.",
                 "Started the lens-mount how-to for the help centre."),
                ("Finish the lens-mount how-to.", "Send the holiday guide to Cara for approval."), ()),
    "scene-designer": ("The Scene Library has 40 holiday scenes that pass the flashing and safety checks.",
                       ("Designed five new holiday scenes; three passed review.",
                        "Two scenes need a fix for the flashing rule."),
                       ("Built a snow-fall scene for storefronts and tested it on two lenses.",
                        "Asked Firmware about the frame rate limit on older units."),
                       ("Fix the two flashing-rule scenes.", "Ship the storefront snow scene."), ()),
    "creators": ("Creators make 12 holiday videos and the partnerships stay under budget.",
                 ("Confirmed 12 creators for the holiday video series; contracts are out for signature.",
                  "Sent each creator the lens-mount guide to read before filming."),
                 ("Two creators asked for a bigger fee; the offer is with Cara for a decision.",
                  "Tracked 40 creator videos from last month; nine mention our projectors."),
                 ("Get the 12 contracts signed.", "Confirm the filming dates."), ()),
    "video-producer": ("The 30-second install video is cut and ready by the first week of December.",
                       ("Cut the install video to 32 seconds; the hook lands in the first three seconds.",
                        "Sent the cut to Cara for a read."),
                       ("Filmed the Storefront install at a test house; the crew needed one extra take.",
                        "Booked the studio for the holiday shoot."),
                       ("Get Cara's note on the 30-second cut.", "Shoot the Home Pro unboxing."), ()),
    "paid-media": ("Keep the cost per Home Pro sale under $80 while spend stays inside the approved budget.",
                   ("Spent $3,700 on search and social this week; cost per Home Pro sale is $71.",
                    "Paused one ad set with no sales in ten days."),
                   ("Moved $600 from search to the holiday forum ads, which convert better.",
                    "Waiting on the ad cap before raising the budget."),
                   ("Raise the budget only once the cap is set.", "Test two new holiday ads."),
                   ("The ad cap waits on Ana's decision.",)),
    "email-marketing": ("Every holiday email goes to the right segment, and only after Cara approves it.",
                        ("Built the holiday segments: 3,800 owners, with 1,100 business leads kept apart.",
                         "The holiday send waits on Cara's approval."),
                        ("The newsletter test had a 41 percent open rate.",
                         "Removed 210 bounced addresses from the list."),
                        ("Send the holiday list once Cara approves it.", "Set up the thank-you email for buyers."),
                        ("The holiday send waits on Cara's approval.",)),
    "seo": ("Our holiday guides rank on page one for the top five holiday search terms.",
            ("The search term projector lights is up 18 percent; the store page ranks fourth.",
             "Found three product pages with missing alt text; fixed two."),
            ("Checked 60 pages for broken links; two were fixed on Monday.",
             "Drafted meta descriptions for the four holiday guides."),
            ("Finish the alt text fixes.", "Track the store page rank weekly."), ()),
    "community": ("Forum questions get an answer within one day, and the good ones go to Support.",
                  ("Answered 22 questions on the holiday light forums; six were calibration questions.",
                   "Pinned the lens-mount guide in the forum."),
                  ("Found a hotel asking about bulk prices in one thread; sent it to Sales.",
                   "Set up a reply template for shop owners."),
                  ("Post the lens-mount guide to the two biggest forums.", "Check the hotel thread for Sales."), ()),
    "marketplace": ("Bigmarket stock matches the warehouse and the listing answers the top buyer questions.",
                    ("Bigmarket listings are live for Home Starter and Home Pro; Home Pro shows 160 free.",
                     "Holding 60 Home Pro for the marketplace until the Thursday count."),
                    ("Bigmarket reviews are at 4.7 stars from 310 buyers.",
                     "Two buyers asked about the lens-mount guide; the answer is in the listing."),
                    ("Move the marketplace hold to Inventory's count.", "Add the lens-mount guide to the Home Pro listing."),
                    ()),
    "support-lead": ("Answer every ticket the same day; 88 percent do today, against a target of 95.",
                     ("The queue is at nine open tickets; eight have a same-day draft.",
                      "Three login problems traced to one cause, which Eli's team has."),
                     ("Same-day answers are at 88 percent, up from 86.",
                      "Two calibration problems need Ben's call on the unit swap rule."),
                     ("Keep the queue under ten tickets.", "Set the calibration callback rule with Ben."),
                     ("The lens replacement rule waits on Ben's decision.",)),
    "support": ("Draft a reply for every ticket the same day, and tag the ones that need a call.",
                ("Drafted replies for 14 tickets; 11 are ready for Ben to send.",
                 "Tagged five tickets as shipping questions and wrote one standing answer."),
                ("Triaged nine tickets; three were calibration failures with one cause.",
                 "Asked Ben for a call on two repeat problems."),
                ("Draft every ticket the same day.", "Write the holiday shipping answer once the port hold clears."), ()),
    "support-qa": ("Calibration help gets the first answer right; first-try is 84 percent today.",
                   ("Reviewed 40 calibration transcripts; 14 failed on the lens-mount step.",
                    "Wrote the lens-mount step fix into the support script."),
                   ("Checked 20 replies for tone; two needed a warmer first line.",
                    "Compared Glowline's support page with ours; ours is shorter."),
                   ("Test the new lens-mount script on 20 transcripts.", "Share the callback rule with Ben."), ()),
    "technical-support": ("Every calibration call ends with a working projector or a clear swap decision.",
                          ("Ran nine calibration calls; seven worked on the call and two need a unit swap.",
                           "Asked Ben whether we can swap the unit on the next call."),
                          ("Logged a Storefront pairing problem with three steps and sent it to Firmware.",
                           "Called back two customers whose calibration failed."),
                          ("Get the unit swap answer from Ben.", "Log the next five calibration calls."),
                          ("The unit swap decision waits on Ben.",)),
    "escalations": ("No escalation waits longer than one business day for an owner.",
                    ("Took three escalations: an installer dispute, a hotel complaint and a lost pallet.",
                     "Owned the hotel complaint and proposed a credit for Ben to approve."),
                    ("Closed two escalations from last week after the customers confirmed the fix.",
                     "The lost pallet is with the carrier, and the claim is filed."),
                    ("Chase the hotel's answer on the credit.", "Track the carrier claim."), ()),
    "reputation": ("Every review gets a reply within two days, and factual problems reach the owner.",
                   ("Read 180 new reviews across the store and marketplace; 4.6 stars overall.",
                    "Two reviews say setup took longer than the box claims; sent to Content."),
                   ("Replied to 22 reviews in the house style.",
                    "Flagged one review with a false warranty claim to Ben."),
                   ("Reply to the last 12 reviews.", "Send the setup-time note to Content."), ()),
    "inbox": ("Ben's inbox has nothing waiting on a reply for more than a day.",
              ("Sorted 42 emails: filed 30 and flagged six that need a reply.",
               "Drafted the answer to the port fee question for Ben to check."),
              ("Sorted 37 emails and unsubscribed from four newsletters nobody reads.",
               "Flagged the accountant's request for the quarterly figures."),
              ("Keep the flagged list under five.", "Draft replies the same morning."), ()),
    "sales-lead": ("Install 40 business sites this season; 22 are booked or done.",
                   ("Installs are at 22 of 40 for the season; two Facade quotes are out.",
                    "The Crown Hotel follow-up waits on Cara's approval to send."),
                   ("Fourteen shops and four hotels are in talks, and three towns are in bidding.",
                    "The Larkford bid price needs Ana's decision before Friday."),
                   ("Get the Crown Hotel and Maple Mall quotes signed.", "Finish the downtown shop call list."),
                   ("The Larkford bid price waits on Ana.",)),
    "sales": ("Every business quote goes out within two days of the site survey.",
              ("Sent three Facade quotes; one hotel asked for a second site visit.",
               "Followed up with the Crown Hotel events contact; one email waits on approval."),
              ("Logged the shop calls: two want a demo next week.",
               "Drafted the Maple Mall proposal from the survey."),
              ("Send the Crown Hotel follow-up after approval.", "Book the two shop demos."), ()),
    "sdr": ("Sales has a researched, scored list of 60 shops and 10 hotels every week.",
            ("Built a list of 60 downtown shops with owners' names and public contacts.",
             "Dropped nine shops that already use another installer."),
            ("Researched the Maple Mall tenants; six stores want holiday windows.",
             "Sent the scored list to Sales with the reason for each score."),
            ("Score the remaining 18 shops.", "Research three towns that opened bids."), ()),
    "account-manager": ("Repeat business accounts book their next season before it starts.",
                        ("Checked the nine repeat shops; seven have booked a second holiday window.",
                         "One shop owner asked for a discount, which is a question for Ana."),
                        ("Updated the account notes for the three hotel accounts.",
                         "Shared a install-day checklist with Crewline."),
                        ("Book two January visits with hotel accounts.", "Ask Ana about the shop discount rule."), ()),
    "customer-success": ("Every installed site is playing its scenes within two days of install.",
                         ("Checked 14 installs from last week; 13 are playing and one needs a re-aim.",
                          "Booked the re-aim visit with Crewline for Thursday."),
                         ("Sent the install follow-up to nine shops; six replied with a photo.",
                          "Two shop owners asked for a scene schedule; drafts are with Cara."),
                         ("Finish the re-aim visit.", "Send the scene schedules after Cara's review."), ()),
    "sales-engineer": ("Every site survey is complete with photos, power and mounts within a week of request.",
                       ("Surveyed the Maple Mall north wall; the mounts take four projectors.",
                        "Sent the survey photos to Crewline for a crew quote."),
                       ("Measured three hotel fronts for the Crown Hotel quote.",
                        "Found one front with no power; sent the electrical note to Crewline."),
                       ("Finish the Larkford survey before the bid is due.", "Book the Crown Hotel second visit."), ()),
    "sales-ops": ("The pipeline is clean and every quote has a next step with a date.",
                  ("Pipeline: 22 installs booked or done, 14 quotes open, six towns in discussion.",
                   "Cleaned 31 duplicate company records in the CRM."),
                  ("Four quotes have had no reply in ten days; reminders are drafted.",
                   "Changed the forecast to use the install date, not the quote date."),
                  ("Send the reminders once Sales approves them.", "Check the forecast with Cara on Monday."), ()),
    "retail": ("A retail partner stocks Home Starter in at least 40 stores before the holidays.",
               ("Homeworks wants Home Starter on 40 shelves in its new stores; talks are at pricing.",
                "Sent Homeworks the wholesale price sheet for review."),
               ("Homeworks' buyer asked for holiday display stands; the quote is with Procurement.",
                "Two more retailers replied to the partner pack."),
               ("Get Homeworks' answer on the shelf count.", "Send the display stand quote."),
               ("The Homeworks returns clause waits on Ana.",)),
    "partners": ("Enough trained crews for every booked install, at a fee that works for both sides.",
                 ("Crewline has nine crews free for January and six for December.",
                  "Crewline asked for a fixed fee per install; a counter-offer is drafted."),
                 ("Confirmed the crew training dates with Crewline for the holiday wave.",
                  "Two installer partners asked for the lens-mount guide."),
                 ("Get Ana's answer on the crew fee counter-offer.", "Confirm the crew count for December."),
                 ("The crew fee counter-offer waits on Ana.",)),
    "bids": ("Win the Larkford town bid at a price that clears our margin.",
             ("The Larkford bid is due Friday; the draft scope is written.",
              "Priced the town square at two levels."),
             ("Checked the bid rules; the town wants an insurance certificate we do not have yet.",
              "Asked Crewline for its insurance certificate."),
             ("Price the Larkford bid once Ana decides.", "Send the insurance proof before Friday."),
             ("The Larkford bid price waits on Ana.",)),
    "finance-lead": ("Books close each week and no bill is paid before it is checked.",
                     ("Bank lines are 97 percent matched within a day; three accounts need a receipt.",
                      "Ad spend is at 92 percent of the monthly cap; spend-watcher tracks it daily."),
                     ("Cash is healthy; December payroll is covered with room for the holiday stock.",
                      "Sent the freight invoice question to Ops for the port fee."),
                     ("Decide the ad cap with Ana on Monday.", "Close October with the tax team."),
                     ("The December ad cap waits on Ana.",)),
    "bookkeeper": ("Books close on time, and every bank line is matched within a day.",
                   ("Matched 412 bank lines; nine need a receipt from a shop.",
                    "Closed the October card expenses; one transaction was a duplicate."),
                   ("Reconciled the Storefront deposits; the total matches the orders.",
                    "Sent the receipt list to Ben's team for the shops."),
                   ("Close the week's receipts.", "Start the month-end reconciliation."), ()),
    "ar-followup": ("Shop and hotel invoices are paid within 30 days.",
                    ("Chased 14 unpaid shop invoices; six paid by the end of the day.",
                     "Two hotel invoices are 30 days late; the reminder went out."),
                    ("Set up payment links on three shop invoices.",
                     "The overdue total is $6,400, down from $9,100."),
                    ("Chase the last overdue hotel invoice.", "Check the Maple Mall deposit."), ()),
    "spend-watcher": ("No spend goes out above a cap without a person's approval.",
                      ("Ad spend is $3,700 of the $4,000 weekly budget; no overspend.",
                       "Flagged one charge from an old software tool."),
                      ("Found a $45 software charge for a tool nobody uses; it is cancelled.",
                       "The holiday forum ad budget is at 80 percent and tracked daily."),
                      ("Track the ad budget daily.", "Check the freight invoices against the port fee."), ()),
    "tax": ("Every filing goes in on time, and the workpapers are ready before each deadline.",
            ("Filed three state sales tax returns; two are due this month.",
             "Prepared the October workpapers; one state asks for a resale certificate."),
            ("Checked the Storefront kit's tax category with the accountant.",
             "Drafted the October estimate for Ana to review."),
            ("Get the resale certificate from the shop.", "Start the sales tax check."), ()),
    "accounts-payable": ("Every supplier bill is paid on time and matched to an order.",
                         ("Paid 18 supplier bills after checks; two are held for a missing receipt.",
                          "The freight invoice is held until the port fee is settled."),
                         ("Matched the LED module invoice to its purchase order; the price matches.",
                          "Scheduled Friday's payments."),
                         ("Release the held freight invoice after the port fee is settled.", "Pay Friday's supplier bills."),
                         ()),
    "pricing": ("Price each product where it wins sales without giving margin away.",
                ("Home Pro is $449 for two projectors; December demand is 12 percent ahead of forecast.",
                 "Drafted two December price options for Home Pro; one is $479."),
                ("Lumenhaus, Brightyard and Glowline are $20 to $40 above Home Pro for two-unit kits.",
                 "Waiting on Ana to choose the December price for Home Pro."),
                ("Get Ana's December price decision.", "Review the Storefront price after the season."),
                ("The December Home Pro price waits on Ana.",)),
    "recruiter": ("Crew lead roles are filled before the January installs.",
                  ("Screened 14 applicants for two install crew lead roles.",
                   "Shortlisted four candidates; interviews are booked for Thursday."),
                  ("Drafted the offer for one crew lead role, pending Ana's approval.",
                   "Closed one role after a successful trial install."),
                  ("Run Thursday's crew lead interviews.", "Send the offer once Ana approves the crew terms."),
                  ("The crew lead offer waits on Ana.",)),
    "legal-review": ("Every contract and public claim is reviewed before it goes out.",
                     ("Reviewed the Homeworks contract draft; the returns clause needs a change.",
                      "Sent the changed clause to Procurement with a plain-English note."),
                     ("Checked the holiday email consent wording against the rules.",
                      "No changes were needed."),
                     ("Get Ana's answer on the Homeworks returns clause.", "Review the Larkford bid insurance terms."),
                     ("The Homeworks returns clause waits on Ana.",)),
}
# A bot that skipped a day, as real ones do: (slug, days back). Four days back is never a Friday.
UPDATE_GAPS = (("creators", 4),)
# A person's update is the Assistant's draft of their day, from their leads' same-day updates (the seed posts it as a draft
# labelled so). Two sets of daily bullets alternate by day, as the bots' do; Friday's is their week in review.
PEOPLE_UPDATES = {
    "human:ben": {
        "daily": (("Support is running 14 calibration redos and calling the owners.",
                   "Two owners failed the redo twice; both are waiting on my call.",
                   "Port customs still holds 300 units; logistics is chasing the broker."),
                  ("Support closed nine of the 14 calibration redos.",
                   "The unit swap for owners who fail twice is waiting on my decision.",
                   "The $310 port fee is still open; the freight invoice waits on it.")),
        "week": {"goal": "Owners get calibrated on the first try, and the port hold clears before the next sailing.",
                 "done": ["Support ran 14 calibration redos and closed nine.",
                          "Ops has 4,450 units out; customs holds 300."],
                 "focus": ["Decide the unit swap rule for owners who fail twice.",
                           "Clear the port hold before the next sailing."],
                 "blockers": ["The port fee decision is mine, and it holds the freight invoice."]},
    },
    "human:cara": {
        "daily": (("Spent $3,700 of the $4,000 weekly ad budget on search and social.",
                   "Cost per Home Pro sale is $71; one ad set with no sales is paused.",
                   "The downtown shop list has 60 names, and eleven calls are logged."),
                  ("Two downtown shops want a demo next week; Sales is booking them.",
                   "The ad budget stays at $4,000 a week until Ana rules on the December cap.",
                   "The Crown Hotel follow-up waits on my approval before it goes.")),
        "week": {"goal": "Holiday demand builds through ads and the downtown shops, within the $4,000 weekly budget.",
                 "done": ["Paid search and social spent $3,700 of the $4,000 budget.",
                          "Eleven downtown shop calls logged; two want a demo."],
                 "focus": ["Book the two shop demos for next week.",
                           "Hold ad spend to the cap until Ana rules on December."],
                 "blockers": ["The December ad cap waits on Ana."]},
    },
    "human:eli": {
        "daily": (("Firmware 2.4 is held at the 5 percent ring while the lens-mount fix goes out.",
                   "The lens-mount fix is in review; PR Reviewer waits on one Linux test.",
                   "Ring 3 waits on my sign-off and cloud's holiday scene load check."),
                  ("Ring 2 ran 48 hours on 1,100 devices with no crashes.",
                   "Calibration retries fell from 16 in 100 to 3 in 100 in the test build.",
                   "Ring 3 stays held until cloud confirms the Thursday load numbers.")),
        "week": {"goal": "Firmware 2.4 reaches every owner with no crash spike and calibration that passes first try.",
                 "done": ["Ring 2 ran 48 hours on 1,100 devices with no crashes.",
                          "The lens-mount fix is in the test build; retries fell to 3 in 100."],
                 "focus": ["Clear the fix review and give ring 3 a date.",
                           "Sign off ring 3 once cloud confirms the load."],
                 "blockers": ["Ring 3 holds on my sign-off and cloud's load check."]},
    },
    "human:ana": {
        "daily": (("The Home Pro price is on my list: $449 now, $479 for December.",
                   "Pricing expects 6 to 9 percent fewer units at $479, about 300 kits.",
                   "The FCC filing, the Larkford bid and the December ad cap wait on me."),
                  ("The rivals sit $20 to $40 above Home Pro, so we still win on price.",
                   "The season is 6,000 units; 4,450 are out.",
                   "Three decisions are on my list; the rest are with the team.")),
        "week": {"goal": "Ship the season's 6,000 units at a Home Pro price that keeps the margin up.",
                 "done": ["Ops has 4,450 of 6,000 units out.",
                          "Firmware 2.4 ring 2 ran 48 hours with no crashes."],
                 "focus": ["Decide the Home Pro price for December.",
                           "Sign the FCC filing for Home Pro 2.",
                           "Choose the Larkford bid price."],
                 "blockers": ["Three decisions wait on me, and the port fee waits on Ben."]},
    },
}

# Each bot's status focus right now, one short line.
FOCUS = {"coo": "Routing requests", "botops": "Checking both computers", "ops-manager": "Watching the port hold",
         "procurement": "Lens order for December", "inventory": "Counting stock for orders",
         "logistics": "Freeing the held units", "quality": "Batch 14 lens rejects",
         "compliance": "FCC paperwork", "returns": "Replacing warranty lenses",
         "product-manager": "Calibration first-try rate", "pr-reviewer": "Reviewing the calibration fix",
         "issue-triage": "Linking calibration reports", "devops": "Watching the render queue",
         "release-notes": "Firmware 2.4 notes", "health-monitor": "Fleet check",
         "firmware": "Ring 3 waiting on sign-off", "mobile": "Calibration screen beta",
         "cloud": "Holiday scene load", "product-analyst": "Calibration cohort",
         "marketing-lead": "Home Pro share", "content": "Holiday setup guide review",
         "scene-designer": "Flashing-rule fixes", "creators": "Creator contracts",
         "video-producer": "30-second install cut", "paid-media": "Holiday ad spend",
         "email-marketing": "Holiday owner list", "seo": "Holiday search terms",
         "community": "Forum answers", "marketplace": "Bigmarket stock",
         "support-lead": "Same-day answers", "support": "Ticket drafts", "support-qa": "Lens-mount script",
         "technical-support": "Calibration callbacks", "escalations": "Hotel credit",
         "reputation": "Store reviews", "inbox": "Sorting Ben's mail",
         "sales-lead": "Install pipeline", "sales": "Crown Hotel follow-up", "sdr": "Downtown shop list",
         "account-manager": "Repeat hotel accounts", "customer-success": "Installed sites playing",
         "sales-engineer": "Larkford survey", "sales-ops": "Stalled quotes", "retail": "Homeworks shelves",
         "partners": "Crane crew quote", "bids": "Larkford bid", "finance-lead": "December ad cap",
         "bookkeeper": "October books", "ar-followup": "Overdue shop invoices", "spend-watcher": "Daily ad budget",
         "tax": "October sales tax", "accounts-payable": "Holding the freight bill",
         "pricing": "Home Pro December price", "recruiter": "Crew lead interviews",
         "legal-review": "Homeworks clause", "librarian": "Watching Lumenhaus"}

# (slug, title, body, owner, requester, final status, days ago created, days ago last change, note, parent key)
# Human-owned titles start with a verb because a person reads them. A parent key makes a subtask.
TASKS = [
    # Firmware 2.4, rolled out in rings
    ("fw24", "Roll firmware 2.4 out in three rings",
     "Ring 1 is 50 test units, ring 2 is 1,100 owners, ring 3 is the rest. Stop the rollout if the crash rate rises.",
     "bot:firmware", "human:eli", "doing", 6, 1,
     "Rings 1 and 2 are done with no crashes. Ring 3 waits on Eli's sign-off.", None),
    ("fw-watch", "Watch ring 2 crash reports for 48 hours",
     "Check crash reports each morning and post anything over the alert line.",
     "bot:health-monitor", "bot:firmware", "done", 4, 2,
     "No crash spike; zero crashes per 1,000 devices.", "fw24"),
    ("fw-fix", "Fix the lens-mount calibration bug",
     "The calibration step times out when the lens mount is loose. Fix it in the test build.",
     "bot:firmware", "bot:product-manager", "done", 3, 1,
     "Fix is in the test build; retries fell from 16 in 100 to 3.", None),
    ("fw-review", "Review the lens-mount calibration fix",
     "Check the change and its tests before it goes into ring 3.",
     "bot:pr-reviewer", "bot:firmware", "review", 1.5, 0.4,
     "Waiting on one test that only fails on Linux.", "fw24"),
    ("fw-notes", "Write the firmware 2.4 release notes",
     "Plain notes that Support can repeat. Final once ring 3 has a date.",
     "bot:release-notes", "bot:firmware", "doing", 1.2, 0.2, "Draft is in Docs.", "fw24"),
    # Calibration, the off-track goal
    ("cal-redo", "Find why 16 in 100 first calibrations fail",
     "Group the failed calibrations by cause and by lens batch, and say which fix to ship first.",
     "bot:product-analyst", "human:eli", "doing", 5, 0.5,
     "The lens-mount step explains 11 of the 16. Still checking two units from batch 14.", None),
    ("cal-callbacks", "Call back owners whose calibration failed twice",
     "Ring each owner, run the last step with them, and log the result on the ticket.",
     "bot:technical-support", "bot:product-analyst", "doing", 2, 0.2,
     "Four owners called back; two asked for a unit swap.", "cal-redo"),
    ("cal-script", "Add the lens-mount step to the support script",
     "Support answers calibration questions with the new steps, starting with the lens mount.",
     "bot:support-qa", "bot:product-analyst", "done", 2.8, 1.1,
     "Script is live. First-try is up on the pilot group.", "cal-redo"),
    ("mobile-beta", "Ship the calibration screen to every owner",
     "The beta with 200 owners is clean. Release it to every owner when Eli says so.",
     "bot:mobile", "human:eli", "review", 2, 0.5, "Beta is clean; waiting for Eli's go.", None),
    ("health-alert", "Set the alert for render stalls over five minutes",
     "Post to the operations channel when a render makes no progress for five minutes.",
     "bot:health-monitor", "human:eli", "done", 1.7, 1.3, "Alert set; the first test fired and was cleared.", None),
    # Render stall
    ("render-stall", "Find the cause of Tuesday's render stall",
     "Renders stopped for 40 minutes. Find the cause and whether it can happen again.",
     "bot:cloud", "bot:health-monitor", "done", 4, 3.5,
     "The render box's disk was full. The clean-up now runs hourly.", None),
    ("render-fix", "Schedule the render box clean-up every hour",
     "Add a clean-up job that keeps the disk under 60 percent.",
     "bot:devops", "bot:cloud", "done", 3.8, 3.4, "Clean-up deployed; the disk is at 41 percent.", "render-stall"),
    ("render-read", "Read the render stall report and confirm the fix",
     "Read the report and say whether the fix is enough for the holiday load.",
     "human:eli", "bot:health-monitor", "closed", 3.6, 3.2,
     "Confirmed. Close it once the holiday load is checked.", None),
    ("render-scale", "Scale the render workers for the holiday load",
     "Two more workers for the holiday peak, starting Friday.",
     "bot:devops", "bot:cloud", "done", 1.5, 1.1, "Six workers are running; the queue stays under two minutes.", None),
    # Port hold and freight
    ("port-ship", "Get the 300 held units through the port",
     "Customs is holding 300 units. Find the paperwork and a release date.",
     "bot:logistics", "bot:ops-manager", "waiting", 2.4, 0.6, "Waiting on the broker for the customs form.", None),
    ("port-customs", "Send the broker the customs form",
     "The broker needs the signed form before the release. Send it and confirm receipt.",
     "bot:logistics", "bot:ops-manager", "doing", 1.8, 0.3, "Form sent; the customs stamp is next.", "port-ship"),
    ("ap-freight", "Hold the freight invoice until the port fee is settled",
     "Do not pay the freight bill while the $310 port fee is in dispute.",
     "bot:accounts-payable", "bot:logistics", "waiting", 1.2, 0.6,
     "Held. Waiting on Ben's decision about the port fee.", "port-customs"),
    # Stock, procurement and returns
    ("procure-lens", "Order 1,200 lens sets for December",
     "Send the purchase order to the cheaper supplier once Ops approves the size.",
     "bot:procurement", "bot:ops-manager", "waiting", 2, 0.5, "Waiting on Ops to approve the order size.", None),
    ("reorder-mounts", "Set the reorder point for Storefront mounts",
     "Reorder when stock falls to 40 mounts.", "bot:inventory", "bot:ops-manager", "done", 2.6, 2.2,
     "Reorder point set at 40 mounts.", None),
    ("batch14", "Send the supplier the batch 14 report",
     "Six lens mounts came loose in the first 200 units. Send the report and ask for a fix date.",
     "bot:quality", "bot:ops-manager", "doing", 1, 0.2, "Report drafted; the supplier has not replied.", None),
    ("returns-ops", "Replace the lenses from last week's returns",
     "Four lenses are owed under warranty. Ship them and log each one.",
     "bot:returns", "bot:ops-manager", "done", 1.6, 0.9, "All four shipped on Monday.", None),
    # Marketplace
    ("bigmarket-stock", "Keep Home Pro stock on bigmarket matched to the warehouse",
     "Hold 60 Home Pro for the marketplace and show the count on the listing.",
     "bot:marketplace", "human:cara", "doing", 2.5, 0.6, "Holding 60 Home Pro for the marketplace.", None),
    ("bigmarket-ship", "Ship the 60 held Home Pro units to bigmarket",
     "Pack the 60 units and hand them to bigmarket's carrier.",
     "bot:inventory", "bot:marketplace", "done", 1, 0.5,
     "Packed and handed over; bigmarket has the tracking numbers.", "bigmarket-stock"),
    # Sales and installs
    ("downtown-shops", "List the 60 downtown shops with a fit score",
     "Research each shop, score it against our install profile, and drop the ones that already use an installer.",
     "bot:sdr", "human:cara", "done", 4.5, 2.1, "60 listed, 42 scored, and nine dropped for using another installer.", None),
    ("shop-calls", "Call the scored downtown shops",
     "Call the high-score shops and book demos for the ones that want one.",
     "bot:sales", "bot:sdr", "doing", 2.6, 0.5, "Eleven calls logged; two want a demo next week.", "downtown-shops"),
    ("crown-quote", "Send the Crown Hotel lobby and terrace quote",
     "Four projectors for the lobby and two for the terrace, with a yearly scene license.",
     "bot:sales", "human:cara", "waiting", 2.3, 0.4,
     "Quote drafted. The follow-up email waits on Cara's approval.", None),
    ("pipeline-reminders", "Send reminders to quotes with no reply in ten days",
     "Four quotes have had no reply for ten days. Draft a reminder for each.",
     "bot:sales-ops", "human:cara", "review", 0.9, 0.3, "Four drafts are ready; Sales needs to approve them.", None),
    ("crew-training", "Confirm crew training dates for the holiday wave",
     "Crewline needs training dates for each crew before the December installs.",
     "bot:partners", "human:cara", "done", 2.5, 1.9, "Crewline confirmed nine crews for December.", None),
    ("survey-larkford", "Survey the Larkford town square for the bid",
     "Check power, mounts and crane access at each of the four mounting points.",
     "bot:sales-engineer", "bot:bids", "doing", 3.2, 1.4,
     "Power and mounts are fine. Crane access is the cost driver.", None),
    ("crew-larkford", "Price the crane crew for the square",
     "Ask Crewline for a crane crew quote for the four mounts.",
     "bot:partners", "bot:sales-engineer", "open", 1.4, 1.4, "", "survey-larkford"),
    ("bids-larkford", "Draft the Larkford bid at two price levels",
     "Write the scope and two prices: the low one wins on price, the high one covers the crane.",
     "bot:bids", "bot:sales-engineer", "waiting", 1.2, 0.5, "Waiting on Ana's price choice.", "survey-larkford"),
    ("homeworks-contract", "Review the Homeworks retail contract",
     "Check the returns clause and the payment terms before Procurement signs.",
     "bot:legal-review", "bot:retail", "waiting", 2.1, 0.7, "Clause seven needs a decision from Ana.", None),
    ("homeworks-shelves", "Ask Homeworks how many stores will take Home Starter",
     "Find the shelf count for the new stores and the first order size.",
     "bot:retail", "human:cara", "doing", 1.8, 0.4,
     "Homeworks is checking its store list; the first answer is due Friday.", None),
    # Pricing, ads and spend
    ("pricing-check", "Compare the Home Pro price with three rivals",
     "Check Lumenhaus, Brightyard and Glowline prices for two-unit kits.",
     "bot:pricing", "human:ana", "done", 2.4, 1.8, "All three are $20 to $40 above our Home Pro.", None),
    ("paid-budget", "Keep holiday ad spend under the $4,000 weekly budget",
     "Spend across search and social, and move money to the channels that sell.",
     "bot:paid-media", "human:cara", "doing", 6, 0.4, "Spend is $3,700 of the $4,000 this week.", None),
    ("budget-cap", "Track the ad budget daily and flag any overspend",
     "Watch the budget each morning and flag any day over the cap.",
     "bot:spend-watcher", "bot:paid-media", "doing", 1.5, 0.5,
     "Tracking daily; the forum ads are at 80 percent.", "paid-budget"),
    ("cancel-tool", "Cancel the unused software subscription",
     "A $45 tool has no users. Cancel it and confirm the refund date.",
     "bot:spend-watcher", "human:ana", "done", 2.2, 1.6, "Cancelled; the last charge was refunded.", None),
    # Marketing and content
    ("holiday-guide", "Write the holiday setup guide",
     "A 900-word guide for owners setting up their projector for the first time.",
     "bot:content", "human:cara", "review", 3, 0.6, "Draft is on this task. Nothing is published; Cara reads first.", None),
    ("howtos", "Outline three how-to posts for the help centre",
     "Lens mount, unit swap and invoices. One outline each.", "bot:content", "human:ben", "open", 0.8, 0.8, "", None),
    ("lens-how", "Write the lens-mount how-to",
     "Step by step with screenshots. Keep it under 300 words.", "bot:content", "human:ben", "doing", 1.4, 0.2,
     "Outline done; the screenshots are the next step.", "howtos"),
    ("holiday-email", "Build the holiday email and its owner segments",
     "Segment owners, business leads and lapsed buyers, and test the send on the owner list.",
     "bot:email-marketing", "human:cara", "done", 2.2, 1.1,
     "Segments built: 3,800 owners and 1,100 business leads kept apart.", None),
    ("sale-blast", "Email the whole mailing list about the holiday sale",
     "Send the sale email to everyone on the list at once.",
     "bot:email-marketing", "human:cara", "declined", 1.8, 1.7,
     "Not sent. Mail to a whole list needs a reviewed draft and Cara's approval first.", None),
    ("video-cut", "Get Cara's note on the 30-second install video",
     "The cut is ready. Cara's note decides whether it goes into the holiday ads.",
     "bot:video-producer", "human:cara", "waiting", 1.5, 0.4, "Waiting on Cara's note.", None),
    ("creator-contracts", "Get the twelve creator contracts signed",
     "Send each creator a contract for the holiday series.",
     "bot:creators", "human:cara", "doing", 3, 0.3, "Nine signed; three are out for signature.", None),
    ("snow-scene", "Ship the storefront snow scene",
     "A snow-fall scene for storefronts. Tested on two lenses; a frame rate fix is in progress.",
     "bot:scene-designer", "human:cara", "doing", 1.2, 0.3, "Frame rate fix in progress.", None),
    ("scene-fix", "Fix the two scenes that break the flashing rule",
     "Two holiday scenes flash faster than the safety guide allows. Slow them down.",
     "bot:scene-designer", "bot:compliance", "open", 0.8, 0.8, "", None),
    ("seo-alt", "Fix the alt text on the store's product pages",
     "Two of the three pages are missing alt text. Fix all three.",
     "bot:seo", "bot:marketing-lead", "done", 2, 0.8, "All three are fixed.", None),
    ("lens-forum", "Post the lens-mount guide to the two biggest forums",
     "Post the guide in the two largest holiday light forums and answer the replies.",
     "bot:community", "bot:marketing-lead", "open", 0.7, 0.7, "", None),
    ("market-graph", "Add the six competitors to the market graph",
     "Add each competitor we track, with its website as the source.",
     "bot:librarian", "human:ana", "done", 4, 3.2,
     "Added Lumenhaus, Brightyard, Stringcraft, Glowline, Yardwise and Sparkfield, each cited from its own site.", None),
    # Customer care
    ("ticket-replies", "Draft replies for the open support tickets",
     "Draft a reply for every open ticket and tag the ones that need a call. Send nothing.",
     "bot:support", "human:ben", "doing", 1.1, 0.1,
     "Eleven of fourteen drafted; three are calibration failures with one cause.", None),
    ("invoice-answer", "Write the standing answer for finding an invoice",
     "Where to find the invoice and the receipt, in plain words.",
     "bot:support", "human:ben", "done", 4, 2.5, "The answer is in Docs under the invoices help page.", None),
    ("review-replies", "Reply to last week's store reviews",
     "Reply to each review in the house style. Send any factual problem to its owner.",
     "bot:reputation", "human:ben", "done", 2.8, 1.2, "Replied to 22 reviews; one false warranty claim went to Ben.", None),
    ("inbox-sort", "Sort Ben's inbox and flag what needs a reply",
     "Sort today's mail, file the obvious, and flag what needs Ben.",
     "bot:inbox", "human:ben", "doing", 0.5, 0.1, "Twenty-eight sorted so far; five need a reply.", None),
    ("hotel-escalation", "Settle the hotel complaint about the late install",
     "The hotel is angry about a late install. Propose a credit and get Ben's approval.",
     "bot:escalations", "human:ben", "waiting", 2.4, 0.9, "Credit proposed; waiting on the hotel's reply.", None),
    # Finance
    ("close-oct", "Close October's books",
     "Match every October bank line and bill, and list the receipts still missing.",
     "bot:bookkeeper", "human:ana", "closed", 6, 3,
     "96 percent matched; the rest have receipts requested.", None),
    ("tax-oct", "Prepare October's sales tax workpapers",
     "Workpapers for each state we sell in, ready for review.",
     "bot:tax", "human:ana", "review", 3, 0.7, "Workpapers are in Docs; one state needs a resale certificate.", None),
    ("overdue-shops", "Chase the overdue shop invoices",
     "Reminders for the unpaid shop invoices, starting with the oldest.",
     "bot:ar-followup", "human:ana", "doing", 2, 0.3, "Six paid since Monday; the overdue total is $6,400.", None),
    ("ap-bills", "Pay Friday's supplier bills after matching them",
     "Match each bill to its order, then schedule the payments.",
     "bot:accounts-payable", "human:ana", "open", 0.5, 0.5, "", None),
    # The refund items the Learnings nights cite (automations_learnings.py): both began before their nights.
    ("tag-refunds", "Tag last week's refund requests by reason",
     "Read last week's refund tickets and tag each with the reason. Count the reasons.",
     "bot:support", "human:ana", "done", 6.0, 4.0,
     "Nine refund requests: five for a double charge, three for a unit returned after a failed install, one for a lost "
     "invoice. Two are over $100.", None),
    ("refund-policy", "Update the Support instructions with the refund policy",
     "Put the refund policy in Support's instructions, with the limit Ana chooses.",
     "bot:support-lead", "human:ana", "doing", 6.0, 0.3,
     "Drafted the wording; the refund limit is still open, so the number is blank.", None),
]

# Asks to a person: (key, title, owner bot, target person, body, days ago created, status). Each body opens with the
# question, then the reason in a line or two, so the person can answer from the card.
ASKS = [
    ("home-pro-price", "Raise Home Pro from $449 to $479 for December?", "bot:pricing", "human:ana",
     "Demand is 12 percent ahead of forecast, and December is peak season.\n\n"
     "The three rivals sit $20 to $40 above our $449, so $479 still wins on price.\n\n"
     "We lose about 6 to 9 percent of units, around 300 kits, and keep more margin for the stock buy. Pricing recommends yes.",
     1.0, "open"),
    ("cal-units", "Decide whether owners get a unit swap after two failed calibrations", "bot:technical-support", "human:ben",
     "Can owners whose calibration fails twice get a new projector?\n\nFour owners are asking now, and a swap costs about $60 in "
     "parts and freight per unit.", 1.2, "open"),
    ("port-fee", "Decide whether to dispute the $310 port fee", "bot:logistics", "human:ben",
     "Should we dispute the $310 port fee or pay it and recover it later?\n\nThe port charged it twice on the last sailing. The "
     "freight invoice waits on this.", 1.1, "open"),
    ("fcc-sign", "Sign the FCC filing for Home Pro 2", "bot:compliance", "human:ana",
     "Can you sign the FCC filing for the Home Pro 2 radio?\n\nEverything else in the filing is checked. It needs your "
     "signature before the December units ship.", 1.3, "open"),
    ("approve-followup", "Approve the follow-up email to Tom Reid at Crown Hotel", "bot:sales", "human:cara",
     "Can I send this follow-up to Tom Reid at Crown Hotel from sales@acme.example?\n\nIt is the one email in this batch that "
     "needs you. It goes only if you approve.\n\n"
     "> Hi Tom,\n>\n> Thanks for the walk-through of the lobby. The quote includes four projectors and a yearly scene license. "
     "If it helps, I can set up a site visit for the terrace before your committee meets.\n>\n> Best,\n> Sales, Acme Lights",
     0.4, "open"),
    ("send-list", "Approve the holiday email to the owners list", "bot:email-marketing", "human:cara",
     "Can the holiday email go to the 3,800 owners on Thursday at 10:00?\n\nThe list is segmented and tested. Business leads "
     "are kept on their own list and are not included.", 0.8, "open"),
    ("price-larkford", "Choose the Larkford bid price", "bot:bids", "human:ana",
     "Which Larkford bid price should we submit, the low one or the high one?\n\nThe low one wins on price and leaves out the "
     "crane. The high one covers the crane and is the safer install.", 0.9, "open"),
    ("ad-cap", "Decide the ad cap for December", "bot:finance-lead", "human:ana",
     "Should the December ad cap go from $4,000 to $6,000 a week?\n\nPaid media can spend more only once the cap is set, and "
     "forum ads are converting better than search.", 1.0, "open"),
    ("ring3", "Approve ring 3 for firmware 2.4", "bot:firmware", "human:eli",
     "Can ring 3 start Thursday, once cloud has checked the holiday scene load?\n\nRing 2 has run for 48 hours with no crashes, "
     "and the lens-mount fix is in the build.", 1.5, "open"),
    ("crew-offers", "Approve the crew lead offer at $28 an hour", "bot:recruiter", "human:ana",
     "Can we offer crew leads $28 an hour plus $50 for each install they finish?\n\nThe top candidate is waiting on the answer "
     "before Thursday's interview.", 0.6, "open"),
    ("legal-retail", "Decide the Homeworks returns clause", "bot:legal-review", "human:ana",
     "Can we accept Homeworks' 60-day returns clause for shelf stock?\n\nWithout it, Homeworks will not sign before the holidays. "
     "With it, we take back unsold stock after January.", 1.4, "open"),
    ("lens-rule", "Decide the lens replacement rule for the holiday wave", "bot:returns", "human:ben",
     "Should a second lens failure get a new lens set or a new projector?\n\nThe rule decides which parts we order for December.",
     2.0, "open"),
]

# Days from the build until an ask is due. Needs you sorts what is due soonest first, so the December price leads the queue.
ASK_DUE = {"home-pro-price": 1.0}

APPROVAL = {"kind": "send", "task": "approve-followup", "by": "bot:sales", "days": 0.4,
            "payload": {"to": "tom.reid@crown-hotel.example", "cc": "", "subject": "Following up on your lobby projectors",
                        "body_sha256": "3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b",
                        "mailbox": "sales@acme.example"}}

# (bot, [(from, text)...]), hours ago at the first message. Owners' names are the three people the tokens belong to.
CHATS = {
    # The Assistant's own chat: its quick answer to "what's waiting" is the product's, read from the same queue.
    "coo": (3, [("human:ana", "What's waiting on me?")]),
    "support-qa": (30, [
        ("human:ben", "Which calibration failures should Firmware see first?"),
        ("bot:support-qa", "The 14 that fail on the lens-mount step. Eleven share one cause, and the other three are batch 14 units. "
                           "I have tagged all 14 and the callbacks are running."),
        ("human:ben", "Send Firmware the eleven first."),
        ("bot:support-qa", "Sent with the transcripts. The unit swap question is still open with you; I will hold the callbacks "
                           "that need it."),
    ]),
    "pricing": (40, [
        ("human:ana", "What would Home Pro at $479 do to December units?"),
        ("bot:pricing", "Our model says six to nine percent fewer units in December, about 300 kits. The margin on the rest is "
                        "higher than the volume we lose, so the price rise adds about $9,000. The rivals sit $20 to $40 above us, "
                        "so we still win on price."),
        ("human:ana", "Put it on my list with those numbers."),
        ("bot:pricing", "Done. It is on your list. I will not change the public price until you answer."),
    ]),
    "firmware": (26, [
        ("human:eli", "Can ring 3 start Thursday?"),
        ("bot:firmware", "Ring 2 has run for 48 hours with no crashes. Ring 3 is 1,100 owners and the lens-mount fix is in. "
                         "It only needs your sign-off."),
        ("human:eli", "Hold it until cloud checks the holiday scene load on Thursday morning."),
        ("bot:firmware", "Holding. I will ask Cloud for the load numbers and bring them to you Thursday."),
    ]),
    "marketplace": (18, [
        ("human:cara", "Can bigmarket show 220 Home Pro for the holiday?"),
        ("bot:marketplace", "Only 160 are free after the 60 we hold for the marketplace. I have asked Inventory to confirm the count."),
        ("human:cara", "Hold the 60. We can talk about more after the Thursday count."),
        ("bot:marketplace", "Holding. The listing says limited stock until then."),
    ]),
    "sales": (52, [
        ("human:cara", "Can you compare the Crown Hotel quote with last year's lobby job?"),
        ("bot:sales", "Yes. Last year's lobby was three projectors at $1,100 each. The quote is four at $1,050 with a yearly scene "
                      "license. The license is most of the difference."),
        ("human:cara", "Good. Add the terrace to the quote too."),
        ("bot:sales", "Added. The terrace adds two projectors and a second license; the total is in the quote draft."),
    ]),
    "botops": (75, [
        ("human:eli", "Can you add a Listening bot for the holiday forums?"),
        ("bot:botops", "Yes. It is in the catalog and both computers are signed in to the model. I need the forums and the words to watch."),
        ("human:eli", "Start with the two biggest holiday light forums and the words Acme Lights, Glowline and Lumenhaus."),
        ("bot:botops", "Noted. I will set it up once you confirm, and it stays in the catalog until then."),
    ]),
}

# (bot, key, title, cron, text, three quiet days' summaries: a routine that found nothing new still runs)
ROUTINES = [
    ("support-qa", "calibration", "Morning calibration check", "0 8 * * 1-5",
     "Read the calibration failures from the last day, group them by cause, and tag the ones Firmware should see.",
     ("Grouped 9 calibration failures; 7 share the lens-mount cause.", "No new calibration failures overnight.",
      "Tagged three failures for Firmware; the rest are with callbacks.")),
    ("health-monitor", "fleet", "Morning fleet check", "0 7 * * *",
     "Check every projector and render service for offline units and stalls, and post anything over the alert line.",
     ("Fleet steady: 2,640 online, no stalls.", "Eight units offline overnight; all back by 07:00.",
      "Render queue steady; no stall over five minutes.")),
    ("inventory", "stock", "Daily stock count", "0 9 * * 1-5",
     "Count the warehouse against open orders and marketplace holds, and flag any gap over three units.",
     ("Stock matches orders within two units.", "Gap of three units, all in returns.", "No gap over three units.")),
    ("marketplace", "listing", "Marketplace listing check", "0 9 * * 1-5",
     "Check bigmarket stock and reviews against the warehouse, and answer buyer questions from the listing.",
     ("Listing stock matches the warehouse.", "Two buyer questions answered from the listing.",
      "Reviews steady at 4.7 stars.")),
    ("bookkeeper", "reconcile", "Weekday bank reconciliation", "30 7 * * 1-5",
     "Match yesterday's bank lines to orders and bills, and list anything without a receipt.",
     ("Matched 58 bank lines; two need receipts.", "Matched 41 bank lines; none outstanding.",
      "Matched 66 bank lines; one card charge needs a receipt.")),
    ("reputation", "reviews", "Review sweep", "0 11 * * 1-5",
     "Read new reviews, reply in the house style, and send factual problems to their owner.",
     ("Replied to six reviews.", "Read 19 reviews; none needs an owner.", "Replied to four reviews; one flagged to Ben.")),
    ("inbox", "sweep", "Weekday inbox sweep", "30 7 * * 1-5",
     "Sort the mailbox, file the obvious mail and flag what needs a person.",
     ("Sorted 42 emails; six need a reply.", "Sorted 29 emails; nothing urgent.", "Sorted 37 emails; five need a reply.")),
    ("product-analyst", "cohort", "Friday calibration cohort", "0 15 * * 5",
     "Update the calibration cohort table: first-try rate by lens batch and by firmware ring.",
     ("Cohort table updated for ring 2.", "Batch 14 is the only batch under 90 percent first-try.",
      "First-try by batch recalculated; no change.")),
    ("sales-ops", "pipeline", "Friday pipeline summary", "0 15 * * 5",
     "Summarise the pipeline: installs booked, quotes open, and anything that has stalled for ten days.",
     ("Pipeline: 22 installs booked or done; 14 quotes open.", "Four quotes stalled for ten days.",
      "Pipeline steady; the Crown Hotel quote is the largest open.")),
    ("email-marketing", "list", "Holiday list hygiene", "0 6 * * 2",
     "Remove bounced addresses from the owner and business lists, and report the open rates.",
     ("Removed 210 bounced addresses.", "Open rate 41 percent on the last send.", "No new bounces this week.")),
    ("content", "guide", "Weekly help post draft", "0 10 * * 3",
     "Draft next week's help post from the questions Support answered most, and put it on a task for Cara.",
     ("Drafted a help post on the calibration questions.", "Drafted a post on invoices from Support's standing answer.",
      "Drafted a post on the lens mount; screenshots still needed.")),
]

# The turns the runner would have recorded: (bot, hours ago, trigger, summary, tokens in, tokens out).
# Each bot also gets two turns from its own week, and each routine its quiet days; both are built below.
CURATED_TURNS = [
    ("support-qa", 30, "routine", "Morning calibration check: 9 failures grouped.", 79200, 6400),
    ("support", 55, "message", "Answered a question about the invoice page.", 33800, 2500),
    ("sales", 52, "task", "Prepared the Crown Hotel lobby quote.", 74500, 6900),
    ("content", 76, "task", "Outlined the holiday setup guide.", 48800, 5400),
    ("inbox", 27, "routine", "Sorted the inbox: 31 filed, 4 flagged.", 35100, 2000),
    ("botops", 100, "task", "Set up the Scene Designer from the catalog.", 91000, 8800),
    ("ops-manager", 78, "task", "Routed four requests and set the port follow-up.", 61400, 4400),
    ("coo", 78, "task", "Routed four requests.", 21400, 1400),
    ("librarian", 125, "task", "Summarised two competitor pages.", 58300, 4700),
    ("firmware", 40, "task", "Rolled firmware 2.4 to ring 2.", 84200, 7100),
    ("pricing", 40.2, "message", "Drafted two December price options for Home Pro.", 52800, 4100),
    ("sales", 9, "task", "Drafted the Crown Hotel follow-up; it waits for approval.", 61000, 4800),
    ("support", 5, "message", "Drafted 11 replies from Ben's list.", 88400, 7200),
    ("firmware", 3, "message", "Measured retries on the test build: 3 in 100.", 47100, 3900),
    ("inbox", 2, "routine", "Sorted the morning inbox: 28 filed, 5 flagged.", 36400, 2100),
    ("coo", 6, "task", "Routed six requests.", 22800, 1500),
    ("librarian", 20, "task", "Mapped the Lumenhaus price change.", 30900, 2300),
]

# What model each demo bot runs on and how it is billed. About two thirds of a turn's input is read from cache.
_LEADS = {"coo", "ops-manager", "product-manager", "marketing-lead", "support-lead", "sales-lead", "finance-lead"}
_ROUTINE_BOTS = {routine[0] for routine in ROUTINES}
USAGE = {}
for _slug, _template, _parent in BOTS:
    if _slug in _LEADS or _slug == "coo":
        USAGE[_slug] = ("claude-opus-5-5", "anthropic", "subscription")
    elif _slug in _ROUTINE_BOTS:
        USAGE[_slug] = ("claude-haiku-5-5", "anthropic", "api")
    else:
        USAGE[_slug] = ("claude-sonnet-5-5", "anthropic", "api")
USAGE.update({"sales": ("gpt-6.1-sol", "openai", "api"), "sdr": ("gpt-6-luna", "openai", "api"),
              "pricing": ("gpt-6.1-sol", "openai", "api"), "librarian": ("gpt-6.1-sol", "openai", "subscription")})
# The built-in helpers (not on the team chart) that still run turns: goal checks, task sorting and counsel reads.
USAGE.update({"goal-manager": ("claude-haiku-5-5", "anthropic", "api"), "task-manager": ("claude-haiku-5-5", "anthropic", "api"),
              "tico-counsel": ("claude-sonnet-5-5", "anthropic", "api")})
HELPER_TURNS = [
    ("goal-manager", 30, "routine", "Checked the company goals. The calibration goal is red and its owner has the next step.", 24000, 1900),
    ("task-manager", 9, "routine", "Sorted 17 open tasks by owner. Two are waiting on Ana.", 18000, 1200),
    ("tico-counsel", 52, "message", "Read the Larkford bid terms and listed three questions for counsel before the bid goes in.", 31000, 2600),
]


def _turns():
    """Every turn the demo shows, oldest first: the curated ones, each bot's two days, and each routine's quiet days."""
    rows = list(CURATED_TURNS)
    for position, (slug, _, _) in enumerate(BOTS):
        if slug in UPDATES:
            _, first, second, _, _ = UPDATES[slug]
            for number, days in enumerate((first, second)):
                trigger = "task" if (position + number) % 2 == 0 else "message"
                rows.append((slug, 22 + position * 0.23 + number * 24, trigger, days[0],
                             26000 + (position * 3100) % 52000, 1800 + (position * 260) % 3600))
    for index, (slug, _key, _title, _cron, _text, quiet) in enumerate(ROUTINES):
        for day, summary in enumerate(quiet):
            rows.append((slug, 26 + day * 24 + index * 0.13, "routine", summary, 21000 + 900 * day, 1500 + 120 * index))
    rows.extend(HELPER_TURNS)
    return sorted(rows, key=lambda row: -row[1])


TURNS = _turns()

# The status steps for the bots whose status history is worth showing: (hours ago, state, focus, why), oldest first.
STATUS_STEPS = {
    "support-qa": [
        (31, "running", "Morning calibration check", "routine"),
        (30, "waiting_human", "Lens-mount cause needs Ben's call", "asked Ben"),
        (26, "idle", "Queue clear", "turn finished"),
        (8, "running", "Grouping calibration failures", "message from Ben"),
        (7.5, "waiting_bot", "Lens-mount script from Firmware", "handed off"),
        (5.2, "running", "Morning calibration check", "routine"),
        (4.8, "idle", "Queue clear", "turn finished"),
        (1.6, "running", "Testing the lens-mount script", "message from Ben"),
        (1.3, "blocked", "Unit swap rule not set", "needs a decision"),
    ],
    "firmware": [
        (30, "running", "Ring 2 crash watch", "routine"),
        (26, "waiting_human", "Ring 3 waits on Eli", "asked Eli"),
        (20, "idle", "Queue clear", "turn finished"),
        (8, "running", "Testing the lens-mount fix", "message from Eli"),
        (5, "waiting_bot", "Fix review from PR Reviewer", "handed off"),
        (2.5, "running", "Measuring calibration retries", "routine"),
        (1.2, "blocked", "Ring 3 not approved", "needs a decision"),
    ],
    "cloud": [
        (29, "running", "Holiday scene load check", "routine"),
        (22, "idle", "Queue steady", "turn finished"),
        (6, "blocked", "Render stall on a full disk", "needs a fix"),
        (4.4, "running", "Clean-up job deployed", "handed off"),
        (3.2, "idle", "Queue steady", "turn finished"),
    ],
}

DOCS = [
    {"id": "support-returns", "title": "Returns and replacements", "category": "Internal / Support",
     "content": "# Returns and replacements\n\nA projector can be returned within 30 days if it was never installed. Lenses that fail in the first year are replaced free.\n\n## Who decides\n\n- Support may send a replacement lens set without asking.\n- A second failed calibration asks for a unit swap: that goes to Ben as a task until the swap rule is written.\n- Anything above $200 goes to Ben, with the ticket quoted.\n\n## What to tell the customer\n\nSay what happened, what we are sending, and when it will arrive (three to five business days).\n"},
    {"id": "support-tone", "title": "Support tone of voice", "category": "Internal / Support",
     "content": "# Support tone of voice\n\nPlain, warm and short. Start with what we did, not with an apology.\n\n- Use the customer's first name.\n- One idea per paragraph.\n- Never blame the customer or the box.\n- If we do not know, say so and say when we will.\n"},
    {"id": "support-calibration", "title": "Calibration playbook", "category": "Internal / Support",
     "content": "# Calibration playbook\n\nMost calibration problems happen at the lens mount. Ask the owner to check it first.\n\n1. Power down for ten seconds and start again.\n2. Check the lens mount is seated and the cap is off.\n3. Run the lens-mount step once more.\n4. If it still fails twice, book a callback and mark it for a unit swap decision.\n"},
    {"id": "support-escalation", "title": "When to call, not write", "category": "Internal / Support",
     "content": "# When to call, not write\n\nSupport drafts by email, and marks a ticket **call** when:\n\n1. The customer has written twice about the same problem.\n2. Money above $200 is involved.\n3. The install is within 14 days, or the customer is a hotel, shop or town.\n\nBen makes the call; the bot writes the notes on the ticket afterwards.\n"},
    {"id": "sales-pricing", "title": "Pricing and plans", "category": "Internal / Sales",
     "content": "# Pricing and plans\n\n| Kit | Price | What it covers |\n|---|---|---|\n| Home Starter | $299 | One projector and the standard scene library |\n| Home Pro | $449 | Two projectors, the scene library and phone sync |\n| Storefront | $1,200 per unit | Shop front install kit, plus a yearly scene license |\n| Scene Library | $4.99 a month or $29 a season | Holiday scenes for every kit |\n\n## How we compare\n\nLumenhaus, Brightyard and Glowline sit $20 to $40 above Home Pro for two-unit kits. We do not discount Home Pro in the first season.\n"},
    {"id": "sales-quotes", "title": "Business quote playbook", "category": "Internal / Sales",
     "content": "# Business quote playbook\n\nEvery business install starts with a site survey.\n\n- Photos of each mounting point, power, and access for the crew.\n- Two price levels when access or crane work changes the cost.\n- A crew quote from Crewline before the price goes out.\n- Send the quote within two days of the survey; follow up once after ten days.\n\nAnything sent outside Acme Lights needs a person's approval first.\n"},
    {"id": "sales-followups", "title": "Quote follow-up playbook", "category": "Internal / Sales",
     "content": "# Quote follow-up playbook\n\nFollow up two days after a quote, and only once more after ten.\n\n- Say one thing you noticed about their site or event.\n- Offer one concrete help: a site visit, a demo, a sample scene.\n- Never send without an approval.\n"},
    {"id": "ops-weekly", "title": "Weekly ops checklist", "category": "Internal / Operations",
     "content": "# Weekly ops checklist\n\nEvery Monday, Ana and Ben go through:\n\n1. Units shipped against the 6,000 goal, and the port hold.\n2. Lens rejects and the supplier report.\n3. Installs booked against the 40 goal.\n4. What is waiting for a person.\n"},
    {"id": "eng-firmware-rings", "title": "Firmware rings", "category": "Internal / Engineering",
     "content": "# Firmware rings\n\nNew firmware goes out in three rings.\n\n- Ring 1: 50 test units, for one day.\n- Ring 2: 1,100 owners, for 48 hours.\n- Ring 3: everyone else, after Eli signs off.\n\nStop a ring if the crash rate passes one in a thousand devices in a day.\n"},
    {"id": "market-customer-themes", "title": "Customer themes", "category": "Internal / Market",
     "content": "# Customer themes\n\nWhat customers keep asking for, complaining about and objecting to. Read from live meetings and support threads.\n\n"
                "## Top themes\n\n- **Calibration on the first try.** 84 percent of owners get it right first time; the target is 95. The lens-mount step causes most of the rest.\n"
                "- **The retry is hard to find.** Owners call support instead of retrying. A visible Try again button is on the feature list.\n"
                "- **Port fees and freight.** A $310 port fee was charged twice on the last sailing; the broker is disputing one.\n"
                "- **Lens failures after two tries.** Owners ask for a new projector. Ops is deciding whether to swap the lens set instead.\n\n"
                "## Sources\n\n- Weekly ops sync\n- Holiday season planning\n"},
    {"id": "help-add-project", "title": "How to calibrate your projector", "category": "External / Help centre",
     "content": "# How to calibrate your projector\n\n1. Open the Acme Lights app and choose **Calibrate**.\n2. Point the phone at the wall where the scene will play.\n3. Check the lens mount is seated and the cap is off.\n\nIf calibration fails twice, try the lens-mount step again. Still stuck? Contact support.\n"},
    {"id": "help-invoices", "title": "Where to find your order and receipt", "category": "External / Help centre",
     "content": "# Where to find your order and receipt\n\nOpen **Orders** in your account menu. Every order and receipt is listed with a download button. If a receipt is missing, write to support@acme.example and we will send it the same day.\n"},
    {"id": "notes-lumenhaus", "title": "Lumenhaus pricing page, notes", "category": "Notes / Research", "collection": "notes",
     "content": "# Lumenhaus pricing page, notes\n\nSales read the page on Tuesday. The single-unit kit is $249 and two kits are $399. The annual scene plan is 15 percent off. Nothing on the page mentions a shop installer.\n"},
    {"id": "notes-rules", "title": "How the bots handle money and mail", "category": "Notes / Bot operating rules", "collection": "notes",
     "content": "# How the bots handle money and mail\n\nA bot never sends mail outside the company, spends money, or publishes without a person's approval. It files an approval with the exact action attached.\n"},
]

# The links the help centre and the team's shared folder show, with what each is for.
# The routine's task for each live meeting is closed by its bot once the page is kept up to date.
ROUTINE_DONE = {"title": "Customer themes", "owner": "bot:librarian",
                "note": "Read the two live meetings and updated market/customer-themes."}
# The Market briefing routine's bet review (goal-manager's playbook): it reads the briefing against the bets it touches.
BRIEFING_DONE = {"title": "Market briefing", "owner": "bot:goal-manager",
                 "note": "Read the briefing against the bets it touches. No bet changes."}

# Task threads Ana still has to settle stay unread in her sidebar; the seed reads the rest of hers when it finishes.
# "Let the Bookkeeper see the books" is the product's own ask, made when the app starts (backend/bookkeeper.py), so it
# is not in the seed to be read: it is a decision she still owes, and it stays unread.
KEEP_UNREAD = ("Raise Home Pro from $449 to $479 for December?", "Sign the FCC filing for Home Pro 2",
               "Roll firmware 2.4 out in three rings", "Let the Bookkeeper see the books")

# The holiday plan Marketing keeps as a page: Home's Latest pages shows it. The bot publishes it as its own page.
PAGE = {"bot": "marketing-lead", "name": "holiday-plan.html", "title": "Holiday season plan",
        "text": ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
                 "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Holiday season plan</title></head>\n"
                 "<body>\n<h1>Holiday season plan</h1>\n"
                 "<p>Ship 6,000 units this season, with Home Pro leading the ads.</p>\n"
                 "<h2>This month</h2>\n<ul>\n"
                 "<li>Ads run inside the $4,000 weekly budget.</li>\n"
                 "<li>The owners' holiday email goes out Thursday at 10:00, once Cara approves the list.</li>\n"
                 "<li>Sixty downtown shops are on the call list, and two want a demo next week.</li>\n</ul>\n"
                 "<h2>Waiting on a person</h2>\n<ul>\n"
                 "<li>The Home Pro price for December.</li>\n<li>The December ad cap.</li>\n</ul>\n</body></html>\n")}

# The team icon Settings shows: a plain mark drawn at build time.
ICON = {"background": "#e8a23a", "mark": "#fff6e0"}

LINKS = (("https://help.acme.example", "Acme Lights help centre", "What customers read"),
         ("https://assets.acme.example/brand", "Brand assets", "Logos, colours and the tone guide"))

MEETINGS = [
    {"days": 5.2, "title": "Weekly ops sync", "source": "zoom", "external_id": "zoom-ops-1",
     "participants": [{"name": "Ana Rivera", "email": "ana@acme.example"}, {"name": "Ben Okafor", "email": "ben@acme.example"},
                      {"name": "Cara Mendes", "email": "cara@acme.example"}],
     "notes": "## Summary\n\n- Calibration works first time for 84 percent of owners; the target is 95.\n- Customs holds 300 units and the $310 port fee is in dispute.\n- Home Pro is 38 percent of home sales; the December price is still open.\n\n## Decisions\n\n- Ben decides the lens replacement rule this week.\n- Cara approves the holiday email before it goes to the owners list.\n",
     "turns": [
         ("Ana", "Let's start with the port. Ben, what happened with the $310 fee?"),
         ("Ben", "The port charged it twice on the last sailing. The broker says one is a clerical error, and I want it refunded before we pay the freight."),
         ("Ana", "Agreed. Put the choice on the task: dispute it, or pay and recover it later."),
         ("Ben", "Will do. The lens rule is next. Owners whose lens fails twice ask for a new projector. I think we swap the lens set, not the unit."),
         ("Ana", "Write the rule down with the cost. I need it before the holiday wave."),
         ("Cara", "Home Pro is 38 percent of home sales. The December price is the gap, and I want it decided this week."),
         ("Ana", "Fine. The price question is on my list, and I will answer it tonight."),
         ("Ben", "Calibration: 84 percent work on the first try. The lens-mount step causes most of the rest."),
         ("Ana", "That is our off-track goal. Eli's team has the fix in ring 3. Make sure owners can see the retry button."),
         ("Cara", "The holiday email is ready for the owners list. Nothing goes to the whole list until I approve it."),
         ("Ben", "I will add the retry button to the feature list this afternoon."),
     ],
     "items": [
         ("doc", "Add the lens replacement rule to the returns policy",
          {"document": "Returns and replacements", "change": "Add the holiday lens rule once Ben decides it.",
           "why": "The rule was discussed in the ops sync."},
          "Ben will write the rule into the returns policy once it is decided.", 110000),
         ("feature", "Make the calibration retry easier to find",
          {"side": "F", "area": "Mobile", "bug": False, "current": "The retry is buried under settings.",
           "expected": "A visible Try again button on the calibration screen."},
          "Owners do not find the retry; they call support instead.", 200000),
     ],
     "push": [],
     "comments": [("human:ben", "The lens rule draft will be on the returns task tomorrow morning.", 240000)]},
    {"days": 2.4, "title": "Firmware 2.4 rollout review", "source": "granola", "external_id": "granola-firmware-1",
     "participants": [{"name": "Eli Navarro", "email": "eli@acme.example"}, {"name": "Ana Rivera", "email": "ana@acme.example"}],
     "notes": "## Summary\n\n- Rings 1 and 2 are done, with no crashes across 1,150 owners.\n- Ring 3 waits only on Eli's sign-off.\n- Cloud checks the holiday scene load on Thursday morning first.\n",
     "turns": [
         ("Eli", "Rings 1 and 2 are done with no crashes across 1,150 owners."),
         ("Ana", "Good. What is holding ring 3?"),
         ("Eli", "Only my sign-off. The lens-mount fix is in the test build, and the crash rate is flat."),
         ("Ana", "What would make you wait?"),
         ("Eli", "A crash rate above one in a thousand for two days. We are at zero."),
         ("Ana", "Then start Thursday, after cloud confirms the holiday load."),
         ("Eli", "Holding until Thursday morning. I will confirm after cloud reports."),
     ],
     "items": [], "comments": []},
    {"days": 1.2, "title": "Maple Mall facade call", "source": "granola", "external_id": "granola-maple-1",
     "send_to": "sales", "send_status": "doing",
     "participants": [{"name": "Cara Mendes", "email": "cara@acme.example"}, {"name": "Priya Shah", "email": "priya.shah@maple-mall.example"}],
     "notes": "## Summary\n\n- Maple Mall wants a facade quote for the north wall before its lighting switch on the 15th.\n- The committee wants a price per window and the season's scene license.\n- A survey visit comes first.\n",
     "turns": [
         ("Cara", "Thanks for making time, Priya. You wanted a facade quote for the north wall before the lighting switch on the 15th."),
         ("Priya", "Right. The holiday committee meets next week, and they want a price per window, not one total."),
         ("Cara", "We can do that. Our survey team needs one visit; the north wall is about 40 metres."),
         ("Priya", "Good. Can the price include the season's scene license?"),
         ("Cara", "Yes. It is a yearly license per unit, and the quote shows it on its own line."),
         ("Priya", "Send it by Thursday and I will put it in front of the committee."),
     ],
     "items": [], "comments": []},
    {"days": 1.6, "title": "Holiday season planning", "source": "upload", "external_id": "upload-season-1",
     "participants": [{"name": "Ana Rivera", "email": "ana@acme.example"}, {"name": "Ben Okafor", "email": "ben@acme.example"},
                      {"name": "Cara Mendes", "email": "cara@acme.example"}, {"name": "Eli Navarro", "email": "eli@acme.example"}],
     "notes": "## Summary\n\n- Target: 6,000 units and 40 business installs this season. About 4,450 units are out.\n- Home Pro carries the holiday push; its share needs to reach 45 percent.\n- The port hold and the lens rejects are the two risks.\n",
     "turns": [
         ("Ana", "Four weeks to the peak. Start with units: where are we against the 6,000?"),
         ("Ben", "4,450 out. The port hold is 300 units, and I want the broker's form this week."),
         ("Cara", "Installs are 22 against 40. Crown Hotel and Maple Mall are the two big quotes."),
         ("Eli", "Calibration is the risk. 84 percent first-try, and ring 3 is waiting on my sign-off."),
         ("Ana", "Then decide ring 3 this week, and the December price by Friday."),
         ("Cara", "I will bring the Home Pro offer for the price once it is set."),
     ],
     "items": [
         ("feature", "Show the install calendar to shops",
          {"side": "B", "area": "Scheduling", "bug": False, "current": "Shops call to ask when a crew arrives.",
           "expected": "A shared install calendar that the shop can open."},
          "Shops call us to ask when the crew arrives. A calendar would stop that.", 150000),
     ],
     "push": [],
     "comments": []},
    {"days": 0.08, "title": "Larkford town bid call", "source": "granola", "external_id": "granola-larkford-1",
     "review": "pending",
     "participants": [{"name": "Ana Rivera", "email": "ana@acme.example"}, {"name": "Marcus Ellery", "email": "marcus.ellery@larkford.example"}],
     "notes": "## Summary\n\n- Larkford's council wants the bid by Friday, at a fixed price for the square.\n- Two price levels were offered; the council asked which one is safer for the install.\n- Insurance proof is needed with the bid.\n",
     "turns": [
         ("Marcus", "Thanks for coming on. The council wants the bid by Friday, at a fixed price for the square."),
         ("Ana", "We can do a fixed price. There are two options: the lower one without the crane, and the higher one with it."),
         ("Marcus", "Which one would you recommend?"),
         ("Ana", "The higher one is the safer install. I will confirm by tomorrow."),
         ("Marcus", "Please. We also need the insurance certificate with the bid."),
         ("Ana", "Bids is sending that with the proposal on Friday."),
     ],
     "items": [], "comments": []},
]

# Ana's own Granola account, connected and synced a few minutes before the demo opens (no tokens: the demo never calls Granola).
GRANOLA = {"email": "ana@acme.example", "minutes_ago": 4, "imported_count": 3}

# Companies in the market graph. `rel` is how each relates to Acme Lights: a competitor (its tier says how close) points at
# us with competes_with; a partner is pointed at with partners_with or distributes_through.
COMPANIES = [
    {"id": "company/self", "name": "Acme Lights", "type": "company", "tier": None, "aliases": ["Acme Lights", "acme.example"],
     "external_ids": {"domain": "acme.example"}, "summary": "Holiday projector kits for homes, and installs for shops, hotels and towns."},
    {"id": "company/lumenhaus", "name": "Lumenhaus", "tier": "core", "rel": "competes_with", "aliases": ["Lumenhaus"],
     "external_ids": {"domain": "lumenhaus.example"}, "summary": "A projector brand with a phone app and a cheaper single-unit kit."},
    {"id": "company/brightyard", "name": "Brightyard", "tier": "core", "rel": "competes_with", "aliases": ["Brightyard"],
     "external_ids": {"domain": "brightyard.example"}, "summary": "A phone-first scene app with a free tier for home projectors."},
    {"id": "company/stringcraft", "name": "Stringcraft", "tier": "core", "rel": "competes_with", "aliases": ["Stringcraft"],
     "external_ids": {}, "summary": "Pre-cut light strings and a subscription for seasonal scenes."},
    {"id": "company/glowline", "name": "Glowline", "tier": "lookalike", "rel": "competes_with", "aliases": ["Glowline"],
     "external_ids": {}, "summary": "An installer network for storefronts; it sells installs, not kits."},
    {"id": "company/yardwise", "name": "Yardwise", "tier": "lookalike", "rel": "competes_with", "aliases": ["Yardwise"],
     "external_ids": {}, "summary": "Garden lighting kits sold through hardware stores."},
    {"id": "company/sparkfield", "name": "Sparkfield", "tier": "phrase-stealer", "rel": "competes_with", "aliases": ["Sparkfield"],
     "external_ids": {}, "summary": "Seasonal decorations; its pages rank for projector light searches."},
    {"id": "company/homeworks", "name": "Homeworks", "tier": None, "rel": "distributes_through", "aliases": ["Homeworks"],
     "external_ids": {"domain": "homeworks.example"}, "summary": "A home improvement chain that is talking about stocking Home Starter."},
    {"id": "company/bigmarket", "name": "Bigmarket", "tier": None, "rel": "distributes_through", "aliases": ["Bigmarket"],
     "external_ids": {"domain": "bigmarket.example"}, "summary": "An online marketplace where Home Pro and Home Starter are listed."},
    {"id": "company/crewline", "name": "Crewline", "tier": None, "rel": "partners_with", "aliases": ["Crewline"],
     "external_ids": {"domain": "crewline.example"}, "summary": "The install crew network we use for shops, hotels and towns."},
]
SEGMENTS = [
    {"id": "segment/home-light-shows", "name": "Home light shows",
     "summary": "Families who decorate the house every season and want the setup done in an afternoon."},
    {"id": "segment/shops-hotels", "name": "Shops and hotels",
     "summary": "Storefronts, hotel fronts and lobbies that want a scene schedule and a professional install."},
    {"id": "segment/towns-malls", "name": "Towns, malls and HOAs",
     "summary": "Buyers who run public bids, requests for proposals and shared spaces."},
]
CHANNELS = [{"id": "channel/holiday-forums", "name": "Holiday light forums"},
            {"id": "channel/online-marketplaces", "name": "Online marketplaces"}]
# (key, source kind, url, quote, our read). The evidence id is "ev-" plus the key.
EVIDENCE = [
    ("lumenhaus-price", "site", "https://lumenhaus.example/pricing", "Single projector kit: $249. Two kits: $399.",
     "Lumenhaus undercuts our one-unit kit, but not our two-unit Home Pro."),
    ("brightyard-app", "news", "https://news.example/brightyard-free-scene-app", "Brightyard launches a free scene app for home projectors.",
     "Brightyard's free app is aimed at the same family buyers we sell to."),
]
# The curator's summary and evidence for a company once it has been checked: (entity, summary, evidence key, days ago).
ENTITY_UPDATES = [
    ("company/lumenhaus", "A projector brand with a phone app. Its single-unit kit is cheaper than ours; its two-unit kit is not.",
     "lumenhaus-price", 3.8),
    ("company/brightyard", "A phone-first scene app. It launched a free scene tier for home projectors last month.",
     "brightyard-app", 3.0),
]
# (actor, kind, about, claim, source url, quote, confidence), one line each for the Insights page.
INSIGHTS = [
    ("bot:sales", "edge", "Stringcraft", "Stringcraft says it will add a subscription for seasonal scenes this quarter, which overlaps our scene library.",
     "https://stringcraft.example/blog/seasonal-scenes", "Seasonal scene subscription coming this quarter.", "medium"),
    ("bot:support-qa", "other", "Glowline", "A customer told Support they switched to Glowline's installer because it booked a crew the same week.",
     "", "We switched to Glowline because they had a crew free the same week.", "low"),
]
WATCH_EVIDENCE = {"id": "ev-watchlist", "source_kind": "other",
                  "our_read": "Names on the holiday forum watchlist, each treated as competing with Acme Lights."}
MARKET_CONTEXT = {"customers": "Families who decorate, and shops, hotels, malls and towns that buy installs",
                  "competitors": "Lumenhaus, Brightyard, Stringcraft, Glowline, Yardwise, Sparkfield",
                  "channels": "Holiday light forums and online marketplaces"}

# key, title, owner, parent key, colour a person set (None: the Strategy Bot's colour from the KPIs), why,
# [(kpi name, unit, target, readings oldest first, days until the target is due)]
GOALS = [
    ("season", "Ship 6,000 units and 40 business installs this season", "company", None, None,
     "", [("Units shipped this season", "units", 6000, [3100, 3900, 4450], 58)]),
    ("installs", "Install 40 business sites before the holidays", "bot:sales-lead", "season", None,
     "", [("Business installs booked or done", "sites", 40, [9, 16, 22], 58)]),
    ("calibration", "Calibration works first time for 95 percent of owners", "bot:product-manager", "season", "red",
     "Off track: 84 percent first-try, and the lens-mount fix only reaches ring 3 next week.",
     [("First-try calibration", "%", 95, [88, 87, 84], 30)]),
    ("orders", "Ship 95 percent of orders within two days", "bot:ops-manager", "season", None,
     "", [("Orders shipped within two days", "%", 95, [91, 92, 94], 30)]),
    ("home-pro", "Home Pro is 45 percent of home sales by December", "bot:marketing-lead", "season", "yellow",
     "Behind: 38 percent, and the December price is still open.", [("Home Pro share of home sales", "%", 45, [33, 35, 38], 30)]),
    ("tickets", "Answer every support ticket the same day", "bot:support-lead", "season", None,
     "", [("Tickets answered the same day", "%", 95, [84, 86, 88], 30)]),
    ("guides", "Publish two useful holiday guides a month", "bot:content", "home-pro", None,
     "", [("Guides published this month", "guides", 2, [0, 1, 1], 20)]),
    ("books", "Match 98 percent of bank lines within a day", "bot:finance-lead", None, None,
     "", [("Bank lines matched within a day", "%", 98, [93, 95, 97], 30)]),
]

# The curator's own pages, written over the seed's outlines: id, title, category, body.
MARKET_PAGES = [
    ("market/overview", "Overview", "Market / Overview",
     "# Market\n\n"
     "Acme Lights sells holiday projector kits to families, and installs scene systems for shops, hotels, malls and towns.\n\n"
     "## Who we compete with\n\n"
     "- **Lumenhaus** is the price setter on single-unit kits. Its one-unit kit is $249.\n"
     "- **Brightyard** is the fast follower. Its free scene app targets the same family buyers.\n"
     "- **Stringcraft** sells light strings and a seasonal subscription; it overlaps on the scene library.\n"
     "- **Glowline** and **Yardwise** are lookalikes: installers and garden kits that solve one slice well.\n\n"
     "## What we hear from buyers\n\n"
     "Families on the holiday forums ask for the same thing: a setup they can finish in an afternoon. "
     "Shops ask when the crew arrives. Both ask for scenes that look right on their building.\n"),
    ("market/structure-and-size", "Structure and size", "Market / Structure",
     "# Structure and size\n\n"
     "The market has three layers: kit sellers for families, scene subscriptions, and installers for businesses. "
     "Acme Lights sells in the first two and installs for the third through Crewline.\n\n"
     "| Segment | Who buys | Typical spend |\n|---|---|---|\n"
     "| Home light shows | Families with one house | $299 to $449 a kit |\n"
     "| Shops and hotels | Storefronts and lobbies | $1,200 a unit plus a yearly license |\n"
     "| Towns, malls and HOAs | Public bids and shared spaces | Quoted per site |\n\n"
     "We treat any size figure as a thesis with a source, not as a fact about a company.\n"),
    ("market/theses", "Theses", "Market / Theses",
     "# Theses\n\n"
     "1. **Installs are where the margin is.** Shops and hotels pay per unit plus a license, and they renew it. "
     "Source: this season's quotes and the two repeat hotel accounts.\n"
     "2. **Price is not the only fight.** Lumenhaus wins on one-unit price, but our two-unit kit is still cheaper for families.\n"
     "3. **Scenes keep customers.** Owners who calibrate in the first hour keep using scenes; 78 percent of them do.\n"),
    ("market/people-who-matter", "People who matter", "Market / People",
     "# People who matter\n\n"
     "The graph does not track individual people yet. The names that come up most in buyer conversations are "
     "Tom Reid, events manager at Crown Hotel, and Priya Shah on the Maple Mall holiday committee. The moderators of the "
     "two biggest holiday light forums matter for launches.\n"),
]

# The shared inbox: the address it reads, what its bot is told, and the mail that is already there:
# (subject, body, labels, from, to).
INBOX = "ben@acme.example"
INBOX_INSTRUCTIONS = "# Instructions\n\nTriage mail and draft replies for review. Never send; file a draft for a person."
MAIL = [
    ("Projector will not pair after calibration",
     "Our projector stops at step 4 of the calibration. Can you help us this afternoon?",
     ["INBOX", "hub/needs-owner"], "kim@homes.example", INBOX),
    ("Re: Projector will not pair after calibration",
     "Draft: Hold the lens cap off, power the projector down for ten seconds, then start calibration again. Tell us which step fails.",
     ["DRAFT", "hub/drafted"], INBOX, "kim@homes.example"),
    ("Second kit for the family room",
     "Thanks for the walk-through. We are ready to order a second Home Pro kit.",
     ["INBOX"], "owner@homes.example", INBOX),
]
# The support channel the Slack connection reads: its name, a note, the thread already in it, and the bot's reply.
SLACK = {"channel": "support", "note": "Demo support channel", "subject": "Slack support", "team": "TDEMOTEAM1", "app": "ADEMOAPP1",
         "thread": (("Kim", "human", "A customer needs the calibration steps again."),
                    ("Support", "bot", "Draft reply: hold the lens cap off, power down for ten seconds, then calibrate again.")),
         "feed": "Review the support channel's calibration thread.",
         "draft": "Draft: hold the lens cap off, power down for ten seconds, then calibrate again."}

# Example files the bots have made, as (bot, path, type, text). The last two versions of one file show a draft and its edit.
FILES = [
    ("support", "reports/ticket-topics.csv", "text/csv", "topic,tickets\nCalibration,21\nShipping,9\nInvoices,6\n"),
    ("content", "guides/holiday-setup.md", "text/markdown", "# Holiday setup guide\n\nFirst draft: the first-time setup in six steps.\n"),
    ("content", "guides/holiday-setup.md", "text/markdown",
     "# Holiday setup guide\n\nReviewed draft: added the lens-mount step and two screenshots.\n"),
]
FILE_LINK = {"bot": "sales", "url": "https://sales.acme.example/holiday-deck", "title": "Holiday sales deck (sample link)"}
