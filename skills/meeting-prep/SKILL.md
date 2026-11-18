---
name: meeting-prep
description: Prepare for a scheduled call you are on. Read when your task says "Prepare for <title>" (Tico gives it 30 minutes before the call). Read the call's thread, the notes of its earlier calls, its linked tasks and files and your own recent work, then post one short prep message in the thread.
---

# Meeting prep

A person booked a call and put you on it. Before it starts, you post one short note in the call's thread, so the
people come in knowing what you will bring and what needs deciding. Your task names the call and its thread.

## Read first

- The thread: `tico thread show <thread> --flat` (and `tico thread catchup <thread>` for what is new to you).
- Earlier calls in that thread: `tico call list <thread>`. Their notes are the thread messages marked as call notes
  (`refs.call.notes`): the summary and action items each call left.
- What the thread links to: the tasks and files its messages name (`tico task show <id>`, the files they attach).
- Your own work on it: your recent tasks and what you said in the thread.

Read only what bears on the call. Do not start new work for it here.

## Post one message

Post it in the call's thread, once: `tico thread post <thread> "<message>"`. About 150 words at most, as bullets
under three short labels, leaving out a label that has nothing:

- **I'll bring**: what you have ready, with numbers or links where you have them.
- **Open questions**: what you need answered, each one line.
- **Decisions needed**: what the people on the call must decide, with the options.

No greeting, no recap of the thread, no ids or paths. If the thread refuses your post, make the same message your
reply on this task instead (your reply is posted there). Then mark your task done; what you posted is your context
when the call starts.
