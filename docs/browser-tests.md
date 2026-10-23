# Browser coverage

The browser suite keeps feature paths and client-side safety checks. Server rules also have Python
coverage; a passing API test does not replace a keyboard interaction or DOM security assertion.
`node scripts/ui-tests.cjs --all` runs 48 scripts; the default runner still includes the main pages,
permission and privacy boundaries. Scripts use bounded condition waits and the shared load factor.

The suite cut removes six standalone scripts and repeated presentation walkthroughs. No product,
privacy policy, browser engine or release check changes. The removed coverage is explicit below.

| Removed script | Retained coverage | Coverage intentionally lost |
| --- | --- | --- |
| agent-chips.cjs | team-groups.cjs covers the Team chart and management; connect-agent.cjs covers token creation, first-call detection and revocation | Chip overflow labels, chip-versus-row representation, history display and chart Connect-button placement |
| page-layouts.cjs | docs.cjs, meetings.cjs, goals.cjs, assistant.cjs and bot-tools.cjs cover those feature paths | Cross-route layout-class cleanup, precise alignment, full-height rails, whole-page overflow and the extended tooltip keyboard/wording matrix |
| rails.cjs | tasks-page.cjs keeps saved-view preference persistence | Sidebar drag width, arrow-key/end limits, device/account width restore, double-click reset and hidden phone edge; these rail-specific assertions have no remaining equivalent |
| subscriptions-weekly.cjs | None: named subscriptions left the page with one login per AI tool per computer (#1259), and subscription-identity.cjs with them; backend/tests/test_subscription_refresh.py covers refresh state and access | Pure wording/state rendering matrix and exact JavaScript polling schedule/stop assertion |
| subscriptions-refresh.cjs | None, as above (#1259); backend/tests/test_subscription_refresh.py keeps the server contract | Repeated weekly-copy assertions and phone overflow measurement |
| sweep-fixes.cjs | getting-started.cjs and docs.cjs keep Market paths; bot-tools.cjs, assistant.cjs and meetings.cjs keep their feature paths | Light-theme pixel colors, exact display-name/credential wording, tag-row alignment and filter clipping |

Within retained scripts:

- meetings.cjs uses four scenarios instead of sixteen: setup plus transcript/manual note creation,
  populated-list filtering/opening, member cannot configure an importer, and readable retired-source
  data. Removed duplicate light-theme runs, phone-only geometry/empty-state runs, disconnected-source
  copy walkthrough, repeated historical phone run, exact headings/placeholders/status wording, source
  logos/dots and one-line alignment. Mixed-version retired-importer and permission checks remain.
- bot-tools.cjs keeps desktop and phone navigation, update reads and empty sections, using one theme
  each. Removed two repeated light-theme runs, rail geometry, row heights, logo alignment and contrast.
- connect-agent.cjs keeps desktop and protected-host phone paths in one theme, reducing four paths
  to two. Token containment, revocation, immutable MCP URL and polling-stop checks remain. Removed
  repeated light-theme runs, picker logo/initials matrix and picker/steps width assertions.
- goals.cjs removes row-height/alignment and long-title ellipsis/tooltip checks. Goal/KPI writes,
  confirmations, evidence, escaped live Markdown and read-only controls remain.
- team-icon.cjs and changelog.cjs remove phone overflow assertions; owner controls, retries,
  persisted read state and content escaping remain.
- task-detail-state.cjs removes the exact 390px dialog width; pending drafts, version ordering,
  failed-write preservation and dialog ownership remain.
- usage-count.cjs removes exact notice wording; owner-only notice, persisted dismissal, toggle and
  install-ID reset remain.
- settings-forms.cjs removes two local-sign-in informational text assertions; form drafts, provider
  changes and computer revocation remain, with permission boundaries in people-access.cjs.

People access, task privacy, bot permissions, sign-in redirects, credentials and upgrade/rollback
coverage remain. The runner drops the removed page-layouts name from CORE so its list remains current.
Timing comparisons must use the same provisioned host, record printed jobs/slowdown and load, and
report failures. Do not infer a runtime percentage from script counts or rerun a red receipt until green.
