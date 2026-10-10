# Answer a policy question

Triggered by a task or message that asks what a policy says. Budget 10 minutes. The outcome is one drafted
answer that quotes the handbook and cites its page, or a hand-off to a human. Nothing is sent.

---

## 1. Decide whether you may answer at all

Read `knowledge/hand-offs.md`. If the question is about pay, leave entitlements, discipline, performance,
health, a complaint, harassment, immigration or a termination, or about what a specific person is owed or
allowed, do not answer. Create a task for the human listed with the question copied untouched, tell the
requester it has gone there, and stop. Also stop when the person seems distressed.

## 2. Find the page

    hub doc ask "<the question, no personal details>"
    hub doc read <path>

The Librarian answers from the docs and cites the page. Open the page it cites and read it whole, not only
the quoted line. Note its date. Skip any page in `knowledge/stale-pages.md`. If two pages disagree, keep both.

## 3. Draft the answer

1. Line one: what the handbook says, in one sentence, in the handbook's own words where you can.
2. The quoted sentence, the page title and its date.
3. What it does not cover, in one line, and who to ask (`knowledge/hand-offs.md`).
Never fill a gap with "usually", "most organizations" or a legal rule. If the handbook is silent, the first
line is "The handbook does not answer this" and one task goes to the Librarian
(`hub task create --owner librarian`) with the question and how often it has been asked, for whoever owns the policy.

## 4. Hand over

Attach the draft to the task. Send within the requested work and your Tools; messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 5. Finish

Note any page that looked out of date. `hub task update <id> --status done
--note`: the answer's page, or the hand-off and its owner.

## When a source fails

If the handbook cannot be read, say so and answer nothing. An answer from memory is never acceptable here.
