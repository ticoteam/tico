# Research a lead

Triggered by a task that names one lead, and used for each lead in `playbooks/weekday-prospecting.md`.
Budget 15 minutes. The outcome is one file in `knowledge/leads/` a salesperson can read before a call,
with a tier and a suggested angle.

---

## 1. Read what you have

    hub task show <id>

Look in `knowledge/leads/` for an existing file and update it rather than starting a second. Check `knowledge/do-not-contact.md` first: a stop is not research. Read
`knowledge/icp.md` and `knowledge/scoring.md` before you decide anything is interesting.

## 2. Read the public sources, in order

1. What the organization says about itself: site, product pages, pricing page, about page.
2. What changed lately: news, its own posts, changelog, hiring pages (a new team or role is a signal).
3. Who would buy, by role, from public profiles only; nothing personal.
4. What its customers or users say in public.

## 3. Sort what you found

- **Signal**: something dated in the last 90 days that suggests need or timing: a hire, launch, funding,
  tool change, a public complaint, a question on the site. The last 30 days weigh most.
- **Context**: size, offer, market. One line each.
- **Noise**: awards, generic marketing, anything older than a year and unchanged. Leave it out.

## 4. Write the file

`knowledge/leads/<lead>.md`, five short headings and nothing longer: **Fit** (against the ICP, which
criteria met), **Signals** (each with date and link), **Angle** (the one true thing to open with and
why it matters to them), **Unknown** (what could not be found), **Sources** (one dated line each).
End with the tier and its score reasons.

## 5. Finish

Add the lead to the pack or, for a task, attach the brief and any draft. `hub task update <id> --status
done --note`: the tier, the angle in one line, and the sources you could not read. A source that refused
you is a named gap, never silence.
