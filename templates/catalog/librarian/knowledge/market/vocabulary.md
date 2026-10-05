# Relation vocabulary

`rel` is one of these sixteen, and nothing else. The server rejects any other word.

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
| invested_in | `company/umbrella invested_in company/initech` |
| employs | `company/example-co employs person/jane-doe` |
| led_by | `company/example-co led_by person/john-roe` |
| founded | `person/jane-doe founded company/initech` |
| formerly | `company/initech-labs formerly company/hooli` when one became the other |
| regulated_by | `company/self regulated_by regulation/local-licensing-rule` |
| subject_of | `company/example-co subject_of event/2026-lawsuit` |
| member_of | `company/self member_of channel/trade-association` |
| mentioned_in | `company/example-co mentioned_in channel/r-smallbusiness` |

Add a verb by editing the constant in `backend/market.py` and this page in the Librarian template. Do not invent one in a write.
