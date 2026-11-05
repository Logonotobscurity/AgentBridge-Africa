"""Versioned, fail-closed minimization for telemetry and evaluation exports.

This policy is intentionally lossy: unknown strings are omitted rather than
copied into observability or evaluation datasets. It is not a general-purpose
DLP engine and must not be described as anonymization.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

POLICY_VERSION = "2026-11-05.v1"
REDACTED = "[REDACTED]"
OMITTED = "[OMITTED]"

_SENSITIVE_KEYS = frozenset({
    # Nigerian and other government identity identifiers.
    "bvn", "bvnnumber", "bankverificationnumber",
    "nin", "ninnumber", "nationalid", "nationalidnumber",
    "nationalidentitynumber", "nationalidentificationnumber",
    "identitynumber", "identitydocumentnumber", "passport",
    "passportnumber", "driverslicence", "driverslicencenumber",
    "driverlicense", "driverlicensenumber", "taxid",
    "taxidentificationnumber", "tin", "ssn",
    # Financial and payment credentials/details.
    "accountnumber", "accountno", "acctnumber", "acctno",
    "bankaccount", "bankaccountnumber", "bankaccountno",
    "bankdetails", "bankname", "bankbranch", "bankcode", "bankaccountname", "bankroutingnumber", "accountdetails", "accountreference", "accountref", "accountholder",
    "accountholdername", "accountname", "iban", "routingnumber",
    "sortcode", "swift", "bic", "cardnumber", "pan", "cvv", "cvc",
    "pin", "otp", "passkey", "authorization", "cookie",
    "setcookie", "password", "secret", "clientsecret", "apikey",
    "accesstoken", "refreshtoken", "bearertoken", "privatekey",
    "idempotencykey", "checkoutrequestid",
    # Personal identity and contact fields / containers.
    "customer", "customerid", "customername", "customerprofile", "customeremail", "customerphone", "customeraddress",
    "beneficiary", "beneficiaryid", "beneficiaryname",
    "beneficiarydetails", "beneficiaryemail", "beneficiaryphone", "beneficiaryaddress", "recipient", "recipientid", "recipientname", "recipientemail", "recipientphone", "recipientaddress",
    "firstname", "middlename", "lastname", "fullname", "surname",
    "email", "emailaddress", "phone", "phonenumber", "mobile",
    "mobilenumber", "msisdn", "dateofbirth", "dob", "birthdate",
    "address", "streetaddress", "residentialaddress",
    "postaladdress", "homeaddress", "postalcode", "postcode",
    "transactionreference", "providerreference",
    "transactionid", "quoteid",
})

_SENSITIVE_PREFIXES = (
    "bvn", "nin", "bankaccountnumber", "accountnumber", "accountno",
    "acctnumber", "acctno", "nationalidnumber", "nationalidentitynumber",
    "passportnumber", "taxidentificationnumber", "emailaddress",
    "phonenumber", "mobilenumber", "msisdn", "beneficiaryname",
    "recipientname", "customername", "streetaddress",
    "residentialaddress", "accesstoken", "refreshtoken", "bankname", "bankbranch", "bankcode", "accountreference", "customeremail", "customerphone", "customeraddress", "beneficiaryemail", "beneficiaryphone", "beneficiaryaddress", "recipientemail", "recipientphone", "recipientaddress",
)

_FREE_TEXT_KEYS = frozenset({
    "goal", "prompt", "systemprompt", "developerprompt", "userprompt",
    "context", "rationale", "reason", "reasoning", "thought",
    "message", "messages", "notes", "description", "details",
    "comment", "comments", "text", "transcript", "conversation",
    "body", "requestbody", "responsebody", "callbackbody", "rawbody",
    "narration", "memo", "stacktrace", "exceptionmessage",
    "errormessage", "error", "query", "summary", "content",
    "inputtext", "outputtext", "rawinput", "rawoutput",
})

_SAFE_TOOLS = frozenset({
    "mpesa_stk_push", "mpesa_stk_query", "mpesa_b2c_disburse",
    "paystack_initialize", "paystack_verify", "bank_transfer_ngn",
    "quote_payment", "process_payment", "check_transaction",
    "execute_payment",
})
_SAFE_ENUMS: dict[str, frozenset[str]] = {
    "tool": _SAFE_TOOLS,
    "toolname": _SAFE_TOOLS,
    "oauthscopes": frozenset({"payments:execute", "payments:read", "payments:status"}),
    "currency": frozenset({"KES", "NGN", "GHS", "UGX", "USD", "ZAR", "RWF", "TZS", "EGP", "MAD", "ETB", "EUR", "GBP"}),
    "provider": frozenset({"paystack", "mpesa", "daraja", "mtn_momo", "bank"}),
    "rail": frozenset({"bank", "ussd", "mobile_money", "mpesa", "paystack", "mtn_momo"}),
    "status": frozenset({
        "created", "awaiting_confirmation", "submitted", "pending_callback",
        "succeeded", "failed", "reversed", "draft", "pending_approval",
        "callback_received", "confirmed", "unknown", "success", "pending",
        "processing", "rejected", "expired", "ongoing", "accepted",
        "denied", "approved", "cancelled", "timeout", "error", "unset", "ok",
    }),
    "paymentstatus": frozenset({
        "created", "awaiting_confirmation", "submitted", "pending_callback",
        "succeeded", "failed", "reversed", "draft", "pending_approval",
        "callback_received", "confirmed", "unknown", "success", "pending",
        "processing", "rejected", "expired", "ongoing", "accepted",
        "denied", "approved", "cancelled", "timeout", "error",
    }),
    "source": frozenset({"otel", "langfuse"}),
    "environment": frozenset({"test", "development", "staging", "production", "sandbox"}),
    "decision": frozenset({"allow", "deny", "approved", "rejected", "blocked", "escalate", "require_approval", "unknown"}),
    "policydecision": frozenset({"allow", "deny", "approved", "rejected", "blocked", "escalate", "require_approval", "unknown"}),
    "outcome": frozenset({"success", "failed", "pending", "unknown", "timeout", "rejected", "approved"}),
    "event": frozenset({
        "charge.success", "transfer.success", "transfer.failed",
        "transfer.reversed", "refund.processed", "refund.failed",
    }),
    "country": frozenset({"NG", "KE", "GH", "UG", "ZA", "RW", "TZ", "EG", "MA", "ET"}),
    "destinationcountry": frozenset({"NG", "KE", "GH", "UG", "ZA", "RW", "TZ", "EG", "MA", "ET"}),
    "spanname": frozenset(_SAFE_TOOLS | {
        "agent.plan", "agent.execute", "tool.dispatch", "tool.result",
        "policy.evaluate", "budget.evaluate", "payment.quote",
        "payment.execute", "payment.reconcile", "webhook.verify",
        "webhook.ingest", "llm.generate", "checkpoint.write",
    }),
    "code": frozenset({"UNSET", "OK", "ERROR"}),
    "servicename": frozenset({"agentbridge-africa"}),
    "reasons": frozenset({
        "destructive_without_idempotency_key", "execute_without_scope",
        "read_only_tool_mutated_state", "retry_budget_exceeded",
    }),
    "privacypolicyversion": frozenset({POLICY_VERSION}),
}

_SAFE_NUMERIC_KEYS = frozenset({
    "amount", "amountminor", "amountinminorunits", "cost", "costusd", "spendusd", "llmcostusd",
    "processingfeeusd", "retrycount", "maxretries", "paymentretrycount",
    "paymentmaxretries", "toolargumentaccuracy", "executiongrounding",
    "failureretrybehavior", "latencyms",
    "httpstatus", "statuscode", "score", "budget", "budgetusd",
    "threshold", "resultcode", "starttimeunixnano", "endtimeunixnano",
    "attempt", "attemptcount", "duration", "durationms", "stepcount",
})
_IDENTIFIER_KEYS = frozenset({
    "id", "traceid", "spanid", "parentspanid",
    "sourcetraceid", "sourceobservationid",
})
_ID_RE = re.compile(r"^(?:(?:[0-9a-fA-F]{16}|[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9A-HJKMNP-TV-Z]{26})|(?:otel|langfuse)_(?:missing|(?:[0-9a-fA-F]{16}|[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9A-HJKMNP-TV-Z]{26})))$")


def normalize_key(key: Any) -> str:
    """Normalize case, punctuation, underscores and camelCase spellings."""
    return "".join(character for character in str(key).casefold() if character.isalnum())


def is_sensitive_key(key: Any) -> bool:
    normalized = normalize_key(key)
    return normalized in _SENSITIVE_KEYS or any(
        normalized.startswith(prefix) or normalized.endswith(prefix)
        for prefix in _SENSITIVE_PREFIXES
    )


def safe_identifier(value: Any) -> str:
    """Keep only opaque, structurally valid trace/span identifiers.

    A valid identifier is still pseudonymous and linkable; this function does
    not transform it into anonymous data.
    """
    text = str(value or "").strip()
    return text if _ID_RE.fullmatch(text) else ""


def _safe_scalar(value: Any, normalized_key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if normalized_key in _SAFE_NUMERIC_KEYS else OMITTED
    if isinstance(value, str):
        if normalized_key in _IDENTIFIER_KEYS:
            return safe_identifier(value) or OMITTED
        if normalized_key in _SAFE_ENUMS:
            candidate = value.strip()
            allowed = _SAFE_ENUMS[normalized_key]
            if candidate in allowed or candidate.casefold() in allowed:
                return candidate
        return OMITTED
    return OMITTED


def redact_sensitive_data(value: Any, key: str = "") -> Any:
    """Return a minimized JSON-compatible value using the v1 policy.

    Unknown strings and unclassified numeric values are omitted by default.
    Sensitive keys are redacted as a whole, including nested objects. Free-text
    keys are omitted wholesale. Only explicitly allowlisted enums, identifiers,
    booleans and operational numeric fields survive.
    """
    normalized_key = normalize_key(key) if key else ""
    if normalized_key and is_sensitive_key(normalized_key):
        return REDACTED
    if normalized_key in _FREE_TEXT_KEYS:
        return OMITTED
    if isinstance(value, Mapping):
        return {
            str(child_key): redact_sensitive_data(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive_data(item, key) for item in value]
    return _safe_scalar(value, normalized_key)
