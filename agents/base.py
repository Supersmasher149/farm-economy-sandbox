"""Agent interface. Agents only make decisions; they never mutate state
directly — the engine applies their choices through simulation/actions.py.
"""

from abc import ABC, abstractmethod


class Agent(ABC):
    name = "base"
    description = ""

    # Probability [0, 1] that the agent waters the farm on any given day.
    # 1.0 (the default) means the farm is always watered on schedule and
    # crops never accrue neglect; override this to model inattentive care.
    watering_diligence = 1.0

    @abstractmethod
    def choose_crop(
        self, player, crops: list, crops_by_id: dict, upgrades_by_id: dict
    ) -> dict | None:
        """Return the crop dict to plant next, or None to leave slots open."""
        raise NotImplementedError

    @abstractmethod
    def should_buy_upgrade(self, player, upgrade: dict) -> bool:
        """Return True if the agent wants to buy this not-yet-owned upgrade today."""
        raise NotImplementedError

    def should_water(self, player, planted, crop: dict) -> bool:
        return player.day - planted.last_watered_day >= crop.get("water_interval_days", 3)

    def should_fertilize(self, player, planted, crop: dict, fertilizer_config: dict) -> bool:
        return False

    def choose_contracts(self, player, offers: list) -> list[str]:
        return []

    def choose_contract_deliveries(self, player) -> list[dict]:
        return [
            {"contract_id": contract.id, "quantity": contract.remaining}
            for contract in player.active_contracts
            if not contract.resolved
        ]

    def choose_processing(self, player, recipes: list, items_by_id: dict) -> list[dict]:
        return []

    def choose_sales(self, player, channels: list, items_by_id: dict) -> list[dict]:
        return [
            {"item_id": lot.item_id, "quantity": lot.quantity, "channel_id": "spot"}
            for lot in player.inventory_lots
        ]

    def should_use_fertilizer(self, player, crop: dict, fertilizer_config: dict) -> bool:
        """Return True to buy and apply fertilizer to the crop about to be planted.
        Default: never fertilize -- only agents that specifically reason about it
        (or don't reason about anything) need to override this.
        """
        return False
