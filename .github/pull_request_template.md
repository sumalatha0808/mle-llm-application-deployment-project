# Checklist for the LLM Application Deployment Project

<!-- If you are done with a topic mark the checkboxes with an `x` (like `[x]`) -->

- [ ] I read and understood the tasks.
- [ ] My FastAPI inference API exposes typed `/health`, `/ready`, and `/generate` routes, and I tested their input and output contracts.
- [ ] LLM provider calls are asynchronous, bounded, and replaceable in tests.
- [ ] Invalid requests fail before the LLM provider is called, and provider failures produce an explicit API response.
- [ ] The generation cache key includes every output-affecting parameter, and repeated identical requests to `/generate` return a cache hit.
- [ ] I configured a cache TTL, a limit on concurrent provider calls, and request timeouts.
- [ ] The Docker container builds locally, and `/health` returns 200 from the running container locally and on Render.
- [ ] I deployed the same container as a Free Render web service, and the LLM provider and Langfuse credentials are set in Render, not committed.
- [ ] Langfuse observations include nested cache and generation stages, token usage, cost, release, and a quality score.
- [ ] I followed one request's trace ID from the API response to its Langfuse observation tree and compared a cache miss with a cache hit.
- [ ] All shared evaluation cases in `data/evaluation/cases.json` run without editing their labels.
- [ ] I ran the load profile in `data/operations/load_profile.json` against the API.
- [ ] I wrote `reports/deployment_report.md` comparing the load results with all supplied objectives, with one trace-based failure analysis and an explicit deployment recommendation.
- [ ] I added tests and they pass from the documented environment.
- [ ] I documented how to run the project and answered the submission questions.

Bonus:

- [ ] I ran the service with a live LLM provider in addition to the offline dry provider.
- [ ] I created a `render.yaml` reference Blueprint that documents my Render web service settings.
- [ ] I built a Langfuse dashboard with latency, cost, and answer-quality widgets for my service.
- [ ] I documented any disagreements with the evaluation labels in the report.
