"""Allowlisted API diagnostics: never persist exception text, prompts or headers."""
import re

KINDS = {"BadRequestError", "AuthenticationError", "PermissionDeniedError", "RateLimitError",
         "NotFoundError", "APIConnectionError", "APITimeoutError", "InternalServerError",
         "TimeoutError", "ConnectionError", "ValueError", "TypeError"}
CODES = {"invalid_value", "invalid_parameter", "invalid_request_error", "unsupported_parameter",
         "unsupported_value", "model_not_found", "content_policy_violation", "moderation_blocked",
         "safety_violation", "insufficient_quota", "rate_limit_exceeded", "billing_hard_limit_reached",
         "organization_verification_required", "permission_denied", "invalid_api_key"}
PARAMETERS = {"model", "prompt", "size", "quality", "output_format", "response_format", "n",
              "background", "moderation", "output_compression", "stream", "partial_images"}
GUIDANCE = {
    "unknown": "The provider's specific reason is unavailable. Use the request ID for support before retrying.",
    "parameter": "The provider rejected a request parameter. Correct the named setting before retrying.",
    "verification": "The provider requires organization verification. Check verification in your OpenAI account.",
    "access": "The provider rejected model access or authentication. Check the selected model and backend account permissions.",
    "safety": "The provider rejected the image under its content rules. Review the request; do not bypass the safety decision.",
    "quota": "The provider reported a quota or rate limit. Check account limits and billing before retrying.",
    "connection": "No reliable completion response arrived. Check the request and billing before any retry.",
}


class LocalRequestError(ValueError):
    pass


def safe_request_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"req_[A-Za-z0-9_-]{3,100}", value) else None


def safe_error(error):
    body = getattr(error, "body", None)
    body = body if isinstance(body, dict) else {}
    body = body.get("error", body)
    body = body if isinstance(body, dict) else {}
    code, parameter = body.get("code"), body.get("param")
    code = code if isinstance(code, str) and code in CODES else None
    parameter = parameter if isinstance(parameter, str) and parameter in PARAMETERS else None
    status = getattr(error, "status_code", None)
    status = status if type(status) is int and 400 <= status <= 599 else None
    kind = type(error).__name__ if type(error).__name__ in KINDS else "UnknownError"
    reason = "unknown"
    message = body.get("message", "")  # Inspect only; never store/return this private text.
    if code == "organization_verification_required" or (isinstance(message, str) and
            re.search(r"organization.{0,50}(?:must be verified|verification required)", message, re.IGNORECASE)):
        reason = "verification"
    elif code in {"content_policy_violation", "moderation_blocked", "safety_violation"}:
        reason = "safety"
    elif code in {"insufficient_quota", "rate_limit_exceeded", "billing_hard_limit_reached"} or status == 429:
        reason = "quota"
    elif code in {"model_not_found", "permission_denied", "invalid_api_key"} or status in {401, 403, 404}:
        reason = "access"
    elif parameter or code in {"invalid_value", "invalid_parameter", "unsupported_parameter", "unsupported_value"}:
        reason = "parameter"
    elif kind in {"APIConnectionError", "APITimeoutError", "TimeoutError", "ConnectionError"}:
        reason = "connection"
    # A genuine synchronous 4xx means the provider replied and rejected the request
    # before producing any output; a 5xx or no response at all leaves real doubt
    # about whether generation started, so only 4xx counts as a known zero charge.
    certain_zero_charge = status is not None and 400 <= status < 500
    return {"http_status": status, "error_type": kind, "error_code": code, "parameter": parameter,
            "reason": reason, "guidance": GUIDANCE[reason], "raw_message_saved": False,
            "certain_zero_charge": certain_zero_charge}


def public_diagnostic(record):
    saved = record.get("diagnostic") or {}
    saved = saved if isinstance(saved, dict) else {}
    reason = saved.get("reason")
    reason = reason if isinstance(reason, str) and reason in GUIDANCE else "unknown"
    kind = saved.get("error_type", record.get("error_type"))
    code, parameter, status = saved.get("error_code"), saved.get("parameter"), saved.get("http_status")
    return {"request_id": safe_request_id(record.get("request_id")),
            "error_type": kind if isinstance(kind, str) and kind in KINDS else "UnknownError",
            "http_status": status if type(status) is int and 400 <= status <= 599 else None,
            "error_code": code if isinstance(code, str) and code in CODES else None,
            "parameter": parameter if isinstance(parameter, str) and parameter in PARAMETERS else None,
            "guidance": GUIDANCE[reason], "legacy_missing_details": not bool(saved),
            "billing": ("The provider replied with a definite rejection before producing any output; "
                        "this is not expected to be billed." if saved.get("certain_zero_charge")
                        else "Unknown unless returned usage or separate billing evidence establishes the charge."),
            "automatic_retry": False}


def validate_image_request(model, size, quality, output_format, prompt):
    """Current documented settings; local failures never reach the paid ledger."""
    family = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    if family not in {"gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5", "gpt-image-2",
                       "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"}:
        raise LocalRequestError("Image model has no verified local parameter contract. No image request was sent.")
    qualities = {"low", "medium", "high", "auto"}
    if family.startswith("gpt-image-2.5"):
        qualities |= {"xhigh", "max"}
    if quality not in qualities:
        raise LocalRequestError("Image quality is unsupported for the selected model. No image request was sent.")
    if size not in {"1024x1024", "1024x1536", "1536x1024"} or output_format not in {"png", "jpeg", "webp"}:
        raise LocalRequestError("Image size or output format is outside the verified local contract. No image request was sent.")
    if not prompt.strip() or len(prompt) > 32000:
        raise LocalRequestError("Image prompt must contain 1–32000 characters. No image request was sent.")
