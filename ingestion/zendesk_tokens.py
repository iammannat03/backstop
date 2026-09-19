"""Keeps shared/zendesk_auth.py usable on Lambda without editing it.

That module keeps the OAuth access/refresh pair in memory and mirrors it to a
local file (TOKEN_CACHE_PATH). Lambda containers are ephemeral and Zendesk
rotates the refresh token on every use, so a token that only lives in a file
is lost with the container and cannot be recovered without a browser
re-authorization. This adapter makes DynamoDB the source of truth:

  - before a request, the stored blob is written to a file under /tmp and the
    module's token manager is told to reload it
  - after a request, if the manager's tokens changed (a refresh happened),
    the new blob is written back to DynamoDB

Concurrent Lambdas (poller, audit, API routes) can all hit an expired token at
once, and only the first refresh with a given refresh token succeeds. A short
DynamoDB lease serializes refreshes: whoever loses waits, then reloads the
token the winner stored. The one path not covered is the forced refresh after
an unexpected 401 on a token that looked valid, which is rare.

Use authed_request from this module instead of shared.zendesk_auth's.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

import httpx

from ingestion import state_store
from shared import zendesk_auth as za

logger = logging.getLogger("ingestion.zendesk_tokens")

# The package directory is read-only on Lambda, /tmp is the writable location.
CACHE_PATH = Path(os.getenv("ZENDESK_TOKEN_CACHE_PATH", "/tmp/zendesk_oauth_cache.json"))
za.TOKEN_CACHE_PATH = CACHE_PATH

_REFRESH_LEASE = "zendesk_token_refresh"
_REFRESH_LEASE_SECONDS = 30
_REFRESH_WAIT_SECONDS = 20

_loaded: dict | None = None


def _snapshot() -> dict:
    tm = za.token_manager
    return {"access_token": tm._access_token, "refresh_token": tm._refresh_token, "expires_at": tm._expires_at}


def load_tokens() -> None:
    """Pulls the stored blob into the shared token manager. A missing blob
    leaves the manager as it was (unauthorized)."""
    global _loaded
    blob = state_store.get_zendesk_tokens()
    if blob is None:
        return
    CACHE_PATH.write_text(json.dumps(blob))
    za.token_manager._load_cache()
    _loaded = _snapshot()


def save_tokens_if_changed() -> None:
    global _loaded
    snap = _snapshot()
    if snap != _loaded and snap["refresh_token"]:
        state_store.put_zendesk_tokens(snap)
        _loaded = snap
        logger.info("Persisted refreshed Zendesk OAuth tokens to DynamoDB")


def is_authorized() -> bool:
    load_tokens()
    return za.token_manager.is_authorized()


async def store_initial_tokens(token_response: dict) -> None:
    await za.token_manager.store_initial_tokens(token_response)
    save_tokens_if_changed()


def _needs_refresh() -> bool:
    tm = za.token_manager
    return tm._access_token is None or time.time() >= tm._expires_at


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return await za.authed_request(client, method, url, **kwargs)
    finally:
        save_tokens_if_changed()


async def authed_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    load_tokens()
    if not _needs_refresh():
        return await _request(client, method, url, **kwargs)

    owner = str(uuid.uuid4())
    deadline = time.monotonic() + _REFRESH_WAIT_SECONDS
    held = state_store.acquire_lease(_REFRESH_LEASE, owner, _REFRESH_LEASE_SECONDS)
    while not held and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        held = state_store.acquire_lease(_REFRESH_LEASE, owner, _REFRESH_LEASE_SECONDS)
    if not held:
        logger.warning("Zendesk token refresh lease not acquired in time, continuing without it")
    try:
        # Another invocation may have refreshed while this one waited.
        load_tokens()
        return await _request(client, method, url, **kwargs)
    finally:
        if held:
            state_store.release_lease(_REFRESH_LEASE, owner)
