# Access review

Triggered in the first month of each quarter by the monthly page, or by a task that names a tool. Budget 15 minutes per tool.
Nothing is changed unless the task asks for the change, a granted admin Tool allows it, and the tool's owner has decided the
line.

Inputs: an export of users and roles from the tool, from its admin or from `it-support` (as a task attachment), or read
directly where this bot has read access. Also `hub team show`, last quarter's review and `knowledge/controls.md`.

Outcome: `knowledge/access-reviews/<quarter>.md` with one table per tool, shared on the task. Each line has a mark, a reason
and the owner's decision. Where a change is made, a fresh export shows it.

Done when every line is marked and has a decision, the rules used and the before and after exports are filed in the evidence
folder and logged in `knowledge/evidence-log.md`, and the task note says what changed and what is still open.

---

## 1. Get the list of who has access

Record the export's date. The review covers that list and no other. If the export is older than the quarter's start, ask for a
new one before marking anything.

## 2. Mark each line

Follow `skills/review-one-tools-access/SKILL.md`. Marks: **left** (not on the roster), **role changed** (the job no longer needs
the role), **admin** (listed separately), **dormant** (no login within the team's threshold, where the tool shows logins),
**shared or service account** (needs a named owner), or **looks right**. Say why for every line that is not "looks right".

## 3. Record the findings

Write `knowledge/access-reviews/<quarter>.md`: one table per tool with user, role, mark, reason, the keep-or-revoke proposal
and the owner's decision. Admins and anyone marked "left" go at the top. Ask the tool's owner only about a missing rule or an
ambiguous account.

## 4. Close it with evidence

Apply a change only when the task asks for it and a granted admin Tool allows it. Otherwise create a task for `it-support`
naming the tool, the account and the rule (`hub task create --owner it-support`). The review is complete only when a fresh
export shows the changes applied. File the rules used, the before and after exports and their dates in the evidence folder,
and log them in `knowledge/evidence-log.md`.
