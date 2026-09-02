"""
einvoice/services/generate_irn.py

Calls the Generate IRN API with a built payload, handles auth-token
refresh on expiry, and returns the parsed response so the caller can
save it onto the Invoice.
"""

import requests
from django.conf import settings

from .auth import get_auth_token, EInvoiceAuthError
from .payload_builder import build_generate_irn_payload

EINVOICE_BASE_URL = settings.EINVOICE_BASE_URL
EINVOICE_EMAIL = settings.EINVOICE_EMAIL
EINVOICE_USERNAME = settings.EINVOICE_USERNAME
EINVOICE_CLIENT_ID = settings.EINVOICE_CLIENT_ID
EINVOICE_CLIENT_SECRET = settings.EINVOICE_CLIENT_SECRET
EINVOICE_GSTIN = settings.EINVOICE_GSTIN


class EInvoiceGenerationError(Exception):
    """Raised when the Generate IRN call fails (validation, portal error, etc.)."""
    def __init__(self, message, response_data=None):
        super().__init__(message)
        self.response_data = response_data


def _call_generate_irn(payload, auth_token):
    url = f"{EINVOICE_BASE_URL}/type/GENERATE/version/V1_03"
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


def generate_irn(invoice):
    """
    Build the payload for `invoice`, call the Generate IRN API, and
    return the parsed JSON response on success.

    Raises EInvoiceGenerationError on any failure (validation error from
    the portal, network error, or malformed response). Caller is
    responsible for saving the result onto the Invoice and for catching
    this exception to show a clean message to the user.
    """
    payload = build_generate_irn_payload(invoice)
    token = get_auth_token()

    try:
        response = _call_generate_irn(payload, token)
    except requests.RequestException as exc:
        raise EInvoiceGenerationError(f"Network error calling Generate IRN: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise EInvoiceGenerationError(
            f"Non-JSON response ({response.status_code}): {response.text}"
        ) from exc

    # The portal doesn't always signal an expired token via HTTP 401 —
    # it can return HTTP 200 with an "Invalid Token" error INSIDE the
    # body (ErrorCode 1005). Catch that case too and retry once with a
    # freshly-fetched token.
    if response.status_code == 401 or _is_invalid_token_error(data):
        try:
            token = get_auth_token(force_refresh=True)
        except EInvoiceAuthError as exc:
            raise EInvoiceGenerationError(f"Re-authentication failed: {exc}") from exc
        try:
            response = _call_generate_irn(payload, token)
        except requests.RequestException as exc:
            raise EInvoiceGenerationError(f"Network error calling Generate IRN: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise EInvoiceGenerationError(
                f"Non-JSON response ({response.status_code}): {response.text}"
            ) from exc

    if response.status_code != 200 or not _is_success(data):
        raise EInvoiceGenerationError(
            f"Generate IRN failed: {_format_errors(data)}",
            response_data=data,
        )

    return data


def _is_invalid_token_error(data):
    """Check whether a (200 OK, but business-error) response is actually
    an expired/invalid token, identified by ErrorCode 1005."""
    errors = data.get("status_desc")
    if isinstance(errors, list):
        return any(str(e.get("ErrorCode")) == "1005" for e in errors)
    return False


def _is_success(data):
    """
    Confirmed against a real sandbox response:
    Failure looks like:
      {"status_cd": 0, "status_desc": [{"ErrorCode": "...", "ErrorMessage": "..."}, ...]}
    Success is expected to look like:
      {"status_cd": 1, "status_desc": {"Irn": "...", "AckNo": ..., "AckDt": "...", ...}}
    (the success shape is still assumed — confirm once a real success
    response comes back, and adjust save_irn_to_invoice's key names below
    if the field casing/names differ from this guess).
    """
    return data.get("status_cd") == 1


def _format_errors(data):
    """Turn the status_desc error list into a readable string."""
    errors = data.get("status_desc")
    if isinstance(errors, list):
        return "; ".join(
            f"[{e.get('ErrorCode')}] {e.get('ErrorMessage')}" for e in errors
        )
    return str(errors)


def save_irn_to_invoice(invoice, response_data):
    """
    Persist the Generate IRN response onto the Invoice.

    Confirmed against a real successful sandbox response — the IRN
    fields live under response_data["data"].
    """
    data = response_data.get("data", {})
    invoice.irn = data.get("Irn")
    invoice.ack_no = data.get("AckNo")
    invoice.ack_date = data.get("AckDt")
    invoice.signed_qr_code = data.get("SignedQRCode")
    invoice.signed_invoice = data.get("SignedInvoice")
    invoice.irn_status = data.get("Status", "ACT")
    invoice.save()
    return invoice