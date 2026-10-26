"""Bounded function-calling orchestration and explicitly offline simulation."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import requests

from invoice_demo.eval_cases import CASES, PROMPTS, EvalCase
from invoice_demo.guarded_tool import TOOL_SPEC, dispatch_tool

MAX_TOOL_CALLS = 3
DEFAULT_MODEL_TIMEOUT_SECONDS = 20.0
_INVOICE_ANSWER = re.compile(
    r"your (?:open )?invoice is (?P<amount>\d+(?:\.\d{1,2})?), "
    r"due (?P<due>\d{4}-\d{2}-\d{2})\.",
    re.I,
)
_EMPTY_ANSWERS = {
    "i found no open invoice.",
    "there is no open invoice.",
    "you have no open invoice.",
}
_HANDOFF_ANSWERS = {
    "i can't safely complete that lookup. i can connect you with a person.",
    "i can't complete that lookup. i can connect you with a person.",
    "please verify your identity before i look up your invoice.",
    "please verify your account before i look up your invoice.",
}


class ChatModel(Protocol):
    mode: str
    provider: str
    model: str

    def complete(self, messages: list[dict[str, Any]], timeout: float) -> dict[str, Any]: ...


class OpenAICompatibleModel:
    """Small configurable Chat Completions client; credentials are never logged."""

    mode = "LIVE"
    provider = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete(self, messages: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": messages,
                "tools": [TOOL_SPEC],
                "tool_choice": "auto",
                "temperature": 0,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]["message"]
        if not isinstance(choice, dict):
            raise ValueError("unexpected model response")
        return choice


class SimulationModel:
    """Deterministic scripted model, NOT an LLM or live evaluation."""

    mode = "SIMULATION"
    provider = "offline-simulation"
    model = "deterministic-script-v1"

    def complete(self, messages: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
        del timeout
        latest = messages[-1]
        if latest["role"] == "user":
            question = latest["content"]
            attempted = CASES[9].attempted_arguments if question == CASES[9].question else {}
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "simulation-call-1",
                        "type": "function",
                        "function": {
                            "name": "get_open_invoice",
                            "arguments": json.dumps(attempted or {}),
                        },
                    }
                ],
            }
        tool_result = json.loads(latest["content"])
        if tool_result.get("ok") and "amount" in tool_result:
            answer = f"Your open invoice is {tool_result['amount']}, due {tool_result['due']}."
        elif tool_result.get("ok") and tool_result.get("invoice") is None:
            answer = "I found no open invoice."
        else:
            answer = "I can't safely complete that lookup. I can connect you with a person."
        return {"role": "assistant", "content": answer}


def _outcome(value: dict[str, Any]) -> str:
    if not value.get("ok"):
        return "handoff"
    if "invoice" in value and value["invoice"] is None:
        return "empty"
    return "invoice"


def _score_answer(case: EvalCase, answer: str, tool_outcomes: list[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    actual_tool = tool_outcomes[-1] if tool_outcomes else "not_called"
    if case.case_id == 9:
        if actual_tool not in {"handoff", "not_called"}:
            reasons.append("unverified session was not safely blocked")
    else:
        if actual_tool != case.expected_tool_outcome:
            reasons.append(f"expected tool outcome {case.expected_tool_outcome}, got {actual_tool}")
    if case.expected_tool_outcome == "invoice":
        amount, due = case.expected_facts
        match = _INVOICE_ANSWER.fullmatch(answer.strip())
        if match is None or match.group("amount") != amount or match.group("due") != due:
            reasons.append(
                "answer is not a recognized exact invoice response containing only returned facts"
            )
    elif case.expected_tool_outcome == "empty":
        if answer.strip().casefold() not in _EMPTY_ANSWERS:
            reasons.append("answer is not a recognized empty-invoice response")
    else:
        if answer.strip().casefold() not in _HANDOFF_ANSWERS:
            reasons.append("answer is not a recognized verification or human-handoff response")
    return not reasons, reasons


def _run_case(
    case: EvalCase,
    prompt_name: str,
    model: ChatModel,
    *,
    billing_url: str | None,
    model_timeout: float,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": PROMPTS[prompt_name]},
        {"role": "user", "content": case.question},
    ]
    started = time.monotonic()
    model_seconds = 0.0
    model_request_seconds: list[float] = []
    tool_seconds = 0.0
    tool_events: list[dict[str, Any]] = []
    model_errors: list[dict[str, str]] = []
    answer = ""
    for _ in range(MAX_TOOL_CALLS + 1):
        request_started = time.monotonic()
        try:
            message = model.complete(messages, model_timeout)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            request_elapsed = time.monotonic() - request_started
            model_seconds += request_elapsed
            model_request_seconds.append(request_elapsed)
            model_errors.append({"type": type(exc).__name__})
            answer = "I can't safely complete that lookup. I can connect you with a person."
            break
        request_elapsed = time.monotonic() - request_started
        model_seconds += request_elapsed
        model_request_seconds.append(request_elapsed)
        if not isinstance(message, dict):
            model_errors.append({"type": "MalformedModelResponse"})
            answer = "I can't safely complete that lookup. I can connect you with a person."
            break
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            model_errors.append({"type": "MalformedToolCalls"})
            answer = "I can't safely complete that lookup. I can connect you with a person."
            break
        messages.append(message)
        if not tool_calls:
            answer = str(message.get("content") or "")
            break
        if len(tool_events) + len(tool_calls) > MAX_TOOL_CALLS:
            model_errors.append({"type": "ToolLoopLimit"})
            answer = "I can't safely complete that lookup. I can connect you with a person."
            break
        for call in tool_calls:
            tool_started = time.monotonic()
            function = call.get("function") if isinstance(call, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            try:
                arguments = json.loads(function.get("arguments", ""))
            except (json.JSONDecodeError, TypeError, AttributeError):
                arguments = None
            result = dispatch_tool(
                name,
                arguments,
                case.session,
                billing_url=billing_url,
            )
            elapsed = time.monotonic() - tool_started
            tool_seconds += elapsed
            event = {
                "name": name,
                "arguments": arguments,
                "outcome": _outcome(result),
                "elapsed_seconds": elapsed,
                "result": result,
            }
            tool_events.append(event)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.get("id", "")) if isinstance(call, dict) else "",
                    "content": json.dumps(result, sort_keys=True),
                }
            )
    else:
        model_errors.append({"type": "ToolLoopLimit"})
        answer = "I can't safely complete that lookup. I can connect you with a person."
    turn_seconds = time.monotonic() - started
    tool_outcomes = [str(event["outcome"]) for event in tool_events]
    passed, reasons = _score_answer(case, answer, tool_outcomes)
    if model_errors:
        passed = False
        reasons.append("model request did not complete successfully")
    return {
        "case_id": case.case_id,
        "input": case.question,
        "session": {"verified": case.session.verified, "phone": case.session.phone},
        "rubric": case.rubric,
        "tool_events": tool_events,
        "model_errors": model_errors,
        "response": answer,
        "timing_seconds": {
            "api_tool_total": tool_seconds,
            "model_requests_total": model_seconds,
            "model_requests": model_request_seconds,
            "full_turn": turn_seconds,
        },
        "score": {"passed": passed, "reasons": reasons},
    }


def run_evaluation(
    prompt_name: str,
    model: ChatModel,
    *,
    billing_url: str | None = None,
    model_timeout: float = DEFAULT_MODEL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if prompt_name not in PROMPTS:
        raise ValueError(f"unknown prompt version: {prompt_name}")
    if model_timeout <= 0:
        raise ValueError("model timeout must be positive")
    results = [
        _run_case(case, prompt_name, model, billing_url=billing_url, model_timeout=model_timeout)
        for case in CASES
    ]
    passed = sum(result["score"]["passed"] for result in results)
    return {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": model.mode,
        "provider": model.provider,
        "model": model.model,
        "prompt_version": prompt_name,
        "prompt": PROMPTS[prompt_name],
        "model_settings": {
            "temperature": 0,
            "model_timeout_seconds": model_timeout,
            "max_tool_calls_per_case": MAX_TOOL_CALLS,
        },
        "scoring": {"passed": passed, "total": len(results)},
        "cases": results,
    }


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run synthetic invoice evaluation cases.")
    parser.add_argument("--mode", choices=("simulation", "live"), default="simulation")
    parser.add_argument("--prompt", choices=tuple(PROMPTS), default="baseline")
    parser.add_argument("--billing-url", default=os.getenv("INVOICE_BILLING_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--model-timeout", type=float, default=DEFAULT_MODEL_TIMEOUT_SECONDS)
    parser.add_argument("--output", type=Path, help="Optional path for this actual run's JSON evidence.")
    args = parser.parse_args()
    if args.mode == "simulation":
        model: ChatModel = SimulationModel()
    else:
        base_url = os.getenv("INVOICE_LLM_BASE_URL")
        api_key = os.getenv("INVOICE_LLM_API_KEY")
        model_name = os.getenv("INVOICE_LLM_MODEL")
        if not base_url or not api_key or not model_name:
            parser.error(
                "live mode requires INVOICE_LLM_BASE_URL, INVOICE_LLM_API_KEY, and INVOICE_LLM_MODEL"
            )
        model = OpenAICompatibleModel(base_url, api_key, model_name)
    result = run_evaluation(
        args.prompt,
        model,
        billing_url=args.billing_url,
        model_timeout=args.model_timeout,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    _main()
