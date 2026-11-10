# Route a request

Triggered by a task that names a lead, an account or a request and asks who should take it. Budget 10
minutes. The outcome is one route applied within the requested work and Tools, with its reason. Assign requested work with your Tools.

---

## 1. Read the request

    hub task show <id>

Find what it is: a net-new lead, an existing account, a data problem, a proposal, a renewal or
something for a human only. Look the account up in `knowledge/pipeline-rules.md`'s pipeline source and
in `hub meeting search "<account>"` so you do not route a customer as a lead.

## 2. Pick the owner

Use `knowledge/routing.md` first. Otherwise: net-new lead to `lead-generation-owner` (a list or lead magnet), `inbound-sales-owner` (an inbound lead) or `outbound-sales-owner` (a target account), and to `sdr-research` only when none of those is on the team; open deal, follow-up, proposal, quote or questionnaire to whoever takes calls (`sales`, the Account Executive, when the team has one); renewal or expansion to `account-manager`; stale or
duplicate data, forecast or routing rules to `sales-ops`; technical question or proof of concept to
`sales-engineer`; partner-sourced deal to `partnerships`; health problem to `customer-success`. If the deal is in a live
negotiation, or the prospect replied or asked about price, it belongs to a human: name them.

## 3. Propose

On the task, in three lines: the owner, the reason, and what the owner should produce and by when. Ask with `hub task ask <id> --to <person> "<question>" --version N` only when the intended role or owner is missing; otherwise carry out the requested work with your Tools.

## 4. Carry out the requested work

    hub task create --owner <slug> --title "<what and for whom>" --body "<the request, the account, the source>" --parent <id>

Record the routing and its source request in `knowledge/routing.md`. On a no, record the correction as a
rule in `knowledge/routing.md` so the next proposal is right. `hub task done <id> -m "..." --version N`.
