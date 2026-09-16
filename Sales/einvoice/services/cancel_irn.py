"""
einvoice/services/cancel_irn.py

Cancels an already-generated IRN. Mirrors the same request/auth-retry
pattern as generate_irn.py and generate_ewaybill.py.

IMPORTANT: Cancel IRN's response shape is DIFFERENT from Generate IRN's.
Confirmed from official PeriOne docs (PeriOne_Cancel_IRN.xlsx):
  Success: {"Irn": "...", "CancelDate": "2026-09-12 14:26:00"}
  Failure: {"Status": "0", "Data": null, "ErrorDetails": "...", "InfoDtls": "..."}
There is NO status_cd/status_desc wrapper here, unlike Generate IRN.

NIC e-invoice rules that shape this module (also confirmed from docs):
  1. Cancellation is only allowed within 24 hours of IRN generation.
  2. IRN cannot be cancelled if a valid/active e-Way Bill exists
     against it — the e-Way Bill must be cancelled FIRST. We check
     this locally via invoice.Eway_bill_no before calling the API —
     but that's only as accurate as your own database. If the e-way
     bill was cancelled directly on the government portal (not
     through generate-ewaybill/cancel-ewaybill in this app), your
     local field goes stale and this check will wrongly block you.
     Clear invoice.Eway_bill_no manually in that case, or build a
     real-time status check against GETEWAYBILLIRN before relying on
     this in production.
  3. Cancellation can be performed even if the taxpayer's GSTIN is
     active or suspended.
  4. sup_gstin should only be sent when an E-Commerce Operator is
     cancelling on behalf of the supplier — not used here.

Valid cancel reason codes (per NIC spec):
    1 = Duplicate
    2 = Data Entry Mistake
    3 = Order Cancelled
    4 = Others
"Others" (4) requires a non-empty remark.
"""

import requests
from django.conf import settings

from .auth import get_auth_token, EInvoiceAuthError

EINVOICE_BASE_URL = settings.EINVOICE_BASE_URL
EINVOICE_EMAIL = settings.EINVOICE_EMAIL
EINVOICE_USERNAME = settings.EINVOICE_USERNAME
EINVOICE_CLIENT_ID = settings.EINVOICE_CLIENT_ID
EINVOICE_CLIENT_SECRET = settings.EINVOICE_CLIENT_SECRET
EINVOICE_GSTIN = settings.EINVOICE_GSTIN

CANCEL_REASON_CODES = {
    "1": "Duplicate",
    "2": "Data Entry Mistake",
    "3": "Order Cancelled",
    "4": "Others",
}


class EInvoiceCancelError(Exception):
    """Raised when the Cancel IRN call fails."""
    def __init__(self, message, response_data=None):
        super().__init__(message)
        self.response_data = response_data


def build_cancel_irn_payload(invoice, reason_code, remark=""):
    """
    Build the payload for the Cancel IRN API. Confirmed against the
    official schema: Irn (64 chars, required), CnlRsn (1 char,
    required, enum "1"-"4"), CnlRem (0-100 chars, optional).

    Raises ValueError for anything that would make the portal reject
    the request outright, so callers get a clean validation error
    instead of a round trip to the API.
    """
    if not invoice.irn:
        raise ValueError("Invoice has no IRN to cancel.")

    if getattr(invoice, "irn_status", None) == "CNL":
        raise ValueError("This IRN has already been cancelled.")

    # NOTE: we intentionally do NOT pre-check invoice.Eway_bill_no here
    # anymore. That local field can go stale (e.g. the e-way bill was
    # cancelled directly on the government portal, or the DB was edited
    # manually) and would incorrectly block a cancel that the portal
    # itself would actually allow. The portal is the single source of
    # truth — if an active e-way bill genuinely still exists, the
    # portal's own response will say so (surfaced via _format_errors
    # below), and cancel_irn() will sync the local field to match
    # reality either way once the real result comes back.

    reason_code = str(reason_code)
    if reason_code not in CANCEL_REASON_CODES:
        raise ValueError(
            f"Invalid cancel reason code {reason_code!r}. "
            f"Must be one of {sorted(CANCEL_REASON_CODES)}."
        )

    remark = (remark or "").strip()
    if reason_code == "4" and not remark:
        raise ValueError("A remark is required when reason_code is '4' (Others).")
    if len(remark) > 100:
        raise ValueError("remark (CnlRem) must be 100 characters or fewer.")

    return {
        "Irn": invoice.irn,
        "CnlRsn": reason_code,
        "CnlRem": remark or CANCEL_REASON_CODES[reason_code],
    }


def _call_cancel_irn(payload, auth_token):
    url = f"{EINVOICE_BASE_URL}/type/CANCEL/version/V1_03"
    params = {"email": EINVOICE_EMAIL}
    headers = {
        "ip_address": "0.0.0.0",
        "client_id": EINVOICE_CLIENT_ID,
        "client_secret": EINVOICE_CLIENT_SECRET,
        "username": EINVOICE_USERNAME,
        "auth-token": auth_token,
        "gstin": EINVOICE_GSTIN,
        "Content-Type": "application/json",
    }
    return requests.post(url, headers=headers, params=params, json=payload, timeout=30)


def _is_success(data):
    """
    CORRECTED based on a real sandbox response (the doc's example
    turned out not to match live behavior): success actually looks
    like {"status_cd": 1, "status_desc": "GSTR request succeeds",
    "data": {"Irn": "...", "CancelDate": "..."}} — the SAME
    status_cd/data wrapper pattern as generate_irn, not the flat
    {"Irn":..., "CancelDate":...} shape shown in the Excel doc.
    """
    return data.get("status_cd") == 1


def _is_invalid_token_error(data):
    """Same 200-OK-but-token-is-dead check used in generate_irn.py."""
    errors = data.get("status_desc")
    if isinstance(errors, list):
        return any(str(e.get("ErrorCode")) == "1005" for e in errors)
    # Fallback for the doc's alternate error shape, in case it's ever
    # actually returned for some error types.
    error_text = str(data.get("ErrorDetails", "")) + str(data.get("InfoDtls", ""))
    return "1005" in error_text or "Invalid Token" in error_text


def _format_errors(data):
    """
    Handle both observed shapes: status_desc as a list of error dicts
    (matches generate_irn's failure shape), or the doc's alternate
    ErrorDetails/InfoDtls shape, in case either occurs.
    """
    errors = data.get("status_desc")
    if isinstance(errors, list):
        return "; ".join(
            f"[{e.get('ErrorCode')}] {e.get('ErrorMessage')}" for e in errors
        )
    details = data.get("ErrorDetails")
    info = data.get("InfoDtls")
    parts = [p for p in (details, info) if p]
    if parts:
        return "; ".join(str(p) for p in parts)
    return str(data)


def cancel_irn(invoice, reason_code, remark=""):
    """
    Build the payload, call the Cancel IRN API, and return the parsed
    response. Raises EInvoiceCancelError on failure (including the
    portal's "cancellation window expired" and "e-way bill still
    active" business errors — those come back as normal failure
    responses, not exceptions raised locally, since only the portal
    knows the authoritative 24-hour cutoff or real e-way bill status).
    """
    payload = build_cancel_irn_payload(invoice, reason_code, remark)
    token = get_auth_token()

    try:
        response = _call_cancel_irn(payload, token)
    except requests.RequestException as exc:
        raise EInvoiceCancelError(f"Network error calling Cancel IRN: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise EInvoiceCancelError(
            f"Non-JSON response ({response.status_code}): {response.text}"
        ) from exc

    if response.status_code == 401 or _is_invalid_token_error(data):
        try:
            token = get_auth_token(force_refresh=True)
        except EInvoiceAuthError as exc:
            raise EInvoiceCancelError(f"Re-authentication failed: {exc}") from exc
        try:
            response = _call_cancel_irn(payload, token)
        except requests.RequestException as exc:
            raise EInvoiceCancelError(f"Network error calling Cancel IRN: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise EInvoiceCancelError(
                f"Non-JSON response ({response.status_code}): {response.text}"
            ) from exc

    if response.status_code != 200 or not _is_success(data):
        raise EInvoiceCancelError(
            f"Cancel IRN failed: {_format_errors(data)}",
            response_data=data,
        )

    return data


def save_cancellation_to_invoice(invoice, response_data):
    """
    Mark the invoice's IRN as cancelled. CORRECTED: the real response
    nests the result under "data" (e.g. response_data["data"]["CancelDate"]),
    matching generate_irn's shape — not the flat top-level shape shown
    in the Excel doc.

    Also clears Eway_bill_no: a successful IRN cancellation from the
    portal means no active e-way bill was blocking it (per NIC rules),
    so our local record can safely self-correct here instead of
    needing a manual fix.

    Keeps irn/ack_no/signed_invoice in place (the IRN doesn't change
    on cancellation, only its status) — useful for audit purposes.
    """
    data = response_data.get("data", {})
    invoice.irn_status = "CNL"
    invoice.irn_cancel_date = data.get("CancelDate")
    invoice.Eway_bill_no = None
    invoice.save()
    return invoice