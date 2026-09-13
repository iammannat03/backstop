"""Shared Gemini REST client: schema-validated JSON generation with retry."""

import asyncio
import json
import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("worker_agent.gemini")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# "-latest" aliases rather than a pinned dated version, so the model doesn't
# disappear out from under a running key.
# REASONING_MODEL runs on the same lite tier as the classifier: gemini-flash-latest
# is currently unavailable (sustained 503s, confirmed live), not a transient blip.
CLASSIFIER_MODEL = os.getenv("GEMINI_CLASSIFIER_MODEL", "gemini-flash-lite-latest")
REASONING_MODEL = os.getenv("GEMINI_REASONING_MODEL", "gemini-flash-lite-latest")

# Bumped after a real sustained 503 "high demand" window during testing: 3
# attempts at 2s wasn't always enough to ride it out.
_MAX_ATTEMPTS = 5
_RETRY_BACKOFF_SECONDS = 3.0
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


async def generate_json(
    prompt: str,
    schema: dict,
    model: str,
    system_instruction: str | None = None,
) -> dict:
    """Calls generateContent with a JSON response schema, returns the parsed
    dict. Retries on transient errors, fails fast on anything else."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    url = f"{GEMINI_BASE_URL}/models/{model}:generateContent?key={GEMINI_API_KEY}"

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await client.post(url, json=body)
            except httpx.HTTPError as e:
                last_error = e
                logger.warning("Gemini request failed (attempt %d/%d): %s", attempt + 1, _MAX_ATTEMPTS, e)
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            if resp.status_code in _TRANSIENT_STATUS_CODES:
                last_error = RuntimeError(f"Gemini transient error {resp.status_code}: {resp.text}")
                logger.warning(
                    "Gemini transient error (attempt %d/%d): %s %s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    resp.status_code,
                    resp.text,
                )
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            resp.raise_for_status()
            data = resp.json()
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                raise RuntimeError(f"Unexpected Gemini response shape: {data}") from e

    raise RuntimeError(f"Gemini call failed after {_MAX_ATTEMPTS} attempts: {last_error}")
