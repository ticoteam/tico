# IT practice

Reference facts behind the checks in this bot's skills. Each fact names its source. The team's own rules live in
`knowledge/tools.md` and `knowledge/checklists/`, and where they differ from a reference, the team's rule applies and the
difference is written down.

## Passwords

- A password used on its own is at least 15 characters (NIST SP 800-63B-4, 2025). The 15 is for a password used alone;
  check the text before you quote a minimum for a password used with a second factor.
- Accept at least 64 characters so that passphrases work (same source, a "should").
- No composition rules, such as "one symbol and one capital". Length and a blocklist do the work instead.
- No forced change on a timer. Change when there is a sign of compromise (same source).
- Screen new passwords against a blocklist of common and breached values (same source).

## Access reviews and removal

- Review service accounts at least quarterly, and user access at least annually (CIS Controls v8, 2021, safeguards 5.5
  and 6.8). Read from a secondary copy of the control list, not the official text. Check the official wording before you
  quote the cadence.
- Revoke access through a written process, and disable accounts rather than delete them so the record stays (CIS Controls
  v8, safeguard 6.2; same caveat).
- CIS Controls v8 safeguard 6.2 says to disable accounts immediately upon termination, rights revocation or role change.
  That is the reference bar. Check the wording at source before you quote it:
  [CIS requirement](https://www.cisecurity.org/controls/cis-controls-navigator/v8). The team's last-day rule comes from its
  own setup answers. Where the two differ, the team's rule applies and the difference is written down.

## Devices

- A laptop with an unencrypted disk is a finding on the weekly page. Encryption is the first check on a joiner's device
  (team rule, no external source).
- Two failed fix attempts on a device problem mean a person looks at it (team rule, no external source).

## Sources

- 2026-11-01: NIST SP 800-63B-4, Digital Identity Guidelines: Authentication and Lifecycle Management (2025), primary text
  read for the password rules. The month of publication is from secondary sources only.
- 2026-11-01: CIS Controls v8 (2021), safeguards 5.5, 6.2 and 6.8, from a secondary copy; official wording not yet checked.
