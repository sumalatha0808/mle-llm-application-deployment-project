# Functional Evaluation (dry, model `dry-northstar-v1`)

| Case | Success | Expected terms | Cache evidence | Quality | Latency ms | Tokens in/out (new) | Cost USD |
|---|---|---|---|---|---|---|---|
| refund_supported | 2/2 | pass | verified: hits=[False, True], provider_calls=1 | 1.00 | 0.8, 0.3 | 454/49 | 0.000000 |
| account_access | 1/1 | pass | N/A (single request) | 1.00 | 0.8 | 450/42 | 0.000000 |
| privacy_handoff | 1/1 | pass | N/A (single request) | 1.00 | 0.7 | 451/32 | 0.000000 |
| incident_status | 1/1 | pass | N/A (single request) | 1.00 | 0.4 | 453/33 | 0.000000 |
| unsupported_policy | 1/1 | pass | N/A (single request) | 1.00 | 0.6 | 452/22 | 0.000000 |

Cases passing terms: 5/5; mean quality 1.000 over 5 cases; total new cost 0.000000 USD.
