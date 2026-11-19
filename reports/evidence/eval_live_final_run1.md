# Functional Evaluation (groq, model `openai/gpt-oss-20b`)

| Case | Success | Expected terms | Cache evidence | Quality | Latency ms | Tokens in/out (new) | Cost USD |
|---|---|---|---|---|---|---|---|
| refund_supported | 2/2 | pass | verified: hits=[False, True], provider_calls=1 | 1.00 | 615.5, 0.2 | 455/94 | 0.000062 |
| account_access | 1/1 | pass | N/A (single request) | 1.00 | 231.4 | 453/63 | 0.000053 |
| privacy_handoff | 1/1 | pass | N/A (single request) | 1.00 | 399.0 | 455/46 | 0.000048 |
| incident_status | 1/1 | pass | N/A (single request) | 1.00 | 214.6 | 454/49 | 0.000049 |
| unsupported_policy | 1/1 | pass | N/A (single request) | 1.00 | 116.7 | 453/34 | 0.000044 |

Cases passing terms: 5/5; mean quality 1.000 over 5 cases; total new cost 0.000256 USD.
