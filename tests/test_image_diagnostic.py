import json
from types import SimpleNamespace

import pytest

from elsewhere.config import load_settings
from elsewhere.costs import BudgetExceeded
from elsewhere.image_diagnostic import run_once
from elsewhere.openai_service import save_json


@pytest.fixture
def diagnostic_store(tmp_path):
    settings = load_settings()
    save_json(tmp_path / "settings-snapshot.json", settings.raw)
    save_json(tmp_path / "image-prompts.json", {"prompts": ["Exact saved mock prompt"]})
    report = {"calculated_estimate_usd": .0063534, "allowance_usd": .50,
              "requests": [{"stage": "image_01", "status": "failed", "request_id": "req_original",
                            "charge_status": "unknown_ambiguous_failure", "calculated_cost_usd": None}]}
    save_json(tmp_path / "cost-report.json", report)
    return SimpleNamespace(base=settings, load=lambda _: {"busy": False, "status": "failed"},
                           revision_dir=lambda _: tmp_path, report=lambda _: report,
                           check_cancel=lambda _: None)


@pytest.mark.parametrize("fail", [False, True])
def test_exactly_one_attempt_with_preserved_old_charge(diagnostic_store, fail):
    store = diagnostic_store
    directory = store.revision_dir(None)
    original = (directory / "cost-report.json").read_bytes()
    calls = []

    class FakeService:
        def __init__(self, settings, ledger):
            assert settings.safety["max_sdk_retries"] == 0
            self.ledger = ledger

        def create_image(self, prompt, destination, format_name, index):
            assert prompt == "Exact saved mock prompt" and index == 1 and format_name == "short"

            def response():
                calls.append(index)
                if fail:
                    raise TimeoutError("secret payload")

            self.ledger.paid_call(stage="image_01", model="mock", reserve_usd=.20, call=response,
                                  usage_getter=lambda _: {}, cost_calculator=lambda _: (.04, "mock"))

    state = run_once(store, "mock", "req_original", approved=True, service_factory=FakeService)
    assert state["status"] == ("stopped" if fail else "image_saved")
    with pytest.raises(FileExistsError):
        run_once(store, "mock", "req_original", approved=True, service_factory=FakeService)
    assert calls == [1]
    assert (directory / "cost-report.json").read_bytes() == original
    saved = json.loads((directory / "image-diagnostic-0001/cost-report.json").read_text())
    assert len(saved["requests"]) == 1
    assert "secret payload" not in json.dumps(saved)


def test_consent_and_combined_budget_required(diagnostic_store):
    with pytest.raises(ValueError, match="Explicit approval"):
        run_once(diagnostic_store, "mock", "req_original")
    diagnostic_store.report(None)["calculated_estimate_usd"] = .11
    with pytest.raises(BudgetExceeded):
        run_once(diagnostic_store, "mock", "req_original", approved=True)
    assert not (diagnostic_store.revision_dir(None) / "image-diagnostic-0001").exists()
