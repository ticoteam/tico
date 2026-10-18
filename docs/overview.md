# Campus overview

Open Overview from the desktop sidebar or the mobile More menu. Updates remains the default page. Every visible computer becomes a building in a small solarpunk office campus. Bots appear at workstations in their assigned building. A human who works with bots on two computers appears in both buildings; this represents collaboration, not live presence.

Buildings have open cutaway offices, rear garden terraces, solar canopies, glass sides and warm lighting. A recessed, illuminated Loop connects the buildings, with small moving pods. Pods and tunnels are decorative scenery, not bot traffic or execution telemetry. Every teammate is represented; larger teams use additional floors. Select a building to step inside, then select a person or bot to see their permitted status and open their existing page. Tap outside or press Escape to return. Drag to rotate and scroll to zoom. Keyboard-accessible building and teammate buttons appear when focused; the Campus button is available inside a building.

The scene uses the existing `/employees`, `/humans`, `/v2/status` and `/v2/computers` responses. Bots with queued work that is not starting are listed above the campus with their reason and one fix, from the Health answer the app already polls (`/v2/health` `stuck`); a stuck bot's teammate card says why. A bot left in setup (`needs_setup`) for over a day is listed there too, with how long, and a person who may manage it gets **Finish with defaults**: the same setup-done call as `hub bot setup-done`, so the bot starts claiming its work and its template's first routine is turned on, as Start setup would have. Nothing finishes setup on its own. The person the "Finish setting up" reminder goes to gets one message about it, once per bot, unless that reminder is still open (`backend/setup_stuck.py`). Below them one line says how many bots are running on a fallback provider because their plan ran out, since when, and how many are limited (`on_fallback` and `limited` in the same answer). Computer inventory loads only while Overview is open and follows the existing refresh clock. Readable bot assignments also identify buildings, so inventory failure does not remove their offices. Authorized computers with no bots remain visible as empty offices. Revoked computers are excluded from inventory. Human placement uses visible operators, users, owners and reporting chains, with one representation per person per building.

Hidden people, invisible or retired bots and other people's personal branches are excluded. A bot without Read access has no machine or activity details and is placed in the Shared pavilion; its location is never inferred from computer inventory. The pavilion also includes unassigned bots, external agents without a known computer, and people with no placed bots. Free-text task focus and raw inventory details never cross the renderer boundary. Human presence is not tracked. If status refresh fails, bots become unknown and stop their activity animation.

`ui/app/overview.js` supplies this minimal model to a lazy same-origin iframe in `ui/overview/`. Both sides check message origin and source. Profile requests are checked against the current roster before navigating. The iframe, event listeners and GPU resources are disposed when leaving the route. Membership, assignment or computer changes rebuild the campus while preserving the selected building when it still exists; status-only refreshes preserve the camera and selected teammate. No Three.js assets or computer inventory requests load on the default page.

The renderer uses native ES modules and locally vendored Three.js; it needs no build step or external CDN. Geometry and materials are shared and static scenery is batched. Device pixel ratio is capped, invisible documents stop rendering and reduced-motion preferences pause decorative motion, including preference changes while the scene is open. If WebGL is unavailable, the same authorized roster remains accessible as a list grouped by computer and pavilion.

## Bot hours vs human hours

The **Bot hours vs human hours** card sits at the top of the Overview, above the campus. It compares how much time your bots spent working with the hours your people work, as an estimate.

- **Headline:** bot hours per human hour over the last 7 days, for example **6×**. Below 10 it shows one decimal (0.4×, 2.5×); from 10 up, a whole number.
- **Chart:** one point a week for up to the last 12 weeks, with a dashed **Parity 1×** line where bots and people work the same hours. The current week is dotted and hollow: it is the week "so far", not a finished week. Hover a point, or tap it on a phone, to see that week's numbers, for example "Week of Oct 12: 243 bot hours · 40 human hours · 6.1×".
- **Table:** click the card to open the numbers by week: week, bot hours, people, human hours and ratio, newest first.

How the two numbers are counted:

- **Bot hours** are the time bot runs spent working. Each run counts from its start to its finish (to now while it is still going). A silence of more than 30 minutes inside a run, with no activity from the bot, is left out. A finished run with no activity recorded inside it counts in full, start to finish. Every bot counts, BotOps and the Assistant included, and a run that failed counts for the time it ran.
- **Human hours** are the people on the org chart × 40 hours a week. Hidden people and bots don't count. The org chart keeps no history, so every week uses today's count of people.
- **Weeks** run Monday to Sunday in the company's time zone (Pacific time).

It is an estimate: nobody's actual working time is tracked, and every person counts as 40 hours. The card shows totals only, never a bot's or a person's own hours. Everyone who can open the Overview sees it; bots can't read it.

What you see before there is enough data:

- **No bot runs yet:** "No bot work yet", with a link to Bots in Settings.
- **No people on the org chart:** bot hours for the last 7 days with no ratio, and "Add people to the org chart to compare".
- **Less than 7 days of runs:** the headline for the days so far and "Chart fills in as weeks pass".

The numbers come from `GET /api/v2/overview/bot-hours` (`backend/bot_hours.py`), which reads the runs Tico already records; nothing new is stored. The answer is kept for a minute, and the card asks again at most every 5 minutes while the Overview is open. `backend/tests/test_bot_hours.py` checks the counting with sample runs, and the browser test `ui/tests/overview-hours.cjs` checks the card, the hover, the table and a phone-sized screen.

## Replay the last 24 hours

**Replay last 24h** (under the layout switch) turns the campus into a recap of the day. The page reads `GET /api/v2/replay` for the last 24 hours with the viewer's own rights, the same history and visibility rules as the Replay page, and hands the campus only what it shows: who, to whom, task titles, bot states and the first line of each message. Bot focus text and run summaries are not passed in, as for the live campus.

While it plays, each readable bot's desk shows its state at the replay time: working, needs a person (waiting on a human, blocked, crashed or held for review: an amber light) or idle. A bot without Read access stays restricted. A message between two teammates, or a task handed from one to another, draws an arc from one desk to the other with a light travelling along it; a person who sits in several buildings is linked from the nearest seat. Captions list the last few events. Play/pause, 60×, 600× and 3600× speeds and a scrubber with the time control it; quiet stretches are shortened so the day keeps moving. Scrubbing replays states without drawing old arcs. **Back to live** restores the current status. With reduced motion the arcs appear whole, without the travelling light or hops.

The browser test `ui/tests/overview-replay.cjs` checks the 24-hour request, the bot-to-bot link, the replayed desks, that private text stays out of the campus and the return to live, with a recording stand-in for the 3D renderer. The campus's own WebGL scripts timed out under release load and were removed. Run `python -m backend.demo --port 18766` for visual review at `http://localhost:18766/#/overview`; the demo uses fictional sample data.
