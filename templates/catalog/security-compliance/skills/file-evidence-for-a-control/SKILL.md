---
name: file-evidence-for-a-control
description: Check one control's evidence against the team's tests (dated inside the period, shows the thing, complete for the population, clean of credentials) and mark each period collected, claimed or missing, with every period in the window covered.
---
# File evidence for a control

When to use: before any control is marked collected on the monthly page, and before answering an auditor's or customer's sample request.

Steps:
1. Read the control's row in `knowledge/controls.md`: its reference, cadence, the evidence it needs and the period it covers. If the owner or cadence is blank, mark the control "no owner or cadence" and stop there.
2. Search the evidence folder with `hub doc search` for the control's name and the period, then read the candidates. Check `knowledge/evidence-log.md` for an entry. A file that is not logged is not evidence yet: log it, or report it as found but not filed.
3. Test each file against `knowledge/evidence-method.md`:
   - dated inside the period, by a date the file shows and not only its name;
   - shows the thing: a visible date and the system name for a screenshot, the export and filter for a count;
   - complete for the population when a sample is involved, with the full population list kept beside it;
   - clean: no credential, password or personal data, with no exception.
4. Check every period in the window. A quarterly control needs one file per quarter. Name each missing quarter as a gap.
5. Mark each period. **Collected**: the file passes step 3 and is logged. **Claimed**: the owner says it is done and no file exists. **Missing**: nothing is found. Never mark collected from a claim.
6. If a file shows a credential or personal data, do not copy it into any page or log. Ask the owner for a replacement file, and record that the original was replaced and why.
7. For an auditor or customer sample request, list the whole population first, then the sample, and state how the sample was picked. Answer from filed evidence only. The reply stays a draft until a person turns sending on.
8. Log one line per period in `knowledge/evidence-log.md`: control, period, date collected, file link, mark.

Checks before you finish:
- Every mark has a file, or a stated reason for claimed or missing.
- Each file's date falls inside its period.
- A sample names its population and its method.
- Nothing from inside a file is copied into the page except the reference and the date.
- Every missing quarter in the window is named.

Example (generic): "CM-02, September. A CSV of the month's merges with reviewer and date, filed 2026-10-02 and logged. Dated inside September and complete for the month. Marked collected. A screenshot in the same folder shows a password field, so it is not used. The owner is asked for a version without it, and the log records the replacement and the reason."
