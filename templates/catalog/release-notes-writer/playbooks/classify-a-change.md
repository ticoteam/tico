# Classify a change

Used for each merged pull request in `playbooks/weekly-release-notes.md`, and on request for a single pull
request. Budget 3 minutes each. The outcome is one line in the right section, or an entry in "Could not
classify". Never a guess written as fact.

---

## 1. Read it

    gh pr view <n> -R <repo> --json title,body,labels,files,url

Read the labels first, then the description, then the file names. Use `knowledge/labels.md` to map labels.

## 2. Decide what the user sees

Ask: would someone using the product notice this? If no, it is internal: count it, leave it out. If yes,
which one:
- **Added**: something new a user can do.
- **Changed**: something a user already did now behaves differently.
- **Deprecated**: it still works and will stop; say when if the pull request says.
- **Removed**: it no longer works.
- **Fixed**: something that was wrong is now right; say what was wrong.
- **Security**: a vulnerability fix. Say only that a fix is included; the detail waits for a human.

## 3. Is it breaking?

Read `skills/find-hidden-breaking-changes/SKILL.md` before you call a change not breaking.

Breaking means existing users must change something: a removed or renamed option, a changed default, a
changed API response, a required migration. Mark it and write what to do instead. If you suspect it but the
pull request does not say, put it in "Could not classify" with the question.

## 4. Write the line

Read `skills/write-release-notes-for-users/SKILL.md` for the wording.

One line, present tense, from the user's side: "You can now export a report as a CSV." Not "Add CSV export
endpoint". No internal names, no ticket numbers as the message, no customer names. End with the pull request
link. Two pull requests that make one user-visible change become one line with both links.

## 5. When to hold it back

Hold and ask if: the description is empty, the title and the diff disagree, it touches security, billing or
data deletion, or it is behind a flag that is not on. Put it in "Could not classify".

## 6. Finish

The classified lines go into the draft. On a single-change request, reply on the task with the line, the
section and the reason in one sentence.
