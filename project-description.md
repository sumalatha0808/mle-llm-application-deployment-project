# Project Description

Northstar has prototyped a support assistant that answers operational questions.
The prototype works on a developer laptop but has no stable deployment contract,
no capacity evidence, and no way to connect answer-quality regressions to a
release or provider call. Your team must turn it into a deployment candidate.

## Main Task

Build and evaluate an application that is reproducible, observable, and honest
about its operating limits. A reviewer should be able to deploy the container,
verify health and inference, inspect a request trace, reproduce evaluation
results, and decide whether the service meets its stated objectives.

Use the [deployment lesson](https://github.com/neuefische/mle-containerized-llm-deployment)
for the health, cache, and load checks, and the
[observability lesson](https://github.com/neuefische/mle-llm-service-observability)
for trace interpretation. Apply those checks to your own design. You choose
how to supply approved policy context, implement the provider boundary, and
measure quality. A short system prompt that states the Northstar support
policies covered by the [shared cases](data/evaluation/cases.json) is enough
policy context, as long as the assistant declines questions those policies do
not cover. Retrieval over policy documents is optional.

## Required Implementation

### 1. API And Provider Boundary

Expose typed health, readiness, and generation routes. Keep provider code behind
an interface that tests can replace. Invalid input must fail before provider
usage, while upstream failures must produce an explicit service response.

### 2. Cache And Capacity Controls

Hash every output-affecting generation parameter in the cache key. Add a TTL,
bound concurrent provider calls, configure timeouts, and document whether the
cache is process-local or shared across instances.

### 3. Reproducible Deployment

Build a locked Python 3.13 image that runs as the same service locally and as
a Free Render web service. Configure secrets outside the image, expose a lightweight health check,
and justify the selected instance count and scaling strategy.

### 4. LLM Observability

Record a root request observation plus nested cache, retrieval or preparation,
and generation stages. Include model, token usage, cost, latency, status,
environment, release, and a quality score. Define masking and sampling choices
for prompts and outputs.

Use the Langfuse exporter in the observability lesson's
[`recorder.py`](https://github.com/neuefische/mle-llm-service-observability/blob/main/src/observability_lab/recorder.py)
as your pattern. It shows the SDK calls for a root observation, nested spans, a
generation with usage and cost, and a score. Recording each trace right after
the request finishes, from the values your service measured while handling it,
is enough.

### 5. Evaluation And Report

Run every shared case and the supplied load profile. Submit
`reports/deployment_report.md` with:

- deployment architecture and configuration,
- functional result and cache-hit evidence per case,
- throughput, p95 latency, and error rate,
- token, cost, and answer-quality summaries,
- one trace-based failure analysis,
- comparison with every service objective,
- three prioritized changes before production approval.

## Suggested Milestones

### Milestone 1 - Reproducible Service

Start with one local process and a dry provider. Use
`refund_supported` from the [shared cases](data/evaluation/cases.json) as your
first smoke case: send the same prompt and parameters twice. For example,
against your local API on port 8000:

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
for attempt in 1 2; do
  curl -sS http://127.0.0.1:8000/generate \
    -H "Content-Type: application/json" \
    -d '{"prompt":"What evidence is required for a duplicate-payment refund?"}'
  printf '\n'
done
```

The health response confirms that HTTP works; readiness confirms usable
local configuration without calling the provider. Expect the first generation to
miss a fresh cache and the second to hit within the TTL. Check the response
or trace flag and the provider call count in your tests; lower latency alone
does not prove reuse.

Repeat the same requests against the built container. Include tests for a
blank prompt, timeout, and upstream failure. Show which failures reach the
provider and which are rejected before it is called.

**Checkpoint:** a reviewer can run your tests without API keys and reproduce
both the normal response and a negative case.

### Milestone 2 - Hosted And Observable

Deploy the same container as a Free Render web service: choose **New > Web Service** and select **Free** compute.
Select **Docker**, leave **Root Directory** blank, and set **Dockerfile Path**
relative to the repository root (for example, `solution/Dockerfile` for the
reference solution).
The Blueprint creation flow asks for a payment method even on the Free
instance plan, so do not deploy from a Blueprint. Keep secrets in the platform
configuration. The service is public, so anyone who finds its URL can use your
provider quota: use sample prompts, keep live tests brief, and only share the
URL while it runs with the dry provider. Record the service URL, deployed
revision, model, environment, instance count, and limits so the result can be
reproduced.

**Bonus:** add a `render.yaml` reference Blueprint that documents your web
service settings.

Use a synthetic request and follow its trace ID from the API result to the
root observation and its stages. Compare a miss with a hit: the hit should
have cache evidence but no new provider tokens or generation span. Verify
observation delivery separately from the HTTP response; a 200 does not prove
that telemetry arrived. Show one failure trace without counting it as an
answer-quality sample.

**Bonus:** build a Langfuse dashboard with latency, cost, and answer-quality
widgets for your service.

**Checkpoint:** demonstrate a working hosted route and a correlated
observation tree without exposing credentials or raw customer text. State
what remains unverified if a live provider or cloud export was unavailable.

### Milestone 3 - Evidence-Based Decision

Run all five functional cases, including the repeated refund request and
the unsupported-policy case. Keep their results separate from the
[load profile](data/operations/load_profile.json): 40 HTTP attempts with up
to five in flight. The functional cases check behavior; the load run checks
the service under the stated workload.

For a local smoke run, start your API on port 8000 with the dry provider.
From the repository root, run the load probe against the project's API:

```bash
uv run python scripts/load_test.py --url http://127.0.0.1:8000 \
  --requests 40 --concurrency 5
```

The probe expects `POST /generate` to accept a JSON object with a `prompt`.
Expect 40 successes for a healthy dry provider. It cycles three prompts and checks
HTTP status, not the answer, so label the workload as cache-heavy and keep it
separate from the five functional cases. Record whether the cache started
warm or cold. For hosted evidence, repeat the run against your Render URL
only while the service runs with the dry provider, so the probe uses no
provider quota. Otherwise, mark the hosted load result as **untested** in the
report.

| Objective | Compare it with |
|---|---|
| p95 latency at most 2,000 ms | End-to-end HTTP latency for the stated run. Declare whether failures are included and how p95 is computed. |
| Error rate at most 2% | Failed HTTP or transport attempts divided by all attempts. One failure in 40 is 2.5%, so it misses this target. |
| Mean quality at least 0.8 | Scores from successful answers evaluated with your stated rubric. Report the sample count and rubric limitations. |

For each result in the report, record the command, workload, environment,
observed value, target, and interpretation. Separate dry, live, and untested
results. An unmeasured objective is not a pass, and a keyword check is not
evidence of correct policy advice.

For example, a local run with 39/40 successes and p95 of 800 ms passes the
latency objective for that run but fails the error objective (2.5%).
If you did not score the answers, mark quality as **not measured**. Do not
use these local results to approve a hosted rollout.

**Checkpoint:** make a deploy, revise, or stop recommendation. Tie it to one
measured result, one trace, and the next experiment that would change your
decision.

## Minimum Acceptance Checklist

- [ ] API input and output contracts are typed and tested.
- [ ] Provider calls are asynchronous, bounded, and replaceable in tests.
- [ ] Repeated identical requests demonstrate a cache hit.
- [ ] `/health` returns 200 from the running container, locally and on Render.
- [ ] The same container runs as a Free Render web service.
- [ ] Render secrets are not committed.
- [ ] Observations include nested stages, usage, cost, release, and quality.
- [ ] All shared evaluation cases run without editing their labels.
- [ ] Load evidence is compared with all supplied objectives.
- [ ] The final report makes an explicit deployment recommendation.

## Answer The Following Questions

1. Which failure should make readiness fail, and which should affect only one
   generation request?
2. How do cache-hit rate, duplicate provider calls and memory usage change when
   each service instance has its own process-local cache?
3. Which trace fields require masking or restricted retention?
4. What evidence distinguishes provider latency from application saturation?
5. Which measured result most strongly supports your final recommendation?
