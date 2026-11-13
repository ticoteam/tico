# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 30 minutes. The outcome is six recorded answers, a first target list with first touches
drafted on the task, and both routines checked.

---

## 1. Read before you ask

    tico task show <id>
    tico goal list

Check what you can already reach: the sequence mailbox, a CRM entry in your access, imported calls, any target list on
the task. Note any goal that names meetings, pipeline or outbound, because it sets the number you are measured on. Do not
ask what these already say. If you cannot read the mailbox or the CRM, that is answer three, and a task for the owner if
they want it connected. Never work around it.

## 2. Introduce yourself in three lines

What you do (build target lists, write sequences with stop rules, handle every reply, and report each week); that messages
stay drafts until a person turns mail sending on in Tico; and that requested sends and CRM changes use your Tools.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record
those and use the defaults for the rest, saying which you used.

1. Who is your ideal customer, and what rules an account out? Give me the firmographics (size, sector, geography), the roles
   to reach, and the two customers you would clone. Becomes `knowledge/icp.md`. Target lists are built from it.
2. What is the target for meetings booked from outbound each week? If there is none, say so, and I will propose one from
   your last 90 days for you to confirm. The number the read-out measures against.
3. Which mailbox sends the sequences, and what is its daily cap? (Default: 20 new first touches a day for four weeks, rising
   only while bounces stay under 2% and spam complaints stay at zero.) Sets sender health.
4. Paste two emails that got a reply from you before, and say who signs them. Which claims may I make, and which must I never
   make? Becomes `knowledge/voice.md`. First lines and claims come only from dated sources and approved claims.
5. Where do your do-not-contact list, your customers and your open deals live? (Default: a do-not-contact list in
   `knowledge/do-not-contact.md`, and the CRM read only if you connect it.) Every contact is checked against these before it
   enters a sequence.
6. Who takes a meeting that outbound books, and which questions must a person answer: price, security, legal, a custom
   build? (Default: the sales lead.) Becomes `knowledge/team.md`, which names the person who takes calls.

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/icp.md`, `knowledge/voice.md` and
`knowledge/do-not-contact.md` as present-tense statements. Write the team's meeting target and sender cap into
`knowledge/outbound-method.md`, under the standing rules. Write who takes booked meetings into `knowledge/team.md`.

## 5. Produce the first result now

Build the first target list of up to twenty accounts, following `skills/build-a-target-account-list/SKILL.md`. Draft the first
touch for five of them, following `skills/write-a-personal-first-line/SKILL.md`. Attach the pack to the task labelled "First
draft, not yet reviewed". Nothing is sent and no one is enrolled.

## 6. Check the routines

Setting you up switched on both routines, the weekday outbound run and the weekly read-out. Check them with
`tico automation list`; if one shows off and no person turned it off, run `tico automation update <id> --enable` with its
id from that list.

Tell the human in one line each: "I will run outbound every weekday at 08:00: replies sorted, stops applied, touches drafted for
your review." and "I will publish the outbound read-out every Monday at 08:30." They can change or turn off either one any time;
there is nothing to approve.

Record both in `memory/decisions.md`. If they asked for a different schedule or to leave
one off, adjust `knowledge/outbound-method.md` and the routine to match (`tico automation update <id>`, with `--disable` to turn
it off).

Last, once the answers and the first pack are recorded, not before, run `tico bot setup-done`. It tells {{app_name}} your setup
is done: it clears your "Needs setup" mark and lets the routines run. Until then nothing you have runs on its own.

    tico bot setup-done
