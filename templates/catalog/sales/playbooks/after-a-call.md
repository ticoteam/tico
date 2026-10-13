# After a call

Triggered by a task that names a sales call, or by a new imported meeting with a prospect. Budget 20
minutes. The outcome is a recap ready to use, an updated deal note and mutual action plan, and the
next step with a date and an owner on both sides.

---

## 1. Read the call

    hub task show <id>
    hub meeting search "<customer>"
    hub meeting read <meeting-id>

Note, with timestamps: the problem in the buyer's words, what they need to see next, who else must say
yes (the decision maker, the signer, IT or security, procurement), dates they mentioned, and any question
about price, terms or a competitor.

## 2. Update the deal note

In `knowledge/deals/<deal>.md`: the problem quotes, the decision process as far as it is known, who signs,
and open questions. What nobody said stays unknown; never infer a budget or a signer.

## 3. Update the mutual action plan

Steps from today to signature and go-live, each with an owner on each side by name and a date: security
review, legal review, pricing approval, signature, kickoff. Work backwards from the buyer's go-live date if
they gave one. A step with no date is marked "date to agree".

## 4. Write the recap

Under 150 words, plain text, the seller's voice: what we heard (two or three lines in their words), what we
agreed, who does what by when, the next meeting. A price or term question is not answered: it is a line
for the seller. Put the exact text and recipients on the task. Send requested recaps with your Tools when a person has turned mail sending on in Tico; otherwise leave a draft in the seller's mailbox.

## 5. Hand over

If the buyer asked for a proposal or sent a questionnaire, open `playbooks/proposal-or-rfp.md` as a
follow-on task. `hub task update <id> --status done --note`: the next step and date, what is ready to act on.
