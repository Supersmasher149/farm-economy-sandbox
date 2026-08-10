"""Tests for tools/auto_balance.py: the local, offline config-balance search.

Coverage focus: the propose-only guarantee (config/*.json is never written),
the knob-discovery walker's exclusion rules, the derived.py identity-cache
trap the tuner must avoid, bounds correctness, and objective-function
determinism -- the properties the search loop's correctness rests on, not
an exhaustive sweep of every code path.
"""

import copy
import hashlib
import json
import os
import random

import pytest

import tools.auto_balance as ab
from main import load_config
from simulation.configuration import SOIL_DYNAMICS_BOUNDS
from simulation.derived import crop_profile

CONFIG_DIR = os.path.join(ab.REPO_ROOT, "config")


@pytest.fixture(scope="module")
def baseline():
    return load_config()


@pytest.fixture(scope="module")
def knobs(baseline):
    return ab.discover_knobs(baseline, bound_pct=0.4)


# --- knob discovery ---------------------------------------------------


def test_discovery_excludes_identity_and_enum_fields(knobs):
    for knob in knobs:
        assert knob.path[-1] not in ab.EXCLUDED_KEYS, knob.full_path
        # unlock_requirement.id / .type are covered by the blanket id/type
        # exclusion above; this asserts that coverage actually landed there.
        assert "unlock_requirement.id" not in knob.full_path
        assert "unlock_requirement.type" not in knob.full_path


def test_discovery_excludes_id_reference_lists_and_strings(knobs):
    full_paths = {knob.full_path for knob in knobs}
    assert not any("buyers.json" in p and ".items" in p for p in full_paths)
    assert not any("input_item_id" in p or "output_item_id" in p for p in full_paths)
    # Market channel ids are structural (validate() hard-requires "spot" to
    # exist); the walker must never surface a channel's own id as a knob.
    assert not any(p.startswith("markets.json[") and p.endswith("].id") for p in full_paths)


def test_discovery_excludes_simulation_settings_seed(knobs):
    assert "simulation_settings.json.seed" not in {k.full_path for k in knobs}


def test_discovery_includes_known_good_numeric_leaves(knobs):
    full_paths = {knob.full_path for knob in knobs}
    assert "crops.json[quickweed].seed_cost" in full_paths
    assert "soil.json.dynamics.harvest_soil_health_cost" in full_paths
    assert "simulation_settings.json.start_money" in full_paths
    assert any(p.startswith("weather.json.seasons.summer.rainfall_range[") for p in full_paths)


def test_discovery_keeps_unlock_requirement_value_but_not_id_or_type():
    crops = [
        {
            "id": "gated",
            "unlock_requirement": {"type": "total_revenue", "value": 500},
        }
    ]
    found = list(ab._walk_numeric(crops, ()))
    paths = [p for p, _ in found]
    assert (0, "unlock_requirement", "value") in paths
    assert (0, "unlock_requirement", "id") not in paths
    assert (0, "unlock_requirement", "type") not in paths


def test_walk_numeric_excludes_booleans():
    """Regression test: isinstance(True, int) is True in Python, so an
    isinstance-based leaf filter would silently treat a flag as tunable."""
    node = {"flag": True, "count": 3, "nested": {"also_flag": False, "value": 1.5}}
    found = dict(ab._walk_numeric(node, ()))
    assert ("count",) in found
    assert ("nested", "value") in found
    assert ("flag",) not in found
    assert ("nested", "also_flag") not in found


def test_files_filter_restricts_discovery(baseline):
    restricted = ab.discover_knobs(baseline, bound_pct=0.4, files={"soil.json"})
    assert restricted
    assert all(k.file == "soil.json" for k in restricted)


# --- bounds -------------------------------------------------------------


def test_soil_dynamics_bounds_match_the_shared_table(knobs):
    for knob in knobs:
        if knob.file == "soil.json" and knob.path[:1] == ("dynamics",):
            key = knob.path[1]
            if key in SOIL_DYNAMICS_BOUNDS:
                lo, hi = SOIL_DYNAMICS_BOUNDS[key]
                expected_lo = float("-inf") if lo is None else lo
                expected_hi = float("inf") if hi is None else hi
                assert knob.lo == expected_lo
                assert knob.hi == expected_hi


def test_generic_bounds_preserve_sign_and_band(baseline):
    crops, upgrades, config, world = baseline
    lo, hi = ab._bounds_for("config", config, ("start_money",), 60, bound_pct=0.4)
    assert lo == pytest.approx(36.0)
    assert hi == pytest.approx(84.0)
    assert lo >= 0


def test_generic_bounds_never_go_negative_for_a_nonnegative_baseline(baseline):
    crops, upgrades, config, world = baseline
    lo, _hi = ab._bounds_for("config", config, ("start_money",), 5, bound_pct=4.0)
    assert lo >= 0


def test_zero_baseline_gets_a_nonzero_fallback_band():
    lo, hi = ab._bounds_for("markets", {}, ("flat_fee",), 0.0, bound_pct=0.4)
    assert hi > lo


def test_growth_time_reduction_upgrade_is_bounded_below_one():
    upgrades = [{"id": "u", "effect": {"type": "growth_time_reduction", "amount": 0.2}}]
    lo, hi = ab._bounds_for("upgrades", upgrades, (0, "effect", "amount"), 0.2, bound_pct=0.4)
    assert hi < 1.0
    assert lo >= 0.0


# --- the derived.py identity-cache trap ---------------------------------


def test_mutating_a_crop_in_place_serves_a_stale_derived_profile():
    """Documents the exact footgun make_candidate exists to avoid: reusing
    an object's identity after mutating it returns the cached pre-mutation
    value, silently."""
    crop = {"min_moisture": 0.3}
    original = crop_profile(crop)
    assert original.min_moisture == 0.3

    crop["min_moisture"] = 0.9
    still_cached = crop_profile(crop)
    assert still_cached is original
    assert still_cached.min_moisture == 0.3  # stale -- the mutation never took effect


def test_deepcopy_then_mutate_produces_a_fresh_profile():
    crop = {"min_moisture": 0.3}
    crop_profile(crop)  # prime the cache at this identity

    candidate = copy.deepcopy(crop)
    candidate["min_moisture"] = 0.9
    fresh = crop_profile(candidate)

    assert fresh.min_moisture == 0.9
    assert crop_profile(crop).min_moisture == 0.3  # original untouched


def test_make_candidate_mutation_is_visible_through_derived_lookups(baseline, knobs):
    """End-to-end version of the identity-cache test, through auto_balance's
    own make_candidate rather than a hand-rolled example."""
    knob = next(k for k in knobs if k.full_path == "crops.json[quickweed].seed_cost")
    new_value = knob.baseline_value + 1

    candidate = ab.make_candidate(baseline, knob, new_value)
    candidate_crop = ab.get_at(ab.get_root(candidate, "crops"), knob.path[:-1])
    baseline_crop = ab.get_at(ab.get_root(baseline, "crops"), knob.path[:-1])

    assert candidate_crop["seed_cost"] == new_value
    assert baseline_crop["seed_cost"] == knob.baseline_value
    assert candidate_crop is not baseline_crop


# --- objective function ---------------------------------------------------


def _stats(**overrides):
    base = {
        "crop_usage_pct": {},
        "bankruptcy_rate": 0.0,
        "avg_first_upgrade_day": None,
        "first_upgrade_rate": 100.0,
        "avg_final_money": 0.0,
        "avg_crop_loss_rate": 0.0,
    }
    base.update(overrides)
    return base


def test_penalty_is_zero_within_all_thresholds():
    config = {"start_money": 60, "days": 30}
    stats = _stats(avg_final_money=60, first_upgrade_rate=100.0)
    assert ab.continuous_penalty(stats, config, ab.DEFAULT_THRESHOLDS) == 0.0


def test_bankruptcy_penalty_is_monotonic_past_threshold():
    config = {"start_money": 60, "days": 30}
    low = ab.continuous_penalty(_stats(bankruptcy_rate=21), config, ab.DEFAULT_THRESHOLDS)
    high = ab.continuous_penalty(_stats(bankruptcy_rate=40), config, ab.DEFAULT_THRESHOLDS)
    at_threshold = ab.continuous_penalty(_stats(bankruptcy_rate=20), config, ab.DEFAULT_THRESHOLDS)
    assert at_threshold == 0.0
    assert 0 < low < high


def test_dominant_and_dead_crop_penalties():
    config = {"start_money": 60, "days": 30}
    dominant = ab.continuous_penalty(
        _stats(crop_usage_pct={"a": 90}), config, ab.DEFAULT_THRESHOLDS
    )
    dead = ab.continuous_penalty(_stats(crop_usage_pct={"a": 1}), config, ab.DEFAULT_THRESHOLDS)
    fine = ab.continuous_penalty(_stats(crop_usage_pct={"a": 30}), config, ab.DEFAULT_THRESHOLDS)
    assert dominant > 0
    assert dead > 0
    assert fine == 0.0


def test_runaway_economy_penalty_scales_with_run_length():
    stats = _stats(avg_final_money=100_000)
    short = ab.continuous_penalty(stats, {"start_money": 60, "days": 30}, ab.DEFAULT_THRESHOLDS)
    long = ab.continuous_penalty(stats, {"start_money": 60, "days": 365}, ab.DEFAULT_THRESHOLDS)
    assert short > long > 0  # same money is less alarming over a longer horizon


def test_warning_score_sums_across_strategies():
    config = {"start_money": 60, "days": 30}
    summary = {
        "a": _stats(bankruptcy_rate=40),
        "b": _stats(bankruptcy_rate=40),
    }
    combined = ab.warning_score(summary, config, ab.DEFAULT_THRESHOLDS)
    single = ab.continuous_penalty(_stats(bankruptcy_rate=40), config, ab.DEFAULT_THRESHOLDS)
    assert combined == pytest.approx(2 * single)


def test_economics_penalty_flags_a_loss_making_crop():
    crops = [
        {
            "id": "loser",
            "seed_cost": 100,
            "min_yield": 1,
            "max_yield": 1,
            "base_price": 1,
            "loss_chance": 0.0,
            "growth_days": 1,
        }
    ]
    fertilizer = {"cost": 0, "yield_bonus_pct": 0.0, "loss_chance_reduction": 0.0}
    markets = {"channels": []}
    assert ab.economics_penalty(crops, fertilizer, markets) > 0


def test_score_candidate_rejects_invalid_config_without_running_a_batch(baseline, monkeypatch):
    crops, upgrades, config, world = baseline

    def _fail(*_args, **_kwargs):
        raise AssertionError("run_batch must not be called for an invalid candidate")

    monkeypatch.setattr(ab, "run_batch", _fail)

    bad_crops = copy.deepcopy(crops)
    bad_crops[0]["min_yield"] = bad_crops[0]["max_yield"] + 1  # violates validate()

    score, summary = ab.score_candidate(
        (bad_crops, upgrades, config, world),
        [],
        num_runs=1,
        eval_seed=1,
        thresholds=ab.DEFAULT_THRESHOLDS,
    )
    assert score == float("inf")
    assert summary is None


def test_score_candidate_is_deterministic_for_a_fixed_seed(baseline):
    from agents.fast_seller import FastSeller

    agents = [FastSeller()]
    score1, _ = ab.score_candidate(
        baseline, agents, num_runs=3, eval_seed=99, thresholds=ab.DEFAULT_THRESHOLDS
    )
    score2, _ = ab.score_candidate(
        baseline, agents, num_runs=3, eval_seed=99, thresholds=ab.DEFAULT_THRESHOLDS
    )
    assert score1 == score2


# --- CLI: propose-only guarantee and reproducibility -----------------------


def _hash_config_dir() -> dict:
    digests = {}
    for name in sorted(os.listdir(CONFIG_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(CONFIG_DIR, name), "rb") as f:
                digests[name] = hashlib.sha256(f.read()).hexdigest()
    return digests


def test_cli_smoke_run_never_touches_config_on_disk(tmp_path):
    before = _hash_config_dir()

    exit_code = ab.main(
        [
            "--iterations",
            "2",
            "--runs-per-candidate",
            "3",
            "--restarts",
            "0",
            "--final-runs",
            "5",
            "--strategies",
            "fast_seller",
            "--output-dir",
            str(tmp_path),
        ]
    )

    after = _hash_config_dir()
    assert exit_code == 0
    assert before == after, "auto_balance.py must never modify config/*.json"

    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "proposed_diffs.json").exists()
    payload = json.loads((tmp_path / "proposed_diffs.json").read_text())
    assert "diffs" in payload and "confirmation" in payload


def test_search_is_reproducible_for_a_fixed_tuner_and_eval_seed(baseline):
    from agents.fast_seller import FastSeller

    knobs = ab.discover_knobs(baseline, bound_pct=0.4, files={"crops.json"})

    class Args:
        iterations = 4
        runs_per_candidate = 3
        restarts = 0
        step_fraction = 0.2
        step_decay = 0.6
        eval_seed = 42
        max_seconds = None

    def run_once():
        agents = [FastSeller()]
        rng = random.Random(7)
        _current, score, _summary, moves, visited, baseline_score = ab.search(
            baseline, knobs, agents, Args(), ab.DEFAULT_THRESHOLDS, rng
        )
        return score, baseline_score, [(m.knob.full_path, m.new_value) for m in moves], visited

    result1 = run_once()
    result2 = run_once()
    assert result1 == result2


# --- CQ-05: the report must describe the winning configuration -------------
#
# The proposed-diffs table used to be the accepted-move list, sorted by
# individual score delta and truncated. A hill climb revisits knobs, so that
# list holds stale intermediate values for a path tuned more than once, and
# still lists a path that was moved and later moved back. Applying the
# survivors of a sorted, truncated version of it produced a configuration
# that was never evaluated.


def _knob_for(knobs, file, display):
    return next(k for k in knobs if k.file == file and k.display == display)


def _move(knob, old_value, new_value, iteration=1, score_before=1.0, score_after=0.5):
    return ab.Move(iteration, knob, old_value, new_value, score_before, score_after)


def test_config_diff_reports_one_final_value_for_a_revisited_path(baseline, knobs):
    knob = knobs[0]
    original = ab.get_at(ab.get_root(baseline, knob.root), knob.path)
    final_value = original + 7

    best = ab.make_candidate(baseline, knob, final_value)
    # Three sequential moves toward the final value; only the last is current.
    moves = [
        _move(knob, original, original + 3, iteration=1),
        _move(knob, original + 3, original + 5, iteration=2),
        _move(knob, original + 5, final_value, iteration=3),
    ]

    diffs = ab.build_config_diff(baseline, best, knobs, moves)

    assert len(diffs) == 1
    assert diffs[0]["old_value"] == ab._round_val(original)
    assert diffs[0]["new_value"] == ab._round_val(final_value)
    assert diffs[0]["moves_applied"] == 3


def test_config_diff_omits_a_path_the_search_moved_and_moved_back(baseline, knobs):
    knob = knobs[0]
    original = ab.get_at(ab.get_root(baseline, knob.root), knob.path)

    # Ends exactly where it started: nothing to apply, however many moves
    # were accepted along the way.
    best = ab.make_candidate(baseline, knob, original)
    moves = [
        _move(knob, original, original + 4, iteration=1),
        _move(knob, original + 4, original, iteration=2),
    ]

    assert ab.build_config_diff(baseline, best, knobs, moves) == []


def test_config_diff_covers_every_changed_path_without_truncation(baseline, knobs):
    changed = knobs[:5]
    best = copy.deepcopy(baseline)
    for index, knob in enumerate(changed, start=1):
        ab.set_at(
            ab.get_root(best, knob.root),
            knob.path,
            ab.get_at(ab.get_root(baseline, knob.root), knob.path) + index,
        )

    diffs = ab.build_config_diff(baseline, best, knobs, moves=[])

    assert len(diffs) == len(changed)
    assert {(d["file"], d["path"]) for d in diffs} == {(k.file, k.display) for k in changed}


def test_applying_the_reported_diffs_reproduces_the_scored_config(baseline, knobs):
    """The property the whole finding is about: hand-applying the table to
    the checked-in config must land on the configuration that was scored."""
    changed = knobs[:4]
    best = copy.deepcopy(baseline)
    for index, knob in enumerate(changed, start=1):
        ab.set_at(
            ab.get_root(best, knob.root),
            knob.path,
            ab.get_at(ab.get_root(baseline, knob.root), knob.path) + index,
        )

    diffs = ab.build_config_diff(baseline, best, knobs, moves=[])

    # Apply the report by path, in the order it is printed.
    rebuilt = copy.deepcopy(baseline)
    by_display = {(k.file, k.display): k for k in knobs}
    for diff in diffs:
        knob = by_display[(diff["file"], diff["path"])]
        ab.set_at(ab.get_root(rebuilt, knob.root), knob.path, diff["new_value"])

    for knob in knobs:
        assert ab.get_at(ab.get_root(rebuilt, knob.root), knob.path) == ab.get_at(
            ab.get_root(best, knob.root), knob.path
        )


def test_move_history_is_separate_from_the_proposed_diffs(tmp_path):
    exit_code = ab.main(
        [
            "--iterations",
            "2",
            "--runs-per-candidate",
            "3",
            "--restarts",
            "0",
            "--final-runs",
            "5",
            "--strategies",
            "fast_seller",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 0

    payload = json.loads((tmp_path / "proposed_diffs.json").read_text())
    assert "move_history" in payload
    # One entry per changed path at most -- never one per accepted move.
    paths = [(d["file"], d["path"]) for d in payload["diffs"]]
    assert len(paths) == len(set(paths))

    report = (tmp_path / "report.md").read_text()
    assert "## Proposed Diffs" in report
    assert "## Search Diagnostics" in report
    assert "apply all of it or none of it" in report
