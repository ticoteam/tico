A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme security report, Mon 2026-09-28

Sample output for Acme, a fictional studio-software team. Every package, advisory number and
human is invented. Nothing was dismissed, merged or changed. First draft, not yet reviewed.

**Headline: 1 known-exploited flaw is reachable on the public booking API (deadline Thu 2026-10-01);
3 more are reachable; 1 test key is committed and needs rotating.**

## Patch first (tier 1, 3-day deadline)
- **api: `imgparse` 2.3.1 to 2.3.4, GHSA-xxxx-0001 (CVE-2026-1101).** On CISA KEV since 2026-09-24.
  Reachable: `uploads/avatar.py:41` passes the studio's uploaded logo straight to `imgparse.open`.
  Patch-level bump, no API change. Owner: Priya (api). One pull request, Dependabot #512 is already open.

## This sprint (tier 2)
| Repo | Package | From, to | Advisory | Why tier 2 | Owner |
|---|---|---|---|---|---|
| web-app | `markdownish` | 4.1.0, 4.1.2 | GHSA-xxxx-0002 | reachable: class notes render user text; EPSS 0.04 | Marco |
| api | `jwtlite` | 1.8.0, 2.0.1 | GHSA-xxxx-0003 | reachable in sign-in; **major bump**, `decode()` signature changes | Priya |
| api | `yamlkit` (via `configloader`) | 5.3, 5.4 | GHSA-xxxx-0004 | reach unknown: loaded dynamically; bump `configloader` to 3.2 | Priya |

## Queue (tier 3): 9 alerts
All dev or test dependencies (bundler, test runner plugins). Proposed: one batched upgrade in the quarterly
cleanup. List in `knowledge/ledger.md`.

## Credentials
- `web-app`, `tests/fixtures/payments.env`, added in commit 3f9c2a1 on 2026-09-22: a payment provider
  **test** secret key. Proposed: the owner rotates it in the provider's dashboard, then removes the file; deleting it alone
  leaves it in history. Owner: Marco. Value not copied here.

## Past deadline
- None. Last week's 2 tier-2 upgrades merged 2026-09-24 (#498, #501).

## Could not read
- The mobile repository is not in this bot's GitHub list, so its dependencies are not covered.
- Dependabot alert list for `api`: not readable with this bot's access, so the alerts above come from open pull requests only. Alerts not checked.

## Sources
- Dependabot pull requests, api and web-app, 2026-09-28; CISA KEV catalogue and FIRST EPSS, read 2026-09-28;
  knowledge/patch-policy.md (agreed 2026-09-15)
```
