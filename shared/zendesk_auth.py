"""Shared Zendesk OAuth token state. A single shared singleton avoids the
refresh-token-rotation race that two independent token managers would hit.
Auth flow (authorization_code + PKCE) is documented in ingestion/zendesk_webhook.py.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("shared.zendesk_auth")

ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN")
ZENDESK_OAUTH_CLIENT_ID = os.getenv("ZENDESK_OAUTH_CLIENT_ID")
ZENDESK_OAUTH_CLIENT_SECRET = os.getenv("ZENDESK_OAUTH_CLIENT_SECRET")
ZENDESK_OAUTH_SCOPE = os.getenv("ZENDESK_OAUTH_SCOPE", "tickets:read tickets:write users:read")
# Must exactly match a "Redirect URLs" entry registered on the OAuth client in
# Zendesk Admin Center. http://localhost is allowed without TLS.
ZENDESK_OAUTH_REDIRECT_URI = os.getenv("ZENDESK_OAUTH_REDIRECT_URI", "http://localhost:8001/oauth/callback")

BASE_URL = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2" if ZENDESK_SUBDOMAIN else None
OAUTH_TOKEN_URL = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/oauth/tokens" if ZENDESK_SUBDOMAIN else None
OAUTH_AUTHORIZE_URL = (
    f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/oauth/authorizations/new" if ZENDESK_SUBDOMAIN else None
)

TOKEN_CACHE_PATH = Path(__file__).resolve().parent.parent / ".zendesk_oauth_cache.json"


def zendesk_configured() -> bool:
    return bool(ZENDESK_SUBDOMAIN and ZENDESK_OAUTH_CLIENT_ID and ZENDESK_OAUTH_CLIENT_SECRET)


def generate_pkce_pair() -> tuple[str, str]:
    """RFC 7636: code_verifier is a random 43-128 char string; code_challenge is
    BASE64URL-ENCODE(SHA256(code_verifier)), no padding."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


class ZendeskOAuthTokenManager:
    """Holds and refreshes the access/refresh token pair, persisted to a local
    file so a restart doesn't force the browser approval step again."""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._load_cache()

    def is_authorized(self) -> bool:
        return bool(self._access_token or self._refresh_token)

    def _load_cache(self) -> None:
        if not TOKEN_CACHE_PATH.exists():
            return
        try:
            data = json.loads(TOKEN_CACHE_PATH.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._expires_at = data.get("expires_at", 0.0)
        except Exception:
            logger.exception("Failed to load cached Zendesk OAuth tokens from %s", TOKEN_CACHE_PATH)

    def _save_cache(self) -> None:
        TOKEN_CACHE_PATH.write_text(
            json.dumps(
                {
                    "access_token": self._access_token,
                    "refresh_token": self._refresh_token,
                    "expires_at": self._expires_at,
                }
            )
        )

    def _apply_token_response(self, data: dict) -> None:
        self._access_token = data["access_token"]
        # Zendesk rotates the refresh token on every use.
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        self._expires_at = time.time() + data.get("expires_in", 3600) - 30

    async def store_initial_tokens(self, token_response: dict) -> None:
        async with self._lock:
            self._apply_token_response(token_response)
            self._save_cache()
            logger.info("Stored Zendesk OAuth tokens from authorization_code exchange")

    async def get_token(self, force_refresh: bool = False) -> str:
        async with self._lock:
            if force_refresh or self._access_token is None or time.time() >= self._expires_at:
                await self._refresh_locked()
            return self._access_token

    async def _refresh_locked(self) -> None:
        if not self._refresh_token:
            raise RuntimeError(
                "Zendesk is not authorized yet, visit http://localhost:8001/oauth/authorize "
                "in your browser once to grant access."
            )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                OAUTH_TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": ZENDESK_OAUTH_CLIENT_ID,
                    "client_secret": ZENDESK_OAUTH_CLIENT_SECRET,
                },
            )
        resp.raise_for_status()
        self._apply_token_response(resp.json())
        self._save_cache()
        logger.info("Refreshed Zendesk OAuth token via refresh_token grant")


# One shared instance for the whole process, must not be duplicated per-caller.
token_manager = ZendeskOAuthTokenManager()


async def authed_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """GET/POST/PUT with a Bearer token from the shared token_manager, retrying
    once with a forced refresh on a 401."""
    token = await token_manager.get_token()
    resp = await client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if resp.status_code == 401:
        token = await token_manager.get_token(force_refresh=True)
        resp = await client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
    resp.raise_for_status()
    return resp
