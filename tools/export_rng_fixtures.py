"""Export golden fixtures for farm-c's RNG port (farm-c/tests/test_rng.c).

simulation/random_events.py:RandomEvents wraps exactly one `random.Random
(seed)` per run and exposes 7 operations (roll_yield, roll_price, roll_loss,
roll_watering, chance, uniform, choice). For a handful of seeds -- chosen to
exercise both the single-word and two-word branches of Python's integer
seeding (see random_seed() in CPython's _randommodule.c, ported in
farm-c/src/rng.c:rng_seed) -- this drives a long, deterministic, fixed-order
sequence of calls across all 7 methods and records each call's arguments and
result. farm-c/tests/test_rng.c replays the identical call sequence through
one FarmRng seeded the same way and asserts every result matches exactly:
since this is testing the RNG itself (not code built on top of it), Python
is the oracle and any mismatch means the C port's bit stream has drifted.

The sequence is long enough (400 cycles x ~12 words/cycle) to cross
MT19937's 624-word regeneration boundary several times per seed, and
`choice`'s varying lengths include 1 (which forces `_randbelow`'s
rejection-sampling loop to actually reject and redraw about half the time),
so both the steady-state generator and its edge cases get covered.

Usage: python3 tools/export_rng_fixtures.py
(also invoked by `make fixtures-rng` in farm-c/Makefile)
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from simulation.random_events import RandomEvents  # noqa: E402

OUT_PATH = os.path.join(REPO_ROOT, "farm-c", "tests", "fixtures", "rng.json")

# 0 and small values exercise rng_seed's single-word path (the only one any
# real seed in this repo -- see rng.h's header comment -- actually hits);
# 2**32-1/2**32/2**40+12345 exercise the two-word path for completeness,
# since the port is written to support it even though it's currently dead
# code from this repo's own callers.
SEEDS = (0, 1, 42, 777, 123456789, 2**32 - 1, 2**32, 2**40 + 12345)

CYCLES = 400


def build_calls(seed: int) -> list:
    events = RandomEvents(seed)
    calls = []
    for i in range(CYCLES):
        min_yield = 1 + (i % 5)
        max_yield = min_yield + 1 + (i % 7)
        result = events.roll_yield(min_yield, max_yield)
        calls.append(
            {
                "method": "roll_yield",
                "min_yield": min_yield,
                "max_yield": max_yield,
                "expected": result,
            }
        )

        base_price = 1.0 + (i % 13) * 0.37
        variation = 0.05 + (i % 4) * 0.03
        result = events.roll_price(base_price, variation)
        calls.append(
            {
                "method": "roll_price",
                "base_price": base_price,
                "variation": variation,
                "expected": result,
            }
        )

        loss_chance = (i % 10) / 10.0
        result = events.roll_loss(loss_chance)
        calls.append({"method": "roll_loss", "loss_chance": loss_chance, "expected": result})

        diligence = ((i * 7) % 11) / 11.0
        result = events.roll_watering(diligence)
        calls.append({"method": "roll_watering", "diligence": diligence, "expected": result})

        probability = ((i * 3) % 9) / 9.0
        result = events.chance(probability)
        calls.append({"method": "chance", "probability": probability, "expected": result})

        minimum = -5 + (i % 5)
        maximum = minimum + 1 + (i % 6)
        result = events.uniform(minimum, maximum)
        calls.append(
            {"method": "uniform", "minimum": minimum, "maximum": maximum, "expected": result}
        )

        length = 1 + (i % 6)
        # A list where element == index lets a bare index-selection port
        # (rng_choice_index) be checked directly against Python's choice()
        # return value, with no separate value<->index mapping needed.
        result = events.choice(list(range(length)))
        calls.append({"method": "choice", "length": length, "expected": result})

    return calls


def main():
    fixtures = [{"seed": seed, "calls": build_calls(seed)} for seed in SEEDS]
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({"seeds": fixtures}, f, indent=2)
        f.write("\n")
    total_calls = sum(len(entry["calls"]) for entry in fixtures)
    print(f"Wrote {len(fixtures)} seeds, {total_calls} calls to {OUT_PATH}")


if __name__ == "__main__":
    main()
