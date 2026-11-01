---
name: find-hidden-breaking-changes
description: Find the changes a pull request does not label as breaking but that force an existing user, client, integration or install to change something, by comparing each public interface before and after the change.
---
# Find hidden breaking changes

When to use: for every merged change in a release range before the version is suggested. Labels and titles miss breaking
changes often, so the diff is read for them.

Steps:
1. List the public surfaces the product exposes: API endpoints and their response fields, command-line options, configuration
   keys and environment variables, file formats, webhooks, stored data a user can export, and supported platforms or versions.
2. For each change in the range, compare the surface before and after. Look for these signals:
   - a field, option, endpoint or key removed or renamed;
   - a default changed, so an unchanged setup now behaves differently;
   - stricter validation, so input that was accepted now fails;
   - a changed response shape, sort order, unit, time zone, rounding or error code;
   - a required migration, or a data change that cannot be read back by the old code;
   - a dropped platform, browser, runtime version or integration;
   - a changed file name, path or format that other tools read.
3. Decide with the rule: a change is breaking when an existing user or integration must change something to keep working.
   A new optional field is not breaking. A new required field is.
4. Mark each breaking change at the top of the draft with what the user must do instead. If the pull request does not say,
   and the evidence is only the diff, put it in "Could not classify" with the question for a human.
5. Note the scope: if the change is in a pre-1.0 version, still list it as breaking. Users depend on it whatever the
   version number says.

Checks before you finish:
- Each public surface in the range was compared, or the note says which were not.
- Every breaking change says what the user must do instead.
- No breaking change was guessed into "Changed" without a reason.
- Security-sensitive details stay out of the public text; they wait for a human.

Example (invented, for illustration): "Pull request #433 'Clean up export' is not labelled breaking. The CSV export renames
`studio_id` to `studio`. Existing spreadsheets that match the column will break. Breaking, at the top. What the user does:
rename the column in the import. Question for a human: keep both names for one release?"
