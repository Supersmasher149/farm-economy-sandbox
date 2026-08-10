from agents.fast_seller import FastSeller
from agents.profit_optimizer import ProfitOptimizer
from agents.progression_player import ProgressionPlayer
from main import load_config
from metrics.aggregate_results import aggregate
from runner.batch_run import run_batch
from runner.single_run import run_single
from simulation import contracts
from simulation.state import ContractState, PlayerState


def make_crop_set():
    return [
        {
            "id": "quickweed",
            "role": "fast",
            "seed_cost": 5,
            "growth_days": 3,
            "min_yield": 1,
            "max_yield": 2,
            "base_price": 5,
            "loss_chance": 0.03,
            "water_interval_days": 2,
            "unlock_requirement": None,
        },
        {
            "id": "greenleaf",
            "role": "standard",
            "seed_cost": 18,
            "growth_days": 7,
            "min_yield": 4,
            "max_yield": 6,
            "base_price": 7,
            "loss_chance": 0.05,
            "water_interval_days": 3,
            "unlock_requirement": None,
        },
    ]


def make_player(money=100, reserve=0):
    player = PlayerState(money=money, slots_total=3, operating_reserve=reserve)
    player.lowest_money = money
    player.highest_money = money
    return player


def test_optimizer_falls_back_to_fast_crop_below_operating_reserve():
    player = make_player(money=60, reserve=100)
    crops = make_crop_set()
    crops_by_id = {crop["id"]: crop for crop in crops}

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})

    assert chosen["id"] == "quickweed"


def test_optimizer_keeps_reserve_after_upgrade_and_fertilizer_spend(fertilizer_config):
    agent = ProfitOptimizer()
    upgrade = {"id": "capacity_1", "cost": 120}
    player = make_player(money=125, reserve=100)
    standard = make_crop_set()[1]

    assert not agent.should_buy_upgrade(player, upgrade)
    assert not agent.should_use_fertilizer(player, standard, fertilizer_config)

    # Bump highest_money alongside money: should_buy_upgrade_within_budget
    # caps cumulative upgrade spend against the farm's peak cash, so a
    # fixture that only mutates money (never updates the peak) would
    # otherwise trip that cap rather than exercise the reserve check this
    # test is about.
    player.money = 350
    player.highest_money = 350
    assert agent.should_buy_upgrade(player, upgrade)
    assert agent.should_use_fertilizer(player, standard, fertilizer_config)


def test_contract_filter_uses_market_opportunity_and_capacity():
    player = make_player(money=100, reserve=0)
    player.market_prices = {"greenleaf": 7}
    player.market_channels = [
        {
            "id": "farm_stand",
            "min_quality": "standard",
            "price_multiplier": 1.45,
            "daily_capacity": 8,
            "flat_fee": 1,
        }
    ]
    player.crop_catalog = {crop["id"]: crop for crop in make_crop_set()}
    player.upgrades_catalog = {}

    dominated = ContractState("dominated", "local", "greenleaf", 6, "standard", 8.4, 0, 14, 0.12)
    attractive = ContractState(
        "attractive", "regional", "greenleaf", 6, "standard", 11.0, 0, 14, 0.1
    )

    assert not contracts.is_offer_profitable(player, dominated)
    assert contracts.is_offer_profitable(player, attractive)
    assert contracts.is_offer_feasible(player, attractive)

    oversized = ContractState(
        "oversized", "regional", "greenleaf", 20, "standard", 11.0, 0, 14, 0.1
    )
    assert not contracts.is_offer_feasible(player, oversized)


def test_progression_player_uses_reserve_as_recovery_threshold():
    player = make_player(money=60, reserve=100)
    crops = make_crop_set()
    crops_by_id = {crop["id"]: crop for crop in crops}

    chosen = ProgressionPlayer().choose_crop(player, crops, crops_by_id, {})

    assert chosen["id"] == "quickweed"


def test_full_world_optimizer_diversifies_without_trailing_fast_seller():
    """At the real 365-day default config, ProfitOptimizer should meaningfully
    diversify into Greenleaf/Purple Haze -- unlike the pre-fix run, where a
    too-high `operating_reserve` and ProfitOptimizer's own disabled
    reserve-relaxation ladder left it at ~0% Purple Haze -- without ending up
    clearly worse off than the pure-Quickweed FastSeller baseline (see
    docs/design/2026-08-05-soil-regen-and-reserve-fix.md).

    `avg_final_money` and `avg_bankruptcy_day` are deliberately NOT asserted
    here: at 100% bankruptcy for both strategies, final money is clipped near
    the bankruptcy trigger (~$5) regardless of burn rate, so small
    differences are sample noise, not a real economic signal -- verified by
    the sign of that comparison flipping between adjacent sample sizes (100
    vs. 1,000 runs) during calibration. Crop-usage share is the stable,
    repeatable signal for what this fix actually changed.
    """
    crops, upgrades, config, world = load_config()
    results = run_batch(
        config,
        [FastSeller(), ProfitOptimizer()],
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        num_runs=100,
        base_seed=20260804,
        workers=1,
        world=world,
    )
    summary = aggregate(results)
    optimizer = summary["profit_optimizer"]

    assert optimizer["bankruptcy_rate"] <= summary["fast_seller"]["bankruptcy_rate"]
    assert optimizer["crop_usage_pct"]["purplehaze"] > 2.0
    assert optimizer["crop_usage_pct"]["quickweed"] < 90.0
    # A small number of missed deadlines is an expected side effect of
    # actively producing for slower, non-Quickweed crops now; zero was never
    # really guaranteed once real commitments across longer growth cycles are
    # in play.
    #
    # The bound was 1.0 when this test was written, against the pre-71e1b78
    # economics. That balance pass raised risk/reward across the board and cut
    # starting cash, so the optimizer now commits to more contracts on thinner
    # margins and misses more of them: this fixed seed/sample measures 1.79.
    # Raised to 2.0 to leave headroom over the measured value without letting a
    # genuine regression (a doubling, say) through unnoticed.
    assert optimizer["avg_contracts_failed"] <= 2.0


def _run_long_horizon(agent_cls):
    """One 2,000-day run at seed 42 under the shipped config."""
    crops, upgrades, config, world = load_config()
    long_config = dict(config, days=2000)
    player, _seed, _history = run_single(
        long_config,
        agent_cls(),
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        seed=42,
        world=world,
    )
    return player, config


def test_long_horizon_disciplined_strategies_are_self_sustaining():
    """At a 2,000-day horizon (~5.5x the 365-day default), *disciplined* play
    should not be guaranteed to go bankrupt.

    See docs/design/2026-08-05-soil-regen-and-reserve-fix.md for the full
    day-by-day diagnosis of what this originally caught: `soil_health`,
    `pest_pressure` and `disease_pressure` (alongside nitrogen/phosphorus/
    potassium, fixed in an earlier pass) previously only recovered when a plot
    sat completely fallow -- which no shipped strategy ever does -- so all four
    monotonically marched to their worst value and eventually bankrupted every
    strategy regardless of skill. Regenerating all of them a small amount every
    day, planted or not (`config/soil.json`'s `regen_per_day`, applied in
    `simulation/weather.apply_weather`), fixes that; fallow plots still recover
    faster on top, so deliberate rest/rotation keeps paying off, it's just no
    longer the only way to avoid guaranteed collapse.

    **Scope changed in the 71e1b78 balance pass.** That pass raised risk/reward
    and cut starting cash, and the ~$4,400 / ~$69,700 figures this docstring
    used to cite are pre-tuning and no longer hold. `FastSeller` -- a permanent
    Quickweed monoculture that reinvests everything and holds no reserve -- is
    no longer expected to survive, and is asserted separately in
    `test_long_horizon_naive_monoculture_goes_bankrupt`. What survival is meant
    to prove is that skill, not luck, is what carries a farm across a long
    horizon, so the strategies pinned here are the two that actually manage
    cash: `ProfitOptimizer` and `ProgressionPlayer`. Neither exact figure is
    re-asserted -- the goal is "does not go bankrupt and ends up well ahead,"
    not bit-for-bit reproduction of one seed, which is the replay guard's job.
    """
    for agent_cls in (ProfitOptimizer, ProgressionPlayer):
        player, config = _run_long_horizon(agent_cls)
        assert not player.bankrupt, (
            f"{agent_cls.name} unexpectedly went bankrupt over a 2,000-day run"
        )
        # Not merely "> start_money": at these economics both strategies clear
        # five figures, so a bare survival bound would pass on a farm that had
        # quietly stopped compounding.
        assert player.money > 100 * config["start_money"], (
            f"{agent_cls.name} survived 2,000 days but only reached ${player.money:.2f} "
            f"from ${config['start_money']}"
        )


def test_long_horizon_naive_monoculture_goes_bankrupt():
    """The counterpart to the test above: naive play is *supposed* to fail.

    `FastSeller` exists to probe whether rapid reinvestment into the shortest-
    growth crop dominates. Post-71e1b78 it does not -- with no operating
    reserve and a permanent same-family replant penalty it runs out of cash
    almost immediately. Pinning that outcome rather than merely tolerating it
    means a config change that accidentally makes naive monoculture viable
    again shows up here instead of quietly passing.

    The death is currently very early (day 19 of 2,000, ending at $1.56 at
    seed 42). The bound below is deliberately loose -- the point is "dies well
    inside a normal 365-day run", not the exact day.
    """
    player, _config = _run_long_horizon(FastSeller)

    assert player.bankrupt, "FastSeller unexpectedly survived a 2,000-day run"
    assert player.bankruptcy_day is not None
    assert player.bankruptcy_day < 365, (
        f"FastSeller survived to day {player.bankruptcy_day}, past a full default-length run; "
        "naive monoculture may have become viable again"
    )
