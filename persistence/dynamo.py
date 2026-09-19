"""DynamoDB access layer, replacing the SQLAlchemy models and raw session
usage that backstop-prac's callers had scattered across worker_agent,
verifier_agent, execution, audit, ingestion, and the UI's query layer.

Schema: single table, four item shapes sharing one partition key per ticket.

    Ticket             PK=TICKET#<id>        SK=METADATA
    PolicyDecision     PK=TICKET#<id>        SK=POLICY#<created_at>#<id>
    VerificationResult PK=TICKET#<id>        SK=VERIFICATION#<created_at>#<id>
    AuditRecord        PK=TICKET#<id>        SK=AUDIT#<created_at>#<id>

A single table keeps "get everything about ticket X" to one partition, which
is the shape both the ticket detail page and the pipeline stages actually
need (load ticket, append audit records, read the latest policy decision or
verification result). created_at is stored as a sortable ISO 8601 string, so
SK ordering on a Query doubles as time ordering, no separate sort needed for
"list this ticket's audit trail in order" or "get the latest policy decision".

Four sparse GSIs cover the access patterns raw SK order cannot, all of them
in service of the UI's ticket list and stats bar (see backstop-prac's
ui/lib/queries.ts, ported against this same schema in a later phase):

    StatusIndex (GSI1)   GSI1PK=STATUS#<status>   GSI1SK=<updated_at>#<id>
        Ticket items only. Backs "list tickets in status X, newest first"
        and the 24h auto-executed count in queue stats (SK range on top of
        STATUS#resolved is a cheap way to bound by time without a scan).

    AllTicketsIndex (GSI2)   GSI2PK=TICKET   GSI2SK=<updated_at>#<id>
        Ticket items only. Backs "all tickets, newest first" for status=all
        and status=in_progress, where in_progress is defined by exclusion
        (NOT IN resolved/escalated/blocked) rather than a single status
        value a key condition can express.

    ZendeskIndex (GSI3)   GSI3PK=ZENDESK#<zendesk_ticket_id>   SK=METADATA
        Ticket items only. Backs the ingestion poller's dedup check
        (has this Zendesk ticket already been ingested) without a scan.

    EntityDateIndex (GSI4)   GSI4PK=<entity_type>   GSI4SK=<created_at>#<id>
        PolicyDecision, VerificationResult, and AuditRecord items. Backs
        the verifier mismatch rate in queue stats (all verification_result
        items in the last 24h), which otherwise has no cross-ticket access
        path in a design keyed by ticket partition.

Everything list_tickets() cannot express as a DynamoDB key condition
(free-text search over ticket_text/customer_email, customer_id substring
match, action_type, and the created_at date range) is applied as a Python
filter over the query result rather than a DynamoDB FilterExpression, so
pagination counts stay exact. That trades a real ceiling, a few hundred to
low thousands of tickets, comfortably past hackathon scale, for correctness
and simplicity. A higher-volume deployment would want OpenSearch or a
denormalized search table instead of pushing that filter further into
DynamoDB.

Numbers that round-trip through Pydantic as floats (confidence scores,
refund amounts computed on the fly) get converted to Decimal on write and
back to float/int on read, DynamoDB has no native float type.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "backstop-local")

STATUS_INDEX = "StatusIndex"
ALL_TICKETS_INDEX = "AllTicketsIndex"
ZENDESK_INDEX = "ZendeskIndex"
ENTITY_DATE_INDEX = "EntityDateIndex"

NEEDS_REVIEW_STATUSES = ("escalated", "blocked")
TERMINAL_OR_HOLD_STATUSES = ("resolved", "escalated", "blocked")

_table = None


def _resource():
    kwargs = {"region_name": os.getenv("AWS_DEFAULT_REGION", "us-east-1")}
    endpoint_url = os.getenv("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.resource("dynamodb", **kwargs)


def get_table():
    global _table
    if _table is None:
        _table = _resource().Table(TABLE_NAME)
    return _table


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def _from_decimal(value):
    if isinstance(value, Decimal):
        return float(value) if value % 1 != 0 else int(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


def _ticket_item_to_dict(item: dict) -> dict:
    return {
        "id": item["id"],
        "zendesk_ticket_id": item["zendesk_ticket_id"],
        "customer_id": item.get("customer_id"),
        "customer_email": item["customer_email"],
        "ticket_text": item["ticket_text"],
        "status": item["status"],
        "classification": _from_decimal(item.get("classification")),
        "stripe_context": _from_decimal(item.get("stripe_context")),
        "proposed_action": _from_decimal(item.get("proposed_action")),
        "action_type": item.get("action_type"),
        "slack_channel": item.get("slack_channel"),
        "slack_thread_ts": item.get("slack_thread_ts"),
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


def create_ticket(zendesk_ticket_id: str, customer_email: str, ticket_text: str) -> str:
    ticket_id = _new_id()
    now = _now_iso()
    item = {
        "PK": f"TICKET#{ticket_id}",
        "SK": "METADATA",
        "entity_type": "ticket",
        "id": ticket_id,
        "zendesk_ticket_id": zendesk_ticket_id,
        "customer_id": None,
        "customer_email": customer_email,
        "ticket_text": ticket_text,
        "status": "new",
        "classification": None,
        "stripe_context": None,
        "proposed_action": None,
        "action_type": None,
        "slack_channel": None,
        "slack_thread_ts": None,
        "created_at": now,
        "updated_at": now,
        "GSI1PK": "STATUS#new",
        "GSI1SK": f"{now}#{ticket_id}",
        "GSI2PK": "TICKET",
        "GSI2SK": f"{now}#{ticket_id}",
        "GSI3PK": f"ZENDESK#{zendesk_ticket_id}",
        "GSI3SK": "METADATA",
    }
    get_table().put_item(Item=item)
    return ticket_id


def ticket_exists_for_zendesk_id(zendesk_ticket_id: str) -> bool:
    resp = get_table().query(
        IndexName=ZENDESK_INDEX,
        KeyConditionExpression=Key("GSI3PK").eq(f"ZENDESK#{zendesk_ticket_id}"),
        Limit=1,
    )
    return len(resp.get("Items", [])) > 0


def get_ticket_by_zendesk_id(zendesk_ticket_id: str) -> dict | None:
    resp = get_table().query(
        IndexName=ZENDESK_INDEX,
        KeyConditionExpression=Key("GSI3PK").eq(f"ZENDESK#{zendesk_ticket_id}"),
        Limit=1,
    )
    items = resp.get("Items", [])
    return _ticket_item_to_dict(items[0]) if items else None


def get_ticket(ticket_id: str) -> dict | None:
    resp = get_table().get_item(Key={"PK": f"TICKET#{ticket_id}", "SK": "METADATA"})
    item = resp.get("Item")
    return _ticket_item_to_dict(item) if item else None


def _update_ticket(ticket_id: str, set_expr_parts: list[str], values: dict, names: dict | None = None) -> None:
    now = _now_iso()
    set_expr_parts = set_expr_parts + ["updated_at = :updated_at"]
    values[":updated_at"] = now
    kwargs = {
        "Key": {"PK": f"TICKET#{ticket_id}", "SK": "METADATA"},
        "UpdateExpression": "SET " + ", ".join(set_expr_parts),
        "ExpressionAttributeValues": values,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names
    get_table().update_item(**kwargs)


def update_ticket_status(ticket_id: str, status: str) -> None:
    now = _now_iso()
    _update_ticket(
        ticket_id,
        ["#status = :status", "GSI1PK = :gsi1pk", "GSI1SK = :gsi1sk", "GSI2SK = :gsi2sk"],
        {
            ":status": status,
            ":gsi1pk": f"STATUS#{status}",
            ":gsi1sk": f"{now}#{ticket_id}",
            ":gsi2sk": f"{now}#{ticket_id}",
        },
        names={"#status": "status"},
    )


def set_ticket_classification(ticket_id: str, classification: dict) -> None:
    _update_ticket(
        ticket_id,
        ["classification = :classification"],
        {":classification": _to_decimal(classification)},
    )


def set_ticket_slack_thread(ticket_id: str, channel: str, thread_ts: str) -> bool:
    """Only sets it if this ticket has no thread yet, mirrors the
    "first post opens the thread" behavior in the original slack_notifier."""
    try:
        get_table().update_item(
            Key={"PK": f"TICKET#{ticket_id}", "SK": "METADATA"},
            UpdateExpression="SET slack_channel = :channel, slack_thread_ts = :thread_ts, updated_at = :updated_at",
            ConditionExpression="attribute_not_exists(slack_thread_ts) OR slack_thread_ts = :empty",
            ExpressionAttributeValues={
                ":channel": channel,
                ":thread_ts": thread_ts,
                ":updated_at": _now_iso(),
                ":empty": None,
            },
        )
        return True
    except get_table().meta.client.exceptions.ConditionalCheckFailedException:
        return False


def set_proposed_action(
    ticket_id: str,
    proposed_action: dict,
    status: str,
    customer_id: str | None,
    event_type: str,
    actor: str,
    event_detail: dict,
) -> str:
    """Ticket update (proposed_action, status, and customer_id if known) plus
    the audit record for it. Mirrors action_proposer.py's propose_action(),
    which did this as one commit in backstop-prac. TransactWriteItems would
    be the direct equivalent, but it is unreliable on LocalStack's community
    edition (fails even minimal items with "Invalid attribute value type"),
    so this stays two sequential writes like every other ticket-update path
    in this module. The only invariant that actually depends on atomicity,
    the Stripe idempotency key in execution, does not go through here."""
    now = _now_iso()
    action_type = proposed_action.get("action_type")

    set_expr_parts = ["proposed_action = :proposed_action", "action_type = :action_type"]
    values = {":proposed_action": _to_decimal(proposed_action), ":action_type": action_type}
    if customer_id:
        set_expr_parts.append("customer_id = :customer_id")
        values[":customer_id"] = customer_id

    _update_ticket(
        ticket_id,
        set_expr_parts + ["#status = :status", "GSI1PK = :gsi1pk", "GSI1SK = :gsi1sk", "GSI2SK = :gsi2sk"],
        {
            **values,
            ":status": status,
            ":gsi1pk": f"STATUS#{status}",
            ":gsi1sk": f"{now}#{ticket_id}",
            ":gsi2sk": f"{now}#{ticket_id}",
        },
        names={"#status": "status"},
    )
    return append_audit_record(ticket_id, event_type, actor, event_detail)


# ---------------------------------------------------------------------------
# Policy decisions
# ---------------------------------------------------------------------------


def write_policy_decision(
    ticket_id: str,
    decision: str,
    matched_rule: str | None,
    reason: str | None,
    raw_input: dict | None,
    raw_output: dict | None,
) -> str:
    policy_id = _new_id()
    now = _now_iso()
    item = {
        "PK": f"TICKET#{ticket_id}",
        "SK": f"POLICY#{now}#{policy_id}",
        "entity_type": "policy_decision",
        "id": policy_id,
        "ticket_id": ticket_id,
        "decision": decision,
        "matched_rule": matched_rule,
        "reason": reason,
        "raw_input": _to_decimal(raw_input),
        "raw_output": _to_decimal(raw_output),
        "created_at": now,
        "GSI4PK": "policy_decision",
        "GSI4SK": f"{now}#{policy_id}",
    }
    get_table().put_item(Item=item)
    return policy_id


def get_latest_policy_decision(ticket_id: str) -> dict | None:
    resp = get_table().query(
        KeyConditionExpression=Key("PK").eq(f"TICKET#{ticket_id}") & Key("SK").begins_with("POLICY#"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        return None
    item = items[0]
    return {
        "id": item["id"],
        "ticket_id": item["ticket_id"],
        "decision": item["decision"],
        "matched_rule": item.get("matched_rule"),
        "reason": item.get("reason"),
        "raw_input": _from_decimal(item.get("raw_input")),
        "raw_output": _from_decimal(item.get("raw_output")),
        "created_at": item["created_at"],
    }


# ---------------------------------------------------------------------------
# Verification results
# ---------------------------------------------------------------------------


def write_verification_result(
    ticket_id: str,
    consistent: bool,
    mismatch_type: str | None,
    verifier_rationale: str,
    notes: str,
    final_decision: str,
    raw_verification_data: dict | None,
) -> str:
    verification_id = _new_id()
    now = _now_iso()
    item = {
        "PK": f"TICKET#{ticket_id}",
        "SK": f"VERIFICATION#{now}#{verification_id}",
        "entity_type": "verification_result",
        "id": verification_id,
        "ticket_id": ticket_id,
        "consistent": consistent,
        "mismatch_type": mismatch_type,
        "verifier_rationale": verifier_rationale,
        "notes": notes,
        "final_decision": final_decision,
        "raw_verification_data": _to_decimal(raw_verification_data),
        "created_at": now,
        "GSI4PK": "verification_result",
        "GSI4SK": f"{now}#{verification_id}",
    }
    get_table().put_item(Item=item)
    return verification_id


def get_latest_verification_result(ticket_id: str) -> dict | None:
    resp = get_table().query(
        KeyConditionExpression=Key("PK").eq(f"TICKET#{ticket_id}") & Key("SK").begins_with("VERIFICATION#"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        return None
    item = items[0]
    return {
        "id": item["id"],
        "ticket_id": item["ticket_id"],
        "consistent": item["consistent"],
        "mismatch_type": item.get("mismatch_type"),
        "verifier_rationale": item["verifier_rationale"],
        "notes": item["notes"],
        "final_decision": item["final_decision"],
        "raw_verification_data": _from_decimal(item.get("raw_verification_data")),
        "created_at": item["created_at"],
    }


# ---------------------------------------------------------------------------
# Audit records
# ---------------------------------------------------------------------------


def _audit_item(ticket_id: str, audit_id: str, now: str, event_type: str, actor: str, detail: dict | None) -> dict:
    return {
        "PK": f"TICKET#{ticket_id}",
        "SK": f"AUDIT#{now}#{audit_id}",
        "entity_type": "audit_record",
        "id": audit_id,
        "ticket_id": ticket_id,
        "event_type": event_type,
        "actor": actor,
        "detail": _to_decimal(detail),
        "created_at": now,
        "GSI4PK": "audit_record",
        "GSI4SK": f"{now}#{audit_id}",
    }


def append_audit_record(ticket_id: str, event_type: str, actor: str, detail: dict | None) -> str:
    audit_id = _new_id()
    now = _now_iso()
    get_table().put_item(Item=_audit_item(ticket_id, audit_id, now, event_type, actor, detail))
    return audit_id


def get_audit_trail(ticket_id: str) -> list[dict]:
    items = []
    kwargs = {
        "KeyConditionExpression": Key("PK").eq(f"TICKET#{ticket_id}") & Key("SK").begins_with("AUDIT#"),
        "ScanIndexForward": True,
    }
    while True:
        resp = get_table().query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return [
        {
            "id": item["id"],
            "ticket_id": item["ticket_id"],
            "event_type": item["event_type"],
            "actor": item["actor"],
            "detail": _from_decimal(item.get("detail")),
            "created_at": item["created_at"],
        }
        for item in items
    ]


# ---------------------------------------------------------------------------
# UI-facing list and stats queries
# ---------------------------------------------------------------------------


def _query_all(index_name: str, pk_value: str, scan_index_forward: bool = False, sk_gte: str | None = None) -> list[dict]:
    key_cond = Key("GSI1PK" if index_name == STATUS_INDEX else "GSI2PK").eq(pk_value)
    sk_name = "GSI1SK" if index_name == STATUS_INDEX else "GSI2SK"
    if sk_gte is not None:
        key_cond = key_cond & Key(sk_name).gte(sk_gte)
    items = []
    kwargs = {
        "IndexName": index_name,
        "KeyConditionExpression": key_cond,
        "ScanIndexForward": scan_index_forward,
    }
    while True:
        resp = get_table().query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def list_tickets(
    status: str = "all",
    action_type: str = "all",
    q: str | None = None,
    customer_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 8,
) -> dict:
    """Mirrors ui/lib/queries.ts's getTickets(). See the module docstring for
    why filtering happens in Python rather than as a DynamoDB
    FilterExpression."""
    if status == "resolved":
        raw_items = _query_all(STATUS_INDEX, "STATUS#resolved")
    elif status == "needs_review":
        raw_items = []
        for s in NEEDS_REVIEW_STATUSES:
            raw_items.extend(_query_all(STATUS_INDEX, f"STATUS#{s}"))
        raw_items.sort(key=lambda i: i["updated_at"], reverse=True)
    else:
        # "all" and "in_progress" both start from the full ticket set,
        # in_progress narrows by excluding the terminal/hold statuses below.
        raw_items = _query_all(ALL_TICKETS_INDEX, "TICKET")
        if status == "in_progress":
            raw_items = [i for i in raw_items if i["status"] not in TERMINAL_OR_HOLD_STATUSES]

    tickets = [_ticket_item_to_dict(i) for i in raw_items]

    if action_type != "all":
        tickets = [t for t in tickets if t.get("action_type") == action_type]
    if q:
        needle = q.lower()
        tickets = [
            t for t in tickets
            if needle in (t["ticket_text"] or "").lower() or needle in (t["customer_email"] or "").lower()
        ]
    if customer_id:
        needle = customer_id.lower()
        tickets = [t for t in tickets if t.get("customer_id") and needle in t["customer_id"].lower()]
    if date_from:
        tickets = [t for t in tickets if t["created_at"] >= date_from]
    if date_to:
        # created_at < date_to + 1 day, matching the original's inclusive-day semantics.
        upper = (datetime.fromisoformat(date_to) + timedelta(days=1)).isoformat()
        tickets = [t for t in tickets if t["created_at"] < upper]

    total = len(tickets)
    start = (page - 1) * page_size
    page_items = tickets[start : start + page_size]

    return {"tickets": page_items, "total": total, "page": page, "page_size": page_size}


def get_queue_stats() -> dict:
    """Mirrors ui/lib/queries.ts's getQueueStats()."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    all_tickets = [_ticket_item_to_dict(i) for i in _query_all(ALL_TICKETS_INDEX, "TICKET")]
    in_flight = sum(1 for t in all_tickets if t["status"] not in ("resolved", "escalated"))

    resolved_recent = _query_all(STATUS_INDEX, "STATUS#resolved", sk_gte=cutoff)
    auto_executed_today = len(resolved_recent)

    escalated_recent = _query_all(STATUS_INDEX, "STATUS#escalated", sk_gte=cutoff)
    cycle_seconds = []
    for item in resolved_recent + escalated_recent:
        created = datetime.fromisoformat(item["created_at"])
        updated = datetime.fromisoformat(item["updated_at"])
        cycle_seconds.append((updated - created).total_seconds())
    median_cycle_seconds = None
    if cycle_seconds:
        cycle_seconds.sort()
        mid = len(cycle_seconds) // 2
        median_cycle_seconds = (
            cycle_seconds[mid] if len(cycle_seconds) % 2 else (cycle_seconds[mid - 1] + cycle_seconds[mid]) / 2
        )

    verification_items = []
    kwargs = {
        "IndexName": ENTITY_DATE_INDEX,
        "KeyConditionExpression": Key("GSI4PK").eq("verification_result") & Key("GSI4SK").gte(cutoff),
    }
    while True:
        resp = get_table().query(**kwargs)
        verification_items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    total_verifications = len(verification_items)
    mismatched = sum(1 for i in verification_items if not i.get("consistent", True))
    verifier_mismatch_rate_pct = (mismatched / total_verifications * 100) if total_verifications else None

    return {
        "in_flight": in_flight,
        "auto_executed_today": auto_executed_today,
        "median_cycle_seconds": median_cycle_seconds,
        "verifier_mismatch_rate_pct": verifier_mismatch_rate_pct,
    }
