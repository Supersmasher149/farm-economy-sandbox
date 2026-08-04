from simulation import economy_rules


def test_crop_with_no_unlock_requirement_is_always_unlocked(player, fast_crop):
    assert economy_rules.is_crop_unlocked(fast_crop, player)


def test_crop_locked_until_revenue_threshold_met(player, premium_crop):
    player.total_revenue = 50
    assert not economy_rules.is_crop_unlocked(premium_crop, player)
    player.total_revenue = 150
    assert economy_rules.is_crop_unlocked(premium_crop, player)


def test_effective_growth_days_unaffected_without_upgrade(player, fast_crop, efficiency_upgrade):
    upgrades_by_id = {"efficiency_1": efficiency_upgrade}
    assert economy_rules.effective_growth_days(fast_crop, player, upgrades_by_id) == 3


def test_effective_growth_days_reduced_with_upgrade(player, fast_crop, efficiency_upgrade):
    player.upgrades_owned.add("efficiency_1")
    upgrades_by_id = {"efficiency_1": efficiency_upgrade}
    # 3 days * (1 - 0.20) = 2.4 -> rounds to 2
    assert economy_rules.effective_growth_days(fast_crop, player, upgrades_by_id) == 2


def test_effective_growth_days_never_below_one(player, fast_crop):
    tiny_crop = dict(fast_crop, growth_days=1)
    huge_reduction = {"id": "e", "effect": {"type": "growth_time_reduction", "amount": 0.99}}
    player.upgrades_owned.add("e")
    upgrades_by_id = {"e": huge_reduction}
    assert economy_rules.effective_growth_days(tiny_crop, player, upgrades_by_id) >= 1


def test_expected_profit_per_day_is_positive_for_profitable_crop(player, standard_crop):
    profit = economy_rules.expected_profit_per_day(standard_crop, player, {})
    assert profit > 0
