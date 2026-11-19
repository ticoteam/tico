---
name: meeting-prep
description: Prepare for a call you are on. Read when your task says "Prepare for <title>". Gather likely answers and add them to your call notes. For a scheduled call, also post a short prep message in its thread.
---

# Meeting prep

Your task names the call and its thread. If the call is live, get the first useful facts into your brief as soon as
you have them. Aim to get those facts in within a minute or two, then finish the task.

## Gather likely answers

- Read your role, skills and knowledge for the questions people usually bring to you. Pull the latest numbers,
  recent work and open tasks that answer those questions. For example, an SEO bot checks its latest rankings and
  traffic with their dates.
- Read `tico thread show <thread> --flat` and the linked tasks or files that bear on the call. The thread's earlier
  call summaries and action items are marked `refs.call.notes` in its JSON. For an earlier call you were on, read
  your brief with `tico call notes --call <earlier-call-id>` if it is available.

Use readily available sources. If a source is slow, move on to what you can verify. Include dates and links when
they help, and make clear when a number is stale. Keep facts shareable with the people in the thread.

## Write the brief

Use `tico call note --call <call-id> "<text>"` with the call ID from this task. Add the first useful facts right
away, then append any further facts worth having ready. Keep each entry to a few short lines. Mark unknowns as
open questions. The call can pick up the notes as you write them.

## For a scheduled call

Post it in the call's thread, once: `tico thread post <thread> "<message>"`. About 150 words at most, as bullets
under three short labels, leaving out a label that has nothing:

- **I'll bring**: what you have ready, with numbers or links where you have them.
- **Open questions**: what you need answered, each one line.
- **Decisions needed**: what the people on the call must decide, with the options.

No greeting, no recap of the thread, no ids or paths. If the thread refuses your post, make the same message your
reply on this task instead (your reply is posted there). Mark your task done after writing the brief and, for a
scheduled call, the prep message.
