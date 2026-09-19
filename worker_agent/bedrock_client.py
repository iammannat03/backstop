"""Bedrock Converse API client: schema-validated JSON generation with retry.
Replaces worker_agent/gemini_client.py from the Postgres/Gemini version of
this project.

Structured output is forced through a single tool whose inputSchema is the
caller's JSON schema. The model has no other tool to pick, so its tool call
arguments are the JSON result. This is Bedrock's equivalent of Gemini's
responseSchema/responseMimeType generation config.

One difference from the old gemini_client.py callers need to know about:
schema dicts here are standard JSON Schema (lowercase types: object, string,
boolean, number, integer, array), not Gemini's schema format (uppercase
types: OBJECT, STRING, BOOLEAN, NUMBER, INTEGER). Anyone porting a schema
dict from classifier.py, reasoning_engine.py, cross_reference.py, or
command_parser.py needs to lowercase the type names when moving it here.

Selection between the real client and the mock client (worker_agent/
bedrock_mock.py) is read once from the LLM_CLIENT environment variable:
LLM_CLIENT=bedrock (default) uses the real Converse API, LLM_CLIENT=mock
uses fixture responses and never touches AWS, LLM_CLIENT=gemini calls the
Gemini REST API (worker_agent/gemini_client.py, needs GEMINI_API_KEY). Any
other value raises. The model id constants below follow the same variable,
so callers pick up the right tier ids for whichever client is active. Call generate_json() at
module level, it lazily builds and reuses the selected client.
"""

import asyncio
import logging
import os
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("worker_agent.bedrock")

_LLM_MODE = os.getenv("LLM_CLIENT", "bedrock").lower()

if _LLM_MODE == "gemini":
    # Worker tiers run on flash-lite, the verifier on the stronger flash model
    # so it is a genuinely different model from the worker's, not just a
    # second call to the same one.
    from worker_agent.gemini_client import (
        CLASSIFIER_MODEL,
        REASONING_MODEL,
        VERIFIER_MODEL,
    )
else:
    # Placeholders. Confirm these are actually enabled for the target account and
    # region in the Bedrock console before Phase 8's real deployment, then adjust
    # via the env vars below rather than editing the defaults.
    CLASSIFIER_MODEL = os.getenv("BEDROCK_CLASSIFIER_MODEL", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
    REASONING_MODEL = os.getenv("BEDROCK_REASONING_MODEL", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
    # A genuinely different, stronger model than the worker's tier, same reason
    # gemini_client.py used a separate VERIFIER_MODEL: a second call to the same
    # model is not real independent verification.
    VERIFIER_MODEL = os.getenv("BEDROCK_VERIFIER_MODEL", "us.anthropic.claude-3-7-sonnet-20250219-v1:0")

# Bumped to match gemini_client.py's own tuning, kept identical here since
# Bedrock throttling under load behaves similarly to the Gemini 503 window
# that prompted the original values.
_MAX_ATTEMPTS = 5
_RETRY_BACKOFF_SECONDS = 3.0
_TRANSIENT_ERROR_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "ModelNotReadyException",
    "InternalServerException",
}

_TOOL_NAME = "emit_result"


class LLMClient(Protocol):
    """Interface BedrockClient, GeminiClient and the mock in bedrock_mock.py satisfy."""

    async def generate_json(
        self,
        prompt: str,
        schema: dict,
        model: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> dict: ...


def _tool_config(schema: dict) -> dict:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": _TOOL_NAME,
                    "description": "Emit the structured result for this task.",
                    "inputSchema": {"json": schema},
                }
            }
        ],
        "toolChoice": {"tool": {"name": _TOOL_NAME}},
    }


class BedrockClient:
    """Real implementation, calls bedrock-runtime's Converse API."""

    def __init__(self) -> None:
        region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        # AWS_ENDPOINT_URL lets this point at LocalStack for local testing,
        # unset in real deployment so boto3 hits the real Bedrock endpoint.
        endpoint_url = os.getenv("AWS_ENDPOINT_URL") or None
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            endpoint_url=endpoint_url,
            # Retries are handled explicitly below to match gemini_client.py's
            # backoff shape, so boto3's own retry layer stays out of the way.
            config=Config(retries={"max_attempts": 0}),
        )

    def _converse_once(
        self,
        prompt: str,
        schema: dict,
        model: str,
        system_instruction: str | None,
        temperature: float | None,
    ) -> dict:
        kwargs: dict = {
            "modelId": model,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            **_tool_config(schema),
        }
        if system_instruction:
            kwargs["system"] = [{"text": system_instruction}]
        if temperature is not None:
            kwargs["inferenceConfig"] = {"temperature": temperature}
        return self._client.converse(**kwargs)

    async def generate_json(
        self,
        prompt: str,
        schema: dict,
        model: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> dict:
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await asyncio.to_thread(
                    self._converse_once, prompt, schema, model, system_instruction, temperature
                )
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code not in _TRANSIENT_ERROR_CODES:
                    raise
                last_error = e
                logger.warning(
                    "Bedrock transient error (attempt %d/%d): %s", attempt + 1, _MAX_ATTEMPTS, e
                )
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            except EndpointConnectionError as e:
                last_error = e
                logger.warning(
                    "Bedrock connection failed (attempt %d/%d): %s", attempt + 1, _MAX_ATTEMPTS, e
                )
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            try:
                content = response["output"]["message"]["content"]
                tool_use = next(block["toolUse"] for block in content if "toolUse" in block)
                return tool_use["input"]
            except (KeyError, StopIteration) as e:
                raise RuntimeError(f"Unexpected Bedrock response shape: {response}") from e

        raise RuntimeError(f"Bedrock call failed after {_MAX_ATTEMPTS} attempts: {last_error}")


_client_singleton: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton

    mode = os.getenv("LLM_CLIENT", "bedrock").lower()
    if mode == "mock":
        from worker_agent.bedrock_mock import MockLLMClient

        _client_singleton = MockLLMClient()
    elif mode == "bedrock":
        _client_singleton = BedrockClient()
    elif mode == "gemini":
        from worker_agent.gemini_client import GeminiClient

        _client_singleton = GeminiClient()
    else:
        raise RuntimeError(f"Unknown LLM_CLIENT={mode!r}, expected 'bedrock', 'mock' or 'gemini'")
    return _client_singleton


async def generate_json(
    prompt: str,
    schema: dict,
    model: str,
    system_instruction: str | None = None,
    temperature: float | None = None,
) -> dict:
    """Module-level convenience wrapper, mirrors gemini_client.py's
    generate_json() signature so callers barely change on port. Dispatches to
    whichever client LLM_CLIENT selected."""
    client = get_llm_client()
    return await client.generate_json(
        prompt=prompt, schema=schema, model=model, system_instruction=system_instruction, temperature=temperature
    )
