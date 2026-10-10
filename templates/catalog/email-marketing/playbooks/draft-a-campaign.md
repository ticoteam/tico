# Draft a campaign

Triggered by a task asking for an email or sequence, and used by the weekly draft. Budget 35
minutes for one email, 60 for a sequence. The outcome is one folder in `reports/` holding the draft,
its plain-text version and the checklist.

---

## 1. Read the brief and the record

    hub task show <id>

Then `knowledge/voice.md`, `knowledge/segments.md`, `knowledge/results.md` and
`knowledge/do-not-email.md`. Note what this audience was promised when they signed up.

## 2. Write the draft

`reports/YYYY-MM-DD-<campaign>/email.md`:
- **Audience** and why they get it; **goal** and the single call to action.
- **Three subject lines**, honest to the content, under about 50 characters. Recommend one; propose a
  one-variable test on a slice of the list.
- **Preview line**, 40 to 90 characters.
- **Body**: open with the point for this reader, one idea, short paragraphs, a link with words. No
  invented numbers, quotes, discounts or dates: mark each gap for the owner.
- **Plain-text version** in the same folder.

## 3. Read it as the recipient

Would they know why they got it? Is the promise they signed up under kept? Does it sound like
`knowledge/voice.md` or like a machine? If it fails the last check twice, keep it internal and say so.

## 4. The checklist

Attach it with each item marked "confirm" for the sender: real sender name and address; audience and
consent match; visible unsubscribe and postal address in the footer; the subject matches the content;
links work; plain-text version present; sending domain authentication and complaint rate are the
sender's to check. You cannot verify these and never mark one done.

## 5. Hand over

Commit, then `hub task update <id> --status done --note`: the email in one line, the folder, the gaps
to fill and the checklist items open. Send or schedule requested email with the exact text, audience and sender using your Tools when a person has turned mail sending on in Tico; otherwise keep the draft.
