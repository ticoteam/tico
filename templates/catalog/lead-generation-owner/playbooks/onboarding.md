# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 30 minutes.
The outcome is five recorded answers, a first lead report attached to the task as a draft, and the first routine checked.

---

## 1. Read before you ask

    tico task show <id>
    tico market segment tree     # the segments, for the segment question
    tico goal list               # any lead target already set; do not ask again what it says

Look at what you can already reach: the CRM where it is connected, and the lead export a person attached. Do not ask what
these already say. If you cannot read the leads, that is answer two, and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do: a weekly count of qualified leads by source, the rule that makes a lead qualified, the hand-off of each
qualified lead to Inbound or Outbound Sales with its reasons, and public lead lists for outbound. That you send nothing, change
no CRM record and never work a deal: the sales owners do that. That a rule, a source or a spend change goes to its owner as a
task, and that it is made only when the task asks for it and a granted Tool allows it.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. What makes a lead qualified here? Give me the profile (size, industry, region, buyer role), and one lead that was
   qualified and one that was not.
   Why: this becomes the qualified-lead rule. Two real examples settle more than a description.
2. Where do leads come from today, and how many arrive a week? Which sources do you keep, and which have you stopped?
   Why: sets the source list every count is split by. A source with no count is shown as not measured.
3. Who takes inbound leads and who takes outbound leads, and how soon must each be picked up? Default: inbound the same
   business day, outbound within two business days.
   Why: these become the hand-off rules. A qualified lead with no owner or no deadline is a lead that waits.
4. Which segments matter, which public sources may we build lists from, and what must never be listed: competitors,
   customers, anyone who opted out?
   Why: sets the segments for every count and the limits on every list. Lists come from public business sources only.
5. Where can I read the CRM, and who should see the weekly count? Default: the person who runs sales, read on Monday.
   Why: sets the read access and who the report is for. The count is read once a week, not live.

## 4. Record

Write each answer, dated, into the `knowledge/` file it belongs to. Write the profile, the not-qualified reasons and the hand-off
times into `knowledge/lead-method.md`, and the sources with their tags into `knowledge/sources.md`. Keep the profile and the
segment list fixed from here on: a change to either is a proposal for the owner.

## 5. Produce the first report now

Follow `playbooks/weekly-lead-report.md` from the counts the owner named. Publish the report with
`tico file publish reports/YYYY-MM-DD-leads.md` and attach it to the task as a draft labelled "First draft, not yet reviewed".
If the owner named no counts, the report says so in its first line and every count reads "not measured". Hand off nothing
and send nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list`. If it shows off, arm it with
`tico automation update <id> --enable`. Then tell the human in one line: "I will send the lead report every Monday at 09:00."
They can change it or turn it off at any time; nothing waits for approval.

Record it in `memory/decisions.md`. If they asked for a different time or to leave it
off, change it with `tico automation update <id>`, using `--disable` to turn it off.

## 7. Finish

Run this once the answers and the first report are recorded, not before. It clears your "Needs setup" mark and lets the
routine run.

    tico bot setup-done
