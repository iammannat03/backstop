"""Small DynamoDB-backed state that the ingestion and audit Lambdas need but
that is not part of a ticket: the Zendesk poll cursor, the poller lease, the
OAuth token blob, the pending OAuth handshake, Slack event de-dupe keys and
the Slack thread to ticket lookup.

All of it lives in the same table as the ticket data under non-ticket keys
(PK=CONFIG#..., LEASE#..., DEDUP#..., SLACKTHREAD#...), so nothing here shows
up in any ticket partition or GSI. Items that should expire carry a numeric
"ttl" attribute, which the table has TTL enabled on. DynamoDB deletes expired
items lazily (up to a couple of days late), so every reader also checks the
expiry itself instead of trusting that a stale item is gone.
"""

import time
from decimal import Decimal

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from persistence import dynamo

_CONFIG_SK = "STATE"


def _table():
    return dynamo.get_table()


def _get(pk: str, sk: str) -> dict | None:
    return _table().get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True).get("Item")


def _is_conditional_failure(e: ClientError) -> bool:
    return e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


# ---------------------------------------------------------------------------
# Poll cursor
# ---------------------------------------------------------------------------


def get_cursor() -> str | None:
    item = _get("CONFIG#zendesk_poll", _CONFIG_SK)
    return item.get("cursor") if item else None


def save_cursor(cursor: str) -> None:
    _table().put_item(
        Item={
            "PK": "CONFIG#zendesk_poll",
            "SK": _CONFIG_SK,
            "cursor": cursor,
            "updated_at": dynamo._now_iso(),
        }
    )


# ---------------------------------------------------------------------------
# Leases and one-shot keys
# ---------------------------------------------------------------------------


def acquire_lease(name: str, owner: str, ttl_seconds: int) -> bool:
    """True if this caller now holds the lease. An expired lease is taken over,
    which covers an invocation that was killed before it could release."""
    now = int(time.time())
    try:
        _table().put_item(
            Item={
                "PK": f"LEASE#{name}",
                "SK": "LEASE",
                "owner": owner,
                "expires_at": now + ttl_seconds,
                "ttl": now + ttl_seconds + 3600,
            },
            ConditionExpression="attribute_not_exists(PK) OR expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except ClientError as e:
        if _is_conditional_failure(e):
            return False
        raise


def release_lease(name: str, owner: str) -> None:
    try:
        _table().delete_item(
            Key={"PK": f"LEASE#{name}", "SK": "LEASE"},
            ConditionExpression="#owner = :owner",
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":owner": owner},
        )
    except ClientError as e:
        if not _is_conditional_failure(e):
            raise


def claim_key(key: str, ttl_seconds: int) -> bool:
    """True the first time a key is claimed, False for every later attempt
    until it expires. Used to de-duplicate Slack events and notifications."""
    now = int(time.time())
    try:
        _table().put_item(
            Item={"PK": f"DEDUP#{key}", "SK": "DEDUP", "expires_at": now + ttl_seconds, "ttl": now + ttl_seconds},
            ConditionExpression="attribute_not_exists(PK) OR expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except ClientError as e:
        if _is_conditional_failure(e):
            return False
        raise


def release_key(key: str) -> None:
    _table().delete_item(Key={"PK": f"DEDUP#{key}", "SK": "DEDUP"})


# ---------------------------------------------------------------------------
# Zendesk OAuth
# ---------------------------------------------------------------------------


def get_zendesk_tokens() -> dict | None:
    item = _get("CONFIG#zendesk_oauth", _CONFIG_SK)
    if not item:
        return None
    return {
        "access_token": item.get("access_token"),
        "refresh_token": item.get("refresh_token"),
        "expires_at": float(item.get("expires_at", 0)),
    }


def put_zendesk_tokens(tokens: dict) -> None:
    _table().put_item(
        Item={
            "PK": "CONFIG#zendesk_oauth",
            "SK": _CONFIG_SK,
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": Decimal(str(tokens["expires_at"])),
            "updated_at": dynamo._now_iso(),
        }
    )


def save_oauth_pending(state: str, code_verifier: str, ttl_seconds: int = 600) -> None:
    now = int(time.time())
    _table().put_item(
        Item={
            "PK": "CONFIG#oauth_pending",
            "SK": _CONFIG_SK,
            "state": state,
            "code_verifier": code_verifier,
            "expires_at": now + ttl_seconds,
            "ttl": now + ttl_seconds,
        }
    )


def pop_oauth_pending() -> dict | None:
    """Returns and deletes the pending handshake, so a callback can only be
    completed once."""
    resp = _table().delete_item(Key={"PK": "CONFIG#oauth_pending", "SK": _CONFIG_SK}, ReturnValues="ALL_OLD")
    item = resp.get("Attributes")
    if not item or int(item.get("expires_at", 0)) < time.time():
        return None
    return {"state": item["state"], "code_verifier": item["code_verifier"]}


# ---------------------------------------------------------------------------
# Slack thread to ticket lookup
# ---------------------------------------------------------------------------


def put_thread_mapping(thread_ts: str, ticket_id: str) -> None:
    _table().put_item(Item={"PK": f"SLACKTHREAD#{thread_ts}", "SK": "MAP", "ticket_id": ticket_id})


def get_ticket_id_for_thread(thread_ts: str) -> str | None:
    item = _get(f"SLACKTHREAD#{thread_ts}", "MAP")
    if item:
        return item["ticket_id"]
    # Ticket items have no index on slack_thread_ts, so a thread whose mapping
    # item is missing (write failed after the thread was recorded) is found by
    # a filtered scan. It only runs for unknown threads, which are rare.
    kwargs = {
        "FilterExpression": Attr("SK").eq("METADATA") & Attr("slack_thread_ts").eq(thread_ts),
        "ProjectionExpression": "id",
    }
    while True:
        resp = _table().scan(**kwargs)
        items = resp.get("Items", [])
        if items:
            return items[0]["id"]
        if "LastEvaluatedKey" not in resp:
            return None
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
