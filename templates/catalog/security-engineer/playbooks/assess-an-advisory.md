# Assess an advisory

Triggered by a task or message naming a CVE, a GHSA, a package or a news story ("are we affected by the
parser flaw in this morning's newsletter?"), and used for each alert in the weekly report. Budget 20 minutes. The outcome is a
yes, no or unknown on "are we affected", the tier, and the fix. Nothing is changed.

---

## 1. Read the advisory

EPSS is the chance of exploitation in the next 30 days, not the damage. Rate the damage as
`knowledge/security-practice.md` describes.

`hub doc fetch <advisory url>`. Note the affected versions, the fixed version, the vulnerable function or
configuration, and the date. Check CISA's KEV catalogue and the EPSS score for the CVE; record both with the
date read.

## 2. Are we on an affected version?

Search each in-scope repository's lockfiles for the package. Record direct or transitive (and via which
parent), the installed version, and whether it is a runtime or a dev/test dependency.

## 3. Can our code reach it?

Follow `skills/trace-reachability-of-a-vulnerable-call/SKILL.md`.

Search the code for imports of the package and calls to the vulnerable function or the risky configuration.
Record the file and line of each call site, and whether it handles untrusted input on an internet-facing
service from `knowledge/exposure.md`. No call site found is "not reachable as far as traced", with what you
searched. A dynamic import or a framework that calls it for you is "unknown".

## 4. Decide and write

Three lines on the task: **Affected** yes, no or unknown with the evidence; **Tier** and the deadline from
the policy; **Fix**: the upgrade (from and to), the parent to bump if transitive, or the mitigation if there
is no fixed version. Add a line to `knowledge/ledger.md`, commit, `hub task update <id> --status done --note`.
A tier 1 answer also becomes a proposed task for the area's owner, created when their decision is needed.
