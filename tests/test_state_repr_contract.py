"""`repr()` of the state dataclasses is simulation input, not just debug output.

`PlayerState.decision_random` (simulation/state.py) derives a replayable policy
value from `blake2b(repr((run_seed, day, context)).encode())`. That makes the
exact text of `repr()` load-bearing: anything that changes how these objects
stringify changes agent decisions, and therefore changes the outcome of every
recorded seed.

The realistic way to break this is not editing `__repr__` by hand -- it is
adopting a tool that replaces it as a side effect. A Cython `cdef class` or a
mypyc native class does not inherit the `@dataclass`-generated `__repr__`, so
compiling simulation/state.py with either would silently rewrite every
`decision_random` value in the project. See the plan in CLAUDE.md's Performance
section and .claude/skills/replay-guard.

These tests are the tripwire for that. They are pure Python and cost nothing;
if one fails, do not update the constant until you know why it moved.
"""

import types

from simulation.state import (
    ContractState,
    InventoryLot,
    PlantedCrop,
    PlayerState,
    PlotState,
    ProcessingJob,
)


def test_plot_state_repr_is_unchanged():
    assert repr(PlotState()) == (
        "PlotState(moisture=0.65, nitrogen=0.75, phosphorus=0.75, potassium=0.75, "
        "ph=6.5, soil_health=0.7, pest_pressure=0.05, disease_pressure=0.03, "
        "previous_crop_family=None, crop=None)"
    )


def test_planted_crop_repr_is_unchanged():
    planted = PlantedCrop(crop_id="quickweed", day_planted=3, growth_days_required=4)
    assert repr(planted) == (
        "PlantedCrop(crop_id='quickweed', day_planted=3, growth_days_required=4, "
        "last_watered_day=3, neglect_days=0, fertilized=False, plot_index=None, "
        "water_stress=0.0, nutrient_stress=0.0, temperature_stress=0.0, "
        "pest_stress=0.0, disease_stress=0.0, accrued_cost=0.0)"
    )


def test_inventory_lot_repr_is_unchanged():
    assert repr(InventoryLot(item_id="quickweed", quantity=5)) == (
        "InventoryLot(item_id='quickweed', quantity=5, quality='standard', "
        "produced_day=0, shelf_life_days=7, unit_cost=0.0, item_type='crop', "
        "age_days=0, effective_shelf_life_days=None)"
    )


def test_decision_random_is_pinned_to_an_exact_value():
    """The whole point of the repr contract, asserted end to end.

    Compared as hex so a last-bit change cannot hide behind decimal
    formatting -- the same reason the replay guard records hex.
    """
    player = PlayerState(money=100.0, slots_total=2, run_seed=777, day=5)
    value = player.decision_random("choose_crop", 3, ("a", "b"))
    assert value.hex() == "0x1.975b3d0a66f7dp-2"


def test_decision_random_ignores_untracked_state():
    """Only (run_seed, day, context) may feed the hash.

    If a future refactor passes an object into the context, this is the test
    that should be extended -- a dataclass instance in `context` would make
    every agent decision depend on that class's repr as well.
    """
    a = PlayerState(money=100.0, slots_total=2, run_seed=777, day=5)
    b = PlayerState(money=999.5, slots_total=9, run_seed=777, day=5)
    assert a.decision_random("upgrade", "barn") == b.decision_random("upgrade", "barn")
    assert a.decision_random("upgrade", "barn") != a.decision_random("upgrade", "silo")


def test_state_classes_are_ordinary_python_types():
    """Canary for a compiled build that converts the dataclasses.

    Cython `cdef class` and mypyc native classes are not instances of `type`.
    If this fails, a build has replaced the dataclass-generated `__repr__` and
    every recorded seed now means something different -- run the replay guard
    before touching anything else.
    """
    for cls in (
        PlantedCrop,
        PlotState,
        InventoryLot,
        ContractState,
        ProcessingJob,
        PlayerState,
    ):
        assert type(cls) is type, f"{cls.__name__} is no longer an ordinary Python class"
        # A cdef class / native class exposes __repr__ as a slot wrapper or a
        # cython_function_or_method, never as a plain interpreted function.
        assert isinstance(cls.__repr__, types.FunctionType), (
            f"{cls.__name__}.__repr__ is {type(cls.__repr__).__name__}, not the "
            "@dataclass-generated Python function"
        )
