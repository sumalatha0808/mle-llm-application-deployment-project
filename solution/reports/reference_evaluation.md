# Reference Deployment Evaluation

## Decision

The deterministic reference passes 5/5 cases: each must match
all expected terms in every response and verify caching when repeated.
It is suitable as a deployment scaffold, not as evidence that a live
provider meets production latency or answer-quality objectives.

## Case Results

| Case | Expected Terms Matched | Cache Verified | Mean Heuristic Score | Mean Latency ms |
|---|---:|---:|---:|---:|
| refund_supported | True | True | 0.90 | 0.03 |
| account_access | True | N/A | 0.90 | 0.02 |
| privacy_handoff | True | N/A | 0.90 | 0.02 |
| incident_status | True | N/A | 0.90 | 0.01 |
| unsupported_policy | True | N/A | 0.90 | 0.01 |

N/A means the case has no repeated request. Cache verification requires an
initial miss followed by hits; mean latency includes every repeat, not just the
last cached response. Neither these timings nor the keyword scores prove a live SLO.
Expected-term matching uses whole words or phrases; it does not detect negation
or establish that an answer is correct.

## Service Health And LLM Signals

- Health and readiness contracts are separate.
- Traces distinguish cache hits from generation and count no new provider tokens on hits.
- The quality score is a keyword heuristic, not a factuality or policy-compliance metric.
- Dry latency is a smoke metric and must not be treated as live capacity.

## Required Next Experiments

1. Run the same cases against the configured live provider.
2. Run the supplied load profile against the deployed Render URL.
3. Calibrate quality scoring with human-reviewed responses.
