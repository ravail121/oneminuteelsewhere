from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .api_diagnostics import safe_error, safe_request_id


class BudgetExceeded(RuntimeError):
    pass


class AmbiguousPaidRequest(RuntimeError):
    """A request may have reached the API, so retrying could duplicate a charge."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    if hasattr(value, "model_dump"):
        return _dump(value.model_dump(mode="json", warnings=False))
    if hasattr(value, "to_dict"):
        return _dump(value.to_dict())
    return str(value)


def request_id(value: Any) -> str | None:
    direct = getattr(value, "_request_id", None)
    if direct:
        return str(direct)
    headers = getattr(value, "headers", None)
    if headers:
        return headers.get("x-request-id") or headers.get("X-Request-Id")
    return None


def exception_request_id(error: BaseException) -> str | None:
    value = getattr(error, "request_id", None)
    if value:
        return str(value)
    response = getattr(error, "response", None)
    return request_id(response)


class CostLedger:
    def __init__(self, path: Path, allowance_usd: float, pricing: dict[str, Any], *,
                 before_request=None, on_change=None):
        self.before_request = before_request
        self.on_change = on_change
        self.path = path
        self.allowance_usd = float(allowance_usd)
        if not math.isfinite(self.allowance_usd) or self.allowance_usd <= 0:
            raise ValueError("Allowance must be a finite positive amount")
        self.pricing = pricing
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            recorded = float(self.data["allowance_usd"])
            if abs(recorded - self.allowance_usd) > 1e-9:
                raise ValueError(
                    f"Resume allowance must remain ${recorded:.2f}; got ${self.allowance_usd:.2f}"
                )
        else:
            self.data = {
                "schema_version": 1,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "allowance_usd": self.allowance_usd,
                "currency": "USD",
                "billing_confirmation": (
                    "Calculated from API-returned usage where available; this report is not an "
                    "invoice and does not confirm account billing."
                ),
                "pricing_as_of": pricing["pricing_as_of"],
                "pricing_sources": pricing["pricing_sources"],
                "requests": [],
            }
            self.save()

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.data["requests"]

    def save(self) -> None:
        known = [
            float(item["calculated_cost_usd"])
            for item in self.requests
            if item.get("calculated_cost_usd") is not None
        ]
        unknown = [
            item
            for item in self.requests
            if str(item.get("charge_status", "")).startswith("unknown")
        ]
        self.data["updated_at"] = utc_now()
        self.data["summary"] = {
            "calculated_estimate_usd": round(sum(known), 6),
            "confirmed_billing_usd": None,
            "unknown_charge_requests": len(unknown),
            "remaining_before_unknown_charges_usd": round(
                max(0.0, self.allowance_usd - sum(known)), 6
            ),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)
        if self.on_change:
            self.on_change(self)

    def add_local_stage(self, stage: str, detail: str) -> None:
        if any(item.get("stage") == stage and item.get("kind") == "local" for item in self.requests):
            return
        self.requests.append(
            {
                "kind": "local",
                "stage": stage,
                "status": "completed",
                "model": None,
                "request_id": None,
                "usage": None,
                "calculated_cost_usd": 0.0,
                "cost_basis": "no API call",
                "charge_status": "not_applicable",
                "detail": detail,
                "started_at": utc_now(),
                "completed_at": utc_now(),
            }
        )
        self.save()

    def calculated_total(self) -> float:
        return sum(
            float(item["calculated_cost_usd"])
            for item in self.requests
            if item.get("calculated_cost_usd") is not None
        )

    def stage_succeeded(self, stage: str) -> bool:
        return any(
            item.get("stage") == stage and item.get("status") == "succeeded"
            for item in self.requests
        )

    def ensure_budget(self, reserve_usd: float, stage: str) -> None:
        if not math.isfinite(reserve_usd) or reserve_usd <= 0:
            raise ValueError("Request reserve must be a finite positive amount")
        if any(
            item.get("charge_status") in {
                "unknown_ambiguous_failure", "unknown_usage_unestimated", "unknown_in_progress"
            }
            for item in self.requests
        ):
            raise BudgetExceeded(
                "A prior request has an uncertain charge; refusing further paid work until billing is checked"
            )
        remaining = self.allowance_usd - self.calculated_total()
        if reserve_usd > remaining + 1e-9:
            raise BudgetExceeded(
                f"{stage} reserves ${reserve_usd:.2f}, but only ${remaining:.2f} remains"
            )

    def paid_call(
        self,
        *,
        stage: str,
        model: str,
        reserve_usd: float,
        call: Callable[[], Any],
        usage_getter: Callable[[Any], Any],
        cost_calculator: Callable[[Any], tuple[float | None, str]],
        max_known_zero_charge_retries: int = 6,
    ) -> Any:
        retry_count = 0
        while True:
            if self.before_request:
                self.before_request(reserve_usd, stage)
            self.ensure_budget(reserve_usd, stage)
            item: dict[str, Any] = {
                "kind": "api",
                "stage": stage,
                "status": "in_progress",
                "model_requested": model,
                "model_returned": None,
                "attempt_number": 1
                + sum(1 for prior in self.requests if prior.get("stage") == stage),
                "automatic_retry_count": retry_count,
                "request_id": None,
                "pricing_source": self._pricing_source(stage),
                "pricing_as_of": self.data["pricing_as_of"],
                "usage": None,
                "reserve_before_request_usd": reserve_usd,
                "calculated_cost_usd": None,
                "cost_basis": "pending",
                "charge_status": "unknown_in_progress",
                "started_at": utc_now(),
                "completed_at": None,
            }
            self.requests.append(item)
            self.save()
            try:
                response = call()
                break
            except Exception as error:  # noqa: BLE001 - every ambiguous paid failure must halt without leaking its payload
                diagnostic = safe_error(error)
                # A genuine synchronous 4xx means the provider rejected the request before
                # producing any output: a known $0 charge. Anything without that confirmation
                # (timeout, connection drop, 5xx) stays ambiguous, is never auto-retried, and
                # keeps the project-wide lock, since the request may have gone through.
                known_zero_charge = diagnostic.get("certain_zero_charge", False)
                item.update(
                    status="failed",
                    request_id=safe_request_id(exception_request_id(error)),
                    cost_basis=("Provider rejected the request before producing output; API usage/charge "
                                "was not returned" if known_zero_charge else
                                "Request failed; API usage/charge was not returned"),
                    charge_status="known_zero_charge_rejected" if known_zero_charge else "unknown_ambiguous_failure",
                    completed_at=utc_now(),
                    error_type=diagnostic["error_type"], diagnostic=diagnostic,
                    error_message=diagnostic["guidance"],
                )
                self.save()
                if known_zero_charge and retry_count < max_known_zero_charge_retries:
                    retry_count += 1
                    continue
                if known_zero_charge:
                    raise AmbiguousPaidRequest(
                        f"{stage} failed {retry_count + 1} times in a row (never billed). "
                        f"{diagnostic['guidance']} This may be a real content issue, not just noise; resume to try again."
                    ) from None
                raise AmbiguousPaidRequest(
                    f"{stage} failed. {diagnostic['guidance']} Charges remain unknown; no automatic retry."
                ) from None
        # Backend-only archive, written before parsing assets or calculating usage.
        # Never served by the dashboard. A crash here leaves an in-progress charge
        # and blocks a repeat instead of silently spending twice.
        if hasattr(response, "model_dump"):
            archive = self.path.parent / "api-responses"
            archive.mkdir(exist_ok=True)
            target = archive / f"{len(self.requests):04d}-{stage}.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(_dump(response), ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
        usage = usage_getter(response)
        cost, basis = cost_calculator(usage)
        charge_status = (
            "unknown_usage_unestimated"
            if cost is None
            else "unknown_usage_estimated"
            if usage is None or basis.startswith("Conservative per-request estimate")
            else "estimated_from_usage_not_billing_confirmation"
        )
        item.update(
            status="succeeded",
            model_returned=(
                str(response.model)
                if getattr(response, "model", None) is not None
                else None
            ),
            request_id=request_id(response),
            usage=_dump(usage),
            calculated_cost_usd=None if cost is None else round(float(cost), 8),
            cost_basis=basis,
            charge_status=charge_status,
            completed_at=utc_now(),
        )
        self.save()
        return response

    def _pricing_source(self, stage: str) -> str:
        if stage.startswith("image_") and stage != "image_prompts":
            kind = "image"
        elif stage == "speech_generation":
            kind = "speech"
        elif stage == "caption_alignment":
            kind = "alignment"
        else:
            kind = "text"
        sources = self.data["pricing_sources"]
        return str(sources.get(kind) or sources.get("text") or "unknown")

    def revise_latest(
        self,
        stage: str,
        *,
        calculated_cost_usd: float | None,
        cost_basis: str,
        charge_status: str | None = None,
        usage: Any = None,
    ) -> None:
        for item in reversed(self.requests):
            if item.get("stage") == stage and item.get("kind") == "api":
                item["calculated_cost_usd"] = (
                    None if calculated_cost_usd is None else round(calculated_cost_usd, 8)
                )
                item["cost_basis"] = cost_basis
                if charge_status is not None:
                    item["charge_status"] = charge_status
                if usage is not None:
                    item["usage"] = _dump(usage)
                self.save()
                return
        raise ValueError(f"No request found for stage {stage}")


def text_cost(usage: Any) -> tuple[float | None, str]:
    if usage is None:
        return None, "API returned no usage; charge unknown"
    raw = _dump(usage)
    input_tokens = raw.get("input_tokens")
    output_tokens = raw.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return None, "API usage omitted input/output token counts; charge unknown"
    details = raw.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    uncached = max(0, int(input_tokens) - cached)
    cache_writes = min(uncached, int(details.get("cache_write_tokens") or 0))
    cost = (uncached * 0.20 + cache_writes * 0.05 + cached * 0.02 + int(output_tokens) * 1.20) / 1_000_000
    return cost, "Calculated from returned usage at standard gpt-5.6-luna token rates"


def image_cost(usage: Any) -> tuple[float | None, str]:
    if usage is None:
        return 0.20, "Conservative per-request estimate; API returned no image usage"
    raw = _dump(usage)
    output_tokens = raw.get("output_tokens")
    input_details = raw.get("input_tokens_details") or {}
    text_tokens = input_details.get("text_tokens")
    image_tokens = input_details.get("image_tokens") or 0
    if output_tokens is None or text_tokens is None:
        return 0.20, "Conservative per-request estimate; returned image usage was incomplete"
    cost = (int(text_tokens) * 5.00 + int(image_tokens) * 8.00 + int(output_tokens) * 30.00) / 1_000_000
    return cost, "Calculated from returned text/image input and image output tokens"


def speech_estimated_cost(duration_seconds: float, text_tokens: int | None = None) -> tuple[float, str]:
    # OpenAI publishes token rates but the binary speech response does not expose usage.
    # Reserve a conservative five cents; report the duration-based approximation separately.
    text_component = (text_tokens or 0) * 0.60 / 1_000_000
    audio_component = duration_seconds / 60.0 * 0.015
    return text_component + audio_component, (
        "Duration-based estimate (~$0.015/min) derived from published $12/M audio-token rate; "
        "speech response returned no usage, so exact charge is unknown"
    )


def transcription_cost(duration_seconds: float) -> tuple[float, str]:
    return duration_seconds / 60.0 * 0.006, (
        "Calculated from measured input duration at the published whisper-1 $0.006/min rate"
    )
