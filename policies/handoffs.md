# Handoff policy

Tico's database is the only work queue. GitHub Issues are disabled and must not be used for
tracking. Bots use the `hub` CLI; people use Tico at `https://hub.acme.example`.

## Starting a task

1. Read `state.md`, then `hub task show <task-id>` and its comments.
2. Set it to doing with `hub task update <task-id> --status doing --note "<current focus>"`.
3. If the goal or deliverable is unclear, ask one focused question (`hub task ask`) and set the
   task to waiting. Otherwise state the smallest necessary assumption and continue.

Before starting a newly created task, compare it with the other active tasks between the same
people or bots. The new-task message may include a `reconcile.active` list for this purpose. Reuse
research, files, and decisions that genuinely apply. If two tasks ask for the same outcome, keep
the earlier or higher-ranked task as the canonical one and return the duplicate with a note naming
that task. Keep separate approvals, audiences, deliverables, and materially different decisions as
separate tasks even when their subject is similar. Never silently discard either request.

Your queue is in order: `hub task list --owner me` returns your open tasks first-to-last, and the
first one is what to do next. There is no priority field; a person who wants something sooner
moves it up.

## Writing a title

A title is plain English. No reference numbers (`DC040302`, `#18823`, a card id), no all-caps
words: say what the thing is and put the number in the body or attach the link
(`hub task link <task-id> <url>`). Tico records a title that breaks this on the task today and
refuses it once the rule is enforced; `hub task create --dry-run` shows what it would say.

## Lanes, labels, links and comments

- A change to the product goes in the **product** lane (`--lane product`; the default when you
  are a product or engineering bot) and moves Backlog → Doing → In review → Ready to ship →
  Shipped. Set `--status review` when your pull request is open and attach it with
  `hub task link <task-id> <pr url>`; the merge and the deploy move it the rest of the way.
  Everything else is the **Team** lane.
- A project is a label: `hub task label <task-id> --add pricing-page`. So is a kind (`bug`).
- Another task that belongs with yours but neither waits on it nor is part of it (the same bug
  seen twice, the front-end half of a back-end change): `hub task relate <task-id> <other-id>`.
  Both tasks then list the other under Related.
- What you are waiting on, when it is another task: link it as a child and mark the parent
  `waiting` with the child ID in its note. If your role can change `blocked_by`, set it too;
  specialist bots must not retry that restricted field after a refusal. Tico wakes the parent
  owner when the child closes.
- Progress, a finding, a question for the people on the task: `hub task comment <task-id> "<text>"`.
  A comment is on the record with your name; it is not a chat.

## Delegating to another employee

Create a child task with a concrete deliverable and the current task as its parent:

```text
hub task create --owner <slug> --title "<verb-first title>" --body-file <file> --parent <task-id>
hub task update <task-id> --status waiting --note "Waiting for child task <child-id>."
```

Files are scoped to their task's participants. Attach the packet needed for review directly to
the child with `hub task attach <child-id> <file>`; a bot assigned to the child may not be able to
download a file attached only to the parent. Keep a parent copy if the original requester needs it.

Do not poll the other bot. Tico wakes the requester when the child is done and wakes the parent
owner when the child closes. Read the child's result, incorporate it, and continue the parent.
When the improvement belongs in another bot's repository, assign the task to that bot with the
observed problem and expected outcome. Its owner implements, tests and merges its own change.

`--parent` and the `waiting` status are what make that wake happen. A task you file and then go
on working past is not something you are waiting for: its result still reaches you, but Tico does
not spend a run of yours telling you it finished. Use the parent link when you genuinely cannot
continue without the answer, and not otherwise.

## Work that can wait for the other bot's next run

When the other bot does not need to start now — a note for its next daily check, a release to
watch — file it with `--next-run`:

```text
hub task create --owner <slug> --title "<verb-first title>" --body "<what and why>" --next-run
```

It does not wake the bot. The next run it has for any reason (its routine, a person, other work)
carries the task in the same prompt, under "Next-run tasks", as a task of its own. Handle each one
in that run and mark it done or declined like any other. Until a run carries it, closing it is a
cancel and wakes nobody. Only a bot has a next run; a task for a person is always an ordinary one.

## Quiet notes: telling a bot something without waking it

When another bot should know something but has nothing to do about it now, leave a note:

```text
hub note create <slug> "<what it should know, plain, with links>"
```

It wakes nobody and asks nothing. The bot's next run, whatever starts it, carries every note
waiting for it in the same prompt, each with the time it was sent. `hub note list --to me --since 24h`
reads the ones you have been left, including notes an earlier run already carried; `hub note list
--from me` the ones you left. A note no run has carried yet can be taken back with
`hub note delete <id>`. If the other bot must act, file a task instead (`--next-run` when it can
wait); a note is never an ask.

## Talking to another bot

A task when you want something done. A chat only when you need an answer back. Anything that does
not need a reply is a task, and a task needs no acknowledgement: Tico wakes the requester when it
is done.

Every message to a bot starts a whole run for it — its role, its state, its playbooks and its
memory are read before it writes a word — so "thank you", "acknowledged" and "right call" cost
about what the work costs. A bot cannot see that price, so the rule has to be written down:

- **Reply only if you are answering a question or changing a decision.** If neither is true, say
  nothing. The work you did is the reply.
- **Disagreeing is worth a message. Agreeing is not.**
- **Work that is not yours**: re-file it on the bot that owns it, close yours, and name that bot
  in one line on the task. Do not open a conversation about whose it is.

A build ticket that reaches the wrong bot can take seven
messages between two bots and eight runs to hand over; two of the messages were the work and the rest were
manners and a disagreement the bot that raised it withdrew. The cap that eventually stops this is
twenty messages an hour in one conversation, which is twenty runs — far past the point anyone
would want it stopped.

## Asking the owner for anything

The owner reads requests on a phone between meetings. `hub task ask` sends to the task's requester.
Use it once when that requester is the owner and one answer unblocks the task. If the task
requester is yourself or another bot, find or create one decision task for the owner, parented to
the current work and include the exact packet there. Put that decision task in the **Team** lane (`--lane company`), even when its parent is a product task, so it appears in **Needs you**;
keep the product work in the product lane. That human task is the ask. Do not call
`hub question ask` or `hub message send` to the owner for the same question; each sends another notification. A direct
request already gets your final answer automatically, so link the task there once. The question
should be the only thing the reader has to read: one decision, two options if you can name them, no playbook,
coverage stats, or commit hashes. The human task title starts with a verb, its first line contains
the question, and its body stays under 120 words outside a quoted draft. Do not paste the original
brief into the task note. A decision is a General task: a task on a custom type is a ticket on that
type's board, which these checks leave alone, not a place for an ask.

Give one recommendation with the material consequence and include the relevant draft or summary.
Longer supporting files belong in the task's S3 deliverables prefix. Check for an existing request
before creating another. Ask only when information is missing or a real choice remains. Work already requested needs no separate approval.

Example:

```text
Should the homepage headline be our next test?

I recommend testing “Run your business with less busywork” against the current headline.
If the test is requested, run it with the available Tools after the site-health checks pass.

Full proposal: s3://<company>-tico-hub/cro/deliverables/<task-id>/funnel-review.md
```

## Make human assignments actionable

Assign a person a task only when their decision, access, or action is necessary. Resolve routine
formatting failures, retryable tool errors, and message limits internally. A refused write is not
itself a request for a human decision. Never forward raw refusal text or ask someone whether an
unspecified event is “a mistake to fix or a limit to keep.” Check for an existing decision first.

Internal coordination is never a human decision. Do not ask a person which parent task to use,
whether to remind or hand work to another bot, whether to mark completed bot work done, or whether
to open a branch or draft pull request. Read the task graph and route the work yourself. If another
bot owns the next step, create one concrete task for that bot. Opening a branch or draft pull
request is preparation; proceed when the assigned work authorizes it. Ask a person only for an
actual business choice or an action requiring Tools the bot lacks.

Rules:
- **Title** is the decision in plain words, starting with a verb: "Choose the wording for the reply about fees".
- **First line is the ask** with the choices spelled out. Never make the reader scroll to find it.
- **System attribution.** Call the background system **Tico** in text for people; name the actual bot when its identity is known. Keep actor IDs unchanged in commands.
- **Plain language.** No internal codes, tags, or scores (no "keeper", "class", "GEO citation",
  "routine 3"). If a term needs the spec to understand, do not use it.
- **Speech-ready.** The first sentence must make sense when read aloud with no task title or prior
  conversation. Name the real-world subject before a status, hold, workflow or review stage.
- **Under 120 words** outside the quoted draft. One link is fine; a wall of links is not.
- Include the recommendation and any material condition or consequence. Do not repeat obvious
  instructions for approving, editing, or skipping; the app has comment and close controls.
- Include the relevant draft or summary in the request itself. Longer supporting Markdown goes
  in S3 with a full URI so it can be previewed in the app; no required trip to another repo.
- Before opening a request, check existing open requests for the same decision. Update the
  existing request instead of creating another batch or reminder. Resolved and FYI-only items
  belong in the work log, not the human inbox.
- Put the same text in the task body (new task) or in your message (your own task). Do not
  paste routine output, memory notes, or your reasoning.

Every human assignment must be understandable without opening logs or another document:

- Title: the concrete action or decision, in ordinary language.
- First sentence: exactly what you need the person to do or answer.
- Recommendation: your preferred answer and the practical reason.
- Consequence: what you will do after the answer, and what remains blocked without it.
- Evidence: a short relevant excerpt and a readable source link when available. Keep technical
  diagnostics in the bot's audit record, not in the human's request.

Example title: “Confirm who handles customer tax filings”

“Should our public documentation say Acme files taxes for customers, or that customers file them?
Our current pages disagree. I recommend keeping the customer responsible until you confirm the
service we provide. Reply ‘Acme files’ or ‘customer files’; I will then prepare consistent wording
for review. The documentation change is waiting on this answer.”

Do not invent a recommendation or consequence. If evidence is missing, investigate first. For missing Tools, name the exact action, destination and access needed; explain what remains blocked. Do not turn a tool failure into a request to approve work already assigned.

## From review to result

When the deliverable is ready but the business result depends on a person, record one exact next
action on the existing task: the artifact and version, the destination, the decision owner, and
the check you will run after the decision. Keep a product task with an open review PR in `review`
and attach the PR link; that link is the handoff, so do not add a generic merge approval. Keep a
team task awaiting a missing decision in `waiting`. For requested publication, verify the facts, assets and placement, then publish with your Tools when `outbound_send` is on. Ask only if the destination or scope is missing. Do not count
acceptance as publication.

If the source task was already closed before the placement or outcome decision, leave it closed.
Find an active task for that same artifact and decision first. If the owner requested an active
bot-owned task, attach the exact packet there, ask the owner one scoped question on it, and leave it
`waiting`. Otherwise create one decision task for the owner with `--parent <closed-source-task-id>`
and the question and packet; the bot is its requester and receives the answer. The human task
itself is the ask, so do not call `hub task ask` on it. Do not create a generic approval or
duplicate release task to compensate for a closed editorial task.

On the next turn, check the current source of truth for that action (for example, PR state or a
live URL) before repeating the ask. Use the exact URL already attached to the task; do not guess
the repository or destination from its title. If a lookup fails, inspect that link and retry the
source check. If the action happened, update the existing task and verify the
result; then record the first useful outcome measure and its date. If it has not happened, complete it when the requested work and Tools allow; otherwise leave the same dependency visible and continue independent work. Do not create another reminder task.

When the live placement is verified but the outcome signal will not be available until a later
date, keep that measurement in the Hub queue. Reuse an existing measurement task if it names the
same artifact and metric; otherwise create one dated follow-up task linked to the shipped task.
Set `--due` to the earliest reliable check date; a date only in the body does not schedule the
work. Include the exact source, query or URL, and comparison window in the task body.
Close the implementation task after the live check, and close the measurement task only after
recording the observed value or a concrete source failure. This is outcome work, not another
merge reminder or permission request.

## Finishing

Update durable knowledge, state, and learnings; commit your repository; then mark the task done:

```text
hub task update <task-id> --status done --note "<result and deliverable URIs>"
```

The requester reviews and closes the task. A bot owner never closes a task it did not request.
When work cannot continue, use `waiting` with the exact dependency or `declined` with the reason.

The final note stays under 200 words and covers:

```text
## Done
## Deliverable
## Notes for requester
```

Do not create tasks for internal steps of your own work, leave a task in `doing` when you stop, or
recreate any of the GitHub Issues discarded in the cutover to the Hub database. The dated outcome
task above is for a later observed result, not an internal step of preparing the deliverable.
