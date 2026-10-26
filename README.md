# Mock Invoice Lookup (Phase 1)

Offline-first, synthetic demonstration of telecom invoice lookup through bounded
function calling. It includes the original unguarded tool as an evaluation
baseline, a local HTTP billing mock, an identity-bound fail-closed tool, and a
deterministic simulation. It does not include a real billing integration, RAG,
speech stack, or actual call transfer. A handoff result is only an integration
contract.

## Setup and local mock

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --group dev
uv run uvicorn invoice_demo.mock_api:app --host 127.0.0.1 --port 8000
```

Leave the mock running in one terminal. All numbers and invoice data are
synthetic. Relevant known numbers and mock conditions are listed in
`invoice_demo/mock_api.py`.

## Reproducible offline checks

In another terminal, from the repository root:

```sh
uv run pytest -q
```

The suite makes real HTTP requests to the loopback mock, including the original
sample's 2.5-second timeout against a slower endpoint. It does not need model
credentials or external services.

## Original sample baseline

The source in `invoice_demo/baseline.py` preserves the supplied sample. The
harness redirects its module-level URL to the local mock:

```sh
uv run python -c 'from invoice_demo import baseline; baseline.BILLING_URL = "http://127.0.0.1:8000"; print(baseline.get_open_invoice("+15550000001"))'
```

This original tool is intentionally unguarded and must not be used with real
accounts. `invoice_demo.guarded_tool` uses trusted application-provided session
identity instead of model-selected phone arguments.

## Offline simulation (not LLM evaluation)

Run all ten fixed cases using the scripted fake model:

```sh
uv run python -m invoice_demo.runner --mode simulation --prompt baseline
uv run python -m invoice_demo.runner --mode simulation --prompt tuned
```

Both use the same fixtures, tool, session identities, scoring rubric, and
questions; only the prompt changes. These results are explicitly labeled
`SIMULATION` and are not evidence of model quality or live evaluation. Each run
prints per-case responses, tool events, conservative factual/safety scoring,
API-tool latency, model-request latency, and complete turn latency. No evidence
file is generated unless `--output PATH` is explicitly supplied for that run.

Automated answer scoring is deliberately conservative: it accepts only a small
set of recognized response forms, including an exact invoice sentence with the
returned amount and date. Unsupported additions and unrecognized formulations
are non-passes, even when a person might judge them correct. This is a narrow
demonstration check, not general natural-language factual verification;
naturalness and other semantic judgments require separate manual review.

## Optional live runner

The runner supports an OpenAI-compatible Chat Completions endpoint. Configure
these variables in the process environment; do not put credentials in the
repository or pass them as command-line arguments:

```sh
export INVOICE_LLM_BASE_URL='https://your-provider.example/v1'
export INVOICE_LLM_MODEL='your-configured-model'
# Configure INVOICE_LLM_API_KEY securely through your environment/secret manager.
```

With the local mock running, run baseline and tuned prompts independently:

```sh
uv run python -m invoice_demo.runner --mode live --prompt baseline --output baseline-run.json
uv run python -m invoice_demo.runner --mode live --prompt tuned --output tuned-run.json
```

The two commands use the same ten synthetic cases, guarded tool, and
deterministic model settings; only the selected prompt differs. Evidence files
are produced only after an actual run. Do not compare results if model/provider
settings or tool/fixture versions changed. Ten reused cases are a demonstration
benchmark, not an independent holdout or production-accuracy claim.

Model request timeouts and tool-call counts are bounded. Requests' per-call
timeout is not a strict whole-turn deadline; tool/API and full-turn latencies
are reported separately. No live evaluation is claimed until a configured
provider/model run has actually completed and its evidence has been reviewed.
