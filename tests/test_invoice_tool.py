from __future__ import annotations

from unittest.mock import Mock

import requests

from invoice_demo import tool
from invoice_demo.eval_cases import CASES
from invoice_demo.tool import SessionIdentity, dispatch_tool, get_open_invoice
from invoice_demo.mock_api import (
    BAD_JSON_PHONE,
    BAD_SHAPE_PHONE,
    EMPTY_PHONE,
    ERROR_PHONE,
    INVALID_PHONE,
    MISSING_PHONE,
    MULTIPLE_PHONE,
    OTHER_PHONE,
    PRIMARY_PHONE,
    STALL_PHONE,
)


def test_tool_distinguishes_invoice_empty_and_failure(billing_url):
    assert get_open_invoice(SessionIdentity(True, PRIMARY_PHONE), billing_url=billing_url) == {
        "ok": True,
        "amount": "42.50",
        "due": "2026-11-15",
    }
    assert get_open_invoice(SessionIdentity(True, EMPTY_PHONE), billing_url=billing_url) == {
        "ok": True,
        "invoice": None,
    }
    assert get_open_invoice(SessionIdentity(True, ERROR_PHONE), billing_url=billing_url) == {
        "ok": False,
        "action": "handoff_to_human",
    }


def test_ambiguous_missing_invalid_and_unexpected_http_payloads_fail_closed(billing_url):
    for phone in (MULTIPLE_PHONE, MISSING_PHONE, INVALID_PHONE, BAD_JSON_PHONE, BAD_SHAPE_PHONE):
        assert get_open_invoice(SessionIdentity(True, phone), billing_url=billing_url) == {
            "ok": False,
            "action": "handoff_to_human",
        }, phone


def test_model_arguments_cannot_choose_an_account_or_supply_a_phone(monkeypatch):
    def must_not_query(*args, **kwargs):
        raise AssertionError("unauthorized dispatch must not call billing API")

    monkeypatch.setattr(tool.requests, "get", must_not_query)
    assert dispatch_tool(
        "get_open_invoice",
        {"phone": OTHER_PHONE},
        SessionIdentity(True, PRIMARY_PHONE),
    ) == {"ok": False, "action": "handoff_to_human"}
    assert dispatch_tool(
        "get_open_invoice",
        {},
        SessionIdentity(False, PRIMARY_PHONE),
    ) == {"ok": False, "action": "handoff_to_human"}
    assert dispatch_tool(
        "get_open_invoice",
        {},
        SessionIdentity(True, None),
    ) == {"ok": False, "action": "handoff_to_human"}


def test_api_response_identity_must_match_trusted_session(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "items": [{"phone": OTHER_PHONE, "amount": "999.99", "due_date": "2026-12-31"}]
    }
    monkeypatch.setattr(requests, "get", Mock(return_value=response))
    assert get_open_invoice(
        SessionIdentity(True, PRIMARY_PHONE), billing_url="http://mock"
    ) == {"ok": False, "action": "handoff_to_human"}


def test_invoice_field_validation_rejects_invalid_dates_and_amounts(monkeypatch):
    for amount, due_date in (
        ("-1", "2026-11-15"),
        ("NaN", "2026-11-15"),
        ("42.500", "2026-11-15"),
        ("42.50", "2026-02-30"),
        ("42.50", None),
    ):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "items": [{"phone": PRIMARY_PHONE, "amount": amount, "due_date": due_date}]
        }
        monkeypatch.setattr(requests, "get", Mock(return_value=response))
        assert get_open_invoice(
            SessionIdentity(True, PRIMARY_PHONE), billing_url="http://mock"
        ) == {"ok": False, "action": "handoff_to_human"}


def test_request_exception_returns_handoff(monkeypatch):
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.Timeout))
    assert get_open_invoice(
        SessionIdentity(True, PRIMARY_PHONE), billing_url="http://mock", timeout=0.01
    ) == {"ok": False, "action": "handoff_to_human"}


def test_invalid_timeout_budgets_fail_closed_without_request(monkeypatch):
    def must_not_query(*args, **kwargs):
        raise AssertionError("invalid timeout must not call billing API")

    monkeypatch.setattr(tool.requests, "get", must_not_query)
    for timeout in (0, -1, float("inf"), float("nan"), True):
        assert get_open_invoice(
            SessionIdentity(True, PRIMARY_PHONE), billing_url="http://mock", timeout=timeout
        ) == {"ok": False, "action": "handoff_to_human"}


def test_timeout_budget_is_applied_to_real_http_request(billing_url):
    result = get_open_invoice(
        SessionIdentity(True, STALL_PHONE), billing_url=billing_url, timeout=0.1
    )
    assert result == {"ok": False, "action": "handoff_to_human"}


def test_ten_cases_are_reproducibly_defined():
    assert len(CASES) == 10
    assert [case.case_id for case in CASES] == list(range(1, 11))
    assert all(case.question and case.rubric for case in CASES)
