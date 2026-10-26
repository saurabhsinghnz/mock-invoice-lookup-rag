# Mock Invoice Lookup

A Python demo of looking up a telecom invoice through a function-calling tool.
It includes a local billing API, a guarded lookup tool, and a runner for ten
synthetic scenarios. Despite the repository name, there is no RAG implementation:
no document retrieval, embeddings, or vector database.

There is also no real billing integration, identity-verification service, voice
interface, or call transfer. All customer data is synthetic; a human handoff is
only a returned action, not an executed transfer.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).
Run these commands from the repository root.

```sh
uv sync --group dev

# Terminal 1: start the local billing API.
uv run uvicorn invoice_demo.mock_api:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
uv run python -m invoice_demo.runner --mode simulation --prompt baseline
```

This runs all ten cases using a scripted model and real HTTP requests to the
local API. No model credentials are needed. The runner prints JSON containing
responses, tool calls and results, pass/fail reasons, and tool, model-request,
and full-turn timings. Add `--output run.json` to also save the report.

To select the other prompt:

```sh
uv run python -m invoice_demo.runner --mode simulation --prompt tuned
```

The scripted model does not interpret either prompt, so this is a check of the
tool and runner, **not a measurement of prompt or LLM quality**. Both prompt
options use the same guarded tool; `baseline` and `tuned` select only the prompt.

## How lookup works

1. The runner supplies a question and a predefined `SessionIdentity` for each case.
2. The model requests `get_open_invoice` with no arguments. Account identity comes
   from the application-provided session, never from model-selected phone numbers.
3. The guarded tool checks verification and phone format, then calls
   `GET /invoices?phone=...&status=open` with a 2.5-second request timeout.
4. It validates the response shape, account identity, positive amount, and due
   date before returning one of three results:

| Result                  | Tool response                                          |
| ----------------------- | ------------------------------------------------------ |
| One valid invoice       | `{"ok": true, "amount": "42.50", "due": "2026-11-15"}` |
| No open invoice         | `{"ok": true, "invoice": null}`                        |
| Unsafe or failed lookup | `{"ok": false, "action": "handoff_to_human"}`          |

Unverified sessions, supplied tool arguments, multiple invoices, malformed data,
HTTP errors, and timeouts produce a handoff result. The runner allows at most
three tool calls per case.

The ten scenarios cover a valid invoice, an empty result, an unknown number,
multiple invoices, a missing amount, invalid JSON, a slow API, a server error,
an unverified session, and an attempted account override. Scoring accepts only
a small set of exact answer forms; other wording can fail even if it is correct.

## Code organization

| File                                                         | Purpose                                                                                                                  |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| [invoice_demo/mock_api.py](invoice_demo/mock_api.py)         | FastAPI billing mock: `/health`, `/invoices`, synthetic phone numbers, and failure fixtures.                             |
| [invoice_demo/guarded_tool.py](invoice_demo/guarded_tool.py) | Session identity, no-argument tool schema, dispatch checks, HTTP lookup, and invoice validation.                         |
| [invoice_demo/runner.py](invoice_demo/runner.py)             | Scripted and HTTP model clients, bounded tool loop, scoring, timing, JSON reports, and CLI.                              |
| [invoice_demo/eval_cases.py](invoice_demo/eval_cases.py)     | Ten shared scenarios, expected outcomes, answer rubrics, and baseline/tuned prompts.                                     |
| [tests/conftest.py](tests/conftest.py)                       | Starts and stops a local billing server for tests.                                                                       |
| [tests/test_invoice_tool.py](tests/test_invoice_tool.py)     | Guarded-tool tests, including identity checks, malformed responses, and HTTP timeouts.                                   |
| [tests/test_runner.py](tests/test_runner.py)                 | Model-client, simulation, scoring, model-error, and tool-loop-limit tests.                                               |
| [pyproject.toml](pyproject.toml), [uv.lock](uv.lock)         | Python requirements, dependencies, pytest configuration, and dependency lockfile.                                        |

## Tests

```sh
uv run pytest -q
```

Tests start their own local HTTP servers; no separately running mock, credentials,
or external services are needed.
