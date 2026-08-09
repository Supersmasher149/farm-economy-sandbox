from simulation import crop_growth
from simulation.state import PlantedCrop


def test_multiplier_bound_literals_match_the_named_constants():
    """The bounds are inlined as literals on the hot path; keep them honest.

    `harvest_multipliers` and `compute_harvest_outcome` spell the clamp bounds
    out as literals rather than unpacking `*YIELD_MULTIPLIER_BOUNDS`, because
    the unpacking call was measurably hot. That is only safe while the
    literals and the named constants agree -- this test is what makes editing
    one without the other fail loudly instead of silently changing balance.
    """
    assert crop_growth.YIELD_MULTIPLIER_BOUNDS == (0.1, 1.5)
    assert crop_growth.QUALITY_MULTIPLIER_BOUNDS == (0.0, 1.25)


def test_crop_not_mature_before_growth_days():
    pc = PlantedCrop(crop_id="fast", day_planted=0, growth_days_required=3)
    assert not pc.is_mature(current_day=2)


def test_crop_mature_exactly_at_growth_days():
    pc = PlantedCrop(crop_id="fast", day_planted=0, growth_days_required=3)
    assert pc.is_mature(current_day=3)


def test_crop_mature_after_growth_days():
    pc = PlantedCrop(crop_id="fast", day_planted=5, growth_days_required=3)
    assert pc.is_mature(current_day=10)
