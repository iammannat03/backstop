"""Helpers for API Gateway HTTP API (payload format 2.0) Lambda handlers."""

import base64
import json


def json_response(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def html_response(status: int, body: str) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": body}


def redirect_response(url: str) -> dict:
    return {"statusCode": 307, "headers": {"Location": url}, "body": ""}


def raw_body(event: dict) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode()


def header(event: dict, name: str) -> str:
    # HTTP API lower-cases header names.
    return (event.get("headers") or {}).get(name.lower(), "")


def query_params(event: dict) -> dict:
    return event.get("queryStringParameters") or {}
