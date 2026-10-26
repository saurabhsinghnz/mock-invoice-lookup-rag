"""Identity-bound, fail-closed invoice lookup tool."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

import requests

BILLING_URL = "https://billing.example-telco.internal"
REQUEST_TIMEOUT_SECONDS = 2.5
PHONE_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
AMOUNT_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$")

TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "get_open_invoice",
        "description": "Fetch the open invoice for the verified customer in this call session.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class SessionIdentity:
    """Trusted application context; never populate this from model arguments."""

    verified: bool
    phone: str | None


def handoff() -> dict[str, Any]:
    return {"ok": False, "action": "handoff_to_human"}


def _validated_invoice(payload: Any, expected_phone: str) -> dict[str, str] | None:
    if not isinstance(payload, dict) or set(payload) != {"phone", "amount", "due_date"}:
        raise ValueError("unexpected invoice shape")
    if payload["phone"] != expected_phone:
        raise ValueError("invoice identity mismatch")
    amount = payload["amount"]
    if not isinstance(amount, str) or not AMOUNT_PATTERN.fullmatch(amount):
        raise ValueError("invalid amount")
    try:
        if not Decimal(amount).is_finite() or Decimal(amount) <= 0:
            raise ValueError("invalid amount")
        due = date.fromisoformat(payload["due_date"])
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid invoice values") from exc
    if due.isoformat() != payload["due_date"]:
        raise ValueError("invalid due date")
    return {"amount": amount, "due": due.isoformat()}


def get_open_invoice(
    session: SessionIdentity,
    *,
    billing_url: str | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return only one validated invoice bound to trusted verified session context."""
    phone = session.phone
    if (
        not session.verified
        or not isinstance(phone, str)
        or not PHONE_PATTERN.fullmatch(phone)
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(timeout)
        or timeout <= 0
    ):
        return handoff()
    try:
        response = requests.get(
            f"{(billing_url or BILLING_URL).rstrip('/')}/invoices",
            params={"phone": phone, "status": "open"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or set(payload) != {"items"}:
            return handoff()
        items = payload["items"]
        if not isinstance(items, list):
            return handoff()
        if not items:
            return {"ok": True, "invoice": None}
        if len(items) != 1:
            return handoff()
        invoice = _validated_invoice(items[0], phone)
        if invoice is None:
            return handoff()
        return {"ok": True, **invoice}
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return handoff()


def dispatch_tool(
    name: str,
    arguments: Any,
    session: SessionIdentity,
    *,
    billing_url: str | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Validate model tool dispatch; identity is not accepted from arguments."""
    if name != "get_open_invoice" or not isinstance(arguments, dict) or arguments:
        return handoff()
    return get_open_invoice(session, billing_url=billing_url, timeout=timeout)
