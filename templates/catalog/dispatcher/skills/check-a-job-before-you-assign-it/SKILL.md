---
name: check-a-job-before-you-assign-it
description: Run the five hard-limit checks on one job (certificate, parts on the van, confirmed access, customer window, hours and leave) before a crew is put on it, so an assignment that will fail never reaches the plan.
---
# Check a job before you assign it

When to use: for every job in tomorrow's plan, and for each job a same-day change moves. Run it before the route is
built, not after. A job that fails a check is a clash, with its fix, not an assignment.

Steps:
1. Certificate and skill. Read the job type in `knowledge/job-types.md` and the crew's line in `knowledge/crews.md`.
   The crew must hold each certificate the job needs, and the certificate must be in date.
2. Parts. List the parts the job type usually needs and compare them with the crew's van stock. A part not on the van is
   a clash, unless the job has a named supply run before the visit.
3. Access. Confirm the customer has given the access the job needs: keys, a gate code, a building permit, a pet shut
   away. "Not confirmed" is a clash whose fix is a call to the customer.
4. Window. Read the window the customer was given. The planned arrival is the previous job's finish plus the travel time from it. Compare that arrival alone with
   the window, and never add travel a second time. A window the plan cannot keep is a clash.
5. Hours and leave. The whole day, travel included, stays inside the crew's working hours. Check leave in the calendar
   where it is connected. Where it is not connected, write "leave not checked" against that crew on the plan.
6. Place the job on the route only when all five pass. A failed check stays a clash, with its named fix, until the fix is done and the check passes again.

Checks before you finish:
- Each job in the plan has a pass, or a named clash, for each of the five checks.
- "Access not confirmed" is never recorded as "confirmed".
- No job sits on a crew whose certificate has lapsed.

Example (generic): "Job 4071, electrical test, needs a current electrical certificate. Crew B holds one; van stock has the
test kit. The customer confirmed access on 2026-09-28. Window 09:00 to 11:00; the crew's first job ends 08:40 with
25 minutes' travel, so the planned arrival is 09:05. Leave not checked: the calendar is not connected, and the plan says so."
