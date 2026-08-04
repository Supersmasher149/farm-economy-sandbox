"""Seeded randomness for a single simulation run.

Every run is driven by exactly one RandomEvents instance so the whole run
(yields, prices, losses) is reproducible from a single recorded seed.
"""
import random


class RandomEvents:
    def __init__(self, seed=None):
        if seed is None:
            seed = random.SystemRandom().randrange(2 ** 32)
        self.seed = seed
        self._rng = random.Random(seed)

    def roll_yield(self, min_yield: int, max_yield: int) -> int:
        return self._rng.randint(min_yield, max_yield)

    def roll_price(self, base_price: float, variation: float) -> float:
        factor = 1 + self._rng.uniform(-variation, variation)
        return max(0.01, base_price * factor)

    def roll_loss(self, loss_chance: float) -> bool:
        return self._rng.random() < loss_chance

    def roll_watering(self, diligence: float) -> bool:
        """True if the player waters the farm today, given their watering diligence (0..1)."""
        return self._rng.random() < diligence

    def chance(self, probability: float) -> bool:
        return self._rng.random() < probability

    def uniform(self, minimum: float, maximum: float) -> float:
        return self._rng.uniform(minimum, maximum)

    def choice(self, values):
        return self._rng.choice(values)
