---
name: read-a-rating-trend
description: Read an app's rating on each store as a trend, naming which rating (current version or all versions) and how many ratings sit under it, and say up, down or about the same only when the number is larger than its noise.
---
# Read a rating trend

When to use: each weekly app store review, and whenever a rating is said to have dropped, risen or stayed the same.

Steps:
1. For each store, take the rating from the export or the listing page on the day it was pulled. Note the date and the store.
2. Name which rating it is: the current version, or all versions. The two move differently after a release. Use the same one
   each week, and say which one the trend uses.
3. Read the number of ratings under it. Name it beside the rating.
4. Compare with the same rating from last week and from the 28 days before. A change is "down" or "up" only when it is larger
   than the noise for that count of ratings. With few ratings, a small number of 1 or 2 star reviews moves the rating a lot, so
   say that.
5. If the current version changed in the window, name the version and its release date. A drop that starts on the release date
   is a release finding, and it goes to the product owner.
6. Read the reviews in the same window. A rating drop with a named complaint pattern (the same crash, the same charge) is
   explained by that pattern. A drop with no pattern is "no pattern found yet", not a cause.
7. Record the store, the rating, its version basis, the count, the change and the date in `reports/` and `knowledge/reviews.md`.

Checks before you finish:
- Every rating is quoted with its version basis and its count.
- Ratings from the two stores are never averaged together.
- A rating is not called stable from one day's reading.
- The cause is named only when a pattern in the reviews supports it.

Example (invented, for illustration): "App Store, current version 3.8.2, 318 ratings on 2026-11-05: 4.1, down from 4.4 on
2026-10-29 (release date 2026-10-27). Nine reviews in the window name a launch crash. Down, and the crash pattern explains
it. All-versions rating 4.5, unchanged."
