# Vendor follow-up

Triggered by a task that names a vendor, and used for each quiet thread in
`playbooks/weekly-ops-checklist.md`. Budget 10 minutes per vendor. The outcome is one internal draft a human
can send with one edit, and a line in `knowledge/vendors.md`. Nothing is sent.

---

## 1. Read what was said

    hub task show <id>

Find the last exchange: the operations mailbox if connected (`$HUB_DIR/scripts/mail.sh search "<vendor>"`),
otherwise the task, `hub doc search "<vendor>"` and `knowledge/vendors.md`. Read the whole thread before
you write. A follow-up that repeats a question already answered wastes the vendor's goodwill.

## 2. Decide whether a follow-up is right

Not when the vendor replied and the ball is with the team: that is a task for the owner. Not when the
vendor asked for a decision, a price or a signature: that is a human's, and goes at the top of the page.
Yes when the team asked something and the agreed wait has passed with no answer.

## 3. Draft

Plain text, under 100 words, one ask, one date. Say what the team asked and when, what it needs, and by
when. Add something new that makes an answer easy (the exact document, a smaller question, a named
alternative time), never "just checking in". No price, commitment, cancellation or threat: a draft that
needs one leaves a marked gap. Include the recipient, subject and the source that shows the thread was quiet.

## 4. Hand over

Attach the draft to the task. Send the requested text to its recipient with your Tools when a person has turned mail sending on in Tico; otherwise keep the draft.

## 5. Record

Update the vendor's row: last touch, follow-up drafted on the date, next check date. If a second follow-up
has also gone unanswered, say so on the page and suggest who at the team should call.

## When a source fails

If you cannot read the thread, say so and draft nothing: a follow-up written blind is worse than a late one.
