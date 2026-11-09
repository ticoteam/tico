# Counsel eval results

Run 2026-10-31: `python3 evals/counsel/run.py --url http://127.0.0.1:18720 --owner-token <owner token>`

A local install, Counsel on pi + kimi-k3 (high effort). Counsel stops at about 10 suggestions a document, so it often picks other valid obligations than the labelled ones (counted as extra). One turn (github-aup) failed partway; its suggestions written before that are scored.

| document | expected | found | missed | extra | wrong scope | quote not in source | suggested |
|---|---|---|---|---|---|---|---|
| anthropic-aup | 4 | 3 | 1 | 7 | 0 | 0 | 10 |
| apple-app-review | 5 | 1 | 4 | 9 | 0 | 0 | 10 |
| aws-customer-agreement | 3 | 1 | 2 | 9 | 0 | 0 | 10 |
| common-paper-csa | 5 | 5 | 0 | 5 | 0 | 0 | 10 |
| common-paper-dpa | 3 | 0 | 3 | 10 | 0 | 0 | 10 |
| commonpaper-mutual-nda | 3 | 1 | 2 | 9 | 0 | 0 | 10 |
| ecfr-coppa-312 | 3 | 0 | 3 | 10 | 0 | 0 | 10 |
| ecfr-tcpa-64-1200 | 3 | 1 | 2 | 9 | 0 | 0 | 10 |
| ftc-can-spam | 6 | 6 | 0 | 4 | 0 | 0 | 10 |
| ftc-health-breach | 3 | 3 | 0 | 2 | 0 | 0 | 5 |
| github-aup | 4 | 2 | 2 | 8 | 0 | 0 | 10 |
| google-ads-policies | 4 | 2 | 2 | 8 | 0 | 0 | 10 |
| hubspot-terms | 3 | 1 | 2 | 9 | 0 | 0 | 10 |
| meta-platform-terms | 5 | 2 | 3 | 8 | 0 | 0 | 10 |
| slack-api-terms | 5 | 3 | 2 | 7 | 0 | 0 | 10 |
| stripe-restricted | 4 | 1 | 3 | 9 | 0 | 0 | 10 |
| stripe-ssa | 4 | 2 | 2 | 8 | 0 | 0 | 10 |
| twilio-a2p-10dlc | 3 | 3 | 0 | 7 | 0 | 0 | 10 |
| twilio-aup | 3 | 2 | 1 | 8 | 0 | 0 | 10 |
| twilio-messaging-policy | 7 | 2 | 5 | 8 | 0 | 0 | 10 |
| whatsapp-business-policy | 6 | 4 | 2 | 6 | 0 | 0 | 10 |
| **total** | 86 | 45 | 41 | 160 | 0 | 0 | 205 |

Recall (found / expected): 0.52. Quotes not in the source: 0.

Wrong scope counts suggestions that match a not-ours sentence (a vendor's own duty). Extra counts suggestions that match no label.
