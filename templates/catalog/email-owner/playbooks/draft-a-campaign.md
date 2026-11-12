# Draft a campaign

How to make the real thing when a bet is an email or a flow. Used inside a Building task, at the step "Make the real
thing" in `playbooks/build-a-bet.md`, and when a person asks on a task for an email to be written. Budget 35 minutes for
one email, 60 for a flow. The outcome is one folder in `reports/` holding the email exactly as it will go live, its
plain-text version and the send check. Nothing is sent: what goes live, and when, is the Go-live page's to say.

Inputs: the task (audience, job, call to action, when it should reach people); `knowledge/voice.md`,
`knowledge/segments.md`, `knowledge/results.md` and `knowledge/do-not-email.md`.
Done when: the folder holds the email, the plain-text version and the send check with every item marked confirm; each
gap is named; the drafts are attached to the task.

---

## 1. Read the task and the record

    tico task show <id>

Then `knowledge/voice.md`, `knowledge/segments.md`, `knowledge/results.md` and `knowledge/do-not-email.md`. Note what
this audience agreed to get when it signed up. If the audience never agreed to this kind of email, stop and say so on
the task: it is not a bet you can build.

## 2. Write the email

`reports/YYYY-MM-DD-<campaign>/email.md`:
- **Audience** and why they get it; **goal** and the single call to action.
- **The subject line**, honest to the content, under about 50 characters. A subject line test is part of the bet:
  name the two lines and the one variable.
- **Preview line**, 40 to 90 characters.
- **Body**: open with the point for this reader, one idea, short paragraphs, a link with words. No invented numbers,
  quotes, discounts or dates: mark each gap for the owner.
- **Plain-text version** in the same folder.
- **For a flow**: each email with the delay before it and the event that stops the flow (a purchase, a reply, an
  unsubscribe, a booking).
- **For a test**: the click, reply or conversion metric, the smallest lift worth finding and the sample size, per
  `skills/design-an-ab-test/SKILL.md`.

## 3. Read it as the recipient

Would they know why they got it? Is the promise they signed up under kept? Does it sound like `knowledge/voice.md` or
like a machine? If it fails the last check twice, rewrite it before it goes on a Go-live page.

## 4. The send check

Run `skills/check-a-send-before-it-goes/SKILL.md`. Attach the check with each item marked "confirm" for the person who
sends: real sender name and address; audience and consent match; visible unsubscribe and postal address in the footer;
the subject matches the content; links work; plain-text version present; marketing mail has a one-click unsubscribe
header; sending domain authentication and complaint rate are the sender's to check. You cannot verify these and never
mark one done.

## 5. Hand back to the bet

Commit, and attach the drafts to the task. Then go on with `playbooks/build-a-bet.md`: the brand check and the Go-live
page. The email goes live only after a person OKs that page, and then as `playbooks/run-and-measure.md` says: by you
with a granted Tool once a person has turned mail sending on in Tico, or by the person your playbook names.

When a person asked for the email on a task of its own, attach the drafts there and finish that task with what is
still open. You send nothing from it: the person sends it, or it becomes an idea in the pipeline.
