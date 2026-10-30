# Counsel eval results

No scored run yet. The corpus and labels are checked; the live run needs a dev install with a Counsel bot and an owner
API token (Settings > Computers).

- Self-check (`python3 evals/counsel/run.py --self-check`): 21 documents, 86 expected obligations, 2 not-ours quotes;
  every quote is in its document.
- Scoring was checked with synthetic suggestion files only (every label echoed back: recall 1.00; a fabricated quote is
  counted as not in source; a not-ours sentence is counted as wrong scope). Those numbers are not model results.

Live run, once a dev install is up:

    python3 evals/counsel/run.py --url http://127.0.0.1:18300 --owner-token <owner API token>

Documents not in the corpus: the municipal short-term-rental ordinance and the HOA CC&R sample (no public copy could be
fetched; a stand-in was not substituted). Documents with `"truncated": true` in their JSON were cut at 60,000 characters.
