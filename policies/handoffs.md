# Handoff policy

Tico's database is the only work queue. GitHub Issues are disabled and must not be used for
tracking. Bots use the `tico` CLI; people use Tico at `https://hub.acme.example`.

Every task sits in a pipeline, at one stage, and always shows whose turn it is. A task is in
General (one stage, To do) unless it was filed into another pipeline. Its stage says what it needs
to leave. Every command that changes a task carries `--version N`, the version your wake or
`tico task show` printed; an old one is refused with "changed since you looked".

## Starting a task

1. Read `tico task show <task-id>` and its conversation. It prints the version, the
   stage, whose turn it is and what is missing to leave the stage.
2. There is no status to set. Your run shows that you are working; keep `tico bot status set` to one
   line about what you are doing now.
3. If the goal or deliverable is unclear, ask one focused question on the task
   (`tico task ask <task-id> --to <person> "<question>" --version N`). Otherwise state the smallest
   necessary assumption and continue.

Before starting a newly created task, compare it with the other open tasks between the same
people or bots. The new-task message may include a `reconcile.active` list for this purpose. Reuse
research, files, and decisions that genuinely apply. If two tasks ask for the same outcome, keep
the earlier or higher-ranked task and close the duplicate with a reason that names the task you
kept. A bot can't close a person's request by itself: ask them on it instead. Keep separate OKs,
audiences, deliverables, and materially different decisions as separate tasks even when their
subject is similar. Never silently discard either request.

Your queue is in order: `tico task list --owner me` returns your open tasks first-to-last, and the
first one is what to do next. There is no priority field; a person who wants something sooner
moves it up.

## Writing a title

A title is plain English. No reference numbers (`DC040302`, `#18823`, a task id), no all-caps
words: say what the thing is and put the number in the body or attach the link
(`tico task link <task-id> <url>`). Tico records a title that breaks this on the task today and
refuses it once the rule is enforced; `tico task create --dry-run` shows what it would say.

## Pipelines, labels, links and comments

- A task goes into another pipeline only when you file it there: `--pipeline <id>` on
  `tico task create` or `tico task file`. The default is General.
- A pull request you open for a task: attach it with `tico task link <task-id> <pr url>`.
  When a person asked for the change, request their review (`gh pr create --reviewer <login>`) with the
  GitHub login on their profile (`github` in `tico human list`); skip it when they have none.
- A project is a label: `tico task label <task-id> --add pricing-page`. So is a kind (`bug`).
- Another task that belongs with yours: `tico task relate <task-id> <other-id>` (related; add
  `--kind duplicate_of` or `follow_up` when that is what it is). A relation is a note. A task waits
  only on what its stage needs and on the tasks it filed.
- Waiting on work another bot or person must do: file it from your task (below). Your task waits
  until that task ends, and you are woken once then.
- Waiting on your pull request's checks, review or release in General: link it with
  `tico task link <task-id> <pr url> --wake-once`, then snooze the task to when you expect it, with
  the pull request in the reason. End the turn instead of polling. On a stage that needs a pull
  request, checks or a release (Engineering), Tico reads GitHub itself: link it and end the turn,
  with no `--wake-once` and no snooze.
- Waiting on something Tico cannot see (a reply by mail, a date, an outside system):
  `tico task snooze <task-id> --until <date> -m "<what you wait for and what to check>" --version N`
  and end the turn. Give a date wherever there is one: "vendor replies by Nov 14". A snooze with no
  date shows as On ice and your manager is told.
- Progress, a finding, a question for the people on the task: `tico task comment <task-id> "<text>"`.
  A comment is on the record with your name; it is not a chat. An @ in it only marks the task unread
  for that person. If you need them, ask them on the task.
- Never message a person about a task outside the task: no chats, emails or pushes about it.

## Pages and pull requests on a stage

`tico task show <task-id>` lists what the stage still needs. A stage moves on by itself the moment
nothing is missing; you do not say done for a need Tico can see.
- A page (a Spec, a Review, a QA page, Checked in production): write it, publish it with
  `tico file publish <path> --task <task-id>` (it prints the file id), then attach it to the need:
  `tico task attach <task-id> page:<file id> --req "<requirement>" --version N`. Jev checks it; a person OKs it
  where the stage asks for one. Sent back: change it, publish again and attach the new version with
  `--changed "<what changed>"` (up to 5 lines) and `--answered "<point>: done|not done, <why>"` for each point
  of their note. Never quote their note back to them: Tico refuses it.
- A review page names the pull request's newest commit. New commits make it stale: write it again.
- A pull request: open it and link it with `tico task link <task-id> <pr url>`. Checks, merges and
  releases are read from GitHub. Never merge a pull request on a stage: the merge queue merges.

## How a run ends

When you stop, the task must be in one of five states. Your wake says so.

1. Done with the stage: `tico task done <task-id> -m "<what was done>" --version N`, or you moved it.
2. Asking a person on the task: `tico task ask`.
3. Waiting on a task you filed: `tico task file`.
4. Snoozed, with a reason and a date wherever there is one: `tico task snooze`.
5. Closed, with a reason: `tico task close <task-id> -m "<why>" --version N`.

Anything else is stopping early, and Tico wakes you again.

## Delegating to another employee

File a task from the one you are working on, with a concrete deliverable:

```text
tico task file --parent <task-id> --owner <slug> --body "<what and why>" "<verb-first title>"
```

Your task waits until the new one ends, and you are woken once then. Sub-tasks nest at most 5 deep.
Files are scoped to their task's readers. Attach the packet needed for review directly to
the new task with `tico task attach <child-id> <file>`; a bot that owns it may not be able to
download a file attached only to the parent. Keep a parent copy if the original requester needs it.

Do not poll the other bot. Read the result when you are woken, incorporate it, and continue the
parent. File review tasks under the implementation task: review one and review two each return to
the builder there. Mark the implementation task done when it is ready; review progress does not
need a separate manager update or relay turn.
When the improvement belongs in another bot's repository, file the task for that bot with the
observed problem and expected outcome. Its owner implements, tests and merges its own change.

`--parent` makes the result return to the task that needs it. A task created without a parent
gives its requester the ordinary completion notice. Use the parent link when its result is needed
to continue the current task.

## Work that can wait for the other bot

A task wakes its owner as soon as it is theirs. When the other bot does not need to start now,
leave a note (below). When it must act, but later, file the task and snooze it to the day it should
start: `tico task snooze <id> --until <date> -m "<why then>" --version N`.

## Quiet notes: telling a bot something without waking it

When another bot should know something but has nothing to do about it now, leave a note:

```text
tico note create <slug> "<what it should know, plain, with links>"
```

It wakes nobody and asks nothing. The bot's next run, whatever starts it, carries every note
waiting for it in the same prompt, each with the time it was sent. `tico note list --to me --since 24h`
reads the ones you have been left, including notes an earlier run already carried; `tico note list
--from me` the ones you left. A note no run has carried yet can be taken back with
`tico note delete <id>`. If the other bot must act, file a task instead; a note is never an ask.

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
- **Work that is not yours**: hand it to the bot that owns it
  (`tico task reassign <task-id> <slug> -m "<why>" --version N`). Do not open a conversation about
  whose it is.

Keep to your reach: your manager, your reports and your peers, plus anyone the Rules page adds.
Tico refuses a task, an ask or a message beyond it.

A build task that reaches the wrong bot can take seven messages between two bots and eight runs to
hand over; two of the messages were the work and the rest were manners and a disagreement the bot
that raised it withdrew. The cap that eventually stops this is twenty messages an hour in one
conversation, which is twenty runs — far past the point anyone would want it stopped.

## Asking the owner for anything

The owner reads asks on a phone between meetings. Ask on the task the work belongs to:

```text
tico task ask <task-id> --to <owner> "<one sentence>" --why "<why it matters>" \
  --choice "<Label>|<what happens>" --choice "<Label>|<what happens>" --recommend <Label> [--show page:<id>] --version N
```

It is one line in their Needs you, with buttons. The task stays in its stage until they answer, and
their answer wakes you once. Ask once, when one answer lets the work go on. If there is no task yet,
file one you own and ask on it. Do not also send the owner the same question with
`tico message send`, a chat or an email. The ask should be the only thing the reader has to read:
one decision, two choices if you can name them, no playbook, coverage stats, or commit hashes. It is
one or two plain sentences saying what you need and what happens after, at most 40 words, with no
ids, hashes, file paths, `s3://` links or tool names, though `PR #1555` is fine; Tico refuses one
that breaks this. Put the draft or summary on the task, or show it with `--show`.

Real work a person has to go and do (sign a filing, call a vendor) is a task for them, filed from
yours: `tico task file --parent <task-id> --owner <person> "<verb-first title>"`. Its title starts
with a verb, its body's first paragraph is the ask, and its body stays under 120 words outside a
quoted draft.

Give one recommendation with the material consequence and include the relevant draft or summary.
Longer supporting files belong on the task. Check for an open ask on the same thing before asking.
Ask only when information is missing or a real choice remains. Work already requested needs no
separate OK.

Example:

```text
Should the homepage headline be our next test?

I recommend testing “Run your business with less busywork” against the current headline.
If you say yes, I run it with the available Tools after the site-health checks pass.

Full proposal: <the link `tico task attach` printed>
```

## Make asks to people actionable

Ask a person or give them a task only when their decision, access, or action is necessary. Resolve
routine formatting failures, retryable tool errors, and message limits yourself. A refused write is
not itself a question for a person. Never forward raw refusal text or ask someone whether an
unspecified event is “a mistake to fix or a limit to keep.” Check for an open ask first.

Internal coordination is never a person's decision. Do not ask a person which parent task to use,
whether to remind or hand work to another bot, whether to mark finished bot work done, or whether
to open a branch or draft pull request. Read the task graph and route the work yourself. If another
bot owns the next step, file one concrete task for that bot. Opening a branch or draft pull
request is preparation; proceed when the assigned work authorizes it. Ask a person only for an
actual business choice or an action requiring Tools the bot lacks.

Rules:
- **The ask** is the decision in plain words, with the choices spelled out. Never make the reader
  scroll to find it.
- **System attribution.** Call the background system **Tico** in text for people; name the actual bot when its identity is known. Keep actor IDs unchanged in commands.
- **Plain language.** No internal codes, tags, or scores (no "class", "GEO citation", "routine 3").
  If a term needs the spec to understand, do not use it.
- **Speech-ready.** The first sentence must make sense when read aloud with no task title or prior
  conversation. Name the real-world subject before a status or stage.
- **Under 120 words** in a task body you give a person, outside the quoted draft. One link is
  fine; a wall of links is not.
- Include the recommendation and any material condition or consequence. Do not repeat obvious
  instructions for answering; the ask has its own buttons.
- Show the relevant draft or summary with the ask. Longer supporting files go on the task, so they
  open in the app; no required trip to another repo.
- Before asking, check for an open ask on the same decision. One ask per need: asking again on the
  same task replaces your open one. Finished and FYI-only items belong in the work log, not in a
  person's Needs you.
- Do not paste routine output, memory notes, or your reasoning.

Every ask must be understandable without opening logs or another document:

- The sentence: exactly what you need the person to do or answer.
- Recommendation: your preferred answer and the practical reason.
- Consequence: what you will do after the answer.
- Evidence: a short relevant excerpt and a readable source link when available, on the task. Keep
  technical diagnostics in the bot's audit record, not in the ask.

Example: “Should our public pages say Acme files taxes for customers, or that customers file them?”
with the choices `Acme files,Customers file`, and on the task: “Our current pages disagree. I
recommend keeping the customer responsible until you confirm the service we provide. I will then
prepare consistent wording.”

Do not invent a recommendation or consequence. If evidence is missing, investigate first. For missing Tools, name the exact action, destination and access needed. Do not turn a tool failure into a request to OK work already assigned.

## From review to result

When the deliverable is ready but the business result depends on a person, ask one exact question
on the existing task: the artifact and version shown, the destination, and the check you will run
after the answer. A pull request linked to the task is the handoff; do not add a separate OK to
merge it. For requested publication, verify the facts, assets and placement, then publish with your
Tools when your sending policy in Tico lets you send. Ask only if the destination or scope is
missing. Do not count an OK as publication.

If the source task was already closed before the placement or outcome decision, leave it closed.
Find an open task for that same artifact and decision first, and ask on it. Otherwise file one task
you own, linked to the closed one with `tico task relate <new-id> <closed-id> --kind follow_up`, put
the packet on it, and ask the owner there. Do not file a second ask or a duplicate release task to
make up for a closed task.

On the next turn, check the current source of truth for that action (for example, pull request state
or a live URL) before asking again. Use the exact URL already attached to the task; do not guess
the repository or destination from its title. If a lookup fails, inspect that link and retry the
source check. If the action happened, say so on the task and verify the result; then record the
first useful outcome measure and its date. If it has not happened, complete it when the requested work and Tools allow; otherwise keep the same ask open and continue independent work. Do not file another reminder task.

When the live placement is verified but the outcome signal will not be available until a later
date, keep that measurement in Tico. Reuse an open measurement task if it names the same artifact
and metric; otherwise file one follow-up task related to the shipped task, and snooze it until the
earliest reliable check date. A date only in the body wakes nobody. Include the exact source, query
or URL, and comparison window in the task body. Mark the implementation task done after the live
check, and the measurement task only after recording the observed value or a concrete source
failure. This is outcome work, not another merge reminder or a request for an OK.

## Finishing

Update durable knowledge, state, and learnings; commit your repository; then mark the stage done:

```text
tico task done <task-id> -m "<result and deliverable links>" --version N
```

In General, done is Done. Nobody checks it, and a person can reopen it with a note. When the work
cannot go on, ask a person on the task, snooze it with the exact thing you wait for, or close it with
the reason. A bot can't close a person's request by itself: ask them, and they reassign or close it.

Chat shows a note folded under its task title; the person reads it only when they open it. Your
reply in chat carries the plain-English result, without IDs, hashes or `s3://` URIs. The final note
stays under 200 words and covers:

```text
## Done
## Deliverable
## Notes for requester
```

Do not file tasks for internal steps of your own work, stop without leaving the task in one of the
five states above, or recreate any of the GitHub Issues discarded in the cutover to the Hub
database. The dated outcome task above is for a later observed result, not an internal step of
preparing the deliverable.
