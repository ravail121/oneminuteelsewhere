import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from elsewhere.api_diagnostics import (
    LocalRequestError,
    public_diagnostic,
    safe_error,
    validate_image_request,
)
from elsewhere.costs import AmbiguousPaidRequest, BudgetExceeded, CostLedger
from elsewhere.openai_service import AIService


def bad_request(body):
    return BadRequestError("private exception text", response=httpx.Response(400,
        request=httpx.Request("POST", "https://api.openai.com/v1/images/generations"),
        headers={"x-request-id": "req_offline_diagnostic"}), body=body)


@pytest.mark.parametrize("code,param,reason", [("invalid_value", "size", "parameter"),
    ("content_policy_violation", None, "safety"), ("organization_verification_required", None, "verification"),
    ("model_not_found", "model", "access"), ("insufficient_quota", None, "quota")])
def test_safe_error_preserves_actionable_fields_without_private_payload(code, param, reason):
    error = bad_request({"code": code, "param": param, "message": "sk-test-private-secret story prompt email@example.com"})
    result = safe_error(error)
    assert result["error_code"] == code and result["parameter"] == param
    assert result["http_status"] == 400 and result["reason"] == reason
    assert "sk-test" not in json.dumps(result) and "email@example.com" not in json.dumps(result)


def test_nested_unknown_or_malicious_error_fields_are_not_echoed():
    error = bad_request({"error": {"code": ["sk-private"], "param": {"secret": "sk-private"}, "message": "private prompt"}})
    result = safe_error(error)
    assert result["error_code"] is None and result["parameter"] is None
    assert "private" not in json.dumps(result)
    diagnostic = public_diagnostic({"request_id": "sk-secret", "diagnostic": {"guidance": "private",
        "error_code": "sk-private", "reason": ["bad"], "http_status": True}})
    assert diagnostic["request_id"] is None and diagnostic["http_status"] is None
    assert "private" not in json.dumps(diagnostic)


def test_failure_persists_safe_reason_but_keeps_unknown_charge_and_blocks_retry(tmp_path):
    ledger = CostLedger(tmp_path / "cost-report.json", .5, {"pricing_as_of": "2026-09-13", "pricing_sources": {}})
    count = 0

    def fail():
        nonlocal count
        count += 1
        raise bad_request({"code": "invalid_value", "param": "size", "message": "sk-secret prompt data"})

    with pytest.raises(AmbiguousPaidRequest, match="parameter"):
        ledger.paid_call(stage="image_01", model="mock", reserve_usd=.1, call=fail,
                         usage_getter=lambda _: None, cost_calculator=lambda _: (None, "unknown"))
    record = ledger.requests[-1]
    assert record["diagnostic"]["parameter"] == "size"
    assert record["request_id"] == "req_offline_diagnostic"
    assert record["calculated_cost_usd"] is None and record["charge_status"] == "unknown_ambiguous_failure"
    assert "sk-secret" not in ledger.path.read_text() and "prompt data" not in ledger.path.read_text()
    with pytest.raises(BudgetExceeded):
        ledger.ensure_budget(.1, "image_01")
    assert count == 1


@pytest.mark.parametrize("quality,size,prompt", [("ultra", "1024x1536", "test"),
    ("medium", "1x1", "test"), ("medium", "1024x1536", "x" * 32001)])
def test_invalid_image_settings_fail_locally(quality, size, prompt):
    with pytest.raises(LocalRequestError):
        validate_image_request("gpt-image-2.5-flare", size, quality, "png", prompt)


def test_current_request_parameters_pass_without_a_provider_call():
    validate_image_request("gpt-image-2.5-flare", "1024x1536", "medium", "png", "Saved scene prompt " * 300)


def test_service_preflight_runs_before_creating_a_cost_record(tmp_path):
    from elsewhere.config import load_settings
    service = object.__new__(AIService)
    service.settings = load_settings("config.yaml")
    service.settings.raw["models"]["image_quality"] = "not-a-quality"
    service.ledger = SimpleNamespace(paid_call=lambda **_: pytest.fail("Must not start a paid attempt"))
    with pytest.raises(LocalRequestError):
        service.create_image("Local test prompt only", tmp_path / "none.png", "short", 1)
