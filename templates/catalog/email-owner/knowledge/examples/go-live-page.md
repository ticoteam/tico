A sample Go-live page for a fictional team. Every name, URL and number is invented.

```markdown
# Go-live: a second welcome email that sets up text reminders

GX-22 · Email Owner · Acme, studio booking software

## What goes live

A new email in the welcome flow, sent 2 days after a studio signs up for a trial, only to studios that have not turned
on text reminders yet. Sent from "Maya at Acme" (maya@acme.example). The first welcome email does not change.

**Subject:** Cut no-shows in 2 minutes
**Preview line:** Turn on text reminders before your first class. Clients confirm with one tap.

> Hi {first_name},
>
> Most studios lose a few spots a week to people who forget they booked. A text the day before helps.
>
> In Acme, open Settings, then Reminders, and switch on "Text the day before". Clients get a short text and confirm
> with one tap. It takes about 2 minutes.
>
> [Turn on reminders](https://acme.example/settings/reminders)
>
> Questions? Reply to this email and I'll answer.
>
> Maya, Acme
>
> Acme, 100 Main St, Springfield · [Unsubscribe](https://acme.example/unsubscribe)

(The plain-text version and the email as it looks on a phone are attached: welcome-2.txt, welcome-2-phone.png.)

## The bet

- **Metric:** trial studios that turn on reminders within 7 days of signup, counted as conversions from email
  (the email platform's conversion report, welcome flow).
- **Prediction:** from 18% to 26% of new trial studios by Jan 19, 2027.
- **Why:** 18% of trial studios turned on reminders in their first week (conversion report, 8 weeks to Nov 9). Studios
  that turn them on in week one are twice as likely to pay (product analytics, Oct 2026). Three of the last ten
  replies to the first welcome email asked how reminders work (mailbox, Oct 1 to Nov 9).
- **Audience:** trial signups who agreed to product email at signup (`knowledge/segments.md`). About 90 a week. Nobody
  else gets it.
- **Cost:** no money. About 15 minutes of Jordan's time to add it to the flow.
- **Runs:** 8 weeks, Nov 24 to Jan 19.
- **Stop early if:** spam complaints pass 0.1% of sends, or hard bounces pass 2%, on any day.

## Checks

- **Brand check:** passed. Design Owner, GX-23, Nov 13.
- **Send check:** sender, unsubscribe, postal address, links and plain-text version listed for Jordan to confirm.
- **Who puts it live:** Jordan (sends from the email platform). The Email Owner has read access only.
```
