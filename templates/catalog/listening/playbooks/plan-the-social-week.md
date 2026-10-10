# Plan the social week

Triggered every other Monday by a task from the owner or the marketing head, or on request once the
weekday sweep is running. Budget 30 minutes. The outcome is two weeks of posts in
`knowledge/social-calendar.md`, each written for its channel and ready as one batch.
Nothing is published by this playbook.

---

## 1. Read what is already planned

    hub task show <id>
    hub calendar list

Read `knowledge/social-calendar.md`, the last two weeks of sweep digests in `reports/sweeps/`, and
the Content Marketer's plan if the team has one (`hub update list --bot content`). Launches, events
and announcements on the calendar come first; do not invent news.

## 2. Choose the posts

Per account, the cadence in `knowledge/social-calendar.md` (default three a week on the main account).
Mix, in this order of preference: something useful the audience asked in public this fortnight
(from the digests), a piece the team published, a launch or event, a customer story whose subjects agreed to share it. At most one in five posts asks for anything (a sign-up, a demo, a purchase).

## 3. Write each post for its channel

One idea per post. The first line carries it; the link, if any, goes last. Keep to the channel's
length and the voice in the Content Marketer's `knowledge/voice.md` if it exists. A claim needs its
source; a customer, a partner or a person is named only when they agreed in writing on a task.
Paid or gifted endorsements carry a clear disclosure. Alt text for every image you ask for.

## 4. Prepare and post the batch

Write the batch to `reports/social/YYYY-MM-DD-plan.md`: date, account, text, link, image brief, source.
The payload lists each post's account, time and exact text. Publish or schedule requested posts
with your Tools when a person has turned mail sending on in Tico; otherwise keep the drafts and their destinations.

## 5. Record and finish

Mark each post drafted, scheduled, published, changed or dropped in `knowledge/social-calendar.md`.
A correction teaches the voice: add the lesson to `memory/learnings.md`.
`hub task update <id> --status done --note` with the count planned, published and still drafted.
