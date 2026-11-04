---
name: tag-one-feedback-item
description: Tag one piece of customer feedback with one primary theme, a severity, a sentiment and a segment from the shared theme list, then strip personal details before it is logged.
---
# Tag one feedback item

When to use: for every item in a batch, a call excerpt, a review or a survey comment that goes into the weekly log or
an after-launch read.

Steps:
1. Read the whole item once, then the theme list in `knowledge/themes.md`. Do not tag from the first sentence. The
   complaint is often in the second.
2. Pick the one theme the customer is most upset about. If two fit equally, take the one that blocks them, and note the
   other in the log as a secondary tag. Count the item once.
3. Set severity by what the customer could do: **blocked** (could not finish what they came for), **annoyed** (finished,
   with pain), **suggestion** (a wish, no pain stated).
4. Set sentiment from the words, not from a star rating. "Works fine, I just wish it had X" is a suggestion with neutral
   sentiment, even at three stars.
5. Set the segment only when the source states it, using `knowledge/segments.md`. Otherwise write "unknown".
6. Remove names, emails, account ids, phone numbers and any credential before the line is written. A quote keeps its
   sentence, not its identifying detail.
7. If nothing in the list fits, write "unclassified" with a one-line description. Do not stretch a theme to fit.

Checks before you finish:
- One primary theme and one severity per item.
- Praise and empty items are logged as "no issue", not dropped.
- The log line holds no personal detail.
- A subject that fails to fit three items goes to "unclassified", not into a new theme.

Example (invented): a support ticket reads "I searched for ten minutes before I found where to change the reminder time."
Theme: usability. Severity: annoyed, because the customer finished the task. Sentiment: negative. Segment: unknown. A
candidate quote, with the name removed.
