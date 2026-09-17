"""Explicitly authorized, single-image diagnostic; never called by normal production."""
from __future__ import annotations

import hashlib
import json

from .api_diagnostics import safe_error
from .config import Settings
from .costs import BudgetExceeded, CostLedger, utc_now
from .openai_service import AIService, save_json


def run_once(store, project_id, original_request_id, *, approved=False, service_factory=AIService):
    """Caller must hold the dashboard process lock and have explicit one-attempt consent.

    The old uncertain charge stays untouched. A separate ledger records the new
    attempt. The exclusive directory is a durable fence against any repeat.
    """
    if approved is not True:
        raise ValueError("Explicit approval for one diagnostic attempt is required")
    project = store.load(project_id)
    directory = store.revision_dir(project)
    if project["busy"] or project["status"] != "failed":
        raise ValueError("Only an idle failed project may receive this diagnostic")
    destination = directory / "scene-01.png"
    if destination.exists():
        raise ValueError("Image 1 is already saved; it must not be regenerated")
    report = store.report(project_id)
    uncertain = [r for r in report["requests"] if str(r.get("charge_status", "")).startswith("unknown")]
    if (len(uncertain) != 1 or uncertain[0].get("request_id") != original_request_id
            or uncertain[0].get("stage") != "image_01" or uncertain[0].get("status") != "failed"):
        raise ValueError("The uncertain request does not match the explicitly authorized diagnostic")
    reserve, prior_hold = .20, .20
    if report["calculated_estimate_usd"] + prior_hold + reserve > min(.50, report["allowance_usd"]) + 1e-9:
        raise BudgetExceeded("Known costs plus both $0.20 reserves exceed the approved allowance")
    settings = Settings(store.base.root, json.loads((directory / "settings-snapshot.json").read_text()))
    settings.raw["safety"]["max_sdk_retries"] = 0
    settings.raw["costs"]["request_reserves_usd"]["image"] = reserve
    prompt = json.loads((directory / "image-prompts.json").read_text())["prompts"][0]
    attempt = directory / "image-diagnostic-0001"
    attempt.mkdir(exist_ok=False)  # Never remove this fence, including after a crash.
    state = {"approved_at": utc_now(), "scope": "Exactly one image_01 attempt; stop afterwards",
             "original_request_id": original_request_id, "prior_unknown_charge_hold_usd": prior_hold,
             "new_request_reserve_usd": reserve, "known_cost_before_usd": report["calculated_estimate_usd"],
             "project_allowance_usd": report["allowance_usd"], "maximum_request_count": 1,
             "automatic_retries": 0, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
             "model": settings.models["image"], "status": "prepared",
             "billing_note": "Reserves are local estimates, not provider-enforced caps or confirmed charges."}
    save_json(attempt / "diagnostic-state.json", state)

    def guard(amount, stage):
        store.check_cancel(project_id)
        if stage != "image_01" or amount != reserve or ledger.requests:
            raise BudgetExceeded("Only the single explicitly approved Image 1 request is permitted")

    ledger = CostLedger(attempt / "cost-report.json", reserve, settings.costs, before_request=guard)
    try:
        service_factory(settings, ledger).create_image(prompt, destination, "short", 1)
        state["status"] = "image_saved"
    except Exception as error:  # noqa: BLE001 - never expose raw SDK payloads
        state.update(status="stopped", diagnostic=safe_error(error))
        if ledger.requests and ledger.requests[-1].get("diagnostic"):
            state["diagnostic"] = ledger.requests[-1]["diagnostic"]
    finally:
        state["completed_at"] = utc_now()
        save_json(attempt / "diagnostic-state.json", state)
        store.report(project_id)
    return state
