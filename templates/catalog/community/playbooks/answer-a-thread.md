# Answer a thread

Triggered by a waiting question in the digest, or a task linking one thread ("can someone answer
this?"). Budget 10 minutes per thread. The outcome is one reply, sourced, ready to use.

---

## 1. Read the whole thread

Read every message, not just the first. Note what the member tried, their plan or setup if they said
it, and whether another member already answered (then your reply confirms or corrects, and thanks them).

## 2. Decide what it is

- **A how-to question:** answer it here.
- **An account-specific problem** (billing, their data, a login): do not answer in public. Prepare a
  short public line ("We've opened a ticket and will follow up directly") and a task for the Support
  Agent with the link.
- **A bug or outage:** route to the human named for bugs; the public line acknowledges it without a date.
- **A feature request:** thank them, say it has been passed on, promise nothing; route it as feedback.

## 3. Find the answer

    hub doc ask "<the member's question, in their words>"

Use the answer and its citations. If the Librarian says it is not covered, do not guess: report the
gap to it (`hub task create --owner librarian`) and tell the owner the thread needs a human.

## 4. Write the reply

The answer in the first sentence, then the steps, then the doc link. Use the member's words. Under
120 words. Thank a member who helped, by their public name.

## 5. Put it up for review

Add it to the week's batch, or post the requested reply with the thread link and text using your Tools when a person has turned mail sending on in Tico; otherwise keep the draft.
After it is posted, note it in the digest. `hub task update <id> --status done --note`.
