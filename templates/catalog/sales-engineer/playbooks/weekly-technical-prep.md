# Weekly technical deal prep

Schedule: Wednesdays at 09:00 team time (routine `weekly-technical-prep`), after setup. Also run by hand. Budget 40 minutes. The outcome is one page: every deal with a technical
step in the next two weeks, prepared. Nothing goes to a prospect.

---

## 1. Find the deals

    hub task list --owner me
    hub calendar list

Add deals the Account Executive's latest review lists with a demo, a POC, a security review or a
questionnaire. Read last week's prep: what was promised, done or slipped.

## 2. Discovery gaps

For each deal, compare its technical note with `knowledge/technical-discovery.md`. List the questions still
open (systems, integrations, data volume, single sign-on, security review, evaluators) and suggest where to
ask them: the next call, or a line in the Account Executive's recap.

## 3. Demos

For each demo in the next two weeks, write or refresh the script in `knowledge/demos/<deal>.md`: the three
to five workflows from discovery, in the buyer's words, the data to use (never another customer's), and what
to skip. Check each step against `knowledge/never-show.md`.

## 4. Proofs of concept

For each running POC, score its success criteria: met, not yet, at risk, with evidence. A POC past its end
date or with changed criteria goes to the Account Executive at the top of the page.

## 5. Questionnaires due

List sections due with counts: answered from documented sources, waiting on a named owner. Follow
`playbooks/technical-questionnaire.md` for new ones.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-technical-prep.md` in the shape of `knowledge/examples/technical-prep.md`, `hub file publish` it, commit, and `hub task done <id> -m "..." --version N`: deals prepared, POCs at risk,
answers waiting on owners. Always finish the task.
