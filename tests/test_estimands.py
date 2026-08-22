"""Tests for the estimand registry.

The registry's job is to make a reported number unambiguous, so these tests
check the metadata is complete and that the three distinctions it exists to
preserve -- population, mean-of-ratios, undefined-versus-zero -- actually hold
in the extraction functions.
"""

from types import SimpleNamespace

import pytest

from metrics import estimands


def _run(**fields):
    defaults = {
        "final_money": 100.0,
        "bankrupt": False,
        "avg_profit_per_day": 2.5,
        "bankruptcy_day": None,
        "minimum_cash_balance": -5.0,
        "first_upgrade_day": None,
        "crop_loss_rate": None,
        "days_simulated": 30,
    }
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def test_every_estimand_declares_the_metadata_a_reader_needs():
    for estimand_id, estimand in estimands.REGISTRY.items():
        metadata = estimand.to_metadata()
        for field in (
            "name",
            "kind",
            "unit",
            "unit_of_analysis",
            "population",
            "definition",
            "missing_policy",
            "weighting",
            "ci_method",
            "supports_adaptive",
            "supports_comparison",
        ):
            assert metadata.get(field) not in (None, ""), f"{estimand_id} is missing {field}"
        assert estimand.id == estimand_id


def test_unit_of_analysis_is_always_one_run():
    """Run-weighted and planting-weighted metrics must never be mixed; the
    registry states the unit once so nothing downstream has to guess."""
    assert all(
        estimand.unit_of_analysis == estimands.UNIT_OF_ANALYSIS
        for estimand in estimands.REGISTRY.values()
    )


def test_default_and_comparison_sets_reference_real_estimands():
    for estimand_id in estimands.DEFAULT_ESTIMANDS + estimands.DEFAULT_COMPARISON_ESTIMANDS:
        assert estimands.get(estimand_id).id == estimand_id


def test_unknown_estimand_raises_a_helpful_error():
    with pytest.raises(estimands.UnknownEstimand, match="Known:"):
        estimands.get("expected_vibes")


def test_expected_final_money_includes_bankrupt_runs():
    estimand = estimands.get("expected_final_money")
    assert estimand.population == "all_runs"
    assert estimand.observe(_run(final_money=0.0, bankrupt=True)) == 0.0


def test_survivor_estimand_excludes_bankrupt_runs():
    estimand = estimands.get("expected_final_money_survivors")
    assert estimand.observe(_run(final_money=50.0, bankrupt=False)) == 50.0
    assert estimand.observe(_run(final_money=0.0, bankrupt=True)) is None


def test_conditional_bankruptcy_day_is_conditional_and_says_so():
    estimand = estimands.get("conditional_bankruptcy_day")
    assert estimand.observe(_run(bankrupt=True, bankruptcy_day=12)) == 12
    assert estimand.observe(_run(bankrupt=False)) is None
    assert "not a survival estimate" in estimand.notes.lower()
    assert estimand.supports_adaptive is False


def test_profit_per_day_is_a_mean_of_ratios():
    estimand = estimands.get("expected_profit_per_day")
    assert estimand.weighting == "equal_per_run_mean_of_ratios"
    assert estimand.observe(_run(avg_profit_per_day=1.5)) == 1.5


def test_crop_loss_rate_distinguishes_undefined_from_zero():
    estimand = estimands.get("expected_crop_loss_rate")
    assert estimand.observe(_run(crop_loss_rate=None)) is None, "no harvest is not 0% loss"
    assert estimand.observe(_run(crop_loss_rate=0.0)) == 0.0
    assert estimand.missing_policy == "skip_undefined"


def test_proportion_estimands_extract_booleans():
    assert estimands.get("bankruptcy_probability").observe(_run(bankrupt=True)) is True
    first_upgrade = estimands.get("first_upgrade_probability")
    assert first_upgrade.observe(_run(first_upgrade_day=4)) is True
    assert first_upgrade.observe(_run(first_upgrade_day=None)) is False


def test_quantile_estimand_is_excluded_from_adaptive_stopping():
    estimand = estimands.get("final_money_quantile")
    assert estimand.kind == "quantile"
    assert estimand.supports_adaptive is False
    assert estimand.quantile_p == 0.5
    assert estimand.id not in estimands.adaptive_estimands()


def test_metadata_document_is_json_shaped():
    document = estimands.metadata_document(["expected_final_money"])
    assert set(document) == {"expected_final_money"}
    assert document["expected_final_money"]["population"] == "all_runs"
    assert estimands.ESTIMAND_REGISTRY_VERSION.startswith("farm-estimands-")
