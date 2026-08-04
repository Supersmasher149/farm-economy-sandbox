from simulation.state import PlantedCrop


def test_crop_not_mature_before_growth_days():
    pc = PlantedCrop(crop_id="fast", day_planted=0, growth_days_required=3)
    assert not pc.is_mature(current_day=2)


def test_crop_mature_exactly_at_growth_days():
    pc = PlantedCrop(crop_id="fast", day_planted=0, growth_days_required=3)
    assert pc.is_mature(current_day=3)


def test_crop_mature_after_growth_days():
    pc = PlantedCrop(crop_id="fast", day_planted=5, growth_days_required=3)
    assert pc.is_mature(current_day=10)
