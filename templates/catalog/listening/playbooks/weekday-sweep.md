# Weekday morning sweep

Schedule: `0 8 * * 1-5` in the team's timezone, routine `weekday-morning-sweep`. Take the run's
label from the task title.

Budget 20 minutes. The outcome is one digest saved in `reports/sweeps/` and attached to the task,
plus a child task for anything that genuinely needs a human or another bot. Quiet is a normal
result.

---

## 1. Read the watchlist first

    hub task show <id>

Then `knowledge/watchlist.md`, before the first query. It holds the names, the queries, the phrases
that matter, and the sources this sweep reads. Anything not on it is not this sweep's work.
`hub market show <id>` for each organization this brief will name. The graph is what is true about them;
the watchlist is only what to search.

## 2. Run the queries

Work through the sources the watchlist names, in the order it names them, over the window since the
last sweep. Read search results only. Never click, type, submit, or sign in anywhere. A sign in wall
or a challenge means that source is blocked for this run: record it and move on.

Keep a note of every source as you go, in one of two states: returned, or blocked. You will need
both lists in the digest and you will not remember them at the end. A query that keeps returning noise is fixed in the watchlist, as `skills/check-a-query-before-trusting-it/SKILL.md` sets out.

## 3. Sort what came back

| Bucket | What it is |
|---|---|
| Worth a human's reply | A real customer or buyer asking the question {{company_name}} answers, in a live thread, where a plain honest reply would actually help |
| Worth writing about | A question people keep asking in different words, or a claim worth answering in public |
| A real move | Funding, an acquisition, layoffs, a price change, a launch, a new market, a shutdown, a lawsuit, or a notable public complaint thread about a watchlist name |
| Noise | Everything else. Drop it. Do not list it. |

Skip anything cold, duplicated from an earlier sweep, from a vendor promoting itself, or from
someone who is not the kind of person {{company_name}} sells to. A handful of good items is the
cap, not the target.

## 4. Write the digest

`reports/sweeps/YYYY-MM-DD.md`, in this order and nothing longer:

1. **Coverage**: which sources returned and which were blocked, named individually.
2. **Worth a reply**: the link, one line on who they are, one line on why it matters. No draft
   reply unless the owner has said sending is allowed, and even then a human sends it. Before a thread goes to a person, check it with `skills/check-a-reply-before-a-human-posts/SKILL.md`.
3. **Worth writing about**: the link, one line on why, and the angle.
4. **Real moves**: what happened with the link, why it matters for {{company_name}}, and what could
   be done about it. Three lines each. Confirm each move first with `skills/confirm-a-competitor-move/SKILL.md`.

A section with nothing in it is absent, not empty with a note apologising for it.

## 5. Hand over what needs someone

    hub task create --owner content --parent <id>
    hub task create --owner <person> --parent <id>

One child task per real item, never one to show the sweep happened. Something worth writing about
goes to the content bot with the link, the reason, and the angle; you never write the piece. A real
move or a thread a human should see goes to that human, under 200 words.

## 6. Finish the task

Commit, then `hub task update <id> --status done --note`. With findings: one line each and the path
to the digest. Without findings: one line naming the sources that returned and how many results you
reviewed. Always finish it. A scheduled task left open absorbs the next occurrence and quietly stops
the sweep.

## When a source fails

It goes in the coverage line as blocked, with what it refused with. That means unknown coverage for
that source, not a clean sweep, and never "nothing found". Two consecutive failures of the same
source is one line on the owner's task, not a repeated complaint in every digest.
