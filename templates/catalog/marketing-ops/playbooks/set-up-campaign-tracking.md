# Set up campaign tracking

Triggered by a task like "we launch the October email and ads on Monday" or "tag the links for the
expo". Budget 15 minutes. The outcome is every link the campaign needs, tagged to the convention, on
the task for the campaign owner.

---

## 1. Read the request

    tico task show <id>

List the channels (each email, each ad platform, each social post, partner or event), the landing
pages and the owner. If a landing page is missing, ask once with `tico task ask <id> --to <person> "<question>" --version N`.

## 2. Name the campaign

`yyyy-mm-theme` from `knowledge/tracking.md` (for example `2026-10-waitlist-launch`). Reuse the name
if the campaign already exists in `knowledge/campaigns.md`; one campaign, one name, every channel.

## 3. Build the links

One row per placement: landing page, source (the platform or sender), medium (from the convention's
list), campaign, and content where two links in one placement need telling apart. Lowercase, hyphens,
no personal data, no tags on links between the team's own pages. Open each landing page once to
check it loads and that a lead form on it carries the hidden source field.

## 4. Hand over

A table on the task: placement, final URL. Note anything that will not be measurable (a QR code on
print with no short link, a form without the source field) and what to do about it. Add the campaign
to `knowledge/campaigns.md`. `tico task done <id> -m "..." --version N`.
