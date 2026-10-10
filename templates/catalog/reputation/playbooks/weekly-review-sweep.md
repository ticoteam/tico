# Weekly review sweep

Schedule: `0 9 * * 1` America/Los_Angeles, routine `weekly-review-sweep`. Take the run's label
from the task title.

Budget 45 minutes for the read, then `playbooks/work-queue.md` per surface. The outcome is the
ledger brought up to date, one digest in `reports/sweeps/`, one batch per surface with work,
and the outcomes of last sweep's batch recorded. Quiet is a normal result.

---

## 1. Read the surfaces first

    hub task show <id>

Then `knowledge/surfaces.md`, before opening anything. It names every surface, {{company_name}}'s listing on
each, who controls it, and what it allows. A surface not on it is not this sweep's work; a new
one found by search is a line added to `surfaces.md` in this run, then read.

## 2. Read each surface

Newest first, over the window since the last sweep (the newest `review_date` in the ledger for
that surface). Open the listing through the browser tool:

    $HUB_DIR/connectors/browser.py repl --as reputation "const p = await openTab('<listing url>');..."

Reading only, in this step. A sign-in wall or a challenge means that surface is blocked for this
run: record it and move on. Keep a note of every surface as you go: returned, or blocked.

## 3. Update the ledger

Append a row for every review, rating or complaint not yet in `knowledge/ledger.csv`, with
`author_type` and `theme` decided from what the review says. Re-read every row `flagged` more
than 14 days ago and set `upheld` or `rejected`. Edit the `status` of any row whose review was
edited, removed or answered. Never delete a row.

## 4. Work the queue

Run `playbooks/work-queue.md` for each surface with rows that have no lever yet, newest and
most visible first, capped per batch. Then, when the owner has enabled execution (`act` access), execute requested items with the necessary Tools. Replies stay drafts until a person turns mail sending on in Tico; name any missing access on the task.

## 5. Write the digest

`reports/sweeps/YYYY-MM-DD.md`, in this order and nothing longer:

1. **Coverage**: surfaces returned, surfaces blocked.
2. **Counts**: per surface, rating and total today, new rows this week, rows by lever.
3. **Outcomes**: flags upheld and rejected since last sweep, by ground; replies and answers
   posted; the BBB grade and the G2 and Gartner review counts today against last week.
4. **Batches requested**: one line per batch, with the surface and the item count.
5. **Claims and programs**: what is still unclaimed, what the invitation program did this week.

A section with nothing in it is absent. Put coverage, counts and outcomes in five lines or fewer at the top of the task note with the path to the digest. Posting them to the team channel needs the Slack `post` access, which the owner turns on.

## 6. Hand over what needs someone

    hub task create --owner <owner> --parent <id>      # a claim only the business owner can do; one task per listing
    hub task create --owner sales --parent <id>      # the milestone customer list for invitations

Never a task that asks anyone to change copy, and never one to show the sweep happened.

## 7. Finish the task

Commit, then `hub task update <id> --status done --note` with the counts in the first line.
Always finish it.

## When a surface fails

Blocked, with what it refused with, in the coverage line. Two consecutive failures of the same
surface is one line on the owner's task, not a repeated complaint in every digest.
