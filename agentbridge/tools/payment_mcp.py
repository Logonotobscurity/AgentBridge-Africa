"""Safety-annotated payment tool wrappers (M-Pesa, Paystack, bank).

Execution primitives (Tools) are strictly separated from read-only state
(Resources). Orchestrators MUST inspect annotations before dispatch:

- readOnly / readOnlyHint     — no side effects
- destructive / destructiveHint — money movement or irreversible change
- idempotent / idempotentHint — safe to retry with the same arguments
"""

from __future__ import annotations

from agentbridge.tools.mcp_types import Tool

AMOUNT_SCHEMA = {
    "type": "object",
    "required": ["amount", "currency", "phone", "idempotency_key"],
    "properties": {
        "amount": {"type": "number", "minimum": 1},
        "currency": {"type": "string", "enum": ["KES", "NGN", "GHS", "UGX", "USD"]},
        "phone": {"type": "string", "description": "MSISDN in international format"},
        "account_reference": {"type": "string"},
        "idempotency_key": {"type": "string", "minLength": 8},
    },
}

QUERY_SCHEMA = {
    "type": "object",
    "required": ["checkout_request_id"],
    "properties": {
        "checkout_request_id": {"type": "string"},
        "idempotency_key": {"type": "string"},
    },
}

MPESA_STK_PUSH = Tool(
    name="mpesa_stk_push",
    description="Initiate M-Pesa mobile money STK push payment request",
    inputSchema=AMOUNT_SCHEMA,
    annotations={
        "readOnly": False,
        "destructive": True,
        "idempotent": False,
    },
)

MPESA_QUERY_STATUS = Tool(
    name="mpesa_stk_query",
    description="Check status of a pending M-Pesa transaction",
    inputSchema=QUERY_SCHEMA,
    annotations={
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
    },
)

MPESA_B2C_DISBURSE = Tool(
    name="mpesa_b2c_disburse",
    description="Disburse funds via M-Pesa B2C (salary / business payment)",
    inputSchema={
        "type": "object",
        "required": ["amount", "currency", "phone", "idempotency_key"],
        "properties": {
            "amount": {"type": "number", "minimum": 1},
            "currency": {"type": "string", "enum": ["KES"]},
            "phone": {"type": "string"},
            "occasion": {"type": "string"},
            "idempotency_key": {"type": "string", "minLength": 8},
        },
    },
    annotations={"readOnly": False, "destructive": True, "idempotent": False},
)

PAYSTACK_INITIALIZE = Tool(
    name="paystack_initialize",
    description="Initialize a Paystack checkout for NGN (and multi-currency) collections",
    inputSchema={
        "type": "object",
        "required": ["amount", "currency", "email", "idempotency_key"],
        "properties": {
            "amount": {"type": "number", "minimum": 1},
            "currency": {"type": "string", "enum": ["NGN", "GHS", "USD", "ZAR"]},
            "email": {"type": "string", "format": "email"},
            "idempotency_key": {"type": "string", "minLength": 8},
        },
    },
    annotations={"readOnly": False, "destructive": True, "idempotent": False},
)

PAYSTACK_VERIFY = Tool(
    name="paystack_verify",
    description="Verify a Paystack transaction by reference (read-only)",
    inputSchema={
        "type": "object",
        "required": ["reference"],
        "properties": {"reference": {"type": "string"}},
    },
    annotations={"readOnly": True, "destructive": False, "idempotent": True},
)

BANK_TRANSFER_NGN = Tool(
    name="bank_transfer_ngn",
    description="Initiate a NGN bank credit transfer (NIBSS-shaped sandbox)",
    inputSchema={
        "type": "object",
        "required": ["amount", "account_number", "bank_code", "idempotency_key"],
        "properties": {
            "amount": {"type": "number", "minimum": 1},
            "currency": {"type": "string", "const": "NGN"},
            "account_number": {"type": "string", "minLength": 10, "maxLength": 10},
            "bank_code": {"type": "string"},
            "narration": {"type": "string"},
            "idempotency_key": {"type": "string", "minLength": 8},
        },
    },
    annotations={"readOnly": False, "destructive": True, "idempotent": False},
)

QUOTE_PAYMENT = Tool(
    name="quote_payment",
    description="Price a transfer across an African rail without moving money",
    inputSchema={
        "type": "object",
        "required": ["amount", "currency", "rail"],
        "properties": {
            "amount": {"type": "number", "minimum": 1},
            "currency": {"type": "string"},
            "rail": {
                "type": "string",
                "enum": ["bank", "ussd", "mobile_money", "mpesa", "paystack", "mtn_momo"],
            },
            "country": {"type": "string"},
        },
    },
    annotations={"readOnly": True, "destructive": False, "idempotent": True},
)

PROCESS_PAYMENT = Tool(
    name="process_payment",
    description="Process a payment through the best healthy regional provider rail",
    inputSchema={
        "type": "object",
        "required": ["amount", "currency", "destination_country", "recipient", "idempotency_key"],
        "properties": {
            "amount": {"type": "number", "minimum": 1},
            "currency": {"type": "string", "enum": ["KES", "NGN", "GHS", "UGX", "ZAR", "USD"]},
            "destination_country": {"type": "string", "pattern": "^[A-Z]{2}$"},
            "recipient": {"type": "string", "minLength": 3},
            "reference": {"type": "string"},
            "idempotency_key": {"type": "string", "minLength": 8},
        },
    },
    annotations={"readOnly": False, "destructive": True, "idempotent": False},
)

CHECK_TRANSACTION = Tool(
    name="check_transaction",
    description="Check a transaction status without changing payment state",
    inputSchema={
        "type": "object",
        "required": ["transaction_id"],
        "properties": {"transaction_id": {"type": "string", "minLength": 1}},
    },
    annotations={"readOnly": True, "destructive": False, "idempotent": True},
)

EXECUTE_PAYMENT = Tool(
    name="execute_payment",
    description="Execute a previously quoted payment. Requires idempotency_key.",
    inputSchema={
        "type": "object",
        "required": ["quote_id", "idempotency_key", "rail"],
        "properties": {
            "quote_id": {"type": "string"},
            "idempotency_key": {"type": "string", "minLength": 8},
            "rail": {"type": "string"},
            "amount": {"type": "number"},
        },
    },
    annotations={"readOnly": False, "destructive": True, "idempotent": False},
)

PAYMENT_TOOLS = [
    QUOTE_PAYMENT,
    CHECK_TRANSACTION,
    MPESA_QUERY_STATUS,
    PAYSTACK_VERIFY,
    PROCESS_PAYMENT,
    MPESA_STK_PUSH,
    MPESA_B2C_DISBURSE,
    PAYSTACK_INITIALIZE,
    BANK_TRANSFER_NGN,
    EXECUTE_PAYMENT,
]

TOOLS_BY_NAME = {t.name: t for t in PAYMENT_TOOLS}


def require_idempotency(tool_name: str, arguments: dict) -> None:
    tool = TOOLS_BY_NAME.get(tool_name)
    if tool is None:
        return
    from agentbridge.tools.mcp_types import annotations_dict

    anns = annotations_dict(tool)
    if anns.get("destructive") and not arguments.get("idempotency_key"):
        raise ValueError(f"{tool_name} requires idempotency_key")
