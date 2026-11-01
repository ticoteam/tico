---
name: check-a-contact-list-at-list-time
description: Run the suppression rules on each record when it is put on an outreach list, not only when it was labelled, and name the rule that removed each record it drops.
---
# Check a contact list at list time

When to use: before a record is put on an outreach list or given a list date, and before a report says how many records are ready to contact.

Steps:
1. Labelling a record is not permission to contact it. Check the rules at the moment of listing, from current data.
2. Apply the suppression rules in order: a protected status (customer, onboarding, cancelled, do not contact, bad fit); no usable phone or email; a negative status changed in the last 90 days; any contact by anyone on the team in the last 14 days; an open opportunity owned by another person; a hard bounce on the only address; an opt-out or blocked number on the channel.
3. Record the rule that removed each record. A record removed by two rules keeps both.
4. Check the proposed owner against the open opportunity's owner again at the time of assignment.
5. Count the records that pass. Write "not available" for any rule whose data you could not read, and count those records as not cleared.
6. Only then set the list date or the owner. Changing a protected status is never part of listing.

Checks before you finish:
- Every listed record passed every rule, with the date and time of the read.
- Each removal names its rule.
- A rule you could not check leaves its records uncleared, not cleared.
- A pass count says which channel it covers. A pass for one channel is not clearance for another.

Example (invented, for illustration): "Tern Studio list, read 2026-10-14 09:10: 84 candidates. 9 removed for contact in the last 14 days by anyone, 3 for an open opportunity owned by another seller, 2 for a hard bounce. 70 pass. Email channel checked; SMS opt-out check not run, so the 70 are not cleared for SMS."
