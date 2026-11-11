# Documentation practice

Reference for the drift report and the drafts. Each figure carries its source and the year it was read. Where a source
was read only as a search excerpt, the line says so.

## Page types

- Diátaxis describes four types: tutorials, how-to guides, reference and explanation. Its site says documentation should be
  organised around the structures of those needs (diataxis.fr, read 2026-11-01). The site gives no one-line definitions.
  Some secondary write-ups describe two axes behind the four types (action or cognition, acquisition or application). The
  axes were not confirmed on the site, so do not attribute them to it.
- Keep one type per page. A how-to that also explains the concept at length is two pages.

## Style

- Google's developer documentation style guide highlights (developers.google.com/style/highlights, updated 2026-10-09,
  read 2026-11-01): use second person ("you" rather than "we"), active voice, and sentence-case headings.
- House rules, not from that page: avoid "simply" and "just", keep sentences under 25 words, and split any over 30.

## Changelogs

- Keep a Changelog 1.1.0 (keepachangelog.com/en/1.1.0/, read 2026-11-01): six types, Added, Changed, Deprecated, Removed,
  Fixed and Security. Changelogs are for humans, not machines. The latest version comes first, and each version shows its
  release date. Dates use ISO 8601. Keep an "Unreleased" section at the top. Mark a withdrawn release "[YANKED]".
- The same types of change are grouped, and each version and section is linkable.

## Drift checks

- Vendor and marketplace pages on documentation drift (undated search excerpts, not verified) describe a severity order:
  broken links and wrong code samples most serious, style issues and missing sections as warnings. Treat it as one team's
  model, not a standard.
- A link that opens is not the same as a link that answers. Check that the page behind the link supports the sentence.
- Most drift is a changed label, path or default. Check those first.

## Open questions

- No authoritative freshness benchmark for product documentation was found in the sources read. The team sets its own.

## Sources

- diataxis.fr, read 2026-11-01 (the four types and the organising principle). The axes come from secondary write-ups and are not confirmed on the site.
- developers.google.com/style/highlights, updated 2026-10-09, read 2026-11-01.
- keepachangelog.com/en/1.1.0/, read 2026-11-01.
- Vendor and marketplace pages on documentation drift, undated search excerpts, not verified.
