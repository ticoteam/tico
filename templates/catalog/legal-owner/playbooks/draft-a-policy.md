# Draft a policy

Triggered by a task asking for a policy, or by a gap `knowledge/policies.md` records. Budget 45 minutes. The
outcome is a draft a human can adopt with small edits, with its sources and its open questions. It is a draft
for a human, not legal advice, and it is not in force until the owner adopts it.

---

## 1. Scope it

    hub task show <id>
    hub doc search "<policy topic>"

Write down: who it applies to, what it covers and does not, which laws or customer contracts require it (from
`knowledge/obligations.md`; "not checked" where you have not), and who will own it after adoption. Read the
team's existing policies so the new one uses the same terms and does not contradict them.

## 2. Draft

Plain language, one page where the subject allows: purpose, scope, the rules as numbered "we do / we do not"
statements, who to ask, what happens on a breach (a human's decision, never automatic), owner and review date.
Mark every choice the team must make as `[DECIDE:...]` with the options. Do not copy another organization's policy.

## 3. List what it rests on

Under `## Sources`: each law, contract or document it draws on with its date, and every place you assumed.
Under `## Open questions`: the decisions, and whether counsel should review before adoption.

## 4. Hand over

Save `reports/policies/<name>-draft.md`, `hub file publish` it, and put it on the task with the one line:
"Draft for a human, not legal advice. Adopt it here, and I will hand it to the Librarian to publish." Add it to
`knowledge/policies.md` as "draft". When policy adoption is requested: `hub task create --owner librarian
--title "Publish the <name> policy" --body "<path, adopted by, date>"`.

## Done when
- The draft is saved, published and on the task with the "draft for a human" line; `knowledge/policies.md` shows it as "draft".
- Every choice the team must make is a `[DECIDE:...]` with its options, and every source is dated.
- The draft has no clause copied from another organisation's policy.
