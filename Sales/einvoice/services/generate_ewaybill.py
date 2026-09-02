"""
Sales/einvoice/services/generate_ewaybill.py

Generates an e-way bill for an invoice that ALREADY has an IRN.
Mirrors the same pattern as generate_irn.py.
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

# Maps your mode_of_trans text to the API's numeric transport mode code
TRANS_MODE_CODES = {
    "by road": "1",
    "road": "1",
    "rail": "2",
    "air": "3",
    "ship": "4",
}


class EWayBillGenerationError(Exception):
    """Raised when the Generate E-Way Bill call fails."""
    def __init__(self, message, response_data=None):
        super().__init__(message)
        self.response_data = response_data


def _fmt_date(d):
    return d.strftime("%d/%m/%Y") if d else None


def build_generate_ewaybill_payload(invoice):
    """
    Build the payload for the Generate E-Way Bill Using IRN API.
    Raises ValueError if the invoice doesn't have an IRN yet, or is
    missing required transport data.
    """
    if not invoice.irn:
        raise ValueError(
            "Invoice has no IRN yet — generate the IRN first before "
            "creating an e-way bill."
        )
    if not invoice.vehical_no:
        raise ValueError("Invoice is missing vehical_no (vehicle number).")

    trans_mode = TRANS_MODE_CODES.get(
        (invoice.mode_of_trans or "").strip().lower(), "1"
    )

    return {
        "Irn": invoice.irn,
        "Distance": invoice.trans_distance_km or 0,
        "TransMode": trans_mode,
        "TransId": invoice.transporter_gstin or "",
        "TransName": invoice.transporter or "",
        "TransDocDt": _fmt_date(invoice.trans_doc_date) or _fmt_date(invoice.invoice_Date),
        "TransDocNo": invoice.trans_doc_no or "",
        "VehNo": invoice.vehical_no,
        "VehType": invoice.vehicle_type or "R",
    }


def _call_generate_ewaybill(payload, auth_token):
    url = f"{EINVOICE_BASE_URL}/type/GENERATE_EWAYBILL/version/V1_03"
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
    return data.get("status_cd") == 1


def _format_errors(data):
    errors = data.get("status_desc")
    if isinstance(errors, list):
        return "; ".join(
            f"[{e.get('ErrorCode')}] {e.get('ErrorMessage')}" for e in errors
        )
    return str(errors)


def generate_ewaybill(invoice):
    """
    Build the payload, call the Generate E-Way Bill API, and return the
    parsed response. Raises EWayBillGenerationError on failure.
    """
    payload = build_generate_ewaybill_payload(invoice)
    token = get_auth_token()

    try:
        response = _call_generate_ewaybill(payload, token)
    except requests.RequestException as exc:
        raise EWayBillGenerationError(f"Network error: {exc}") from exc

    if response.status_code == 401:
        try:
            token = get_auth_token(force_refresh=True)
        except EInvoiceAuthError as exc:
            raise EWayBillGenerationError(f"Re-authentication failed: {exc}") from exc
        try:
            response = _call_generate_ewaybill(payload, token)
        except requests.RequestException as exc:
            raise EWayBillGenerationError(f"Network error: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise EWayBillGenerationError(
            f"Non-JSON response ({response.status_code}): {response.text}"
        ) from exc

    if response.status_code != 200 or not _is_success(data):
        raise EWayBillGenerationError(
            f"Generate E-Way Bill failed: {_format_errors(data)}",
            response_data=data,
        )

    return data


def save_ewaybill_to_invoice(invoice, response_data):
    """
    Persist the e-way bill number/date onto the Invoice, reusing your
    existing Eway_bill_no / Eway_bill_Date fields.
    """
    data = response_data.get("data", {})
    invoice.Eway_bill_no = data.get("EwbNo")
    ewb_date = data.get("EwbDt")
    if ewb_date:
        # EwbDt typically comes back as "YYYY-MM-DD HH:MM:SS" — take date part
        try:
            from datetime import datetime
            invoice.Eway_bill_Date = datetime.strptime(
                ewb_date.split(" ")[0], "%Y-%m-%d"
            ).date()
        except (ValueError, AttributeError):
            pass  # leave Eway_bill_Date unchanged if format is unexpected
    invoice.save()
    return invoice