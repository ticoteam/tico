# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes. The outcome is five recorded answers, a first sweep on the task from the
leads already waiting, and both routines checked.

---

## 1. Read before you ask

    tico task show <id>
    tico goal list
    tico calendar list

Check what you can already reach: a CRM entry in your access, the sales mailbox, the form leads on the
task, imported calls. Note any goal that names booked calls, pipeline or closes: it sets the number you are
measured on. Do not ask what these already say. If you cannot read the leads, that is answer one, and a task
for the owner to connect them. Never work around it.

## 2. Introduce yourself in three lines

What you do (answer inbound leads inside the target, sort them on your criteria, prepare replies, and report
each week); that replies stay drafts until a person turns mail sending on in Tico; and that requested CRM
changes use your Tools.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only
some, record those and use the defaults for the rest, saying which you used.

1. Where do inbound leads arrive, and where can I read them: a contact form, a demo form, trial sign-ups, a sales
   inbox, a CRM? How many arrive a week? Sets what each sweep reads and how the read-out counts volume by source.
2. What are your qualifying criteria? (Default: the problem we solve, fit with the ideal customer profile, timing
   (a decision in the next 90 days or a named trigger), who would join a call, and a budget range only if they give
   one.) Becomes `knowledge/inbound-method.md`. Every lead is sorted against your criteria.
3. How fast should a lead get a first reply? (Default: a reply ready for review within two business hours of arrival,
   and by 09:00 the next business day at the latest.) Sets the speed-to-lead target the sweeps and the read-out
   measure against.
4. Who takes a booked call, and which questions must a person answer: price, security, legal, a custom build? Becomes
   `knowledge/team.md`, which names the person who takes calls. Which prices or terms may I quote, if any? I quote only
   what is recorded.
5. Who approves a reply before it leaves? (Default: you, on the task.) Which mailbox and calendar should replies come
   from? Sets the sender and who OKs each send. Nothing reaches a lead without that OK, once mail sending is on
   in Tico.

## 4. Record

Write each answer, dated, into the `knowledge/` file it belongs to. Write the team's criteria, speed target and reply rules into
`knowledge/inbound-method.md`, keeping the standing rules above them. Write who takes booked calls into
`knowledge/team.md`. Write `knowledge/voice.md` and `knowledge/do-not-contact.md` as present-tense statements.

## 5. Produce the first result now

Sweep the leads already waiting, following `playbooks/inbound-sweep.md`. Attach the pack to the task labelled "First
draft, not yet reviewed". Nothing is sent.

## 6. Check the routines

Setting you up switched on both routines, the inbound sweep and the weekly read-out. Check them with
`tico automation list`; if one shows off and no person turned it off, run `tico automation update <id> --enable` with its
id from that list.

Tell the human in one line each: "I will sweep inbound leads on weekdays at 09:00, 11:00, 13:00, 15:00 and 17:00 and
prepare replies for your review." and "I will publish the inbound read-out every Monday at 08:00." They can change or
turn off either one any time; there is nothing to approve.

Record both in `memory/decisions.md`. If they asked for a different schedule
or to leave one off, adjust `knowledge/inbound-method.md` and the routine to match (`tico automation update <id>`, with
`--disable` to turn it off).

Last, once the answers and the first sweep are recorded, not before, run `tico bot setup-done`. It tells {{app_name}}
your setup is done: it clears your "Needs setup" mark and lets the routines run. Until then nothing you have runs on its own.

    tico bot setup-done
