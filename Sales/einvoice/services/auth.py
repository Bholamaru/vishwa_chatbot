"""
einvoice/services/auth.py

Handles authentication against the Perione e-Invoice API and caches the
auth-token in Redis so we don't hit the /authenticate endpoint on every
request. Requires django-redis (or any Django cache backend pointed at
Redis) configured as CACHES["default"] in settings.
"""

import requests
from django.conf import settings
from django.core.cache import cache

# ---- Config (pull from Django settings / env, never hardcode) ----------
EINVOICE_BASE_URL = settings.EINVOICE_BASE_URL          # e.g. "https://staging.perione.in/einvoice"
EINVOICE_EMAIL = settings.EINVOICE_EMAIL
EINVOICE_USERNAME = settings.EINVOICE_USERNAME
EINVOICE_PASSWORD = settings.EINVOICE_PASSWORD
EINVOICE_CLIENT_ID = settings.EINVOICE_CLIENT_ID
EINVOICE_CLIENT_SECRET = settings.EINVOICE_CLIENT_SECRET
EINVOICE_GSTIN = settings.EINVOICE_GSTIN
EINVOICE_ENVIRONMENT=settings.EINVOICE_ENVIRONMENT


CACHE_KEY = "einvoice_auth_token"
# NIC e-invoice tokens are typically valid ~6 hours. Cache for slightly
# less than that so we always refresh before the portal expires it.
TOKEN_CACHE_TTL_SECONDS = 5 * 60 * 60 + 45 * 60  # 5h45m


class EInvoiceAuthError(Exception):
    """Raised when authentication with the e-invoice provider fails."""
    pass


def _fetch_new_token() -> str:
    """Call the Authentication API and return a fresh auth-token."""
    url = f"{EINVOICE_BASE_URL}/authenticate"
    params = {"email": EINVOICE_EMAIL}
    headers = {
        "username": EINVOICE_USERNAME,
        "password": EINVOICE_PASSWORD,
        "ip_address": "0.0.0.0",
        "client_id": EINVOICE_CLIENT_ID,
        "client_secret": EINVOICE_CLIENT_SECRET,
        "environment": EINVOICE_ENVIRONMENT, 
        "gstin": EINVOICE_GSTIN,
    } 

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as exc:
        raise EInvoiceAuthError(f"Network error while authenticating: {exc}") from exc

    if response.status_code != 200:
        raise EInvoiceAuthError(
            f"Authentication failed ({response.status_code}): {response.text}"
        )

    data = response.json()

    # NOTE: confirm the exact response key with Perione's docs/sandbox response
    # (commonly "AuthToken", "auth_token", or nested under "Data"/"data").
    token = data.get("AuthToken") or data.get("auth_token") or data.get("data", {}).get("AuthToken")

    if not token:
        raise EInvoiceAuthError(f"Auth token missing from response: {data}")

    return token


def get_auth_token(force_refresh: bool = False) -> str:
    """
    Return a valid auth-token, using the cached one if available.
    Pass force_refresh=True to bypass the cache (e.g. after a 401 from
    a downstream call, to retry once with a fresh token).
    """
    if not force_refresh:
        cached = cache.get(CACHE_KEY)
        if cached:
            return cached

    token = _fetch_new_token()
    cache.set(CACHE_KEY, token, timeout=TOKEN_CACHE_TTL_SECONDS)
    return token