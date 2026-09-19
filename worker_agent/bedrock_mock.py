"""Mock LLM client for local pipeline testing against LocalStack, never
touches AWS or counts against real Bedrock quota. Returns fixture JSON
shaped like the real Bedrock client's output (see bedrock_client.py),
based on the three canonical demo scenarios documented in backstop-prac's
test_tickets/README.md: a clean duplicate charge (Thornbury Logistics), a
proration mismatch (Ashgrove Media), and a divergence case (Milldale
Studio).

Scenario is picked by matching known substrings in the prompt, the
customer's name/email domain or a distinctive line-item description.
Anything that doesn't match falls back to a generic escalate/billing_inquiry
response, so ad hoc test prompts outside the three fixtures still get back
something schema-valid rather than an error.

Selected via LLM_CLIENT=mock, see bedrock_client.get_llm_client().
"""

_CLASSIFICATION_REQUIRED = {"is_billing_relevant", "intent", "urgency", "confidence"}
_REASONING_REQUIRED = {
    "action_type",
    "amount",
    "currency",
    "target_transaction_id",
    "target_subscription_id",
    "rationale",
    "confidence",
    "customer_message",
}
_VERIFIER_REQUIRED = {
    "action_type",
    "amount",
    "currency",
    "target_transaction_id",
    "target_subscription_id",
    "rationale",
    "confidence",
}
_COMMAND_REQUIRED = {
    "action_type",
    "amount",
    "currency",
    "target_transaction_id",
    "target_subscription_id",
    "rationale",
    "customer_message",
}

_SCENARIOS = {
    "thornbury": {
        "match": ("thornbury", "thornbury-logistics-demo"),
        "classification": {
            "is_billing_relevant": True,
            "intent": "duplicate_charge",
            "urgency": "medium",
            "confidence": 0.93,
        },
        "action": {
            "action_type": "refund",
            "amount": 4500,
            "currency": "usd",
            "target_transaction_id": "ch_mock_thornbury_dup",
            "target_subscription_id": None,
            "rationale": (
                "Two identical $45.00 charges posted the same day with no proration "
                "context, the second is a duplicate of the first."
            ),
            "confidence": 0.92,
            "customer_message": None,
        },
    },
    "ashgrove": {
        "match": ("ashgrove", "ashgrove-media-demo"),
        "classification": {
            "is_billing_relevant": True,
            "intent": "refund_request",
            "urgency": "high",
            "confidence": 0.88,
        },
        "action": {
            "action_type": "no_action",
            "amount": 0,
            "currency": "usd",
            "target_transaction_id": None,
            "target_subscription_id": None,
            "rationale": (
                "The $30.00 charge is a Stripe-computed proration from the Basic to Pro "
                "plan upgrade, not an unauthorized or duplicate charge."
            ),
            "confidence": 0.87,
            "customer_message": (
                "This charge was the prorated amount from upgrading your plan from Basic "
                "to Pro partway through your billing cycle. It is not a separate or "
                "unauthorized charge, so no refund is being issued."
            ),
        },
    },
    "milldale": {
        "match": ("milldale", "milldale-studio-demo", "additional seat license"),
        "classification": {
            "is_billing_relevant": True,
            "intent": "duplicate_charge",
            "urgency": "low",
            "confidence": 0.81,
        },
        "action": {
            "action_type": "no_action",
            "amount": 0,
            "currency": "usd",
            "target_transaction_id": None,
            "target_subscription_id": None,
            "rationale": (
                "The two $89.00 charges are for different things, one is the "
                "subscription renewal and the other is a separate Additional Seat "
                "License purchase that happens to cost the same amount."
            ),
            "confidence": 0.86,
            "customer_message": (
                "These are two separate $89.00 charges, your subscription renewal and "
                "a separate Additional Seat License purchase. Both are valid, so no "
                "refund is being issued."
            ),
        },
    },
}

_DEFAULT_CLASSIFICATION = {
    "is_billing_relevant": True,
    "intent": "billing_inquiry",
    "urgency": "medium",
    "confidence": 0.7,
}
_DEFAULT_ACTION = {
    "action_type": "escalate",
    "amount": 0,
    "currency": "usd",
    "target_transaction_id": None,
    "target_subscription_id": None,
    "rationale": "No fixture scenario matched this prompt, escalating rather than guessing.",
    "confidence": 0.5,
    "customer_message": None,
}


def _match_scenario(prompt: str) -> dict | None:
    lowered = prompt.lower()
    for scenario in _SCENARIOS.values():
        if any(needle in lowered for needle in scenario["match"]):
            return scenario
    return None


def _schema_kind(schema: dict) -> str:
    required = set(schema.get("required", []))
    if required == _CLASSIFICATION_REQUIRED:
        return "classification"
    if required == _REASONING_REQUIRED:
        return "reasoning"
    if required == _VERIFIER_REQUIRED:
        return "verifier"
    if required == _COMMAND_REQUIRED:
        return "command"
    raise RuntimeError(f"MockLLMClient does not recognize this schema's required fields: {sorted(required)}")


def _project_action(action: dict, kind: str) -> dict:
    if kind == "verifier":
        return {k: v for k, v in action.items() if k != "customer_message"}
    if kind == "command":
        return {k: v for k, v in action.items() if k != "confidence"}
    return dict(action)


class MockLLMClient:
    """Fixture-backed stand-in for BedrockClient. Implements the same
    LLMClient protocol declared in bedrock_client.py."""

    async def generate_json(
        self,
        prompt: str,
        schema: dict,
        model: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> dict:
        kind = _schema_kind(schema)
        scenario = _match_scenario(prompt)

        if kind == "classification":
            return dict(scenario["classification"] if scenario else _DEFAULT_CLASSIFICATION)

        action = scenario["action"] if scenario else _DEFAULT_ACTION
        return _project_action(action, kind)
