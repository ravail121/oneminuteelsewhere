from pathlib import Path

import pytest

from elsewhere.costs import AmbiguousPaidRequest, BudgetExceeded, CostLedger

PRICING = {
    "pricing_as_of": "2026-09-11",
    "pricing_sources": {"text": "https://developers.openai.com/api/docs/pricing"},
}


def test_failed_paid_request_is_not_retried_and_blocks_more(tmp_path: Path):
    ledger = CostLedger(tmp_path / "cost-report.json", 2.0, PRICING)
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        raise TimeoutError("ambiguous timeout")

    with pytest.raises(AmbiguousPaidRequest):
        ledger.paid_call(
            stage="image_01",
            model="test",
            reserve_usd=0.2,
            call=fail,
            usage_getter=lambda _: None,
            cost_calculator=lambda _: (None, "unknown"),
        )
    assert calls == 1
    with pytest.raises(BudgetExceeded, match="uncertain charge"):
        ledger.ensure_budget(0.01, "review")


def test_budget_reserve_is_enforced(tmp_path: Path):
    ledger = CostLedger(tmp_path / "cost-report.json", 0.1, PRICING)
    with pytest.raises(BudgetExceeded):
        ledger.ensure_budget(0.2, "image")


def test_interrupted_in_progress_request_blocks_spending(tmp_path):
    ledger = CostLedger(tmp_path / "cost-report.json", 0.50, PRICING)
    ledger.requests.append({"charge_status": "unknown_in_progress", "calculated_cost_usd": None})
    ledger.save()
    resumed = CostLedger(ledger.path, 0.50, PRICING)
    with pytest.raises(BudgetExceeded, match="uncertain charge"):
        resumed.ensure_budget(0.01, "image_02")


def test_text_cost_includes_cache_write_surcharge():
    from elsewhere.costs import text_cost
    cost, _ = text_cost({"input_tokens": 1000, "output_tokens": 100,
                         "input_tokens_details": {"cache_write_tokens": 800, "cached_tokens": 100}})
    assert cost == pytest.approx((900 * .20 + 800 * .05 + 100 * .02 + 100 * 1.20) / 1_000_000)
