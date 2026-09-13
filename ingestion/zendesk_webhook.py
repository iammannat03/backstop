"""Zendesk ingestion. Despite the filename, this is a poller, not a webhook
listener (no public tunnel needed for a demo). Spawns one asyncio task per
new ticket for concurrent processing.

Auth is OAuth 2.0 authorization_code + PKCE, not client_credentials or a
static API token (see docs/rules.md for why). One-time setup: visit
GET /oauth/authorize in a browser while logged into Zendesk.
"""

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from persistence.db import SessionLocal
from persistence.models import AuditRecord, Ticket
from shared.zendesk_auth import (
    BASE_URL,
    OAUTH_AUTHORIZE_URL,
    OAUTH_TOKEN_URL,
    ZENDESK_OAUTH_CLIENT_ID,
    ZENDESK_OAUTH_CLIENT_SECRET,
    ZENDESK_OAUTH_REDIRECT_URI,
    ZENDESK_OAUTH_SCOPE,
    authed_request,
    generate_pkce_pair,
    token_manager,
    zendesk_configured,
)
from audit.slack_commands import router as slack_commands_router
from verifier_agent.pipeline import run_verification_pipeline
from worker_agent.pipeline import run_worker_pipeline

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion.zendesk")

# Zendesk's incremental export endpoint caps at 10 req/min, default interval stays
# well under that even before accounting for the in-cycle drain loop below.
POLL_INTERVAL_SECONDS = int(os.getenv("ZENDESK_POLL_INTERVAL_SECONDS", "15"))


async def fetch_requester_email(client: httpx.AsyncClient, requester_id: int) -> str | None:
    resp = await authed_request(client, "GET", f"{BASE_URL}/users/{requester_id}.json")
    return resp.json()["user"]["email"]


async def _already_ingested(zendesk_ticket_id: str) -> bool:
    with SessionLocal() as db:
        existing = db.execute(
            select(Ticket.id).where(Ticket.zendesk_ticket_id == zendesk_ticket_id)
        ).first()
        return existing is not None


async def _run_full_pipeline(ticket_id) -> None:
    """Worker then verifier, as one detached asyncio task per ticket."""
    try:
        proposed = await run_worker_pipeline(ticket_id)
        await run_verification_pipeline(ticket_id, proposed)
    except Exception:
        logger.exception("Pipeline failed for ticket %s", ticket_id)


async def process_ticket(raw_ticket: dict, client: httpx.AsyncClient) -> None:
    """Persists the raw ticket, then dispatches the worker pipeline as its own task."""
    zendesk_ticket_id = str(raw_ticket["id"])
    try:
        email = await fetch_requester_email(client, raw_ticket["requester_id"])
        ticket_text = raw_ticket.get("description") or raw_ticket.get("subject") or ""

        with SessionLocal() as db:
            ticket = Ticket(
                zendesk_ticket_id=zendesk_ticket_id,
                customer_email=email,
                ticket_text=ticket_text,
                status="new",
            )
            db.add(ticket)
            db.flush()
            ticket_id = ticket.id
            db.add(
                AuditRecord(
                    ticket_id=ticket_id,
                    event_type="ticket_ingested",
                    actor="ingestion",
                    detail={"zendesk_ticket_id": zendesk_ticket_id, "customer_email": email},
                )
            )
            db.commit()
            logger.info("Ingested ticket %s (customer=%s)", zendesk_ticket_id, email)

        asyncio.create_task(_run_full_pipeline(ticket_id))
    except Exception:
        logger.exception("Failed to ingest ticket %s", zendesk_ticket_id)


class ZendeskPoller:
    def __init__(self) -> None:
        self._cursor: str | None = None
        # Zendesk requires start_time to be at least 1 minute in the past.
        self._start_time = int(time.time()) - 90
        self._client: httpx.AsyncClient | None = None
        self._running = False

    def ensure_client(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)

    async def poll_once(self) -> int:
        """Drains every page currently available (loops until end_of_stream),
        so a backlog doesn't wait multiple poll intervals to clear. Returns the
        number of new tickets dispatched."""
        self.ensure_client()

        dispatched = 0
        while True:
            params = {"cursor": self._cursor} if self._cursor else {"start_time": self._start_time}
            resp = await authed_request(
                self._client,
                "GET",
                f"{BASE_URL}/incremental/tickets/cursor",
                params=params,
            )
            data = resp.json()
            self._cursor = data["after_cursor"]

            for raw_ticket in data.get("tickets", []):
                # Zendesk's incremental export includes deleted tickets in the
                # stream (a deletion counts as an update), never treat one as new.
                if raw_ticket.get("status") == "deleted":
                    continue
                if await _already_ingested(str(raw_ticket["id"])):
                    continue
                asyncio.create_task(process_ticket(raw_ticket, self._client))
                dispatched += 1

            if data.get("end_of_stream", True):
                break

        return dispatched

    async def run_forever(self) -> None:
        if not zendesk_configured():
            logger.warning(
                "ZENDESK_SUBDOMAIN / ZENDESK_OAUTH_CLIENT_ID / ZENDESK_OAUTH_CLIENT_SECRET not set, "
                "ingestion poller is not running. Fill in .env and restart."
            )
            return

        self.ensure_client()
        if not token_manager.is_authorized():
            logger.warning(
                "Zendesk not authorized yet, visit http://localhost:8001/oauth/authorize "
                "in your browser once to grant access. Polling will start automatically once done."
            )

        self._running = True
        while self._running:
            try:
                if token_manager.is_authorized():
                    dispatched = await self.poll_once()
                    if dispatched:
                        logger.info("Dispatched %d new ticket(s)", dispatched)
            except Exception:
                logger.exception("Zendesk poll cycle failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False


poller = ZendeskPoller()

# CSRF guard for the authorization_code flow, single global is fine for a
# single-operator hackathon setup, not meant to support concurrent auth attempts.
_pending_oauth_state: str | None = None
_pending_code_verifier: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller.run_forever())
    yield
    poller.stop()
    task.cancel()


app = FastAPI(title="Backstop Ingestion", lifespan=lifespan)
# Slack Events API endpoint for the @backstop in-thread command feature.
app.include_router(slack_commands_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "zendesk_configured": zendesk_configured(),
        "zendesk_authorized": token_manager.is_authorized(),
    }


@app.get("/oauth/authorize")
async def oauth_authorize():
    """Visit this in a browser (while logged into Zendesk) to grant access,
    one-time setup, or whenever the cached refresh token is revoked/deleted."""
    global _pending_oauth_state, _pending_code_verifier
    if not zendesk_configured():
        return {"error": "ZENDESK_SUBDOMAIN / ZENDESK_OAUTH_CLIENT_ID / ZENDESK_OAUTH_CLIENT_SECRET not set"}
    _pending_oauth_state = secrets.token_urlsafe(16)
    _pending_code_verifier, code_challenge = generate_pkce_pair()
    params = {
        "response_type": "code",
        "client_id": ZENDESK_OAUTH_CLIENT_ID,
        "redirect_uri": ZENDESK_OAUTH_REDIRECT_URI,
        "scope": ZENDESK_OAUTH_SCOPE,
        "state": _pending_oauth_state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}")


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    global _pending_oauth_state, _pending_code_verifier
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(f"<p>Zendesk denied authorization: {error}</p>", status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not _pending_oauth_state or state != _pending_oauth_state:
        return HTMLResponse(
            "<p>Invalid or expired authorization attempt, visit /oauth/authorize again.</p>",
            status_code=400,
        )
    code_verifier = _pending_code_verifier
    _pending_oauth_state = None
    _pending_code_verifier = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            OAUTH_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": ZENDESK_OAUTH_CLIENT_ID,
                "client_secret": ZENDESK_OAUTH_CLIENT_SECRET,
                "redirect_uri": ZENDESK_OAUTH_REDIRECT_URI,
                "code_verifier": code_verifier,
            },
        )
    if resp.status_code != 200:
        return HTMLResponse(f"<p>Token exchange failed: {resp.status_code} {resp.text}</p>", status_code=400)

    await token_manager.store_initial_tokens(resp.json())
    return HTMLResponse(
        "<p>Zendesk connected. You can close this tab. Ingestion will start polling automatically.</p>"
    )


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


@app.post("/ingest/submit-ticket")
async def submit_ticket(submission: TicketSubmission):
    """Creates a real Zendesk ticket, lets the normal poller pick it up."""
    if not zendesk_configured():
        raise HTTPException(status_code=503, detail="Zendesk credentials not configured")
    if not token_manager.is_authorized():
        raise HTTPException(status_code=503, detail="Not authorized yet, visit /oauth/authorize in a browser first")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await authed_request(
                client,
                "POST",
                f"{BASE_URL}/tickets.json",
                json={
                    "ticket": {
                        "subject": submission.message[:80],
                        "comment": {"body": submission.message},
                        "requester": {"email": submission.email, "name": submission.name},
                    }
                },
            )
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"Zendesk API error {e.response.status_code}: {e.response.text}") from e

    zendesk_ticket_id = resp.json()["ticket"]["id"]
    logger.info("Created Zendesk ticket %s via public submission form (%s)", zendesk_ticket_id, submission.email)
    return {"zendesk_ticket_id": zendesk_ticket_id}


@app.post("/ingest/poll-now")
async def poll_now():
    """Manual trigger for demo control, don't wait for the next timer tick."""
    if not zendesk_configured():
        return {"error": "Zendesk credentials not configured in .env"}
    poller.ensure_client()
    if not token_manager.is_authorized():
        return {"error": "Not authorized yet, visit /oauth/authorize in your browser first"}
    try:
        dispatched = await poller.poll_once()
        return {"dispatched": dispatched}
    except httpx.HTTPStatusError as e:
        return {"error": f"Zendesk API error {e.response.status_code}: {e.response.text}"}
