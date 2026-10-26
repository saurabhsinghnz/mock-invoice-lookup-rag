"""Synthetic local billing API used by tests and the offline demonstration."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="Synthetic billing mock", docs_url=None, redoc_url=None)

PRIMARY_PHONE = "+15550000001"
OTHER_PHONE = "+15550000010"
EMPTY_PHONE = "+15550000002"
UNKNOWN_PHONE = "+15550000003"
MULTIPLE_PHONE = "+15550000004"
MISSING_PHONE = "+15550000005"
INVALID_PHONE = "+15550000006"
BAD_JSON_PHONE = "+15550000007"
BAD_SHAPE_PHONE = "+15550000008"
STALL_PHONE = "+15550000009"
ERROR_PHONE = "+15550000011"


def invoice(phone: str, amount: Any = "42.50", due_date: Any = "2026-11-15") -> dict[str, Any]:
    return {"phone": phone, "amount": amount, "due_date": due_date}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/invoices")
def get_invoices(phone: str = Query(...), status: str = Query(...)) -> Any:
    if status != "open":
        return {"items": []}
    if phone == STALL_PHONE:
        time.sleep(3.0)
    if phone == ERROR_PHONE:
        return JSONResponse({"error": "synthetic failure"}, status_code=503)
    if phone == BAD_JSON_PHONE:
        return Response(content="{not-json", media_type="application/json")
    if phone == BAD_SHAPE_PHONE:
        return {"unexpected": []}
    if phone == MULTIPLE_PHONE:
        return {"items": [invoice(phone), invoice(phone, "19.00", "2026-12-01")]}
    if phone == MISSING_PHONE:
        return {"items": [{"phone": phone, "due_date": "2026-11-15"}]}
    if phone == INVALID_PHONE:
        return {"items": [invoice(phone, "not-an-amount")]}
    if phone == EMPTY_PHONE or phone == UNKNOWN_PHONE:
        return {"items": []}
    if phone == PRIMARY_PHONE:
        return {"items": [invoice(phone)]}
    if phone == OTHER_PHONE:
        return {"items": [invoice(phone, "999.99", "2026-12-31")]}
    return {"items": []}
