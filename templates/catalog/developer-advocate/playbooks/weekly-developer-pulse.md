# Weekly developer pulse

Schedule: Thursdays at 10:00 team time (routine `weekly-developer-pulse`), after setup. Also run by hand. Budget 50 minutes. The outcome is one page: every open public question with a
ready answer, the friction themes, and one sample worth writing. Nothing is posted.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/channels.md`, `knowledge/do-not-say.md`, `knowledge/friction-log.md` and last week's pulse.
Check which sourced answers were posted and whether the asker replied.

## 2. Sweep the channels

Each channel in `knowledge/channels.md` since the last pulse: new questions, and older ones still without an
answer from the team. For each: link, date, the question in one line, the language and SDK version if
given.

## 3. Prepare the answers

For each open question: find the answer in the docs (`hub doc search`, `hub doc ask`) and the code; write the
reply (the fix, one line of why, the doc link); run any code against the stated SDK version where you can,
and say which version. Check it against `knowledge/do-not-say.md`. A question you cannot answer goes to the
named engineer as a proposed task. Post requested replies with your Tools when a person has turned mail sending on in Tico; otherwise keep drafts with their destinations.

## 4. Update the friction log

Tag each question with the integration step it hit (auth, first call, webhooks, pagination, rate limits,
errors). Add counts and links to `knowledge/friction-log.md`. A step hit twice or more is a theme; name the
fix that would remove it (a doc change for the Technical Writer, a product change for Product, a better error
message for engineering).

## 5. Propose one sample

The theme with the highest count that a working example would answer. One paragraph: what it shows, the
language, how long it would take. Write it with `playbooks/write-a-sample.md` within the requested work.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-developer-pulse.md` in the shape of `knowledge/examples/developer-pulse.md`, `hub file publish` it, commit, then `hub task update <id> --status done --note`.
