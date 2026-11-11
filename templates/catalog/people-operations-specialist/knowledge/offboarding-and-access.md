# Offboarding and access

Reference for why the offboarding checklist runs in this order, and for the access rules behind it. Most figures and rules here
come from consultancy and vendor guides to ISO/IEC 27001, not from the standard's text. The team's own policy decides where it
differs.

## Access

- Access ends on the leaver's last day, not afterwards. Privileged access (admin, finance, production, owner roles) comes first,
  and each access owner confirms in writing.
- The standard asks that security duties continuing after someone leaves or changes role are defined, enforced and
  communicated. Role changes (movers) follow the same steps: access changed, assets returned, confidentiality reminded.
- Timing is not fixed by the standard. One audit checklist expects access to be cut or changed on the effective date. A study
  guide uses 24 hours as a sample window. This file's proposal: privileged access removed on the last day and confirmed within
  24 hours; other access removed by the end of the last day. The team sets its own window in `knowledge/offboarding-base.md`.
- Credentials still active for former staff are among the most common findings in ISO 27001 surveillance audits, per consultancy
  guides. Delay between the last day and removal can run to weeks, which is why the check is scheduled rather than left to
  memory.

## Evidence an auditor would look for

- An exit checklist with an owner and a date for each item.
- System logs showing when each account was disabled, compared with the last day.
- A signed acknowledgement from the leaver of duties that continue after leaving (confidentiality, intellectual property,
  returned assets).

## Companion controls

- Return of assets and access rights are usually built into the same checklist. In the 2022 edition of the standard these sit
  at Annex A controls 5.11 and 5.18; the termination control is 6.5. Consultancy guides map 6.5 to 7.3.1 of the 2013 edition.

## Pay and letters

- Final pay timing, and what it must include, depends on the jurisdiction. Get the rule from payroll and counsel. This file does
  not state one.
- A letter gives dates and title only, unless a human has written a different reason for that letter.

## Limits

- Sources are consultancy and vendor guides about ISO/IEC 27001. The standard text was not read for this file. Check against the
  team's own certification scope, if the team has one.
- The 24-hour window is a proposal, not a legal or standard requirement.

## Sources

- ISO/IEC 27001:2022 Annex A control 6.5, with 5.11 and 5.18, as summarised by consultancy guides:
  - https://www.upguard.com/compliance/iso-27001/6-5/
  - https://advisera.com/iso27001/control-6-5-responsibilities-after-termination-or-change-of-employment/
  - https://hightable.io/how-to-implement-iso-27001-annex-a-6-5
  Read 2026-11-01 through a search. Timing and evidence points are from these summaries.
- Audit checklist guidance for Annex A 6.5: https://www.isms.online/iso-27001/checklist/annex-a-6-5-checklist/. Read 2026-11-01.
- Operating method for records and offboarding checks (evidence labels, a missing record is a process gap and not a finding about
  a person): internal operating notes from a live bot, general method only.
