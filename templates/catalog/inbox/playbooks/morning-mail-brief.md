# Morning mail brief

Schedule: weekdays at 07:30 team time (routine `morning-mail-brief`), after setup. Also run by hand on request. Budget 20 minutes. The outcome is a short brief on the task:
what needs the human, what is drafted, what you would file. Nothing is sent while `Sending` is Off in
`playbooks/inbox-preferences.md`; when it is On, send only what its rules say, to the three kinds of recipient it lists
(team domain, the sender you are answering, the `forward_to:` addresses). Nothing is filed while
`Filing` is Off.

---

## 1. Read the task and the preferences

    hub task show <id>

Then `playbooks/inbox-preferences.md`, `knowledge/voice.md`, and `memory/learnings.md`. The mailbox is
the one named `Mailbox:` in `AGENT.md`. You already hold it; do not pass `--mailbox`.

## 2. Run the rules

    $HUB_DIR/scripts/mail.sh rules run --dry-run     # while Filing is Off
    $HUB_DIR/scripts/mail.sh rules run                # only once Filing is On

Rules file marketing, notifications and the mailbox's own list before a model reads anything. What they
settle is done; do not reopen it. Say in the brief how many the rules settled (or would settle).

## 3. Read what is left

    $HUB_DIR/scripts/mail.sh inbox --untriaged --format brief --decisions

Brief is id, date, from, subject, labels, a snippet, and a `decision:` word from
`questions/mail-triage.json` (in this repository; the same file is in `$HUB_DIR/questions/`). Open a body only for a thread you are about to draft or decide:

    $HUB_DIR/scripts/mail.sh thread <id> --format md

## 4. Sort with the decision word, then with judgement

| Decision | What it is | What you do |
|---|---|---|
| needs-owner | Only the human can act: legal, money, an investor, a regulator, a relationship only they hold | Top of the brief. Label `hub/needs-owner` only if Filing is On |
| reply | A real person waits on an answer a careful assistant could draft | Draft it (step 5); send requested replies only when a person has turned mail sending on in Tico |
| route | It belongs to another team: a lead, a customer, a candidate, a vendor | `hub task create --owner <slug> --parent <id>` per `## Routed to someone else`; do not flag it as needing the human |
| archive | Nothing is asked: a receipt, a notification, a cold pitch | List it as "would file". Archive only if Filing is `labels and archive` |
| read, or unsure | The model was not sure enough | Open the thread and decide yourself |

Anything on the always-reaches list is needs-owner whatever the word says. Legal or money words with an
ask are needs-owner. A message from someone upset is needs-owner. `hub/needs-owner` is scarce; other
teams' mail is not a need for this human.

## 5. Draft replies

Follow `playbooks/draft-a-reply.md` for each `reply`. Use `--dry-run` if draft-writing access is unavailable; otherwise write drafts to Gmail Drafts and include the text in the brief. Sending is a separate switch.

## 6. Write the brief

In the shape of `knowledge/examples/mail-brief.md`: a headline count, "Needs you today" first, then
drafts, then routed items, then what you would file, then anything you could not read. Message ids and
one-line reasons only; no bodies and no address lists in the brief. (A task you file for another bot may carry the
message's own text; see AGENT.md.) Then attach it to the task. If it is worth
keeping, `hub file publish reports/YYYY-MM-DD-mail-brief.md`.

## 7. Finish

Prove the pass with another untriaged read. Commit, then `hub task update <id> --status done --note`:
counts (rules settled, decisions by kind, drafts, routed), the needs-owner ids with one line each, or one
line that the untriaged list was empty. Always finish it: an open scheduled task absorbs the next
occurrence and quietly stops the pass.

## When a mailbox fails

A refusal or a blocked mailbox is a blocked mailbox, not "nothing found". Name the refusal on the task
and finish. Two consecutive failures of the same mailbox is one line on the owner's task.
