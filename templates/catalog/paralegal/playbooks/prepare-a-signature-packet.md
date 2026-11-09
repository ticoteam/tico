# Prepare a signature packet

Triggered by a request to prepare an NDA or standard agreement for signature on its task. Budget 10 minutes.
The outcome is a packet a human can send in one step, and, once signed, a filed copy and an index row. You
never send it and never sign it.

---

## 1. Confirm the final version

    hub task show <id>

The packet uses the exact version requested on the task: name the file and its date. Recheck any changed version against the requested terms; ask only if the intended version is unclear.

## 2. Build the packet

`reports/packets/<party>-<kind>.md`:
- the document to sign (the approved file), and anything it references that must travel with it;
- the signers from `state.md` setup answers, their titles and the order (the other side first or us first);
- the legal names of both parties exactly as written in the document;
- a short cover note to send with Tools when a person has turned mail sending on in Tico, three sentences, no legal commentary.

## 3. Ask for the send

Put the packet on the task. A requested packet goes out with its file, recipient and cover note only when a person has turned mail sending on in Tico and the task asks for it, using your Tools. Otherwise it stays a draft on the task. Record who actually sent it.

## 4. File the signed copy

When the fully signed copy comes back on the task: check every signature block is complete and the dates are
filled; list anything missing instead of filing. Then add a row to `knowledge/executed-index.md` (parties, kind,
date signed, confidentiality period and its end date, where the file lives), commit, and
`hub task update <id> --status done --note`.

## Done when
- The packet is saved in `reports/packets/`, published, and on the task, with the exact version named.
- Every signer, title and legal name is copied from the document, not guessed.
- Once signed, the copy is filed only when complete, and the task is closed with the row added.
