# Functional Evaluation (groq, model `openai/gpt-oss-20b`)

| Case | Success | Expected terms | Cache evidence | Quality | Latency ms | Tokens in/out (new) | Cost USD |
|---|---|---|---|---|---|---|---|
| refund_supported | 2/2 | pass | verified: hits=[False, True], provider_calls=1 | 1.00 | 696.2, 0.4 | 411/80 | 0.000055 |
| account_access | 1/1 | pass | N/A (single request) | 1.00 | 525.4 | 409/63 | 0.000050 |
| privacy_handoff | 1/1 | pass | N/A (single request) | 0.40 | 311.6 | 411/39 | 0.000043 |
| incident_status | 1/1 | pass | N/A (single request) | 1.00 | 187.1 | 410/54 | 0.000047 |
| unsupported_policy | 1/1 | pass | N/A (single request) | 1.00 | 302.8 | 409/34 | 0.000041 |

Cases passing terms: 5/5; mean quality 0.880 over 5 cases; total new cost 0.000235 USD.
