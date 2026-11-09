---
name: write-an-engineering-bug-report
description: Turn a customer's report into a bug report an engineer can act on without asking a question back, with steps, expected and actual result, environment, frequency, impact and evidence.
---
# Write an engineering bug report

When to use: when a case is a defect, or may be one, and engineering needs a report before it can reproduce the problem.

Steps:
1. Title: what breaks and where, in one line. "Waitlist emails do not send for one studio since 2026-09-24" beats "emails broken".
2. Steps to reproduce from a known state: the account type, the screen, the exact action, in order. If you cannot reproduce it, say so, and ask the technical support engineer first with `hub task create --owner technical-support`.
3. Expected and actual result, one sentence each, with the exact message or code the customer saw.
4. Environment: plan, app or browser version, region, and anything unusual about the account, with no personal details.
5. Frequency and reach: how often it happens and how many customers or accounts it touches, from a count you can cite.
6. Impact in the customer's terms: what they could not do, what it cost them, and the deadline at risk.
7. Evidence: ticket quotes with date and time, screenshot names, and log lines with tokens and personal details removed. Never paste a credential, a token or another customer's data.
8. The next step asked of engineering, with a date, and the case owner's name for questions.

Checks before you finish:
- An engineer could reproduce the issue, or knows exactly what is missing.
- Every claim has a date or a source.
- No credential, token, personal detail or other customer's data is in the report.
- "Cause unknown" is written as unknown. No root cause is guessed.

Example (invented, for illustration): "Title: Waitlist emails do not send for one studio since 2026-09-24. Steps: create a class with a waitlist, fill it, cancel one booking. Expected: the first waitlisted member gets an email within 5 minutes. Actual: no email, and the waitlist shows the member as notified. Frequency: 6 of 6 attempts since 2026-09-24. Impact: one studio, 40 waitlisted members. Evidence: ticket of 2026-09-25 09:12, customer quote from the waitlist screen."
