---
name: check-a-schedule-for-clashes
description: Check a week of bookings for double bookings, breached opening hours, missing buffers and time-zone errors, and say which open slots the rules actually allow, before any day is called clear.
---
# Check a schedule for clashes

When to use: in every weekly bookings page, before a reschedule is offered, and whenever someone asks whether a day or a crew is free.

Steps:
1. Read the export and its time zone. If the zone is missing, read the times as the studio's local time and say so on the page.
2. Sort bookings by resource (a person, a crew, a room, a vehicle) and then by start time.
3. For each resource, mark a clash when two bookings overlap, when a booking starts before or ends after opening hours, or when the gap between two bookings is shorter than the buffer in `knowledge/booking-rules.md`.
4. Check the weeks around a daylight saving change by hand. Bookings made before the change and shown after it are the usual errors.
5. Name the earlier booking as the one that holds the slot, from the booking date in the export. Give the options for the later one that keep the rules.
6. List the open slots. Keep only the slots that the rules allow for that service, lead time and resource. Count those, not the raw gaps.
7. Write each clash with both bookings, their times and the rule it breaks. A person confirms any change.

Checks before you finish:
- Each clash names both bookings and the rule.
- The time zone is stated.
- Open slots are counted after the rules, not before.
- Nothing is moved or cancelled in this check.

Example (generic): "Thu 10:00, crew B: booking X (made 22 Sept) and booking Y (made 29 Sept) overlap. X holds the slot. Options for Y that keep the 30-minute buffer: 13:00 or next day 09:00. Open slots this week after the rules: 6 of 11 raw gaps."
