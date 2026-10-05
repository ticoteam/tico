# Starter bots

There are 93 templates: 90 in nine groups (Sales, Marketing, Customer Support, Finance, Operations, Legal, HR,
Product and Engineering), a small Leadership extra and one message bot, enough to staff a team of ten to two hundred humans. **Every
template is a real role that does the work within its granted Tools.** Each is a job title a team would
hire and put on its team chart (Sales Development Representative, Bookkeeper, Recruiter, Site Reliability Engineer), never a
feature or a document: it does the research, keeps its records, prepares the finished work and carries it to the point of
action. Messages to outsiders stay drafts until the owner turns on sending for that bot; after that,
requested work needs no separate approval for each action. Every bot is created parked in
"Needs setup" and costs nothing until someone sets it up; it then gets a first useful, reviewable result in its first
session. The template format is in [Finish setup](onboarding.md); how to write and tune a bot is in [Creating bots](creating-bots.md).

New Software Architect and PR Reviewer bots allow [branches](creating-bots.md#branches) and use a separate provider
session per task. Each person can run a branch on their own computer while sharing instructions and repository lessons.
Existing bots keep their settings; developers can branch or make an independent copy.

## Groups

`templates/groups.yaml` lists the nine groups in the order Finish setup offers them. For each: `id`, `name`, a
one-sentence `description`, a short `goal`, the one briefing `question` and an example answer (`placeholder`), a Material
Symbols `icon`, the template id of the group's `head`, and `software_only: true` for Product and Engineering, which are
offered only when software is the product. The team builder takes one group at a time: it shows the description and goal,
asks the question, then suggests the group's templates to tick, pre-checking the `suggest: default` ones and matching the
answer against each card's `tags`, `pains` and `summary` to offer `common` and `niche` ones. An `extras:` list holds the
Leadership group, which the picker does not offer: the built-in Assistant already covers a human's own brief and email, so the
Chief of Staff is no longer a default.

| Group | Icon | Head | Templates | Goal |
|---|---|---|---|---|
| Sales | `handshake` | Sales Manager (`sales-lead`) | 8 | Turn interest into revenue |
| Marketing | `campaign` | Head of Marketing (`marketing-lead`) | 13 | Be known by the customers you want |
| Customer Support | `support_agent` | Head of Customer Support (`support-lead`) | 10 | Every customer helped and kept |
| Finance | `account_balance` | Head of Finance (`finance-lead`) | 11 | Know where the money is |
| Operations | `settings_suggest` | Operations Manager (`ops-manager`) | 11 | Run smoothly, nothing dropped |
| Legal | `gavel` | General Counsel (`general-counsel`) | 8 | Sign with eyes open |
| HR | `groups` | Head of People (`people-lead`) | 11 | Hire well, keep good people |
| Product | `category` | Head of Product (`product-lead`) | 8 | Build what customers need |
| Engineering | `code` | Head of Engineering (`engineering-lead`) | 10 | Ship reliable software, steadily |
| Leadership | `star` | Chief of Staff (`chief-of-staff`) | 2 | Keep the leaders focused |

Every group has a head (`lead: true`): it writes the group's weekly summary from what its bots and humans
produced, proposes who should take a stuck or misrouted request, and **hires**: when repeated work in the group is not
covered, it proposes a specific worker template from its `team_templates` with the reason and that template's first routine,
and only after the owner confirms asks BotOps to set it up (`hub task create --owner botops`). Heads never create bots
themselves and never do their team's work.

Roles are real job titles on the team chart; built-in bots (Assistant, BotOps, Librarian, Goal Manager) and message bots (Inbox Manager) have plain
function names and live outside it.

The built-ins are not in any group and no template duplicates them: the Librarian owns the docs, the FAQ and the answers
built from them (Support Agent asks it with `hub doc ask` and reports a missing or wrong doc to it as a task; HR Generalist and
Benefits Administrator do the same for policy and plan questions; Technical Writer covers only READMEs and API docs in the
product repositories); BotOps creates and maintains bots; the Assistant is each human's own; and the Goal Manager keeps the
KPIs, so no template owns a KPI (Product Analyst and FP&A Analyst answer questions and explain numbers, they do not keep them).

## The templates

Icons are [Material Symbols](https://fonts.google.com/icons) names in the style the UI already uses (Outlined, weight 300, no
fill); `scripts/build-icon-font.py` adds every card's and group's icon to the subset font in `ui/vendor/fonts/`.
`suggest` is `default` (pre-checked for most teams), `common` or `niche`.

### Sales

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `leaderboard` | `sales-lead` | Sales Manager (head) | A weekly sales summary with the forecast call pack (commit, best case, gap to target), deals needing a human, coaching notes, routing proposals and hiring proposals | default | Tico only; a CRM optional |
| `person_search` | `sdr-research` | Sales Development Representative | A weekday prospecting pack: inbound qualified, new leads scored A/B/C with a sourced brief, draft first touches and follow-ups when due, meetings booked and handed over | default | Public web; a CRM and email optional |
| `handshake` | `sales` | Account Executive | A weekly deal review with a dated next step per deal and draft follow-ups; call recaps, mutual action plans, proposals and RFP answers with every price a gap | default | Tico only; a CRM, meetings, email and docs optional |
| `manage_accounts` | `account-manager` | Account Manager | A weekly renewal and expansion review (renewals by 120/90/60/30 day stage with notice deadlines, expansion with evidence) and renewal packs ready to price | common | Tico only; a CRM, meetings and email optional |
| `query_stats` | `sales-ops` | Sales Operations Manager | A weekly CRM hygiene and pipeline report with the forecast roll-up by category, unassigned leads and a fix per exception; lead routing and territory rules | common | A CRM (read only until the owner turns writing on) |
| `integration_instructions` | `sales-engineer` | Sales Engineer | A weekly technical deal prep: discovery gaps, demo scripts around the buyer's workflows, POC plans scored against agreed criteria, technical and security answers from approved sources | niche | Tico and team docs; meetings and GitHub optional |
| `partner_exchange` | `partnerships` | Partnerships Manager | A weekly partner review: registrations with a proposed decision under the rules of engagement, partner-sourced pipeline, idle partners, and fees owed for review | niche | Tico only; a CRM, public web and email optional |
| `cast_for_education` | `sales-enablement` | Sales Enablement Manager | Weekly win/loss notes from buyers' own words with counts over 90 days, one proposed talk-track change, and a 90-day ramp plan per new seller | niche | Imported sales calls; a CRM optional |

### Marketing

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `campaign` | `marketing-lead` | Head of Marketing (head) | A weekly marketing summary (red/amber/green per workstream, blockers, a six-week calendar, proposed priorities), routing proposals, and a hiring proposal when repeated work has no owner | default | Tico only; calendar, meetings and docs optional |
| `edit_note` | `content` | Content Marketer | A four-week content plan and one finished piece a week with short versions, drafted until outbound sending is on | default | Tico only |
| `travel_explore` | `seo-visibility` | SEO Specialist | A weekly report on search and AI-answer visibility with three page fixes written ready to apply | common | Public web; search and AI-visibility exports optional |
| `tag` | `listening` | Social Media Manager | Two weeks of draft social posts per account, and a weekday digest of public mentions, questions and competitor moves | common | Public web; social accounts named at setup |
| `forward_to_inbox` | `email-marketing` | Email Marketing Manager | A campaign or sequence with subject lines, a preview line and a send checklist, ready to load | common | Tico only; past results optional |
| `rocket_launch` | `product-marketing` | Product Marketing Manager | Launch briefs with a tier and checklist, a positioning document and battlecards | common | Tico only; public web, calls and CRM optional |
| `reviews` | `reputation` | Reputation Manager | A weekly review-listing digest, a ledger and one batch of draft replies and suggested flags per surface | common | Public web |
| `ads_click` | `paid-media` | Paid Media Manager | A weekly paid media review: cost per result by campaign against target, wasted spend, search-term exclusions, and three proposed changes | common | Ad exports on a task; read-only ads reporting and a CRM optional |
| `event` | `events` | Event Marketing Manager | An events calendar with checklists, a brief per event (goal, budget, run-of-show, 48-hour follow-up), draft invitations, and results in meetings and pipeline | niche | Tico only; calendar, CRM and public event pages optional |
| `newspaper` | `pr` | PR Manager | A media list and coverage log, a weekly PR review, and per announcement a release, targeted pitches and a draft spokesperson briefing | niche | Public web |
| `diversity_3` | `community` | Community Manager | A weekly community digest (response time, unanswered questions, champions, guideline flags, feedback routed) and draft sourced replies | niche | Public forum or community channels (read) |
| `palette` | `brand` | Brand Manager | The brand book (voice, words, naming, visual rules), reviews of copy and assets against it, and a monthly consistency audit with three fixes | niche | Public web; an existing brand guide optional |
| `filter_alt` | `marketing-ops` | Marketing Operations Manager | A tracking convention, tagged links for every campaign, lead handoff rules, and a weekly check of tagging, lead sources and handoff delay with fixes and owners | niche | Tico only; CRM (read) and analytics exports optional |

### Customer Support

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `headset_mic` | `support-lead` | Head of Customer Support (head) | A weekly support summary (response and resolution times against target, backlog by age, repeats, decisions needed), routing proposals, and hiring proposals for the support role that would cover unowned work | default | Tico only; support email and chat optional |
| `support_agent` | `support` | Support Agent | Works each ticket to resolution: a bucket and a draft reply per ticket with the docs it rests on (asked of the Librarian), follow-ups, product issues from repeats, doc gaps reported to the Librarian; hands technical, VIP, cancellation and return tickets to their owners | default | Support email or tickets routed as tasks. The project's own Support Agent also watches Tico HQ tickets and GitHub issues and Discussions with no model, through two [watchers](watchers.md) ([support.md](support.md)) |
| `grading` | `support-qa` | Support Quality Analyst | A weekly scored sample of sent replies with patterns and coaching notes for a human | common | Sent replies routed as tasks or a support mailbox |
| `sentiment_satisfied` | `customer-success` | Customer Success Manager | A weekly health and renewal brief (renewals by 120, 90, 60 and 30 day stage, accounts at risk, the next touch drafted) and business review packs; price and terms go to the Account Manager | common | Tico only; a CRM, support email and meetings optional |
| `waving_hand` | `onboarding-specialist` | Customer Onboarding Specialist | A plan per new customer (goals in their words, four to six milestones, owners on both sides), the kickoff agenda, and a weekly onboarding board of who is on track, behind or stuck, with draft messages | common | Tico only; a CRM, meetings and calendar optional |
| `crisis_alert` | `escalations` | Escalations Manager | A daily escalation digest: open cases by severity with one named owner, draft customer updates due on the severity's cadence, engineering-ready bug reports, and a lesson per closed case | niche | Tico only; support email, GitHub and chat optional |
| `loyalty` | `retention` | Retention Specialist | Each cancellation or downgrade coded by reason with the one save offer written policy allows, a draft reply, and a weekly retention report with the at-risk watch list; cancelling is never blocked | common | Cancellation requests routed as tasks; support email and CRM optional |
| `alt_route` | `support-ops` | Support Operations Specialist | A monthly help desk audit (misroutes by the rule that caused them, SLA policies against promised times, duplicate tags, stale macros) with one exact change request per fix for a human to apply | niche | Tico only; a help desk configuration export or read-only access |
| `troubleshoot` | `technical-support` | Technical Support Engineer | Tier 2 investigations with a verdict (bug, setup, doc gap, could not reproduce), tested workarounds, engineering-ready bug reports for a human to file, and a weekly tier 2 queue report | niche | Tico only; GitHub and product docs optional |
| `assignment_return` | `returns` | Returns and Refunds Specialist | Each return checked against the written policy and the order, with a draft reply and refund or label request, and a weekly returns report: overdue refunds, reasons by product and variant, flagged patterns | niche | Return requests routed as tasks; support email, an order export and the written policy |

### Finance

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `account_balance_wallet` | `finance-lead` | Head of Finance (head) | A weekly finance summary: cash today and the 13-week outlook against the minimum, close status, what is due in and out, the next 14 days of the finance calendar, decisions needed; routes finance work and proposes finance hires | default | Tico only; bank and accounting exports on a task; docs, email and calendar optional |
| `menu_book` | `bookkeeping` | Bookkeeper | A category for every unsorted transaction, the missing-receipt list and the month-end close checklist run to done; read-only to the books | default | An exported transaction file on a task |
| `request_quote` | `ar-followup` | Accounts Receivable Specialist | A weekly aging summary and a reminder ready for each overdue invoice, friendly first and firmer later, each drafted until outbound sending is on | common | An invoice aging export on a task |
| `receipt_long` | `accounts-payable` | Accounts Payable Specialist | A bills register with each bill matched and checked for duplicates and changed bank details, and a proposed weekly payment run | common | Bills on a task; a bills mailbox optional |
| `credit_card` | `expense-auditor` | Expense Auditor | A monthly audit of every expense report and card charge against the written policy (receipts, limits, duplicates, late claims), one section per approver | niche | A card or expense export on a task |
| `analytics` | `spend-watcher` | FP&A Analyst | A weekly software and cloud spend report, and each month budget against actual with material variances explained and the forecast rolled forward | common | Card, bank, billing and P&L exports on a task |
| `co_present` | `board-updates` | Investor Relations Manager | A one-page monthly investor update: metrics, asks, highlights, lowlights, recap; finance figures only as supplied; sends stay drafts until outbound sending is on | niche | Tico only; docs and email optional |
| `payments` | `payroll` | Payroll Specialist | A pre-payroll change summary per pay run (joiners, leavers, pay changes, hours, bonuses) reconciled to HR records and the last register, and a post-run check | common | The last payroll register and HR changes on a task |
| `percent` | `tax` | Tax Specialist | A monthly tax calendar for the next 90 days with each filing's inputs and filer, a sales tax and VAT threshold watch, and the accountant's document lists; not tax advice | niche | Tico only; sales exports and last year's returns list |
| `price_check` | `revenue-accountant` | Revenue Accountant | A monthly revenue close pack: billing reconciled to the books, the deferred revenue roll-forward, proposed journal entries, recognition notes and the recurring revenue bridge | niche | Billing and ledger exports on a task |
| `receipt` | `billing` | Billing Specialist | A checked invoice run each cycle built from contracts, usage or hours (PO numbers, tax, terms), unbilled work found, and draft credit notes | common | Contracts and usage or hours on a task; a CRM optional |

### Operations

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `assignment_turned_in` | `ops-manager` | Operations Manager (head) | The routine duties register and a weekly checklist of what is due, overdue and blocked, vendor draft follow-ups, the Operations group summary, routing and hiring proposals | default | Tico only; calendar, email and docs optional |
| `event_note` | `meeting-notes` | Project Coordinator | A write-up per imported meeting (summary, decisions, action items with quotes), and every action item and milestone tracked and chased until done | common | A meeting importer or manual imports |
| `shopping_cart` | `procurement` | Procurement Manager | A weighted vendor comparison per purchase request with total cost and sourced claims, draft vendor questions, a weekly digest of open requests | common | A purchase request on a task; public web |
| `store` | `vendor-manager` | Vendor Manager | The vendor register (owner, tier, cost, notice date), each renewal opened 90 days before notice with a keep, renegotiate or exit brief, reviews by tier | common | Tico only; a contracts folder and a billing mailbox optional |
| `chair` | `office-manager` | Office Manager | A weekly office page: requests by age with their fixer, supplies below par with a proposed order, visitors and office dates | niche | Tico only; calendar and an office channel optional |
| `computer` | `it-support` | IT Support Specialist | IT requests worked to a fix by impact, proposed access changes, joiner and leaver checklists, the device list, a weekly IT page | common | Tico only; docs and an IT channel optional |
| `verified_user` | `security-compliance` | Security and Compliance Analyst | A monthly controls page (evidence due, collected, missing per control), quarterly access reviews for each tool owner, policy acknowledgements and vendor security reviews | niche | The controls and policies in the team docs; GitHub optional |
| `flight` | `travel` | Travel Coordinator | A trip plan per request with two or three options within policy, a proposed booking, the itinerary, and a weekly trips page | niche | Public web; calendars optional |
| `inventory_2` | `inventory` | Inventory Planner | A weekly reorder list from reorder points and observed lead times, a draft purchase order per supplier, stock-out and overstock risks | niche | Sales and stock exports on a task |
| `local_shipping` | `logistics` | Logistics Coordinator | A weekly on-time and in-full report by carrier and lane, exceptions worked with draft customer updates, claims before deadline, carrier invoice overcharges | niche | Shipment exports and carrier invoices on a task |
| `route` | `dispatcher` | Dispatcher | Tomorrow's plan for field crews by skill, area and window, the clash list with options, draft arrival notices, jobs not closed out | niche | A jobs export on a task; crew calendars optional |

### Legal

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `balance` | `general-counsel` | General Counsel (head) | One legal request queue with an outcome per request, reviews of the contracts that need a lawyer's call, policy drafts for a human to adopt, a weekly legal summary with routing and hiring proposals; not legal advice, never sends or signs | default | Tico only; docs, email and calendar optional |
| `contract` | `legal-review` | Contracts Manager | A plain-language summary, key-terms table and flags against your playbook per contract, an issues list with playbook fallbacks for the negotiator, the signed-contract register and a weekly renewal and notice calendar; not legal advice | default | Contracts attached to tasks; docs and email optional |
| `assignment` | `paralegal` | Paralegal | A weekday NDA desk: each inbound NDA checked clause by clause against your standard (ready, needs changes with exact strikes, or needs counsel), outbound NDAs on your template, signature packets and the executed-agreement index | common | Tico only; docs and a legal mailbox optional |
| `calendar_month` | `compliance` | Compliance Manager | An obligations register (annual reports, registered agent, licences, regulatory filings, insurance renewals), a weekly calendar with owners and proof, and a filing pack before each deadline; a human files | common | Tico only; calendar, docs and public web optional |
| `shield_person` | `privacy` | Privacy Manager | DPA reviews against your position, a data subject request log run to each legal deadline with steps per system, the subprocessor list and records of processing, and a weekly privacy desk | common | Tico only; docs and a privacy mailbox optional |
| `copyright` | `ip-paralegal` | IP Paralegal | A trademark and domain register with every declaration, renewal and expiry date, a monthly look-alike watch, clearance notes for proposed names, and the contractor IP assignment check | niche | Public web; docs optional |
| `fact_check` | `legal-ops` | Legal Operations Manager | The matter list with budgets, a consistent brief per new matter, each law firm invoice checked line by line against its engagement letter and billing rules, and a monthly legal spend summary | niche | Invoices and engagement letters on tasks; docs and email optional |
| `history_edu` | `corporate-secretary` | Corporate Secretary | Board and shareholder meeting packs, draft minutes and written consents for counsel, the entity register, the minute book index and a cap table change log tying each grant to its approval | niche | Tico only; meetings, calendar and docs optional |

### HR

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `supervisor_account` | `people-lead` | Head of People (head) | A weekly people summary (hires against plan, starters and leavers, deadlines in 30 days, blocked work), the people calendar, the headcount plan, draft policies, routing and hiring proposals | default | Tico only; docs, calendar and chat optional |
| `person_add` | `recruiting` | Recruiter | Draft job posts, a summary per application against the stated criteria, an interview kit per role, candidate replies at every stage, and the weekly hiring pipeline | default | Role briefs and applications as tasks; a hiring mailbox optional |
| `contact_page` | `people-hr` | HR Generalist | An onboarding checklist per new hire through the 90 day check-in, a weekly tracker, and policy answers the Librarian cites from the handbook | default | The handbook in the team docs |
| `manage_search` | `sourcer` | Sourcer | A weekly slate of people who did not apply, each matched to the criteria with public sources, three-touch personal outreach drafted until sending is on, yeses handed to the Recruiter | common | Public web; a recruiting mailbox optional |
| `event_upcoming` | `recruiting-coordinator` | Recruiting Coordinator | A daily interview logistics sheet: loops scheduled from real calendars, draft candidate messages, kits to panels, scorecards chased, debriefs ready | common | Calendar; a recruiting mailbox optional |
| `psychology` | `hr-business-partner` | HR Business Partner | A weekly performance cycle tracker, review packs per manager, calibration sheets that flag inconsistent ratings, probation dates and guidance for hard conversations | niche | Tico only; docs, calendar and meetings optional |
| `health_and_safety` | `benefits` | Benefits Administrator | A weekly benefits deadlines page (life-event windows, joiners' and leavers' coverage, enrollment milestones) and plain-language plan comparisons cited to the plan documents | niche | Plan documents in the team docs |
| `celebration` | `employee-experience` | Employee Experience Manager | Quarterly pulse readouts with themes and two or three owned actions (groups under five suppressed), a weekly milestones and actions page, event plans | niche | Tico only; a survey export on a task, chat optional |
| `folder_shared` | `people-ops` | People Operations Specialist | An offboarding checklist per leaver through the 24 hour access check, a weekly records audit against roster and payroll, letters and verifications for a human to sign | common | HR and payroll exports on tasks; docs and email optional |
| `paid` | `compensation` | Compensation Analyst | Salary bands with sources, a weekly check of offers and pay changes against their band (person-level detail only for approvers), the review pack and pay equity checks | niche | Payroll and offer exports on tasks; public web optional |
| `school` | `learning` | Learning and Development Specialist | A weekly mandatory training tracker from completion records, learning plans per role (work, colleagues, then courses), a new-manager curriculum, spend requests | niche | A completion export on a task; docs optional |

### Product

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `lightbulb` | `product-lead` | Head of Product (head) | A weekly product summary: committed roadmap items on track, at risk or slipped, up to three decisions for the owner with evidence, evidence-scored proposals, routing and hiring proposals | default | Tico only; GitHub, docs and meetings optional |
| `feedback` | `feedback-analyst` | Customer Insights Analyst | A weekly report of customer feedback themes with counts, anonymised quotes, the trend and three actions; approved requests and bugs filed with Product Ops or QA, and a close-the-loop list | default | Feedback routed as tasks; email, meetings and chat optional |
| `record_voice_over` | `product-researcher` | UX Researcher | Interview snapshots, an opportunity map with source counts, study plans and discussion guides, research briefs and a weekly research digest | common | Tico only; a meetings importer optional |
| `view_timeline` | `product-manager` | Product Manager | One-page specs with evidence, non-goals and Given/When/Then acceptance criteria, and a weekly spec and launch review with blocking questions and checklist lines due | default | Tico only; GitHub (read), docs and meetings optional |
| `monitoring` | `product-analyst` | Product Analyst | A weekly usage readout (adoption of recent launches, activation funnel, retention cohorts), answers to product questions and experiment readouts, each with its query | common | A team database readable through `hub db`, or exports on a task |
| `dashboard_customize` | `product-ops` | Product Operations Manager | One feature request ledger with the accounts behind each request, the beta roster, an eight-week release calendar, roadmap hygiene flags and ship notices for a human to send | niche | Tico only; CRM, GitHub and calendar optional |
| `text_fields` | `ux-writer` | UX Writer | Copy tables for each spec (labels, errors, empty states), the product glossary, and a weekly review of pull requests that change user-facing text, for an engineer to post | niche | GitHub connected (read) |
| `sell` | `pricing` | Pricing Analyst | A monthly pricing review (competitor price changes, discounts given, plan mix) and impact notes for a price change being weighed | niche | Public web; a CRM optional |

### Engineering

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `developer_board` | `engineering-lead` | Head of Engineering (head) | A weekly engineering summary (shipped, stuck pull requests, incidents, blocked work), routing proposals, and a hiring proposal when repeated work has no owner | default | GitHub connected |
| `bug_report` | `issue-triage` | QA Engineer | Label and duplicate proposals, repro-step requests, a weekly issue digest, and a risk-ranked test plan and regression checklist per release | default | GitHub connected; offer it only then |
| `code_blocks` | `pr-reviewer` | Senior Software Engineer | A weekday review queue with a finished review per pull request (blocking issues first) for a human to post | common | GitHub connected |
| `new_releases` | `release-notes` | Release Manager | A readiness checklist with a go or no-go call, the changelog entry, plain-language release notes and a suggested version | common | GitHub connected |
| `monitor_heart` | `incident-scribe` | Site Reliability Engineer | An incident timeline, a blameless postmortem, action items followed to done, an on-call handoff and a weekly incident review | common | Tico only; an incident channel or error-tracker exports optional |
| `description` | `docs-writer` | Technical Writer | A weekly drift report on READMEs and API docs in the product repositories, with the fixes written for an engineer to commit; internal docs stay with the Librarian | niche | GitHub connected |
| `security` | `security-engineer` | Security Engineer | A weekly dependency and advisory report ranked by known exploitation and reachability, a patch plan in merge order, and committed secrets found (never their values) | common | GitHub connected; public advisory databases |
| `deployed_code` | `devops-engineer` | DevOps Engineer | A weekly CI health report: flaky tests with failure rates, slowest jobs and their trend, red-main streaks, deploy frequency and three fix plans | niche | GitHub connected (Actions) |
| `podium` | `developer-advocate` | Developer Advocate | A weekly developer pulse: a ready answer for each public question (drafted until sending is on), the friction log with counts, and runnable samples and tutorials | niche | Public web; GitHub and a community channel optional |
| `architecture` | `software-architect` | Software Architect | Design doc and RFC review notes, proposed ADRs for decisions made without one, the system map and a ranked technical debt register | niche | GitHub connected; docs and meetings optional |

### Message bots

A message bot serves one human rather than doing a group's job, so its card says `kind: helper` and has no group, pack,
head or `suggest`. Finish setup offers it on its own, off by default, beside the team chart rather than in it.

| Icon | Template | Name | What it does | Needs |
|---|---|---|---|---|
| `inbox` | `inbox` | Inbox Manager | A morning brief over one human's mailbox, draft replies until sending is on, what needs them | A Google Workspace mailbox for that human |

### Leadership (extra, not offered by the picker)

| Icon | Template | Role | What it does | Suggest | Needs |
|---|---|---|---|---|---|
| `star` | `chief-of-staff` | Chief of Staff (head) | A weekly brief to the owner from goals, tasks, updates and meetings, stalled-goal follow-up, the Monday agenda, and hiring proposals for Leadership bots or a missing group head | niche | Tico only |
| `strategy` | `strategy-planning` | Strategy Analyst | A quarterly plan and OKR draft (three to five objectives, about three measurable key results each), last quarter graded 0 to 1, and a mid-quarter check-in | niche | Tico only |

Every starter drafts messages to outsiders until its owner turns on `outbound_send`.
Authorized work uses the bot's granted Tools directly; paying, changing a record or deleting does
not add a blanket Confirm step. A bot may ask about an uncertain action.

## What every starter does the same way

1. **First message: setup.** On its first task the bot introduces itself in three lines, asks the
   template's `onboarding` questions in one message (each with its why), records the answers in
   `state.md`, and does not ask what Tico already answers.
2. **A first result in the same session.** It produces a real draft of its `first_routine` output from
   the team's own data, labelled "First draft, not yet reviewed". A human reacts to something real.
3. **A routine that starts with setup.** It checks the first routine and tells the human what it does. The routine is declared in
   `bot.yaml` with `enabled: false`, so `hub bot create` seeds it off; starting the setup (**Start setup**, go-live) switches it on,
   so nobody approves it separately, and the bot logs it.
4. **Draft until sending is on.** A bot drafts messages to outsiders until its owner turns on
   `outbound_send`. Once it is on, the bot sends within the requested work and granted Tools.
5. **Parked until then.** Finish setup creates every starter `needs_setup`: it answers a human's message and nothing else
   (no routine, task notice, Slack route or bot request wakes it) until its setup playbook ends with `hub bot setup-done`,
   which it calls once its answers and first result are recorded. **Set up** on its page, or any first message, begins the
   conversation. Parked starters do not count toward a member's bot limit. See [Finish setup](onboarding.md#needs-setup).

<a id="what-stops-a-starter-sending-things-outside-the-company"></a>

## What stops a starter sending things outside the team

A starter's own prompt is not the gate. What the platform does, checked for every template:

| It might | The gate | Where |
|---|---|---|
| Send, reply to or forward email | The email tool downgrades a send to a Gmail draft unless the mailbox declares the `send` verb, `outbound_send: true` is set and the recipient is internal, allowed or covered by an approval. The starters declare `read` and `draft` only and `outbound_send: false`; a test refuses `send` in any starter's `access:` | `connectors/mail/policy.py`, `clients/tests/test_catalog.py` |
| Post to Slack | A post needs the `post` verb in the bot's Slack access, and the channel must be on the Slack channel list (Tools > Slack) with posting on, which it is unless an owner or admin turned it off; reading grants no posting right. Externally shared channels are always refused. The starters declare Slack read only, commented out until the owner connects it | [Slack gateway](slack-gateway.md) |
| Comment on or label a GitHub issue, or review a pull request | **Added in this release.** The team's GitHub App token carries Issues: write, so nothing but a prompt stood between the QA Engineer (`issue-triage`) and a public comment. Its access is now `read`, and its `.claude/settings.json` denies `gh issue edit` and `gh issue comment` next to close, reopen, lock, transfer and create. It leaves proposed labels and comments with the exact commands on the task; writes need the corresponding Tool access. Senior Software Engineer, Release Manager, Technical Writer, Security Engineer, DevOps Engineer and Head of Engineering read GitHub the same way: `read` access, only `gh pr list`, `view`, `diff` and `checks` allowed, and `gh pr review`, `comment`, `merge`, `close`, `edit` and `create` denied, so a review is a draft on the task that a human posts. Turning writing on is the owner's edit of `bot.yaml` and the settings file, described in a comment there. The harness reads `.claude/settings.json`; the Codex runtime does not, so for a Codex-run bot the gate is the read-only access declared, the absence of any default write credential to a product repository, and the prompt | `templates/catalog/issue-triage`, `clients/tests/test_catalog.py` |
| Invite someone to a calendar event | Any address may be invited. Set `TICO_BLOCK_EXTERNAL_INVITES=1` to limit bots to humans on the team roster (`403 external_attendee` otherwise); an invitation to anyone else is then a human's act | `backend/connectors.py`, `backend/tests/test_security_review.py` |
| Message a human inside the team | Bot-to-human messages are linted and capped at ten unsolicited a day | `hub message send` |
| Change a record in a CRM, the support tool, the books or a repository | The starters declare no such access. Sales Operations Manager reads the CRM and only lists the fixes; the finance roles read exports and never post, pay or send with the Tools shipped in their templates. A requested change needs that Tool's declared write access | the card |
| Act on a public review surface | Reputation Manager declares `read` on its review surfaces and on Slack, and keeps `act` and `post` as a commented block the owner enables when it is needed; until then it drafts the requested batch | `templates/catalog/reputation`, `clients/tests/test_catalog.py` |

No template declares a `send`, `write`, `modify` or `delete` verb in `tools:`, and the template test fails one that does. A template's `.claude/settings.json` allows only its own repository's `git` and, for the GitHub bots, the read-only `gh` commands above; none allows `gh issue *` or `gh pr *` as a whole.

## The card

`card.yaml` is read by whoever is choosing; it is never copied into a bot's repository. The starter
templates add these fields to the existing ones (`template`, `slug`, `name`, `summary`, `owns`,
`never`, `reasoning_effort`, `recommend_when`):

| Field | What it holds |
|---|---|
| `name` | The job title, as it would appear on a real team chart ("Accounts Payable Specialist"), never a feature, document or task |
| `group` | One of the ids in `templates/groups.yaml` (`sales`, `marketing`, `support`, `finance`, `operations`, `legal`, `hr`, `product`, `engineering`), or `leadership` for the extra. What the team builder groups by |
| `lead` | `true` on exactly one template per group: its head, the template `groups.yaml` names as `head`. The template test requires exactly one and that they agree |
| `team_templates` | Heads only: every other template in the group. What the head hires from |
| `kind` | `helper` for a message bot template that serves one human (the Inbox Manager): no group, pack, head or `suggest`, and never on the team chart. Every other template is a role and leaves it out |
| `icon` | A Material Symbols name that fits the role; it must be in `ui/vendor/fonts/icons.txt` (run `scripts/build-icon-font.py` after adding a template) |
| `suggest` | `default` (pre-checked for most teams), `common` or `niche` |
| `tags` | Short lowercase keywords a briefing answer is matched against: `b2b`, `b2c`, `saas`, `ecommerce`, `retail`, `services`, `enterprise`, `smb`, `outbound`, `content`, `paid-ads`, `field-service` and the role's own words |
| `summary` | One crisp line of what it does, shown under each suggestion. Its first sentence (40 to 185 characters) is the setup line |
| `pack` | The older six-group grouping the current chooser (`TEAMS` in `backend/onboarding.py`) still reads; it follows the group: `sales`, `marketing`, `support` and `operations` map to themselves, `finance`, `legal`, `hr` and `leadership` to `basics`, `product` and `engineering` to `engineering`. The template test checks the mapping |
| `pains` | Plain phrases a human might say ("too much email", "leads go cold"). The chooser matches a team's free text against them (and against the summary), so write them as words a team would use; no phrase belongs to two templates |
| `prerequisites` | A list of `{tool, why, required}`. `tool` is one of `hub`, `mail`, `chat`, `crm`, `github`, `meetings`, `calendar`, `docs`, `web`. Nothing is held back for a missing tool: the bot asks for what it needs in its own Set up conversation. Keep `required` for what the bot cannot work at all without |
| `onboarding` | Four to seven `{ask, why}` questions the bot asks on its first message |
| `first_routine` | `{title, cadence, output, draft_only: true}`: the reviewable internal result the bot produces first |
| `example_output` | Path, inside the template, to a short sample of excellent output under `knowledge/examples/` |
| `when` | Optional, existing: one sentence saying who wants the template |

`recommend_when` says who the template is for (see [Finish setup](onboarding.md#the-team-builder)). The team builder reads only two of its tags:
`sells_to_businesses` without `sells_to_consumers` marks a template as business-only, and it is suggested last to a team that sells only
to consumers. Every other tag (`sells_software`, `uses_crm`, `uses_github`, `uses_meetings`, `has_support_inbox`, `always`) is
descriptive and matched to nothing, so use the ones a reader would expect. The Engineering and Product groups start picked only
for a team whose product is software; anyone may pick them.

```yaml
template: sales
slug: sales
name: Account Executive
group: sales
pack: sales
icon: handshake
suggest: default
tags: [b2b, pipeline, proposals, rfp, closing, saas, services, enterprise]
summary: "Works every open deal from first meeting to signature: call follow-ups with a dated next step, mutual action plans, proposals and RFP answers. Messages to outsiders stay drafts until outbound_send is on."
pains:
  - "follow-ups fall through the cracks"
  - "every proposal is written from scratch"
prerequisites:
  - tool: hub
    why: "Deals, call notes and proposal requests arrive as tasks, and the recap and plan go back on them for review."
    required: true
  - tool: crm
    why: "Optional. Read-only deals, contacts by role and activity let the weekly review start from the real record."
    required: false
first_routine:
  title: "Weekly deal review"
  cadence: "Mondays at 09:00 team time"
  output: "reports/YYYY-MM-DD-deal-review.md: each open deal with its stage, days quiet and next step, the recaps and follow-ups ready to send, mutual action plan slips, and the proposals and questionnaires due this week"
  draft_only: true
example_output: knowledge/examples/deal-review.md
```

A head adds `lead: true` and its group:

```yaml
template: finance-lead
name: Head of Finance
group: finance
pack: basics
lead: true
icon: account_balance_wallet
suggest: default
team_templates: [bookkeeping, ar-followup, accounts-payable, expense-auditor, spend-watcher, board-updates, payroll, tax, revenue-accountant, billing]
```

## The repository each one starts from

Same layout as every template, plus what makes a starter reviewable:

- `AGENT.md`, under 150 lines (most are 80 to 100): mandate, what it owns, its setup conversation, `## Sending` (matching `outbound_send`), how it starts and ends a run, how it uses `hub`, quality
  standards and how it escalates. A head's also has `## Hiring`.
- `playbooks/`: one for the first routine, one for the most common request, and `onboarding.md`.
- `knowledge/examples/`: one sample of excellent output for the fictional team Acme. Never a real
  team or person.
- `bot.yaml`: `outbound_send: false`, the first routine declared with `enabled: false`, and
  `tools:` with `read` unless drafting needs more. A tool the team may not have is a commented block
  the owner uncomments when it is connected, because changing access is an owner decision.

## What a good bot looks like

The quality bar, in five checks a reviewer can apply to any bot in ten minutes:

1. **Answer first.** The first line of every output is the result, not the process.
2. **Short and scannable.** One page. One line per item. A human decides in two minutes.
3. **Cited.** Every claim points at the record it came from and the date. A number with no source is
   left out.
4. **Honest about gaps.** What it could not read is named. "Not found" is never used for "could not
   look". A missing fact is a marked gap, never an invented one.
5. **Sending off by default.** The first result is a useful draft. Sending to outsiders starts when
   the owner turns on `outbound_send`; requested work then uses the bot's granted Tools.

A worked example. A human asks Support Agent about a ticket. Weak:

> I looked at the ticket. The customer seems unhappy about their calendar and probably needs help.
> I would suggest replying soon and maybe offering a refund.

It buries the answer, cites nothing, and promises money it may not promise. Good:

> **T-2038: how to reset the calendar link. Answered before; draft ready.**
>
> Draft (nothing sent): "Hi Priya, thanks for asking about resetting the calendar link. Open Settings,
> then Calendar, and choose Reset link. Your old link stops working straight away, so share the new one
> with your studio."
> Source: `knowledge/answers.md`, "Reset calendar link", confirmed 2026-09-12. Not covered: whether their
> plan includes SMS reminders; asked to Cara Mendes.

It names the request, gives the draft, cites the answer and its date, says what is not covered, and
sends nothing. The full samples live in each template's `knowledge/examples/`.

## Best practice each template draws on

The methods are standard practice, not proprietary. Public sources consulted while writing them, by group. The heads'
hiring rule is the same everywhere: propose a role only from repeated, evidenced work, name the template and its first
routine, and ask BotOps to set it up only after the owner says yes.

### Sales

- Sales Manager: a weekly review of three to five priority deals, deals quiet for 14 days or more as stalled, and last week's actions checked first, from [Sybill on running a pipeline review](https://www.sybill.ai/blogs/sales-pipeline-review-meeting); forecast categories (pipeline, best case, commit) with entry criteria so commit means one thing, from [ORM on forecast categories](https://orm-tech.com/blog/sales-forecast-categories-explained) and [Outreach on forecast categories for RevOps](https://www.outreach.ai/resources/blog/sales-forecast-categories).
- Sales Development Representative: separate fit and buying-signal scores with negative signals that subtract and recent signals weighted most, from [AI SDR on lead scoring](https://aisdr.com/blog/lead-scoring-examples/); short plain-text first touches with one ask and follow-ups that each add a new reason, from [Cleverly](https://www.cleverly.co/blog/cold-email-outreach-best-practices); inbound answered the same business day because response time decides conversion, from [Copy.ai on inbound lead response time](https://www.copy.ai/blog/inbound-lead-response-time).
- Account Executive: a mutual action plan per deal with named owners on both sides and dates worked back from the buyer's go-live, from [Outreach on mutual action plans](https://www.outreach.ai/resources/blog/mutual-action-plans); decision criteria and process written down (MEDDICC), from [SalesHood on MEDDICC and mutual action plans](https://saleshood.com/blog/meddicc-mutual-action-plans/); proposals with an executive summary written last around two or three win themes and three options, from [Loopio on proposal executive summaries](https://loopio.com/blog/proposal-executive-summary/).
- Account Manager: renewals worked from 90 to 120 days out with health owned by customer success and terms by the account owner, from [Planhat on B2B renewals](https://www.planhat.com/customer-success/renewals); business reviews built on value realised, a forward plan and the renewal or expansion conversation, with expansion triggered by evidence such as rising usage or new teams, from [Sybill on QBRs](https://www.sybill.ai/blogs/qbr-templates-agendas-and-best-practices).
- Sales Operations Manager: weekly checks of stage, close date, amount and a dated next step, monthly duplicate checks and exceptions trended past 30 days, from [Default on CRM data hygiene](https://www.default.com/post/crm-data-hygiene); commit, best case and pipeline categories with written entry criteria, from [ORM on forecast categories](https://orm-tech.com/blog/sales-forecast-categories-explained).
- Sales Engineer: technical discovery before the demo, and proofs of concept tied to three to five buyer workflows with success criteria agreed up front and a fixed end date, scope changes written down, from [Presales Collective on proofs of concept](https://www.presalescollective.com/content/part-3-dont-derail-the-proof-of-concept) and [Powerhouse Sales Engineering's POC steps](https://www.powerhousesalesengineering.com/poc/10-essential-steps-for-managing-a-successful-proof-of-concept/).
- Partnerships Manager: published rules of engagement applied the same way to every partner, clear eligibility for a valid registration, first complete registration wins, and conflicts resolved by the written rule, from [Channeltivity on deal registration best practices](https://help.channeltivity.com/support/solutions/articles/144850-deal-registration-best-practices) and [Kademi on fair deal registration programs](https://www.kademi.co/blogs/resources/deal-registration-best-practices/).
- Sales Enablement Manager: win/loss run continuously on wins and losses alike, decision drivers taken from buyers rather than the CRM reason, buyers interviewed by someone other than their seller, and findings reported at least quarterly, from [Pragmatic Institute's win-loss best practices](https://www.pragmaticinstitute.com/resources/articles/product/eight-win-loss-analysis-best-practices/) and [Klue's win-loss analysis guide](https://klue.com/blog/win-loss-analysis-guide).

### Marketing

- Head of Marketing: a weekly team review that opens with red/yellow/green status per workstream, then blockers, the marketing calendar and next week's priorities, from [Range and Emily Kramer's weekly marketing meeting agenda](https://www.range.co/templates/marketing-team-weekly-meeting-agenda).
- Content Marketer: three to five content pillars, four to six weeks planned ahead, a status per piece and one owner, from [CoSchedule on editorial calendars](https://coschedule.com/content-marketing/editorial-calendar), with the same people-first questions from [Google Search Central](https://developers.google.com/search/docs/fundamentals/creating-helpful-content).
- SEO Specialist: the fundamentals (unique titles and descriptions, headings, internal links, alt text, sitemaps, structured data), people-first content questions, and the point that AI features need no special markup, only the same helpful pages, from Google's [SEO starter guide](https://developers.google.com/search/docs/fundamentals/seo-starter-guide), [helpful, reliable, people-first content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) and [AI features and your website](https://developers.google.com/search/docs/appearance/ai-features).
- Social Media Manager (listening): goals first, Boolean queries with exclusions, sorting into sentiment, pain points and competitor moves, and routing findings to named owners, from [Hootsuite on social listening](https://blog.hootsuite.com/social-listening-business/).
- Social Media Manager (publishing): one cross-network calendar with a sustainable cadence per network and a healthy content mix, from [Sprout Social's content calendar guide](https://sproutsocial.com/insights/social-media-calendar/) and [how often to post](https://sproutsocial.com/insights/how-often-to-post-on-social-media/); a clear disclosure on any paid or gifted endorsement, from the [FTC's Disclosures 101 for social media influencers](https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers).
- Email Marketing Manager: one variable per subject-line test on a slice of the list, and the sending checklist (accurate sender, honest subject, postal address, working unsubscribe honoured within ten business days, sender authentication, complaint rate under 0.3 percent), from [Mailchimp on subject line testing](https://mailchimp.com/resources/subject-line-testing/), the [FTC CAN-SPAM compliance guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business) and [Google's email sender guidelines](https://support.google.com/a/answer/81126).
- Product Marketing Manager: positioning built in order (competitive alternatives, unique attributes, value, best-fit customers, category) and launches sized by expected impact into three tiers, from [April Dunford's positioning quickstart](https://www.aprildunford.com/post/a-quickstart-guide-to-positioning) and [Pragmatic Institute on launch tiers](https://www.pragmaticinstitute.com/resources/articles/product/prioritize-product-launch-resources-with-launch-tiers/); one-screen battlecards kept current, from [Klue](https://klue.com/blog/competitive-battlecards-101) and the [Competitive Intelligence Alliance](https://www.competitiveintelligencealliance.io/competitive-battlecards-guide-2022/).
- Reputation Manager: no compensation conditioned on sentiment or rating, no suppressing negative reviews, a public reply is allowed, from the [FTC Consumer Reviews and Testimonials Rule Q&A](https://www.ftc.gov/business-guidance/resources/consumer-reviews-testimonials-rule-questions-answers), and the surface's own flag grounds and incentive ban from [Google Maps user-generated content policy](https://support.google.com/contributionpolicy/answer/7400114).
- Paid Media Manager: a weekly search-terms review sorted by cost, irrelevant or non-converting terms added as exclusions (exact match by default), converting terms added as keywords, and negative lists reviewed monthly so they do not block new products, from Google Ads Help, [About the search terms report](https://support.google.com/google-ads/answer/2472708) and [Get negative keyword ideas using the search terms report](https://support.google.com/google-ads/answer/7102466), and [Optmyzr on negative keywords](https://www.optmyzr.com/blog/negative-keywords/).
- Event Marketing Manager: goals set before the event, leads tagged with context at the event, follow-up inside 48 hours, a post-mortem within a week, and results counted in leads, meetings, cost per lead and closed deals rather than attendance, from [Bizzabo on trade show ROI](https://www.bizzabo.com/blog/trade-show-roi) and [Winmo's trade show ROI best practices](https://www.winmo.com/events/how-to-maximize-trade-show-roi-best-practices-for-2025/).
- PR Manager: a researched media list of reporters who cover the topic rather than mass lists, pitches under about 200 words that reference the reporter's recent work, one polite follow-up after about 48 hours, from [Meltwater on pitching a press release](https://www.meltwater.com/en/blog/pitch-a-press-release) and [Business Wire's media relations tips](https://www.businesswire.com/blog/32-media-relations-tips-to-maximize-your-coverage-opportunities).
- Community Manager: health measures such as time to first reply, questions unreplied to and accepted solutions, the metrics chosen from the community's business purpose, from [CMX on community measurement](https://www.cmxhub.com/blog/community-measurement-strategy) and [the SPACES model](https://www.cmxhub.com/blog/the-spaces-model); champions identified and recognised, guidelines enforced consistently, from [HubSpot's community management best practices](https://blog.hubspot.com/marketing/community-management-best-practices).
- Brand Manager: one voice that stays while tone changes with the reader's situation, plain active language, written down with examples, from the [Mailchimp Content Style Guide, Voice and Tone](https://styleguide.mailchimp.com/voice-and-tone/); regular audits of customer-facing content against the guidelines and a yearly review of the guide, from [Hootsuite on brand voice](https://blog.hootsuite.com/brand-voice/).
- Marketing Operations Manager: source, medium and campaign on every campaign link, lowercase values, mediums that match the analytics tool's default channel groups, no internal links tagged, and never personal data in a parameter, from Google Analytics Help, [URL builders: collect campaign data with custom URLs](https://support.google.com/analytics/answer/10917952) and [Improvado's UTM naming conventions guide](https://improvado.io/blog/utm-naming-conventions).

### Customer Support

- Head of Customer Support: a weekly review on named measures (first response time, time to resolution, backlog growth, CSAT, escalations) with two or three owned actions, and backlog aging buckets because a ticket open for two weeks is almost always misrouted or stuck, from [Supportbench on the weekly support ops review](https://www.supportbench.com/weekly-support-ops-review-drive-real-improvements/).
- Support Agent: category, priority and routing, macros checked by a knowledgeable reviewer, and
  knowledge base gaps filled from repeated tickets, from public triage guides such as
  [Pylon](https://www.usepylon.com/blog/customer-support-triage) and
  [Tidio](https://www.tidio.com/blog/ticket-triage/).
- Support Quality Analyst: a short scorecard of accuracy, tone, completeness, policy and next step on a 1 to 3 scale, 4 to 6 categories at most, scored from a 5 to 10 percent random sample with some targeted tickets, from [Zendesk on building a QA scorecard](https://www.zendesk.com/blog/quality-assurance/workforce-optimization/qa-scorecard/) and [Featurebase on customer service quality assurance](https://www.featurebase.app/blog/customer-service-quality-assurance).
- Customer Success Manager: a renewal motion that starts 90 to 120 days out (health check, value review, objections, commercial terms), health from usage, support and relationship signals, and health owned by customer success while price and contract stay with sales, from [June on the renewal playbook](https://www.june.so/blog/customer-success-renewal-playbook) and [Planhat on B2B renewals](https://www.planhat.com/customer-success/renewals).
- Customer Onboarding Specialist: a seamless sales-to-onboarding handoff, a kickoff built around the customer's own goals rather than the implementation checklist, four to six milestones (fewer is vague, more is a checklist nobody reads), and first value reached within the first 30 days because most early churn happens in the first 90, from [ClientSuccess's 30-day time-to-value plan](https://www.clientsuccess.com/resources/blog-the-30-day-time-to-value-plan), [Custify's SaaS onboarding guide](https://www.custify.com/blog/saas-customer-onboarding-guide/) and [GainTrace's onboarding playbook](https://gaintrace.com/blog/customer-onboarding-best-practices).
- Escalations Manager: escalation criteria written as triggers rather than judgment calls, a named owner rather than a queue, one customer clock that never resets on an internal handoff, and updates on a fixed cadence by severity even when there is no news, from [Supportbench's customer escalation management playbook](https://www.supportbench.com/customer-escalation-management/) and [Supportbench on update cadence during long investigations](https://www.supportbench.com/customer-communication-cadence-long-investigations-update-rules/); bug reports in the shape under Technical Support Engineer.
- Retention Specialist: one question on why the customer is leaving, one save offer matched to that reason (a pause for "not using it", a cheaper plan for price), cancelling kept at least as easy as signing up, and no second save attempt, from [ProsperStack on cancellation flows](https://prosperstack.com/blog/cancellation-flow/) and [Chargebee on click-to-cancel compliance](https://www.chargebee.com/blog/comply-ftc-click-to-cancel-rule-retain-subscribers/) (state automatic-renewal laws such as California's limit retention to a single offer).
- Support Operations Specialist: start SLA measurement with first reply time and pick one resolution measure, use tags with a consistent naming convention, and audit triggers and automations because overlapping rules cause unintended routing, from Zendesk's help centre on [defining SLA policies](https://support.zendesk.com/hc/en-us/articles/4408829459866-Defining-SLA-policies) and [routing and automation options for incoming tickets](https://support.zendesk.com/hc/en-us/articles/4408831658650-Routing-and-automation-options-for-incoming-tickets).
- Technical Support Engineer: one bug per report with a concise title, numbered steps from a known state, expected against actual result, the environment and evidence, and a search for an existing report before filing, from [BrowserStack on writing a bug report](https://www.browserstack.com/guide/how-to-write-a-bug-report) and [Marker.io's bug report guide](https://marker.io/blog/how-to-write-bug-report).
- Returns and Refunds Specialist: separate the return decision (the goods) from the refund decision (the money), measure and age the time from receipt to refund against the time the shop publishes, fast-track low-risk returns and route suspicious ones to a human, from [Claimlane on refund policy best practices](https://www.claimlane.com/resources/blog/refund-policy-best-practices) and [Signifyd on ecommerce return policies](https://www.signifyd.com/blog/ecommerce-return-policy/); the refund deadlines when an order cannot ship come from the [FTC Email, Internet, or Telephone Order Merchandise Rule](https://www.ftc.gov/legal-library/browse/rules/mail-internet-or-telephone-order-merchandise-rule).

### Finance

- Head of Finance: a 13-week cash forecast of 8 to 12 lines, rolled forward every week and reconciled to the previous week's actuals, with collections timed from the receivables aging, from [PKF O'Connor Davies on the 13-week cash flow forecast](https://www.pkfod.com/insights/a-cfos-lifeline-mastering-the-13-week-cash-flow-forecast/) and [Accordion's 13-week cash flow guide](https://www.accordion.com/our-insights/knowledge/13-week-cash-flow-forecasting-guide/); the close run in the order of [Business.com's month-end close checklist](https://www.business.com/articles/month-end-close-checklist/).
- Bookkeeper: a month-end checklist that runs in order (feeds complete, receipts collected, uncategorised transactions cleared, accounts reconciled, receivables and payables reviewed, statements compared with last month, period locked by a human), categorising weekly instead of in one batch, and one batched list of owner questions with items for the accountant kept separate, from [Business.com's month-end close checklist](https://www.business.com/articles/month-end-close-checklist/) and [Chaser's monthly bookkeeping checklist](https://www.trychaser.com/checklist-articles/monthly-bookkeeping-checklist-for-small-businesses).
- Accounts Receivable Specialist: overdue invoices followed up in full with polite early reminders and firmer later ones, 50 to 125 words, the invoice details and a clear action in each, a human taking over at the last step, from [Chaser on dunning](https://www.chaserhq.com/blog/what-is-dunning-in-accounts-receivables-and-how-to-optimize-it) and [Gaviti's collection email templates](https://gaviti.com/5-most-effective-collection-email-templates/).
- Accounts Payable Specialist: match the bill to the purchase order and the receipt before paying, and detect duplicates beyond the invoice number (same vendor and amount, normalised numbers), from [Ramp on three-way matching](https://ramp.com/blog/accounts-payable/3-way-match) and [Ramp on duplicate invoices](https://ramp.com/blog/accounts-payable/duplicate-invoices); verify any change to a vendor's payment details by calling a number already on file, never one in the request, from the [FBI on business email compromise](https://www.fbi.gov/how-we-can-help-you/scams-and-safety/common-frauds-and-scams/business-email-compromise).
- Expense Auditor: an accountable plan needs a business connection, substantiation within 60 days and return of any excess within 120, from [IRS Publication 463](https://www.irs.gov/publications/p463); check every report for policy compliance, duplicate receipts and submission timing, from [Emburse's expense report audit guide](https://www.emburse.com/resources/expense-report-audit-guide-for-finance-teams).
- FP&A Analyst (budget and forecast): variance as actual minus budget in money and percent, commentary only on variances over a percentage and an amount, each with an owner, and the forecast updated in the same monthly cycle, from [Wall Street Prep on budget-to-actual variance analysis](https://www.wallstreetprep.com/knowledge/budget-actual-variance-analysis-fpa/) and [Numeric on budget variance](https://www.numeric.io/blog/budget-variance); the weekly spend watch is below.
- FP&A Analyst (spend): inform, optimise, operate; an anomaly defined as an unforecast rise against history with severity thresholds and a named owner, from the [FinOps Foundation anomaly management capability](https://www.finops.org/framework/capabilities/anomaly-management/) and [its guide to managing cloud cost anomalies](https://www.finops.org/wg/managing-cloud-cost-anomalies/); renewal calendars with alerts at 90 and 60 days, notice periods and usage evidence (inactive 90 days, 30 for expensive seats) before any change, from [Zylo's SaaS renewal guide](https://zylo.com/blog/guide-saas-renewal).
- Investor Relations Manager: metrics and asks first and a short recap last, three to five highlights and one to three lowlights, the same metric definitions every month, and bad news paired with what is being done, from [Visible on Y Combinator's investor update advice](https://visible.vc/blog/tips-from-yc-using-asks-metrics-and-a-recap-to-power-your-investor-updates/) and [Visible on writing an investor update](https://visible.vc/blog/how-to-write-the-perfect-investor-update/).
- Payroll Specialist: before every run, confirm new hires, terminations and pay changes, reconcile the roster to the HR record and validate timesheets, bonuses and commissions before cut-off; reconcile the register after every run, from [ADP on payroll reconciliation](https://www.adp.com/resources/articles-and-insights/articles/p/payroll-reconciliation.aspx) and [Homebase's payroll reconciliation guide](https://www.joinhomebase.com/blog/payroll-reconciliation).
- Tax Specialist: due dates taken from the authority's own calendar, which already adjusts for weekends and holidays, from [IRS Publication 509, Tax Calendars](https://www.irs.gov/publications/p509); remote sales create a duty to register once a state's economic nexus threshold (commonly 100,000 in sales) is passed, tracked state by state, from the [Sales Tax Institute's economic nexus chart](https://www.salestaxinstitute.com/resources/economic-nexus-state-guide).
- Revenue Accountant: the five-step model (contract, performance obligations, price, allocation, recognition as each obligation is satisfied), subscriptions recognised ratably over the service period, and the deferred revenue roll-forward (opening plus billings minus recognised equals closing) tied to the ledger every close, from [Numeric on SaaS revenue recognition](https://www.numeric.io/blog/revenue-recognition-saas) and [Maxio on ASC 606 for SaaS](https://www.maxio.com/blog/saas-revenue-recognition-asc-606).
- Billing Specialist: every invoice carries the legal names, a sequential number, dates, description, quantity and rate, tax, total, terms, the payment route and the customer's PO number where required, and a wrong invoice is corrected with a numbered credit note and a new invoice rather than edited, from [Stripe on invoice requirements](https://stripe.com/resources/more/invoice-requirements) and [Paystand on B2B invoicing best practices](https://www.paystand.com/blog/invoicing-best-practices).

### Operations

- Operations Manager: a short weekly checklist of five to nine items, the ones costly to miss or easy to forget, marked read-do or do-confirm, with a named owner, a review date and a proof of completion for every recurring duty, from [The Checklist Manifesto design notes (BYU Design Review)](https://www.designreview.byu.edu/collections/good-checklist-design-from-the-checklist-manifesto) and [Atlassian's guide to writing an SOP](https://www.atlassian.com/software/confluence/templates/sop); notice windows flagged when they open (a renewal's lead time), from [Harvey's contract review checklist](https://www.harvey.ai/blog/contract-review-checklist).
- Project Coordinator (meetings): decisions with their reasons, one named owner and a due date per action item,
  and a review of open items at the next meeting, from public guides such as
  [Fellow](https://fellow.ai/blog/how-to-manage-meeting-tasks-and-action-items/) and
  [Asana](https://asana.com/resources/meeting-notes-tips).
- Project Coordinator (actions and milestones): one log of risks, actions, issues and decisions, each with an owner, a date, a priority and a status, reviewed at every status meeting, from [Atlassian on the RAID log](https://www.atlassian.com/agile/project-management/raid-log) and [Asana on RAID logs](https://asana.com/resources/raid-log); one owner and a due date per action item from the meeting sources above.
- Procurement Manager: must-haves before scoring, three to five shortlisted vendors, percentage weights that total 100, a scoring rubric agreed first, total cost of ownership including renewal rises, security and reference checks, from [Ramp's vendor comparison matrix guide](https://ramp.com/blog/vendor-comparison-matrix) and [Ivalua's vendor selection process](https://www.ivalua.com/blog/vendor-selection-process/).
- Vendor Manager: vendors tiered by data sensitivity, business criticality, regulatory footprint and integration depth, with review depth scaled by tier, from [Atlas Systems on vendor risk management](https://www.atlassystems.com/blog/vendor-risk-management-best-practices); every contract in one place with renewal alerts so an auto-renewal never surprises, and a weighted scorecard, from [Ramp's vendor management best practices](https://ramp.com/blog/vendor-management-best-practices).
- Office Manager: supplies, equipment, maintenance, contracts and visitor reception as the core of the role, from the [US Bureau of Labor Statistics on administrative services and facilities managers](https://www.bls.gov/ooh/management/administrative-services-managers.htm) and [Kisi's office facilities management checklist](https://www.getkisi.com/guides/office-facilities-management-checklist).
- IT Support Specialist: triage by category, impact and urgency with the first troubleshooting step attached, and context gathered at intake, from [Ivanti's IT ticket handling best practices](https://www.ivanti.com/blog/it-ticket-handling-best-practices); account lifecycle (inventory of accounts, least privilege, access revoked when someone leaves, dormant accounts disabled after 45 days) from [CIS Control 5, Account Management](https://cas.docs.cisecurity.org/en/latest/source/Controls5/) and the [CIS Controls](https://www.cisecurity.org/controls/cis-controls-navigator).
- Security and Compliance Analyst: every control with a named owner and an operating cadence before evidence collection starts, evidence for every period of the observation window, and quarterly user access reviews with a decision per user, from the [SOC 2 evidence collection guide (soc2auditors.org)](https://soc2auditors.org/insights/soc-2-evidence-collection-guide/) and [Torii on SOC 2 access reviews](https://www.toriihq.com/articles/soc2-access-reviews); dormant accounts at 45 days from [CIS Control 5](https://cas.docs.cisecurity.org/en/latest/source/Controls5/).
- Travel Coordinator: pre-trip approval with named approvers, booking at least 14 days ahead, the lowest logical fare within a reasonable departure window, and nightly hotel caps by city, from [Ramp's corporate travel policy guide](https://ramp.com/blog/corporate-travel-policy) and [SAP Concur's travel policy best practices](https://www.concur.com/blog/article/8-corporate-travel-policy-best-practices).
- Inventory Planner: reorder point = average daily sales x lead time + safety stock, and safety stock = maximum daily sales x maximum lead time - average daily sales x average lead time, from [inFlow's reorder point and safety stock guide](https://www.inflowinventory.com/blog/reorder-point-formula-safety-stock/) and [Fishbowl on safety stock methods](https://www.fishbowlinventory.com/blog/calculating-the-safety-stock-formula-6-variations-key-use-cases).
- Logistics Coordinator: on-time and in-full measured and failures reviewed for trends, lane-level performance because a carrier's average hides weak lanes, and carrier invoices audited against the contracted rates, surcharges and service levels, from [Red Stag Fulfillment on OTIF](https://redstagfulfillment.com/on-time-and-in-full-otif/) and [Intelligent Audit's freight audit best practices](https://www.intelligentaudit.com/blog/freight-audit-best-practices-from-global-supply-chain-leaders).
- Dispatcher: jobs assigned by skill, proximity and availability, routes grouped to cut driving, and the right skills and parts on the first visit to raise first-time fix, from [ServiceTitan on field service productivity](https://www.servicetitan.com/guides/field-service-management/productivity) and [Forgestik on first-time fix rate](https://www.forgestik.com/en/blog/improve-first-time-fix-rate-field-service).

### Legal

- General Counsel: one intake point for every legal request, triaged by type, urgency, completeness and risk so high-risk and time-sensitive work goes first and the right owner gets it, from [Streamline AI on legal intake for in-house teams](https://www.streamline.ai/blog/legal-intake-form-guide) and [Dazychain's legal intake workflow guide](https://www.dazychain.com/guides/legal-intake-workflow-guide/); a first general counsel's priorities (relationships, the team's structure, good governance, a foundation for efficient process), from [ACC's guide for an organization's first general counsel](https://www.acc.com/resource-library/establishing-house-law-department-guide-organizations-first-general-counsel) and [ACC Docket on the first year as general counsel](https://docket.acc.com/actions-take-during-your-first-year-general-counsel).
- Contracts Manager: read limitation of liability, indemnity, renewal, termination and IP first, tabulate key terms with clause references, flag deviations from the team's own playbook rather than a general view, and have a lawyer review any AI-assisted analysis, from [Spellbook's contract review checklist](https://spellbook.com/learn/contract-review-checklist) and [Harvey's contract review checklist](https://www.harvey.ai/blog/contract-review-checklist).
- Paralegal: a mutual NDA where both sides share, the five standard exclusions from confidential information, a defined confidentiality period (commonly two to five years, trade secrets treated separately), and residuals clauses struck or narrowed, from [Common Paper's Mutual NDA standard](https://commonpaper.com/standards/mutual-nda/) (a committee-drafted, openly licensed standard), [Common Paper on residuals](https://commonpaper.com/standards/mutual-nda/mutual-right-to-use-residual-information/) and [Vaquill on confidentiality clauses and the residuals trap](https://www.vaquill.ai/clauses/confidentiality).
- Compliance Manager: external obligations (state annual reports, a registered agent in each state of registration, renewed business licences and permits) kept current with internal records, and foreign registrations filing in each state where they do business, from the [SBA's Stay legally compliant guide](https://www.sba.gov/business-guide/manage-your-business/stay-legally-compliant); the rule behind each date cited from the authority's own page, and done meaning proven.
- Privacy Manager: a data subject access request answered within one month of receipt (counted from the day it arrives), extendable by up to two months for complex requests, with the clock paused only while genuinely needed clarification is outstanding, from the [ICO's guide to subject access](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/subject-access-requests/a-guide-to-subject-access/); the terms a processor contract must cover and flow down to subprocessors, from [GDPR Article 28](https://gdpr-info.eu/art-28-gdpr/); records of processing (purposes, categories, recipients, transfers, retention, security) from [the ICO on Article 30 documentation](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/documentation/what-do-we-need-to-document-under-article-30-of-the-gdpr/).
- IP Paralegal: US registrations kept alive with a declaration of use between years 5 and 6, a declaration and renewal between years 9 and 10 and every 10 years after, each with a six-month grace period and no reminder guaranteed, from [USPTO, Keeping your registration alive](https://www.uspto.gov/trademarks/maintain/keeping-your-registration-alive); a clearance search before adopting a name, looking for marks similar in sound, appearance or meaning for related goods, from [USPTO on comprehensive clearance searches](https://www.uspto.gov/trademarks/search/comprehensive-clearance-search-similar-trademarks) and [USPTO on likelihood of confusion](https://www.uspto.gov/trademarks/search/likelihood-confusion).
- Legal Operations Manager: firm and vendor management and financial management as core legal operations competencies, from [CLOC on what legal ops is](https://cloc.org/what-is-legal-ops/); invoices reviewed against outside counsel guidelines (time in 0.1 hour units per task, no block billing, rates by timekeeper level from the engagement letter, non-billable admin), from [Brightflag on outside counsel guidelines](https://brightflag.com/resources/tips-for-creating-outside-counsel-guidelines/) and [Legal Bill Review on keeping guidelines current and enforced](https://www.legalbillreview.com/blog/outside-counsel-guidelines-current-and-enforced).
- Corporate Secretary: minutes as the official record of attendance, motions, votes, conflicts and actions, outcomes rather than a transcript, without editorial comment or legal advice, approved at the next meeting; written consents filed in the minute book alongside minutes, from [Diligent's guide to board meeting minutes](https://www.diligent.com/resources/blog/minutes-at-board-meetings) and [BoardEffect on board meeting minutes](https://www.boardeffect.com/blog/board-meeting-minutes-template-best-practices/).

### HR

- Head of People: headcount plan against actual as a living plan rather than an annual exercise, with attrition and hires tracked against it, from [Rippling on headcount planning](https://www.rippling.com/blog/effective-headcount-planning) and [Deel on workforce planning metrics](https://www.deel.com/blog/workforce-planning-metrics/); the same routing and weekly summary pattern as the other group heads.
- Recruiter: job-related, uniformly applied criteria and job posts that do not discourage applicants, from the [EEOC's best practices for employers](https://www.eeoc.gov/initiatives/e-race/best-practices-employers-and-human-resourceseeo-professionals); the same questions and scoring scale for every candidate, agreed with the hiring manager up front, from [Google re:Work's structured interviewing guide](https://rework.withgoogle.com/intl/en/guides/a-guide-to-structured-interviewing-for-better-hiring-practices); timely, consistent communication at every stage, from [SHRM's guide to effective recruiting](https://www.shrm.org/topics-tools/workplace/complete-guide-to-effective-recruiting).
- HR Generalist: compliance, clarification, culture and connection across preboarding, day one, the first week and 30, 60 and 90 day check-ins, from [SHRM's 4 Cs as summarised by HR Cloud](https://www.hrcloud.com/blog/onboarding-best-practices-the-4-cs); collecting only what the purpose needs and reviewing what is held, from the [ICO's data minimisation principle](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-protection-principles/a-guide-to-the-data-protection-principles/data-minimisation/).
- Sourcer: personal messages that cite the person's own work get several times the replies of generic ones, three to five touches over two to three weeks, and tracking which sources produce hires rather than volume, from [Gem on how to source candidates](https://www.gem.com/blog/how-to-source-candidates) and [Truffle on passive candidate sourcing](https://www.hiretruffle.com/blog/sourcing-candidates); no screening by protected characteristic or proxy, from the [EEOC's prohibited practices](https://www.eeoc.gov/prohibited-employment-policiespractices).
- Recruiting Coordinator: slow or repeated rescheduling loses candidates, scheduling is one of a candidate's first impressions, and coordinators lose much of their week to it, from [SHRM on interview scheduling](https://www.shrm.org/topics-tools/news/talent-acquisition/automation-removes-pain-candidate-interview-scheduling); independent scorecards before any debrief, from [Google re:Work's structured interviewing guide](https://rework.withgoogle.com/intl/en/guides/a-guide-to-structured-interviewing-for-better-hiring-practices).
- HR Business Partner: the same rating definitions for every manager, HR as facilitator watching for bias, and performance kept separate from pay conversations, from [Culture Amp on performance review calibrations](https://www.cultureamp.com/blog/performance-review-calibrations) and [Deel on calibration meetings](https://www.deel.com/blog/performance-calibration-meeting/).
- Benefits Administrator: life events open a special enrollment window of typically 30 days, documented and processed inside it, and missed windows are among the most common preventable benefits errors, from [Thatch on special enrollment periods](https://thatch.com/blog/special-enrollment-periods-and-qualifying-life-events) and [HealthCare.gov on qualifying life events](https://www.healthcare.gov/glossary/qualifying-life-event/).
- Employee Experience Manager: results suppressed below a minimum group size of about five, no stacked filters that could identify someone, and visible follow-through as what keeps participation up, from [Gallup on employee surveys](https://www.gallup.com/workplace/692474/workplace-employee-surveys.aspx) and [FeedbackPulse on survey anonymity thresholds](https://feedbackpulse.com/resources/survey-anonymity-thresholds).
- People Operations Specialist: privileged access removed first and on the last day, standard access by end of day, a check 24 hours later that revocation worked, plus knowledge handover and an exit interview, from [CheckFlow's IT offboarding checklist](https://checkflow.io/blog/it-offboarding-security-checklist) and [SHRM on upgrading offboarding](https://www.shrm.org/topics-tools/news/employee-relations/lasting-impressions-upgrade-offboarding).
- Compensation Analyst: bands with a minimum, midpoint and maximum set from a stated philosophy and dated market data, spreads of roughly 30 to 50 percent widening with level, reviewed at least yearly, from [Carta on salary bands](https://carta.com/learn/startups/compensation/bands/) and [Ravio on salary band structures](https://ravio.com/blog/a-best-practice-approach-to-salary-bands-effective-fair-and-easy-to-manage).
- Learning and Development Specialist: most learning from the work and from colleagues with formal courses as the smaller part (the 70-20-10 model), and plans built from the skills a role requires, from [Training Industry on the 70-20-10 model](https://trainingindustry.com/wiki/content-development/the-702010-model-for-learning-and-development/) and [Deel on 70-20-10 development plans](https://www.deel.com/blog/70-20-10-development-plan/).

### Product

- Head of Product: candidates scored on reach, impact (a fixed 3 to 0.25 scale), confidence (100, 80 or 50 percent) and effort in person-weeks, re-scored each planning cycle, from [Intercom on RICE prioritization](https://www.intercom.com/blog/rice-simple-prioritization-for-product-managers/); every proposal tied to one outcome, with opportunities under it, from [Product Talk on the opportunity solution tree](https://www.producttalk.org/opportunity-solution-tree/).
- Customer Insights Analyst: one shared theme list, tagging each item, ranking by frequency and severity weighted by account value, weekly triage, and closing the loop with customers (prepared for a human to send), from [CustomerGauge on voice of customer analysis](https://customergauge.com/blog/voice-of-customer-analysis) and [Umbrex on voice of the customer feedback loops](https://umbrex.com/resources/customer-retention-playbook/voice-of-the-customer-feedback-loops/).
- UX Researcher: story-based interviews and an opportunity map refined every three to four interviews, from [Product Talk on the opportunity solution tree](https://www.producttalk.org/opportunity-solution-tree/); choosing the method by attitudinal versus behavioural, qualitative versus quantitative and context of use, from [NN/g, When to use which user-experience research methods](https://www.nngroup.com/articles/which-ux-research-methods/).
- Product Manager: start with the problem and who has it, explicit scope and non-goals, testable requirements, a short core document kept current, from [Carlin Yuen on writing PRDs](https://carlinyuen.medium.com/writing-prds-and-product-requirements-2effdb9c6def) and [Heretto's guide to product requirements documents](https://www.heretto.com/blog/product-requirements-document); acceptance criteria as Given/When/Then, from [Martin Fowler, Given When Then](https://martinfowler.com/bliki/GivenWhenThen.html).
- Product Analyst: adoption as the share of users who performed the action, retention by signup cohort to find when engagement drops, activation as the leading signal, from [Amplitude on cohort retention analysis](https://amplitude.com/explore/analytics/cohort-retention-analysis) and [Amplitude on cohort analysis](https://amplitude.com/explore/analytics/cohort-analysis); a sample size fixed in advance and no stopping on a peek, from [Evan Miller, How not to run an A/B test](https://www.evanmiller.org/how-not-to-run-an-ab-test.html).
- Product Operations Manager: one intake path, one canonical request per need, duplicates merged with their requesters, and the people who asked told when it ships, from [AnnounceKit's feature request management guide](https://announcekit.app/guides/feature-request-management) and [Practical Product Ops on request intake](https://practicalproductops.substack.com/p/product-ideas-feature-requests-form).
- UX Writer: errors that are visible, human-readable, precise, constructive and never blame the user, and a rubric to score them, from [NN/g error-message guidelines](https://www.nngroup.com/articles/error-message-guidelines/) and [NN/g's error-message scoring rubric](https://www.nngroup.com/articles/error-messages-scoring-rubric/).
- Pricing Analyst: a value metric customers pay for as they grow, tiers mapped to real segments, and willingness to pay measured by study (Van Westendorp, Gabor-Granger) rather than guessed, from [Stripe's guide to SaaS pricing and packaging](https://stripe.com/resources/more/saas-pricing-and-packaging-strategy) and [Monetizely's guide to SaaS pricing research](https://www.getmonetizely.com/articles/the-complete-guide-to-saas-pricing-research-from-surveys-to-insights).

### Engineering

- Head of Engineering: a weekly review of what shipped, what is stuck and what is blocked, using delivery measures about the process and never a person (change lead time, deployment frequency, failed deployment recovery time, change fail rate) and review turnaround, from [DORA's software delivery metrics](https://dora.dev/guides/dora-metrics-four-keys/) and [Google's engineering practices on small changes](https://google.github.io/eng-practices/review/developer/small-cls.html).
- QA Engineer (triage): reproduce, deduplicate, ask for information, label kind and priority, and watch for stale
  issues, from the [Kubernetes issue triage guide](https://www.kubernetes.dev/docs/guide/issue-triage/) and the
  [Python developer guide](https://devguide.python.org/triage/triaging/).
- QA Engineer (test plans): rank what to test by likelihood times impact, put money, sign-in, data and migration paths first, keep a short regression checklist of core flows, and push checks to the fastest level of the test pyramid that catches them, from [Martin Fowler's practical test pyramid](https://martinfowler.com/articles/practical-test-pyramid.html) and the [ISTQB glossary on risk-based testing](https://glossary.istqb.org/en_US/term/risk-based-testing).
- Senior Software Engineer: review the design before the detail, favour approving what improves code health, mark preferences as nits, keep changes small (about 100 lines is reasonable, 1,000 too large), and label each comment as blocking or not, from [Google's standard of code review](https://google.github.io/eng-practices/review/reviewer/standard.html), [why small changes](https://google.github.io/eng-practices/review/developer/small-cls.html) and [Conventional Comments](https://conventionalcomments.org/).
- Release Manager (notes): written for humans, grouped Added, Changed, Deprecated, Removed, Fixed and Security, newest first with dates and links, never a raw commit log, with the version bump taken from the change (major for breaking, minor for a feature, patch for a fix), from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), [Semantic Versioning](https://semver.org/) and [GitHub's generated release notes](https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes) (labels decide the categories).
- Release Manager (readiness): releases that are small, frequent, reproducible and reversible, with a named rollback and a readiness check before each, from [Google's SRE book on release engineering](https://sre.google/sre-book/release-engineering/).
- Site Reliability Engineer (postmortems): a blameless postmortem with impact, root causes, a detailed timeline, what went right and follow-up actions, written soon after the incident and reviewed before it is shared, from [Google's SRE book on postmortem culture](https://sre.google/sre-book/postmortem-culture/), [PagerDuty's postmortem process](https://response.pagerduty.com/after/post_mortem_process/) and [PagerDuty's postmortem guidance](https://postmortems.pagerduty.com/).
- Site Reliability Engineer (on-call handoff): a written handoff at each rotation change with open incidents, noisy alerts and risky changes, and alerts that fire without action tuned or removed, from [Google's SRE workbook on being on call](https://sre.google/workbook/on-call/).
- Technical Writer: one page, one documentation type (tutorial, how-to, reference, explanation), second person, present tense, docs kept with the code so a change ships with its documentation, from [Diataxis](https://diataxis.fr/), the [Google developer documentation style guide](https://developers.google.com/style) and [Write the Docs on docs as code](https://www.writethedocs.org/guide/docs-as-code/).
- Security Engineer: severity (CVSS) is an upper bound, not an order; rank by observed exploitation on [CISA's Known Exploited Vulnerabilities catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog), exploit likelihood from [FIRST's EPSS](https://www.first.org/epss/) and whether the code can reach the flaw, with deadlines per tier; a committed secret is rotated, not just deleted, because history keeps it, from [GitHub's secret scanning documentation](https://docs.github.com/en/code-security/secret-scanning/about-secret-scanning).
- DevOps Engineer: detect flaky tests by a pass and a fail on the same commit, give each one an owner and a fix-or-quarantine decision with a deadline, and never accept reruns as the fix, from [Google Testing Blog, Flaky Tests at Google and How We Mitigate Them](https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html); deploy frequency and failed deploys as in [DORA's metrics](https://dora.dev/guides/dora-metrics-four-keys/).
- Developer Advocate: a friction log that tells the story of a developer's journey step by step and is shared with the product and engineering owners who can remove the friction, from [Developer Relations' introduction to friction logging](https://developerrelations.com/guides/an-introduction-to-friction-logging/) and [a friction log guide on DEV](https://dev.to/thagomizer/friction-logs-3110); one reader and one job per tutorial, as under Technical Writer above.
- Software Architect: one short record per significant decision with its context, the decision, its status and its consequences, superseded rather than deleted, from [adr.github.io](https://adr.github.io/) and [the original ADR template](https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locales/en/templates/decision-record-template-by-michael-nygard/index.md).

### Leadership

- Chief of Staff: the weekly Monday and Friday rhythm and three to five priorities, from
  [First Round Review on the chief of staff role](https://review.firstround.com/how-to-be-an-exceptional-chief-of-staff-advice-for-scaling-impact-at-startups/)
  and [McKinsey on being a great chief of staff](https://www.mckinsey.com/capabilities/strategy-and-corporate-finance/our-insights/how-to-be-a-better-chief-of-staff).
- Inbox Manager: the four Ds (do, delegate, defer, delete), a handful of labels, and set review times, from
  [Superhuman on executive email management](https://blog.superhuman.com/executive-email-management/).
- Strategy Analyst: three to five objectives with about three measurable key results each, grading on a 0 to 1 scale with 0.6 to 0.7 as healthy for stretch goals, outcomes not activities, and mid-quarter check-ins, from [Google re:Work, Set goals with OKRs](https://rework.withgoogle.com/intl/en/guides/set-goals-with-okrs).

## Adding your own

Copy the nearest template in the same group and keep the card fields above; give it a job title, an icon from Material
Symbols, and add it to its head's `team_templates`, then run `scripts/build-icon-font.py` so the icon is in the UI's font.
`clients/tests/test_catalog.py` checks every template except the four built-ins (no list to update) for those
fields: a group from `templates/groups.yaml` with `pack` following it, an icon the UI font holds, `suggest` and
`tags`, exactly one head per group matching `groups.yaml` and listing the rest of it, at least 90 templates, the 150
line limit, a first sentence of the summary that fits the setup line, a declared but paused routine, an example that names
the fictional team, no pain phrase used twice and no `send`, `write`, `modify` or `delete` verb in `tools:`. The card's
`first_routine.title` must be the title of the routine in `bot.yaml`.
