---
name: check-campaign-tagging
description: Check a traffic-by-source export against the team's tagging convention, find each tagging error with its campaign and owner, and say which traffic cannot be read.
---
# Check campaign tagging

When to use: before you report that campaign traffic is tagged correctly, wrongly or not at all, and before you
name the owner of a tagging fix.

Steps:
1. Load the export for the window (last 7 days, and the 7 days before for the trend). Note its date range and
   which sources it covers. If a source is missing, record it as unread.
2. List every distinct source and medium value. Flag any that break the convention in `knowledge/tracking.md`:
   capitals, spaces, a medium the analytics tool does not recognise, or one source spelled two ways.
3. Find campaign traffic that landed as direct or unassigned. Match each campaign name to a live campaign in
   `knowledge/campaigns.md`. A live campaign with no tagged traffic is a finding; a campaign with no
   traffic at all is not a finding until its launch date has passed.
4. Scan every campaign and content parameter for a name, an email address or a personal detail. This is a
   privacy finding and goes first in the report.
5. For each error, write one line: the link or placement, what is wrong, what it should be, and the owner
   from `knowledge/campaigns.md`.
6. Count the share of campaign traffic that is tagged to the convention. Write it as a count over a total
   ("412 of 503 sessions, 82%"), with the export named.

Checks before you finish:
- Every error names the link or placement it came from, not only the campaign.
- No name or address appears in your report, even when you quote the parameter. Describe it as "one parameter
  holds a personal address" and point to the placement.
- Unread sources are "unknown", never zero.
- The share is a count over a total, and the export and its dates are named.

Example (invented, for illustration): "Tagging is 84% (422 of 502 campaign sessions, export 2026-09-21 to
2026-09-27). One email placement uses medium `Email` with a capital letter, so its 39 sessions split across two
rows; fix: `email`, owner: the lifecycle owner. One social placement landed as direct for 6 sessions; its link
was never tagged; owner: the campaign owner."
