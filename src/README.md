# Northstar Support Assistant (`src/`)

This is the project implementation. `solution/` is the course reference and
is left unchanged. Results are in
[reports/deployment_report.md](../reports/deployment_report.md).

| Module | Role |
|---|---|
| `config.py` | Settings read from the environment (`Settings.from_env()`) |
| `models.py` | Typed request, response, error and trace contracts |
| `policy.py` | Versioned system prompt, topic routing, quality rubric |
| `provider.py` | `Provider` protocol, `DryProvider`, `ChatProvider` (LangChain), `ProviderError`/`ProviderTimeout` |
| `cache.py` | Parameter-aware cache key, process-local TTL+LRU cache, single-flight |
| `service.py` | Stage timing, bounded and timed provider calls, trace building |
| `tracing.py` | Masking, sampling, JSONL and Langfuse exporters |
| `app.py` | `/health`, `/ready`, `/generate` |
| `evaluation.py` | Runs every shared case → `reports/generated/` |

## Run (from the repository root, Git Bash)

```bash
uv run pytest tests                                   # no API keys needed
uv run python -m src.evaluation                 # dry, offline
MODEL_PROVIDER=groq uv run python -m src.evaluation --live

MODEL_PROVIDER=dry uv run uvicorn src.app:app --port 8000
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
curl -sS http://127.0.0.1:8000/generate -H "Content-Type: application/json" \
  -d '{"prompt":"What evidence is required for a duplicate-payment refund?"}'
uv run python scripts/load_test.py --url http://127.0.0.1:8000 --requests 40 --concurrency 5
```

## Container

```bash
docker build -t northstar .
docker run --rm -p 127.0.0.1:8000:8000 -e MODEL_PROVIDER=dry northstar
# live: add  -e MODEL_PROVIDER=groq -e GROQ_API_KEY=...  (never bake keys into the image)
```

## Render (Free)

**New > Web Service** → this repository → **Docker**, Root Directory blank,
Dockerfile Path `./Dockerfile`, **Free** compute, health check path
`/health`. Copy the environment variables from `render.yaml` and set secrets
(`GROQ_API_KEY`, `LANGFUSE_*`) in the dashboard. Render injects `PORT`.
Keep `MODEL_PROVIDER=dry` while the URL is shared.

## Error Contract

| Status | `error` | Reached provider? |
|---|---|---|
| 422 | (FastAPI validation) | No |
| 503 | `not_ready` / `overloaded` | No / queued but no slot |
| 504 | `upstream_timeout` | Yes |
| 502 | `upstream_error` | Yes |

Every 5xx body includes the `trace_id`.
