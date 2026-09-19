"""API Gateway routes ported from the FastAPI app in backstop-prac's
ingestion/zendesk_webhook.py. Paths and request/response shapes are unchanged:

    GET  /oauth/authorize
    GET  /oauth/callback
    POST /ingest/submit-ticket
    POST /ingest/poll-now
    POST /ingest/human-decision-notify

The UI's poll-now proxy expects {"dispatched": n} or {"error": "..."} with a
200, and the human-decision route expects {"ok": true}. FastAPI reported
validation problems as 422 {"detail": ...}, the handlers below do the same.

Zendesk calls go through ingestion.zendesk_tokens so the OAuth tokens live in
DynamoDB instead of a local file.
"""

import asyncio
import html
import json
import logging
import secrets
import uuid
from typing import Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from audit.slack_notifier import notify_executed, notify_execution_failed
from audit.zendesk_updater import mark_escalated, mark_resolved
from execution.execute_handler import customer_facing_resolution_message
from ingestion import poll_handler, state_store, zendesk_tokens
from ingestion.http_util import html_response, json_response, query_params, raw_body, redirect_response
from persistence import dynamo
from shared import zendesk_auth as za
from shared.models import ProposedAction

logger = logging.getLogger("ingestion.api_handlers")


def _parse_model(model_cls, event: dict):
    """Returns (model, None) or (None, 422 response)."""
    try:
        return model_cls(**json.loads(raw_body(event) or b"{}")), None
    except ValidationError as e:
        return None, json_response(422, {"detail": json.loads(e.json())})
    except (ValueError, TypeError):
        return None, json_response(422, {"detail": "Request body is not valid JSON"})


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


def oauth_authorize_handler(event, context):
    """Visit this in a browser (while logged into Zendesk) to grant access,
    one-time setup, or whenever the stored refresh token is revoked."""
    if not za.zendesk_configured():
        return json_response(
            200, {"error": "ZENDESK_SUBDOMAIN / ZENDESK_OAUTH_CLIENT_ID / ZENDESK_OAUTH_CLIENT_SECRET not set"}
        )
    state = secrets.token_urlsafe(16)
    code_verifier, code_challenge = za.generate_pkce_pair()
    state_store.save_oauth_pending(state, code_verifier)
    params = {
        "response_type": "code",
        "client_id": za.ZENDESK_OAUTH_CLIENT_ID,
        "redirect_uri": za.ZENDESK_OAUTH_REDIRECT_URI,
        "scope": za.ZENDESK_OAUTH_SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return redirect_response(f"{za.OAUTH_AUTHORIZE_URL}?{urlencode(params)}")


async def _exchange_code(code: str, code_verifier: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            za.OAUTH_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": za.ZENDESK_OAUTH_CLIENT_ID,
                "client_secret": za.ZENDESK_OAUTH_CLIENT_SECRET,
                "redirect_uri": za.ZENDESK_OAUTH_REDIRECT_URI,
                "code_verifier": code_verifier,
            },
        )


def oauth_callback_handler(event, context):
    params = query_params(event)
    error = params.get("error")
    if error:
        return html_response(400, f"<p>Zendesk denied authorization: {html.escape(error)}</p>")

    code = params.get("code")
    state = params.get("state")
    pending = state_store.pop_oauth_pending()
    if not code or not pending or state != pending["state"]:
        return html_response(
            400, "<p>Invalid or expired authorization attempt, visit /oauth/authorize again.</p>"
        )

    async def _finish():
        resp = await _exchange_code(code, pending["code_verifier"])
        if resp.status_code != 200:
            return resp
        await zendesk_tokens.store_initial_tokens(resp.json())
        return resp

    resp = asyncio.run(_finish())
    if resp.status_code != 200:
        return html_response(
            400, f"<p>Token exchange failed: {resp.status_code} {html.escape(resp.text)}</p>"
        )
    return html_response(
        200, "<p>Zendesk connected. You can close this tab. Ingestion will start polling automatically.</p>"
    )


# ---------------------------------------------------------------------------
# submit-ticket
# ---------------------------------------------------------------------------


class TicketSubmission(BaseModel):
    name: str
    email: str
    message: str

    @field_validator("name", "email", "message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


async def _create_zendesk_ticket(submission: TicketSubmission) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await zendesk_tokens.authed_request(
            client,
            "POST",
            f"{za.BASE_URL}/tickets.json",
            json={
                "ticket": {
                    "subject": submission.message[:80],
                    "comment": {"body": submission.message},
                    "requester": {"email": submission.email, "name": submission.name},
                }
            },
        )


def submit_ticket_handler(event, context):
    """Creates a real Zendesk ticket, lets the normal poller pick it up."""
    submission, err = _parse_model(TicketSubmission, event)
    if err:
        return err
    if not za.zendesk_configured():
        return json_response(503, {"detail": "Zendesk credentials not configured"})
    if not zendesk_tokens.is_authorized():
        return json_response(503, {"detail": "Not authorized yet, visit /oauth/authorize in a browser first"})

    try:
        resp = asyncio.run(_create_zendesk_ticket(submission))
    except httpx.HTTPStatusError as e:
        return json_response(
            502, {"detail": f"Zendesk API error {e.response.status_code}: {e.response.text}"}
        )

    zendesk_ticket_id = resp.json()["ticket"]["id"]
    logger.info("Created Zendesk ticket %s via public submission form (%s)", zendesk_ticket_id, submission.email)
    return json_response(200, {"zendesk_ticket_id": zendesk_ticket_id})


# ---------------------------------------------------------------------------
# poll-now
# ---------------------------------------------------------------------------


async def _poll_now() -> dict:
    owner = str(uuid.uuid4())
    if not state_store.acquire_lease(poll_handler.POLL_LEASE_NAME, owner, 60):
        # The scheduled poller is mid-run and will pick up anything new.
        return {"dispatched": 0}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return {"dispatched": await poll_handler.poll_once(client)}
    finally:
        state_store.release_lease(poll_handler.POLL_LEASE_NAME, owner)


def poll_now_handler(event, context):
    """Manual trigger for demo control, don't wait for the next timer tick."""
    if not za.zendesk_configured():
        return json_response(200, {"error": "Zendesk credentials not configured in .env"})
    if not zendesk_tokens.is_authorized():
        return json_response(200, {"error": "Not authorized yet, visit /oauth/authorize in your browser first"})
    try:
        return json_response(200, asyncio.run(_poll_now()))
    except httpx.HTTPStatusError as e:
        return json_response(
            200, {"error": f"Zendesk API error {e.response.status_code}: {e.response.text}"}
        )


# ---------------------------------------------------------------------------
# human-decision-notify
# ---------------------------------------------------------------------------


class HumanDecisionNotify(BaseModel):
    ticket_id: str
    outcome: Literal["resolved", "failed"]
    action: ProposedAction | None = None
    refund_id: str | None = None
    error_detail: str | None = None


async def _notify_human_decision(payload: HumanDecisionNotify, zendesk_ticket_id: str | None) -> None:
    if payload.outcome == "resolved":
        await notify_executed(
            payload.ticket_id,
            payload.action.action_type,
            payload.action.amount,
            payload.action.currency,
            payload.action.rationale,
            payload.refund_id,
        )
        if zendesk_ticket_id:
            await mark_resolved(zendesk_ticket_id, customer_facing_resolution_message(payload.action))
    else:
        await notify_execution_failed(payload.ticket_id, payload.error_detail or "Human decision execution failed")
        if zendesk_ticket_id:
            await mark_escalated(
                zendesk_ticket_id,
                f"Human-approved action failed and needs manual handling: {payload.error_detail}",
            )


def human_decision_notify_handler(event, context):
    """The control-room UI's approve/override/acknowledge buttons write to
    DynamoDB and Stripe directly (no Zendesk token manager there), then call
    this endpoint so the Slack post and Zendesk write-back still go through
    the one place that owns the shared OAuth tokens."""
    payload, err = _parse_model(HumanDecisionNotify, event)
    if err:
        return err
    ticket = dynamo.get_ticket(payload.ticket_id)
    if ticket is None:
        return json_response(404, {"detail": "Ticket not found"})
    if payload.outcome == "resolved" and payload.action is None:
        return json_response(400, {"detail": "action is required when outcome is 'resolved'"})

    asyncio.run(_notify_human_decision(payload, ticket["zendesk_ticket_id"]))
    return json_response(200, {"ok": True})
