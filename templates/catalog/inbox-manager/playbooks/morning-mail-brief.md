# Morning mail brief

Schedule: weekdays at 07:30 team time (routine `morning-mail-brief`), after setup. Also run by hand on request. Budget 20 minutes. The outcome is a short brief on the task:
what needs the human, what is drafted, what you would file. Nothing is sent while `Sending` is Off in
`playbooks/inbox-preferences.md`; when it is On, send only what its rules say, to the recipients they name; Tico
decides each send. Nothing is filed while
`Filing` is Off.

Done when: the brief is on the task with the counts, every mailbox read that failed is named with its refusal, and the task is closed, so no open scheduled task is left to absorb the next pass.

---

## 1. Read the task and the preferences

    tico task show <id>

Then `playbooks/inbox-preferences.md`, `knowledge/voice.md`, and `memory/learnings.md`. The mailbox is
the one named `Mailbox:` in `AGENTS.md`; name it as `<address>` in every `tico email` command.

## 2. Read what the automation did

    tico automation recent-runs --trigger message --range 24h

The inbox's Message automation ran on the server on each new message as it arrived. Its rules filed marketing,
notifications and the mailbox's own lists before a model read anything, and its decide step sorted the rest with
the questions in `questions/mail-triage.json` (in this repository; the same file is in `$HUB_DIR/questions/`). What
it settled is done; do not reopen it. Its archive and label actions run only once Filing is On (the human accepted
them); until then what it would file stays in the inbox. Say in the brief how many it settled (or would settle).

## 3. Read what is left

    tico email inbox <address>

Each line is id, date, from, subject, labels and a snippet; the automation's run for that message says where it
sorted it: `archive`, `needs-owner`, `route`, `reply`, or `read` when it was not sure. Open a body only for a thread you
are about to draft or decide:

    tico email thread <address> <thread-id>

## 4. Sort with the decision word, then with judgement

| Decision | What it is | What you do |
|---|---|---|
| needs-owner | Only the human can act: legal, money, an investor, a regulator, a relationship only they hold | Top of the brief. Label `hub/needs-owner` only if Filing is On |
| reply | A real person waits on an answer a careful assistant could draft | Draft it (step 5); send requested replies only when a person has turned mail sending on in Tico |
| route | It belongs to another team: a lead, a customer, a candidate, a vendor | `tico task create --owner <slug> --parent <id>` per `## Routed to someone else`; do not flag it as needing the human |
| archive | Nothing is asked: a receipt, a notification, a cold pitch | List it as "would file". Archive only if Filing is `labels and archive` |
| read, or unsure | The model was not sure enough | Open the thread and decide yourself |

Anything on the always-reaches list is needs-owner whatever the word says. Legal or money words with an
ask are needs-owner. A message from someone upset is needs-owner. `hub/needs-owner` is scarce; other
teams' mail is not a need for this human.

## 5. Draft replies

Follow `playbooks/draft-a-reply.md` for each `reply` the automation has not already drafted. If you cannot write drafts, put the text on the task; otherwise write drafts to Gmail Drafts and include the text in the brief. Sending is a separate switch.

## 6. Write the brief

In the shape of `knowledge/examples/mail-brief.md`: a headline count, "Needs you today" first, then
drafts, then routed items, then what you would file, then anything you could not read. Message ids and
one-line reasons only; no bodies and no address lists in the brief. (A task you file for another bot may carry the
message's own text; see AGENTS.md.) Then attach it to the task. If it is worth
keeping, `tico file publish reports/YYYY-MM-DD-mail-brief.md`.

## 7. Finish

Prove the pass with another `tico email inbox <address>` read. Commit, then `tico task done <id> -m "..." --version N`:
counts (automation settled, decisions by kind, drafts, routed), the needs-owner ids with one line each, or one
line that nothing was left in the inbox. Always finish it: an open scheduled task absorbs the next
occurrence and quietly stops the pass.

## When a mailbox fails

A refusal or a blocked mailbox is a blocked mailbox, not "nothing found". Name the refusal on the task
and finish. Two consecutive failures of the same mailbox is one line on the owner's task.
