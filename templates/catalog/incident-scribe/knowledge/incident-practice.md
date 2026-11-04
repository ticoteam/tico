# Incident practice

Reference for the timelines, drafts and action items. Each source carries the date it was read. Where a source
was read only through a page summary or a search excerpt, the line says so.

## Blameless postmortems

- Google's SRE book, chapter "Postmortem Culture: Learning from Failure" (sre.google/sre-book/postmortem-culture/,
  read 2026-11-01 through a page summary): blameless postmortems are a tenet of SRE culture. A postmortem focuses
  on the contributing causes of an incident, not on fault, and assumes everyone involved had good intentions. Its
  aim is preventive actions that reduce the likelihood or impact of a recurrence.
- The same chapter describes a postmortem as a written record of an incident, its impact, the actions taken to
  mitigate or resolve it, and the follow-up actions. Reviewers check that the action plan is appropriate and that
  resulting fixes have an appropriate priority.

## Timing

- PagerDuty's postmortem template (response.pagerduty.com/after/post_mortem_template/, read 2026-11-01 through a
  page summary): the review should be scheduled within 5 business days of an event, and the page should be
  completed by the meeting. Its sections include Overview, What Happened, Contributing Factors, Resolution,
  Impact, Responders, Timeline, Action Items and Messaging.
- This bot's three-working-day default for a draft is stricter than that template. It is a team choice, not a benchmark.

## Severity

- PagerDuty's severity page (response.pagerduty.com/before/severity_levels/, read 2026-11-01 through a page
  summary) defines five levels, SEV-1 to SEV-5. It says: "If you are unsure which level an incident is (e.g. not
  sure if SEV-2 or SEV-1), treat it as the higher one." The page does not say who sets the level.
- The owner sets the scale in `knowledge/triggers.md`. When unsure, record the higher level and say so.

## Roles

- Search excerpts from several vendor pages and from two government incident handbooks (the Government of Canada's
  incident management handbook and Login.gov's incident response checklist; read as search excerpts 2026-11-01)
  agree on three core roles: incident commander, communications lead and scribe. Names and reporting lines vary
  by organisation.
- Most of the sources here are vendor blogs (for example, Rootly, undated search excerpt). Treat them as one team's model.
- The scribe keeps the timeline and the action log. This bot takes that role. It does not command the incident and
  does not speak to outsiders.

## Open questions

- No authoritative benchmark was found for how many days a postmortem should take. The team sets its own default.

## Sources

- sre.google/sre-book/postmortem-culture/, read 2026-11-01 (page summary; quotes checked against the summary, not the full text).
- response.pagerduty.com/after/post_mortem_template/, read 2026-11-01 (page summary).
- response.pagerduty.com/before/severity_levels/, read 2026-11-01 (page summary).
- Search excerpts of vendor and government incident handbooks, 2026-11-01. Not read in full.
