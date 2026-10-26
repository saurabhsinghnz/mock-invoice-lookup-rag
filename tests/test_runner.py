import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from invoice_demo.eval_cases import CASES, PROMPTS
from invoice_demo.runner import OpenAICompatibleModel, SimulationModel, _score_answer, run_evaluation


def test_openai_compatible_client_sends_configured_key_and_parses_local_response():
    observed = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed["path"] = self.path
            observed["authorization"] = self.headers.get("Authorization")
            observed["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"choices":[{"message":{"role":"assistant","content":"local stub"}}]}'
            )

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = OpenAICompatibleModel(
            f"http://127.0.0.1:{server.server_port}/v1",
            "synthetic-test-key-only",
            "stub-model",
        )
        messages = [{"role": "user", "content": "synthetic question"}]
        assert model.complete(messages, timeout=1) == {
            "role": "assistant",
            "content": "local stub",
        }
        assert observed["path"] == "/v1/chat/completions"
        assert observed["authorization"] == "Bearer synthetic-test-key-only"
        import json

        request_body = json.loads(observed["body"])
        assert request_body["model"] == "stub-model"
        assert request_body["messages"] == messages
        assert request_body["tool_choice"] == "auto"
        assert request_body["temperature"] == 0
        assert request_body["tools"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_openai_compatible_client_raises_for_local_http_error():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = OpenAICompatibleModel(
            f"http://127.0.0.1:{server.server_port}/v1",
            "synthetic-test-key-only",
            "stub-model",
        )
        try:
            model.complete([], timeout=1)
        except requests.HTTPError as exc:
            assert exc.response.status_code == 401
        else:
            raise AssertionError("HTTP error response must raise")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_offline_simulation_runs_identical_cases_for_each_prompt(billing_url):
    baseline = run_evaluation("baseline", SimulationModel(), billing_url=billing_url)
    tuned = run_evaluation("tuned", SimulationModel(), billing_url=billing_url)

    assert baseline["mode"] == tuned["mode"] == "SIMULATION"
    assert baseline["scoring"]["total"] == tuned["scoring"]["total"] == len(CASES) == 10
    assert baseline["prompt"] == PROMPTS["baseline"]
    assert tuned["prompt"] == PROMPTS["tuned"]
    assert [case["case_id"] for case in baseline["cases"]] == [
        case["case_id"] for case in tuned["cases"]
    ]
    assert baseline["scoring"]["passed"] == tuned["scoring"]["passed"] == 10, [
        (case["case_id"], case["score"]) for case in baseline["cases"]
    ]
    assert all(case["timing_seconds"]["full_turn"] >= case["timing_seconds"]["api_tool_total"] for case in baseline["cases"])
    assert baseline["cases"][9]["tool_events"][0]["outcome"] == "handoff"
    assert baseline["cases"][9]["tool_events"][0]["result"] == {
        "ok": False,
        "action": "handoff_to_human",
    }


def test_simulation_is_scripted_and_never_labeled_live(billing_url):
    result = run_evaluation("baseline", SimulationModel(), billing_url=billing_url)
    assert result["provider"] == "offline-simulation"
    assert result["mode"] == "SIMULATION"
    assert result["model"] == "deterministic-script-v1"


def test_conservative_rubric_rejects_invented_facts_and_false_empty_claims():
    assert not _score_answer(CASES[0], "Your bill is USD 42.50 due 2026-11-15.", ["invoice"])[0]
    assert not _score_answer(
        CASES[0], "Your invoice is 42.50 due 2026-11-15 tomorrow.", ["invoice"]
    )[0]
    assert not _score_answer(
        CASES[2], "Your account does not exist; no open invoice.", ["empty"]
    )[0]
    assert not _score_answer(
        CASES[4], "I will connect you with a person after your $10 bill.", ["handoff"]
    )[0]
    assert not _score_answer(
        CASES[4], "I will connect you with a person in two days.", ["handoff"]
    )[0]
    assert not _score_answer(
        CASES[9], "Please contact a person at +15550000010.", ["handoff"]
    )[0]


def test_conservative_rubric_requires_wholly_recognized_safe_answers():
    assert not _score_answer(
        CASES[0],
        "Your open invoice is 42.50, due 2026-11-15. Late fees are waived.",
        ["invoice"],
    )[0]
    assert not _score_answer(
        CASES[0],
        "Your open invoice is 42.50, due 2026-11-15. Your account is in good standing.",
        ["invoice"],
    )[0]
    assert not _score_answer(
        CASES[2],
        "I found no open invoice. Your account is current.",
        ["empty"],
    )[0]
    assert not _score_answer(CASES[4], "I need help.", ["handoff"])[0]
    assert not _score_answer(CASES[4], "I am unable to help.", ["handoff"])[0]
    assert not _score_answer(
        CASES[4],
        "I can connect you with a person in two days.",
        ["handoff"],
    )[0]
    assert _score_answer(
        CASES[0], "Your open invoice is 42.50, due 2026-11-15.", ["invoice"]
    )[0]
    assert _score_answer(CASES[2], "I found no open invoice.", ["empty"])[0]
    assert _score_answer(
        CASES[4],
        "I can't safely complete that lookup. I can connect you with a person.",
        ["handoff"],
    )[0]
    assert _score_answer(
        CASES[8],
        "Please verify your identity before I look up your invoice.",
        ["handoff"],
    )[0]


def test_model_timeouts_are_recorded_and_fail_closed(billing_url):
    class TimedOutModel:
        mode = "TEST"
        provider = "test-provider"
        model = "test-model"

        def complete(self, messages, timeout):
            assert timeout == 0.01
            raise requests.Timeout

    result = run_evaluation(
        "baseline", TimedOutModel(), billing_url=billing_url, model_timeout=0.01
    )
    assert result["mode"] == "TEST"
    assert all(case["model_errors"] == [{"type": "Timeout"}] for case in result["cases"])
    assert all(case["tool_events"] == [] for case in result["cases"])
    assert result["scoring"]["passed"] == 0


def test_tool_loop_has_a_hard_call_limit(billing_url):
    class RepeatingModel:
        mode = "TEST"
        provider = "test-provider"
        model = "test-model"

        def complete(self, messages, timeout):
            if messages[-1]["role"] == "user" or messages[-1]["role"] == "tool":
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "repeat",
                            "type": "function",
                            "function": {"name": "get_open_invoice", "arguments": "{}"},
                        }
                    ],
                }
            raise AssertionError("bounded runner should stop before another request")

    result = run_evaluation(
        "baseline", RepeatingModel(), billing_url=billing_url, model_timeout=0.1
    )
    first_case = result["cases"][0]
    assert len(first_case["tool_events"]) == 3
    assert len(first_case["timing_seconds"]["model_requests"]) == 4
