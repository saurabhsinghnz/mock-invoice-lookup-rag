"""Original, intentionally unguarded sample retained as an evaluation baseline."""

import requests

BILLING_URL = "https://billing.example-telco.internal"

TOOL_SPEC = {
    "name": "get_open_invoice",
    "description": "Fetch the unpaid invoice for a customer whose phone number has been verified",
    "parameters": {
        "type": "object",
        "properties": {"phone": {"type": "string"}},
        "required": ["phone"],
    },
}


def get_open_invoice(phone: str) -> dict:
    try:
        r = requests.get(
            f"{BILLING_URL}/invoices",
            params={"phone": phone, "status": "open"},
            timeout=2.5,  # on a call, a long silence is a failure
        )
        r.raise_for_status()
    except requests.RequestException:
        # the customer's system failed: don't guess, hand off to a human
        return {"ok": False, "action": "handoff_to_human"}

    items = r.json().get("items", [])
    if not items:
        return {"ok": True, "invoice": None}
    inv = items[0]
    return {"ok": True, "amount": inv["amount"], "due": inv["due_date"]}
