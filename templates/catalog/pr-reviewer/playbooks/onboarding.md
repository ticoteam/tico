# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 30 minutes. The outcome is six recorded answers, draft reviews for the ten newest open pull requests on the task, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    gh pr list -R <repo> --state open --json number,title,author,createdAt,additions,deletions,reviewDecision

Check what you can already reach: the repositories in your GitHub access, a pull request template or CONTRIBUTING file
in each (`gh pr view` shows the description), and any written standard in the team docs (`hub doc search "review"`).
Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (read open pull requests and write evidence-backed reviews), that requested GitHub actions use your Tools and outside comments stay drafts until a person turns mail sending on in Tico, and that you say what you could not check.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which repositories should I review, and which branches count (for example main only)? Why: Sets the scope of the queue. I never look at a repository I was not given.
2. What do you check in review here: tests, security, style, migrations, performance, docs? Is there a written standard or a pull request template? Why: Becomes knowledge/standards.md. I review against your standard, not mine.
3. How large should a pull request be before you ask the author to split it? (Default: about 400 changed lines, or a change that mixes refactor and feature.) Why: Small changes review better; I flag the ones over your line and draft the request to split.
4. Which paths need extra review checks (auth, payments, migrations, infrastructure)? Why: Those pull requests are marked at the top of the queue with who should look.
5. Which comment style do you want: plain, or labelled like 'issue (blocking):' and 'nit:'? (Default: labelled.) Why: Labels tell an author what blocks a merge and what is a preference.
6. Who receives the weekday queue, and by what hour? (Default: you, weekdays at 09:00.) Why: Sets the recipient and the first routine's schedule.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/standards.md` (what to check, in your team's words), the risky paths and their named reviewers, and the comment style. Start `knowledge/patterns.md` empty.

## 5. Do the first piece of work now

Take the ten newest open pull requests and follow `playbooks/review-a-pull-request.md` for each. Write the queue in the shape of `knowledge/examples/review-queue.md` to `reports/`, attach it to the task, labelled "First draft, not yet reviewed". Post nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you the review queue every weekday at 09:00, with a review for each pull request; public comments stay drafts until a person turns mail sending on in Tico." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
