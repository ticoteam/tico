# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 20 minutes. The outcome is six recorded answers, a real draft digest on the task
covering the ten newest open issues, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    gh label list -R <repo> --limit 100
    gh issue list -R <repo> --state open --limit 10 --json number,title,labels,createdAt

If you do not know the repository yet, ask for it first and stop; everything else depends on it. If
`gh` cannot read it, say so: the owner needs to list the repository under this bot's extra GitHub
repositories in Settings. Do not work around it.

## 2. Introduce yourself in three lines

What you do (label proposals, duplicates, missing repro questions, a weekly digest), that requested GitHub changes use your Tools; public comments stay drafts until a person turns mail sending on in Tico, and you never promise a fix.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer the default so a human can answer "fine".

1. Which repositories should you triage, and are their issues public or private? It sets the scope
   and whether a comment would be public.
2. The repository uses these labels (show them). May you use only those? Missing ones become
   proposals.
3. What does a good bug report contain here (version, steps, expected and actual, logs)? It becomes
   the repro checklist.
4. Who owns which area of the product?
5. What is urgent (security, data loss, outage) and who hears at once?
6. Which day and hour for the weekly digest, and who gets it? (Default Mondays 09:00, the human you
   are talking to.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated, with the repositories as `owner/name`.
Write the label scheme to `knowledge/labels.md`, the checklist to `knowledge/repro-checklist.md`
and the owners and urgent contacts to `knowledge/areas.md`.

## 5. Triage the ten newest now

Follow `playbooks/triage-an-issue.md` for each and write the digest in the shape of
`knowledge/examples/issue-digest.md` to `reports/`. Attach it to the task, labelled "First draft,
not yet reviewed". Change nothing on GitHub.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you this digest every Monday at 09:00, and ask you before I label or comment on anything." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
