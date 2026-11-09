# Relation vocabulary

`rel` is one of these sixteen, and nothing else. The server rejects any other word. Read the older words `employs`, `led_by`,
`regulated_by` and `invested_in` as the words below, but write these.

Direction matters. Symmetric relations (`competes_with`, `partners_with`) are stored once and read from either end.

| Relation | Example |
|---|---|
| competes_with | `organization/initech competes_with company/self`, properties.segment = the segment id |
| partners_with | `company/self partners_with organization/example-co` when they actually partner |
| integrates_with | `company/self integrates_with organization/northwind` |
| distributes_through | `company/self distributes_through channel/r-smallbusiness` |
| sells_to | `company/self sells_to segment/independent-shop-owners` |
| operates_in | `organization/example-co operates_in place/united-states` |
| acquired | `organization/hooli acquired organization/example-co` |
| invests_in | `organization/umbrella invests_in organization/initech` |
| works_at | `person/jane-doe works_at organization/example-co`; a leader adds properties.role = leader, as `person/john-roe works_at organization/example-co` |
| founded | `person/jane-doe founded organization/initech` |
| formerly | `organization/initech-labs formerly organization/hooli` when one became the other |
| regulates | `document/local-licensing-rule regulates company/self` |
| subject_of | `organization/example-co subject_of event/2026-lawsuit` |
| member_of | `company/self member_of channel/trade-association` |
| mentioned_in | `organization/example-co mentioned_in channel/r-smallbusiness` |
| located_in | `organization/example-co located_in place/united-states` |

If a real relation keeps being needed and none of these fits, say so in `_librarian/missing.md`. Do not invent one in a write.
