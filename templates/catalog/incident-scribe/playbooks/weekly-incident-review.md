# Weekly incident review

Schedule: Mondays at 10:00 team time (routine `weekly-incident-review`), after setup. Also run by hand.
Budget 45 minutes. The outcome is one page: incidents since last week, a draft postmortem for each that
meets the trigger, action items past due, and repeats. Nothing is posted.

---

## Inputs

- `knowledge/triggers.md`, `knowledge/actions.md`, `knowledge/patterns.md`, and last week's review in `reports/`.
- Tasks and updates from the last seven days: `hub task list --status open --status done` and `hub update list --kind daily`.
- The incident channel and any error-tracker export, if the owner connected or attached them (read only).
- Merged changes and deploys near each incident: `gh pr list -R <repo> --state merged --search "merged:>YYYY-MM-DD"`.
- Debrief meetings: `hub meeting search "incident"`.

## Steps

1. **Read where things stand.** `hub task show <id>`, then the inputs above.
2. **Find last week's incidents.** Use the sources in the order listed. List each incident with its start and
   end time (UTC), severity and status. If there are none, write "no incidents found" and name the sources read.
3. **Build a timeline for each.** Follow `playbooks/build-a-timeline.md`. Reuse a timeline written during the incident.
4. **Draft a postmortem where the trigger is met.** Follow `playbooks/draft-a-postmortem.md`. Below the trigger,
   write two lines in the review and no postmortem.
5. **Follow up the action items.** List items past due, items done since last week, and items with no owner. Ask
   the incident lead about an item past due by two weeks. A cause already in `knowledge/patterns.md` is the headline.
6. **Write and publish the review.** Write `reports/YYYY-MM-DD-incident-review.md`, then run
   `hub file publish reports/YYYY-MM-DD-incident-review.md`.
7. **Finish.** Commit, then `hub task update <id> --status done --note` with the incidents, drafts, past-due
   items and unread sources. Share the review only as the task or Tools allow. Keep details of unpatched
   weaknesses out of any copy that leaves the team.

## Output

`reports/YYYY-MM-DD-incident-review.md`, in this order: headline, incidents (one line each), postmortem drafts
(paths), action items past due, repeats, and what you could not read.

## Done when

- Each incident has a timeline or a named gap.
- Each incident that meets the trigger has a draft. Each one below it has two lines in the review.
- Past-due and no-owner action items are listed.
- The review is published, and the task is closed with a note.

## When a source fails

Name the source and what is therefore unknown in each affected timeline. A timeline built from one of three
sources that reads like a full account is worse than a marked gap.
