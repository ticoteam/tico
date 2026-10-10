# Draft a reply

Triggered by a message the brief marked `reply`, or a human asking "draft a reply to this". Budget five
minutes. The outcome is one draft in the human's voice, a note of anything it left open, and nothing sent.

---

## 1. Read the whole thread

    $HUB_DIR/scripts/mail.sh thread <id> --format md

Read the whole conversation, not just the last message, and the human's calendar if a time is
involved (`hub calendar list`). Never copy a token, key or private detail into the task.

## 2. Decide what the reply must do

One thing. Answer the question asked, or say when they will get an answer. If the reply would need money,
a meeting time, a contract or an introduction, it does not commit: it leaves a marked gap, like
`[<person> to confirm a time]`, and the note says what the human decides.

## 3. Write it

To `knowledge/voice.md`: their greeting, their sign-off, their length. Start with the answer. No filler.
Offer times only from a calendar you read, and only as options. Never claim something the thread does not
support.

## 4. Check and save

    $HUB_DIR/scripts/mail.sh draft --reply-to <thread> --body-file out/reply.txt --issue <task id> --dry-run

Read the result: `gate: flagged commitment_money 0.81` means rewrite once. A refusal is not retried with
the same text and is not routed around. When your Tools allow writing drafts, run the same
command without `--dry-run`; it updates the same Gmail draft if run twice. The thread gets `hub/drafted`.

## 5. Report

On the task: the message id, the draft text, the gaps, and one line on why. While mail sending is off in Tico, keep it as a draft. When sending is on, send requested replies within the mailbox rules using your Tools.

When `Sending` is On and a rule there covers this message, send it with no approval, as a reply to the thread so it goes
to the sender and nobody else: `$HUB_DIR/scripts/mail.sh reply --thread <thread> --body-file out/reply.txt --issue <task id>`.
A forward to a `forward_to:` address is a new message to that address: `mail.sh draft --to <address> --subject "..."`, then
`mail.sh send --draft <id>`. The answer says `"sent": true`, or `"sent": false` with the gate that refused: then it is a
draft, leave it on the task and say why. Everything the rules do not cover stays a request for review.
