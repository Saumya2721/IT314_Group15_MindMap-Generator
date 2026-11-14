# S2-L13: Mind map evaluation harness

`benchmarks.json` contains 12 **curated text fixtures** spanning short topics, longer structured prompts, PDF-derived technical excerpts, and multi-section notes. PDF categories are **PDF-extracted-text examples, not actual PDF files**. Their word counts describe the *provided fixture* (they do not simulate full 20-page documents).

Run unit tests from `llm/`:

```bash
uv run pytest tests/test_evaluation_harness.py
uv run ruff check tests/test_evaluation_harness.py tests/evaluation/harness.py
uv run ruff format --check tests/test_evaluation_harness.py tests/evaluation/harness.py
```

To measure real generation quality, collect the **actual** output/timing for each fixture through the real pipeline and save it as a JSON list, for example:

```json
[{"seed_id":"short_01","duration_seconds":2.6,"context_count":1,"nodes":[{"id":"root","label":"Photosynthesis","description":"Energy conversion","sources":["ctx:1"]}],"edges":[]}]
```

Then run:

```bash
uv run python tests/evaluation/harness.py --results real_outputs.json --output first_report.json
uv run python tests/evaluation/harness.py --results improved_outputs.json --baseline first_report.json --output comparison_report.json
```

`duration_seconds` must be **end-to-end measured generation time**, not fabricated. Comparison reports show metric deltas to quantify prompt/pipeline changes. A one-node example above demonstrates the JSON format only and is **not** a real benchmark result.

Metrics: node count, graph max depth (number of edges from root), description completeness fraction, source-citation coverage proxy, valid context-reference proxy (only with `context_count`), shape/schema validation via the existing LLM validator, and latency. Source citation coverage is a **proxy**, not factual entailment. `ctx:n` indices only show references are within the supplied context range.

NFR-01 is `<30s` for **95%** of quick/balanced requests on approximately 10,000 words. NFR-02 is `<180s` for detailed requests on equivalent inputs. Short fixtures are diagnostic only; to claim NFR compliance create actual ~10,000-word evaluation cases with real measurements and multiple repeated trials. The small bundled dataset **does not prove NFR compliance**.

The harness does not make provider calls itself; integrate it with saved real pipeline outputs, avoiding API credentials in CI. CI unit tests test metric correctness, not model output quality. Do not commit real outputs that contain private document text or credentials.

### Optional real pipeline run

With LLM/Pinecone credentials configured **locally** (never commit `.env`), the harness can run the actual generation graph. This incurs external API costs. For a small trial, from `llm/`:

```bash
uv run python tests/evaluation/live_runner.py --limit 2 --output real_outputs.json
uv run python tests/evaluation/harness.py --results real_outputs.json --output first_report.json
```

For a complete run, remove `--limit 2`; rerun after a prompt/pipeline change and compare reports with `--baseline`. Each case uses its own namespace to prevent mixing retrieval results. PDF fixtures are real small PDFs and are parsed before ingestion. These fixtures are not a realistic 20-page NFR performance workload; see NFR notes above.
