---
name: verify-a-due-date-at-source
description: Confirm a due date or filing rule on the authority's own page before it goes on the calendar or into a pack, write the arithmetic, record the date read, and show both values when the register and the source disagree.
---
# Verify a due date at source

When to use: before any due date goes onto the calendar, into a filing pack, or into a note to an owner, and whenever a register row was last confirmed more than twelve months ago.

Steps:
1. Take the rule from the register row, in its words, for example "the 15th day of the fourth month after year end".
2. Open the authority's own page, on its own domain (`hub doc fetch <url>`). Do not use a summary site as the source.
3. Read the rule in the authority's words. Note the date you read it.
4. Work out the date with the arithmetic written out, for example "year end 31 December 2026, fourth month after, 15th day: 15 April 2027".
5. Compare with the register row. If they agree, mark the row confirmed with the date. If they disagree, record both values with their dates. Do not pick one.
6. If the page cannot be read or does not give the rule, write "date not confirmed", list it on the calendar, and keep it there.

Checks before you finish:
- The source is the authority's page, named with its URL.
- The date read is recorded beside the rule.
- The arithmetic is written out.
- Any disagreement shows both values. Nothing is quietly overwritten.

Example (invented, for illustration): "Register row 4: 'the 15th day of the fourth month after year end'. Authority page read 2026-11-01 says the same. Year end 31 December 2026, so due 15 April 2027. Confirmed with the date read."
