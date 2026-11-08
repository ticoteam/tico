# Relation vocabulary

`rel` is one of these sixteen, and nothing else. The server rejects any other word. Read the older words `employs`, `led_by`,
`regulated_by` and `invested_in` as the words below, but write these.

Direction matters. Symmetric relations (`competes_with`, `partners_with`) are stored once and read from either end.

| Relation | Example |
|---|---|
| competes_with | `company/initech competes_with company/self`, properties.segment = the segment id |
| partners_with | `company/self partners_with company/example-co` when they actually partner |
| integrates_with | `company/self integrates_with company/northwind` |
| distributes_through | `company/self distributes_through channel/r-smallbusiness` |
| sells_to | `company/self sells_to segment/independent-shop-owners` |
| operates_in | `company/example-co operates_in geography/united-states` |
| acquired | `company/hooli acquired company/example-co` |
| invests_in | `company/umbrella invests_in company/initech` |
| works_at | `person/jane-doe works_at company/example-co`; a leader adds properties.role = leader, as `person/john-roe works_at company/example-co` |
| founded | `person/jane-doe founded company/initech` |
| formerly | `company/initech-labs formerly company/hooli` when one became the other |
| regulates | `regulation/local-licensing-rule regulates company/self` |
| subject_of | `company/example-co subject_of event/2026-lawsuit` |
| member_of | `company/self member_of channel/trade-association` |
| mentioned_in | `company/example-co mentioned_in channel/r-smallbusiness` |
| located_in | `company/example-co located_in geography/united-states` |

If a real relation keeps being needed and none of these fits, say so in `_librarian/missing.md`. Do not invent one in a write.
