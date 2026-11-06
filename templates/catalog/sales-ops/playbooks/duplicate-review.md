# Duplicate review

Triggered by a task that asks for duplicates, and by the monthly step in `playbooks/weekly-crm-report.md`.
Budget 30 minutes. The outcome is a list of duplicate candidate groups with a proposed survivor, and requested merges applied with CRM Tools.

---

## 1. Read the rules

`knowledge/hygiene-rules.md` says what counts as a duplicate here. Defaults: two leads sharing an
email or a website domain; two contacts with the same email; two leads whose names match after
removing punctuation and legal suffixes and that share a domain or a city.

## 2. Find candidates

Read leads and contacts created in the last 30 days first, then the rest. Group them. Never store an email
or phone in a file: refer to records by id and organization name.

## 3. Sort each group

- **Sure**: same email or same domain plus same name. Propose the record with the newest activity and the most
  complete fields as the survivor, and list which fields the other holds that would be lost.
- **Maybe**: similar names, different domains. List and say what would settle it.
- **Not a duplicate**: parent and subsidiary, franchise locations. Note the pair so it is not raised again.

## 4. Write the list

In the report or on the task: the group, the records with owners, the proposed survivor, why, and who
should merge. Apply requested merges with your CRM Tools and verify the surviving record. If a group crosses two owners, name both.

## 5. Finish

Record the outcome in `knowledge/exceptions.md`. `tico task update <id> --status done --note`: how many
groups, how many sure, and the sources you could not read.
