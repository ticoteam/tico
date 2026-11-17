# Draft a reply

Triggered by a message the brief marked `reply`, or a human asking "draft a reply to this". Budget five
minutes. The outcome is one draft in the human's voice, a note of anything it left open, and nothing sent.

---

## 1. Read the whole thread

    tico email thread <address> <thread-id>

Read the whole conversation, not just the last message, and the human's calendar if a time is
involved (`tico calendar list`). Never copy a token, key or private detail into the task.

## 2. Decide what the reply must do

One thing. Answer the question asked, or say when they will get an answer. If the reply would need money,
a meeting time, a contract or an introduction, it does not commit: it leaves a marked gap, like
`[<person> to confirm a time]`, and the note says what the human decides.

## 3. Write it

To `knowledge/voice.md`: their greeting, their sign-off, their length. Start with the answer. No filler.
Offer times only from a calendar you read, and only as options. Never claim something the thread does not
support.

## 4. Check and save

    tico email lint <address> --body-file out/reply.txt --to <sender>
    tico email draft <address> --reply-to <thread> --body-file out/reply.txt

Read the result: a lint error or a flagged second review means rewrite once. A refusal is not retried with
the same text and is not routed around. Draft only when your Tools allow writing drafts (while setup is
pending, the text goes on the task instead); running it twice finds the same Gmail draft. The thread gets `hub/drafted`.

## 5. Report

On the task: the message id, the draft text, the gaps, and one line on why. While mail sending is off in Tico, keep it as a draft. When sending is on, send requested replies within the mailbox rules using your Tools.

When `Sending` is On and a rule there covers this message, send it as a reply to the thread so it goes to the sender
and nobody else: `tico email reply <address> --thread <thread> --body-file out/reply.txt`.
A forward to an address the rules name is a new message to that address: `tico email draft <address> --to <recipient> --subject "..." --body-file <path>`,
then `tico email send <address> --draft <draft-id>`. Tico decides every send: the answer says `pending` (it goes after
the undo window), `waiting_approval` (once a person OKs it in Tico), or `blocked` with Tico's reason: then leave the
draft on the task and say why. Everything the rules do not cover stays a request for review.
