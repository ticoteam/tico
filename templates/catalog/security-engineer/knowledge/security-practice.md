# Security practice

The standing method for ranking alerts, tracing reach, handling committed credentials and writing patch plans.
The team's own deadlines and tiers live in `knowledge/patch-policy.md`; this file says how to apply them. Read it at the
start of each run and follow it over a habit.

## Severity: damage first

A scanner's severity label is a starting point. Rate each alert by what an attacker could do in this system:

- **Critical**: unauthenticated code execution, access to a whole data store, or account takeover, reachable as deployed.
- **High**: fully defeats a real control, such as authorisation or signature checks, on a reachable path.
- **Medium**: a limited breach of a boundary, or a flaw that needs an unusual setup or an attacker who already has access.
- **Low**: non-secret internals, or a flaw whose reach you traced to nothing.

## Exploitation signals

- **Known exploited**: the CVE is on CISA's Known Exploited Vulnerabilities catalogue. Record the date you read it.
- **EPSS**: the probability, from 0 to 1, that the flaw is exploited in the next 30 days. It is updated daily. It says
  nothing about damage. A low EPSS does not lower a critical, and a high one does not make a dev-only package urgent.
- The team's EPSS line (0.1 in the weekly report) is this team's threshold, not a value from FIRST.

## Reach

- Trace each alert from an import, through a call site, to the input it handles. Name the file and line.
- A dev or test dependency is not zero. It can run in CI with secrets, so rank it low and keep it on the list.
- "Not reachable as far as traced" names what you searched. A dynamic import or a framework call is "unknown".

## Committed credentials

- Find candidates by pattern in a read-only clone. Print the file, the commit, the kind, the length and a hash. Never the value.
- Propose rotation to the owner first. Deleting the line leaves it in history, and removing it from history is an owner's decision, taken after rotation.
- A key identifier that matches a provider's format does not show that the secret is valid or live. Say which evidence you
  have and which you lack.
- Name the owner from `knowledge/exposure.md` only after you have matched the key to a repository or team.

## Patch plans

- One lockfile change that fixes several alerts ships as one change. Order by tier, then by effort.
- A major version bump names the code change it needs and the person who makes it.
- Each plan names its rollback: the previous version, and what to watch after the upgrade.

## Unknowns

- A source you could not read is "not checked" in the report. Zero is a claim that needs a successful read.
- An empty alert list under limited access is not a clean result until someone confirms the read access.
- Advisories change. Re-query each installed version each run, and say the date read.

## Sources

- FIRST, "Exploit Prediction Scoring System (EPSS)", first.org/epss, read 2026: a 0 to 1 probability of exploitation in the
  next 30 days, updated daily; it is not a measure of impact.
- FIRST, "Common Vulnerability Scoring System v4.0 Specification", 2023 (read as a snippet): the base score is supplemented
  by threat and environmental metrics. This file does not use CVSS scores as the tier.
- OWASP, "OWASP Top 10:2025", 2025 (secondary coverage, not the primary page): A03 Software Supply Chain Failures, A10
  Mishandling of Exceptional Conditions, and server-side request forgery folded into A01.
- CISA, "Known Exploited Vulnerabilities Catalog": the list used for "known exploited". Named here only; its inclusion
  criteria and any deadlines are not restated.
- GitHub Docs, "Secret scanning", 2025 (read as a snippet): replace the exposed credential first; rewriting history is
  often unnecessary once the credential is invalid.
- Team policy: `knowledge/patch-policy.md`, agreed by the owner. It is not an external source.
