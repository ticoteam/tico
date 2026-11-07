# Build a lead list

Use when a task asks for a list of companies for Outbound Sales, or when the outbound owner asks for one. Budget 60 minutes.

Outcome: a list in `knowledge/lists/<slug>.md`, where every company carries its public source and the rule it matched, handed
to the outbound sales owner as a task. Nothing is sent.

Done when the list has no personal contact details, every company has its source and date, the exclusions are applied and
counted, and the hand-off task is open.

---

## 1. Read the brief

From the task, take the segment, size, region, buyer role, the count wanted, the reason for the list and the date it is needed.
If the brief has no profile to match, ask the person who runs sales. Do not build a list on a guess.

## 2. Check for an existing list

Look in `knowledge/lists/` for a list on the same segment. Add to it rather than make a near copy.

## 3. Build the exclusions

Remove: existing customers (from the CRM, where it is readable, or `hub sql "<select>"` for the team's tables), competitors,
anyone on the do-not-contact list, and companies already in an open sequence (from the outbound owner's list, where readable).
Count each exclusion, with its reason.

## 4. Find companies from public sources

Use `skills/build-a-public-lead-list/SKILL.md`. Public sources only: company sites, news, job posts, public directories and
company registries. Do not use pages behind a login or scraped personal profiles.

## 5. Record each company

For each company: its name, its website, the segment it matches, the dated public fact that shows it, and the buyer role title
from a public page. A person's name goes on the list only when the company itself publishes that person as the contact for the
role.

## 6. Qualify against the profile

Score each company with `skills/score-a-lead-against-the-profile/SKILL.md`, treating it as a target: keep the ones that match,
and count the rest with their reasons.

## 7. Write the list

Save `knowledge/lists/<slug>.md`: the brief, the date, the count kept and the count dropped with reasons, the exclusions counted,
and the table of companies, each line ending with its source and date.

## 8. Hand it over

    hub task create --owner outbound-sales-owner --title "Public list: <segment>, <count> companies" --body "<path to the list, the count, the rule, the window>"

The outbound owner decides what to send and to whom. Nothing is sent from this playbook.

## 9. Name what is missing

Name in the list and in the task any source that could not be read, any segment with too few companies, and any buyer role that
could not be found on a public page.
