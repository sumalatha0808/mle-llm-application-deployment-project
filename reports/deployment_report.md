# Northstar Support Assistant – Deployment Report

**Recommendation: REVISE. Do not approve for production yet.** The service
meets all three objectives in the local runs measured below. The hosted
(Render) run, the container run and Langfuse Cloud delivery are still
**untested**. The live quality sample is only 6 answers per run, scored with
a keyword rubric. The full decision is at the end.

Code: `src/` · Tests: `tests/` · Image: `Dockerfile` · Blueprint:
`render.yaml` · Raw evidence: `reports/evidence/`.
Base revision `879cf20` plus uncommitted changes (`879cf20+wip` in traces) ·
policy version `84d477f800fd` · run date 2026-11-18.

## 1. Deployment Architecture And Configuration

```text
client ─► FastAPI (uvicorn, 1 worker)
            ├─ GET /health   liveness only, no config/provider access
            ├─ GET /ready    503 if config unusable (no provider call)
            └─ POST /generate (pydantic-validated → 422 before provider)
                 └─ GenerationService
                      prompt-preparation  topic routing + versioned policy prompt
                      cache-lookup        process-local TTL+LRU, single-flight
                      llm-generation      semaphore(MAX_CONCURRENT) + deadline
                           └─ Provider protocol: DryProvider | ChatProvider (LangChain)
                 └─ trace export (JSONL or Langfuse) off the event loop, 2 s cap
```

| Setting | Value | Why |
|---|---|---|
| Policy context | Versioned system prompt (4 policies + decline rule) | Covers every shared case; `POLICY_VERSION` is part of the cache key |
| Cache key | sha256 of provider, model, policy version, prompt, max_tokens, temperature, top_p | Hashes every input that affects the output |
| Cache | Process-local, TTL 300 s, max 1,024 entries (LRU), failures never cached | One instance, so a shared cache (Redis) is not justified yet |
| Single-flight | Concurrent identical misses share one provider call | Avoids duplicate calls during the cold-cache burst |
| Concurrency | `MAX_CONCURRENT_REQUESTS=4` provider slots; `QUEUE_TIMEOUT_SECONDS=10` → 503 `overloaded` | Bounds upstream rate and memory |
| Timeouts | `REQUEST_TIMEOUT_SECONDS=30` local / 20 on Render → 504; SDK `max_retries=0` | The service owns the deadline |
| Live model | Groq `openai/gpt-oss-20b`, `reasoning_effort=low`, `max_tokens=300` | See the failure analysis |
| Price used | $0.075 / 1M input tokens, $0.30 / 1M output tokens (`INPUT/OUTPUT_COST_PER_MTOK`) | Must be checked against Groq's pricing page; costs are estimates |
| Image | `python:3.13.17-slim`, `uv sync --locked --no-dev`, non-root, `HEALTHCHECK /health`, binds `$PORT` | Same image runs locally and on Render |
| Secrets | Runtime environment only (`docker run -e`, Render dashboard); `.env` is git- and docker-ignored | Nothing secret is baked into the image |
| Instances | **1 Render Free instance, 1 uvicorn worker, no autoscaling** | Free has no scaling. Measured load is about 42 RPS per process against a 5-in-flight workload. More workers would split the cache (see Q2) |

Render notes: Free instances sleep after idle time. The first request after
sleeping includes a cold start that will exceed 2 s, which this report does
not measure. Deploy with **New > Web Service > Docker, Free**, Dockerfile path
`./Dockerfile`, and secrets set in the dashboard. Keep `MODEL_PROVIDER=dry`
while the URL is shared.

## 2. Functional Results (Shared Cases, Labels Unedited)

Command: `uv run python -m src.evaluation [--live]`. The evaluation
calls the service directly, without HTTP. "Expected terms" is the cases'
whole-word check. "Quality" is the rubric in §4.

### Live – Groq gpt-oss-20b (final prompt, run 1 of 2; run 2 identical in outcome)

| Case | Success | Expected terms | Cache evidence | Quality | Latency ms | Tokens in/out (new) | Cost USD |
|---|---|---|---|---|---|---|---|
| refund_supported | 2/2 | pass | **verified**: hits=[False, True], provider_calls=1 | 1.00 | 615.5, 0.2 | 455/94 | 0.000062 |
| account_access | 1/1 | pass | N/A (single request) | 1.00 | 231.4 | 453/63 | 0.000053 |
| privacy_handoff | 1/1 | pass | N/A (single request) | 1.00 | 399.0 | 455/46 | 0.000048 |
| incident_status | 1/1 | pass | N/A (single request) | 1.00 | 214.6 | 454/49 | 0.000049 |
| unsupported_policy | 1/1 | pass | N/A (single request) | 1.00 | 116.7 | 453/34 | 0.000044 |

Cache evidence comes from the response flag **and** the provider call count:
the second refund request made no provider call. The hit's trace has no
`llm-generation` stage and records 0 new tokens.

### Dry provider (offline, `reports/evidence/eval_dry.md`)

5/5 pass, refund cache verified, quality 1.00. **This is not quality
evidence:** the dry answers were written from the same policy as the rubric.

### Earlier live run that exposed a rubric-vs-keyword gap

With `reasoning_effort=low` and prompt v1, `privacy_handoff` **passed the
keyword check** but replied "I cannot confirm this from approved policy…
refer the customer to the privacy team for a human review". That declines a
covered topic. The rubric scored it **0.40** (mean 0.88). I made the prompt
state explicitly that deletion requests are covered, and two reruns scored
1.00. Evidence: `eval_live_low_effort_prompt_v1.md`. A keyword pass is not
evidence of correct advice.

## 3. Load Results (Kept Separate From The Functional Cases)

Workload: `scripts/load_test.py`, 40 POST /generate attempts, 5 in flight,
three cycled prompts. **The workload is cache-heavy.** All three prompts are
out of policy, so the service declines them. p95 is computed by the probe as
the nearest-rank value over **all** attempts, failures included, measured as
end-to-end HTTP latency on the client. Each run uses a fresh process, so the
cache starts **cold**.

| Run | Environment | Command | Success | Error rate | p95 ms | Mean ms | Throughput rps | Provider calls |
|---|---|---|---|---|---|---|---|---|
| L1 dry | local uvicorn, Windows, dry | `load_test.py --url http://127.0.0.1:8000 --requests 40 --concurrency 5` | 40/40 | 0.00% | 33.6 | 18.9 | 60.0 | 3 (37 hits/shared) |
| L2 live **before fix** | local, Groq, effort default | same, port 8001 | 14/40 | **65.00%** | 2,057.9 | 718.0 | 6.3 | 11 (26 × 502) |
| L3 live (final) | local, Groq, `reasoning_effort=low` | same, port 8001 | 40/40 | 0.00% | 378.3 | 93.1 | 41.7 | 3 |
| L4 dry, no cache | local, dry with 400 ms simulated latency, TTL 0 | same, port 8002 | 40/40 | 0.00% | 452.5 | 428.8 | 11.0 | 24 |
| Hosted | Render Free | – | **untested** | **untested** | **untested** | – | – | – |
| Container | Docker, local | – | **untested** (Docker daemon not running) | | | | | |

L1 ran on the earlier policy prompt version. The dry answers and the cache
behavior are the same for both versions.

## 4. Tokens, Cost And Answer Quality

| Item | Value |
|---|---|
| Live functional run (6 requests, 5 provider calls) | 2,270 input / 286 output tokens (new), **$0.000256** |
| Live load L3 (40 requests, 3 provider calls) | 1,353 input / 90 output tokens, **$0.000128** (about $0.0000032 per request at a 92.5% cache/shared rate) |
| Pre-fix L2 | Each failed call was billed about 300 output tokens, but the trace recorded 0 (now fixed: `ProviderError` carries usage) |
| Mean quality, live final | **1.00**, n=6 successful answers per run, 2 runs; plus 40/40 = 1.00 in L3 (3 distinct answers) |

**Rubric** (`src/policy.py`): each prompt is routed to a policy topic.
The score is 0.7 × the share of required concepts present, plus 0.2 × the
share of supporting concepts, plus 0.1 if there is no violation. A violation
(declining a covered topic, or a promise or deadline on an unsupported one)
caps the score at 0.4. Failures get **no** score.

Limitations: the rubric is keyword-based. It is blind to negation and to
wrong advice phrased with the right words. The sample is tiny (5 distinct
prompts), and the topic router is a keyword heuristic. An LLM-judge or human
review on a larger labelled set is required before the quality objective
counts as met for production.

## 5. Trace-Based Failure Analysis (Run L2)

Trace `fe242c0e345b45ef848db27273c24d8b`
(`reports/evidence/failure_trace_before_fix.json`):

- Root: `status=error`, `error_type=ProviderError`, topic `unsupported`, 1,334 ms, `quality_score=null`. It is **not** counted as a quality sample.
- `prompt-preparation`: 0.1 ms, ok. `cache-lookup`: miss, 0 entries.
- `llm-generation`: **1,332 ms, status=error, queue_wait_ms=0.07**. Queueing was zero, so this was not application saturation.
- Server log: Groq returned **HTTP 200** for every call.

Diagnosis: the provider replied, but my boundary rejected the answer. A
direct reproduction (`finish_reason`, `usage`) showed gpt-oss-20b at the
default reasoning effort spending **298 of the 300 `max_tokens` on reasoning**.
It returned an empty answer with `finish_reason=length` for 2 of the 3 probe
prompts. Single-flight then passed each leader's error to its waiters. 11 provider
calls produced 26 failed requests (38 of 40 trace records intact: 9
leader errors and 15 waiter errors). The run also uncovered two
observability bugs:

1. **Interleaved JSONL lines**: concurrent export threads corrupted 2 of 40
   records. Fixed with a writer lock and a regression test.
2. **Hidden cost**: failed calls showed 0 tokens although they were billed.
   Fixed: traces now carry `error_detail` and the billed usage and cost.

Fix: set `reasoning_effort=low` (configurable with `REASONING_EFFORT`).
L3 then had 0% errors with about 30 output tokens per answer. The shared
evaluation cases did not catch this because their prompts happen to need
little reasoning. Coverage is a test-design gap.

## 6. Comparison With Service Objectives

| Objective | Target | Observed (run) | Result | Interpretation |
|---|---|---|---|---|
| p95 latency | ≤ 2,000 ms | 378 ms (L3 live), 34 ms (L1 dry), 2,058 ms (L2 pre-fix) | **Pass locally (L3)**; hosted **untested** | Cache-heavy, cold-start excluded; provider latency (≈300–600 ms) dominates misses |
| Error rate | ≤ 2% | 0/40 = 0.00% (L3, L1, L4); 26/40 = 65% (L2) | **Pass locally (L3)**; hosted **untested** | One failure in 40 would already fail this target |
| Mean quality | ≥ 0.8 | 1.00 (n=6, 2 runs, live) | **Pass, weak evidence** | Keyword rubric with a tiny sample. A prompt variant scored 0.88, with one answer at 0.40 |

Dry results are kept separate from live ones. Local results do not approve a
hosted rollout.

## 7. Three Prioritized Changes Before Production Approval

1. **Measure hosted and in-container.** Build the image, deploy to Render
   Free with the dry provider, and run the 40-request probe plus a cold-start
   probe after idle. Verify that Langfuse Cloud receives the root
   observation, 3 nested stages and the score for one synthetic trace ID.
   Until then, both latency and error rate on Render are unmeasured.
2. **Make quality evidence real.** Add 30+ labelled prompts with paraphrases,
   negations and adversarial "promise me" requests. Add an LLM-judge or human
   review alongside the rubric. Add a non-cached live load profile with
   distinct prompts to measure provider latency and 429 behavior.
3. **Harden the provider path.** Alert on `finish_reason=length` and on
   error-rate spikes per release. Add one bounded retry with jitter for 429
   and 5xx only. Keep the policy prompt version and reasoning effort as
   release-gated config, since both changed quality and errors in this
   report.

## Answers To The Project Questions

1. **Readiness vs. per-request failure.** `/ready` fails (503) for local,
   persistent problems: unknown provider, missing API key, invalid numeric
   config, or an unwritable trace directory. A request cannot succeed until
   someone fixes these, and readiness never calls the provider. Upstream
   timeouts (504), provider errors or truncated answers (502), and
   saturation (503 `overloaded`) affect only that request. They are
   transient, and failing readiness for them would take the whole instance
   out of rotation. `/health` reports liveness only.
2. **Process-local cache with N instances.** Each instance warms its own
   cache, so the hit rate falls to roughly 1/N of the shared-cache rate for
   cold keys. The same prompt can trigger up to N provider calls, one per
   instance, since single-flight only deduplicates inside a process. Memory
   is used N times (up to 1,024 entries each). With 1 Free instance this is
   acceptable. Scaling out means moving to a shared cache (for example Redis
   with the same key and TTL) or sticky routing.
3. **Masking and retention.** Raw prompts and outputs are not exported by
   default. Traces keep lengths, topic, parameters, usage, cost, latency,
   status and score. `TRACE_CONTENT_SAMPLE_RATE` keeps a sample of text, and
   that text is always masked first (emails, card, phone, IBAN and long
   numbers). The Langfuse client's `mask` hook masks everything a second
   time. Fields that need restricted retention: `prompt_masked`,
   `output_masked` (they may still contain names or free-text PII),
   `error_detail`, and any metadata derived from customer text. Credentials
   are never traced. Local JSONL demo files hold synthetic prompts only.
4. **Provider latency vs. saturation.** Each `llm-generation` stage records
   `queue_wait_ms`, the wait for a semaphore slot, separately from the
   provider call time. High provider time with near-zero queue wait points
   to the provider (L4: queue 0 ms, provider about 409 ms; L2's failure
   trace: queue 0.07 ms). Growing queue wait, 503 `overloaded`, or a p95 far
   above provider time points to application saturation.
5. **Strongest supporting result.** Run L3: 40/40 live successes, p95 378 ms,
   0% errors, with exactly 3 provider calls traced after the reasoning fix.
   Set against L2 (65% errors), it shows the failure mode is understood and
   fixed. It is also why the recommendation is *revise*, not *deploy*: L3 is
   local and cache-heavy.

## Decision

**Revise.** Measured result: L3, 0% errors and p95 378 ms locally after the
fix. Trace: `fe242c0e345b45ef848db27273c24d8b`, the reasoning-token
exhaustion that caused 65% errors in L2. **Next experiment that would change
the decision:** the same 40-request probe against the Render Free URL (dry
provider), plus a cold-start probe and confirmed Langfuse delivery of one
trace. If it shows ≤2% errors and p95 ≤2 s (warm), and a larger labelled
quality set stays ≥0.8, the recommendation becomes **deploy (single Free
instance, dry/limited live traffic)**.
