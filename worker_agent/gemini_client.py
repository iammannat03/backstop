"""Gemini REST client: schema-validated JSON generation with retry.

Implements the LLMClient protocol from bedrock_client.py, selected with
LLM_CLIENT=gemini. Callers pass standard lowercase JSON Schema (the same dicts
the Bedrock client takes). Gemini's responseSchema wants uppercase type names
and only a subset of JSON Schema keywords, so _to_gemini_schema converts.

The API key goes in the x-goog-api-key header, never the URL, so it cannot
show up in a logged request URL or an httpx error message.
"""

import asyncio
import json
import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("worker_agent.gemini")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Pinned versions rather than "-latest" aliases: the aliases moved onto models
# that are overloaded or have no quota on this key, which failed live tickets.
CLASSIFIER_MODEL = os.getenv("GEMINI_CLASSIFIER_MODEL", "gemini-3.1-flash-lite")
REASONING_MODEL = os.getenv("GEMINI_REASONING_MODEL", "gemini-3.1-flash-lite")
# A different model from the worker's reasoning engine, so the verifier is an
# independent check and not a second call to the same model.
VERIFIER_MODEL = os.getenv("GEMINI_VERIFIER_MODEL", "gemini-3.5-flash-lite")

_MAX_ATTEMPTS = 5
_RETRY_BACKOFF_SECONDS = 3.0
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_TIMEOUT_SECONDS = 30.0

# Keywords Gemini's responseSchema accepts. Anything else (additionalProperties,
# $schema, default, and so on) is rejected with a 400, so it is dropped.
_ALLOWED_KEYS = {
    "type",
    "format",
    "description",
    "nullable",
    "enum",
    "properties",
    "required",
    "items",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
    "propertyOrdering",
}


def _to_gemini_schema(schema):
    """Converts standard JSON Schema to Gemini's responseSchema subset."""
    if isinstance(schema, list):
        return [_to_gemini_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for key, value in schema.items():
        if key not in _ALLOWED_KEYS:
            continue
        if key == "type" and isinstance(value, str):
            out[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            out[key] = {name: _to_gemini_schema(sub) for name, sub in value.items()}
        elif key == "items":
            out[key] = _to_gemini_schema(value)
        else:
            out[key] = value
    return out


class GeminiClient:
    """Real implementation, calls the generateContent REST endpoint."""

    def __init__(self) -> None:
        self._api_key = os.getenv("GEMINI_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set, required when LLM_CLIENT=gemini")

    async def generate_json(
        self,
        prompt: str,
        schema: dict,
        model: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> dict:
        generation_config: dict = {
            "responseMimeType": "application/json",
            "responseSchema": _to_gemini_schema(schema),
        }
        if temperature is not None:
            generation_config["temperature"] = temperature

        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        url = f"{GEMINI_BASE_URL}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self._api_key}

        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, transport=self._transport()) as client:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    resp = await client.post(url, json=body, headers=headers)
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
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
                    raise RuntimeError(f"Unexpected Gemini response shape: {data}") from e

        raise RuntimeError(f"Gemini call failed after {_MAX_ATTEMPTS} attempts: {last_error}")

    def _transport(self) -> httpx.AsyncBaseTransport | None:
        """Hook for injecting a transport in tests, None means the default."""
        return None
