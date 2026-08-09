"""Agent interface. Agents only make decisions; they never mutate state
directly — the engine applies their choices through simulation/actions.py.
"""

from abc import ABC, abstractmethod

from simulation import markets


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

    def route_sales_by_best_price(self, player, channels: list) -> list[dict]:
        """Sell every lot through the best-paying channel that will take it.

        Not the default `choose_sales` -- dumping at spot is a deliberate model
        of a naive seller, and several agents exist to measure exactly that.
        This is here rather than on one strategy because working out which
        channel pays most for a lot is mechanical, not strategic: an agent's
        identity lives in what it plants and buys, so any agent that is meant
        to sell competently can opt in without restating the routing.

        Walks the highest grades first (a premium lot is the only thing that
        clears a premium-only channel, so spending that capacity on it before
        a lower grade takes the slot is what makes the tiers mean anything),
        and tracks planned capacity across lots so one call cannot promise the
        same daily slot twice.
        """
        planned_capacity = dict(player.channel_capacity_used)
        quantities = {}
        for lot in player.inventory_lots:
            by_quality = quantities.setdefault(lot.item_id, {})
            by_quality[lot.quality] = by_quality.get(lot.quality, 0) + lot.quantity

        routes = {}
        for item_id, by_quality in quantities.items():
            for quality in sorted(
                by_quality, key=lambda value: -markets.QUALITY_MULTIPLIERS[value]
            ):
                remaining = by_quality[quality]
                while remaining > 0:
                    candidates = [
                        (
                            markets.quote(
                                player,
                                item_id,
                                quality,
                                channel,
                                remaining,
                                capacity_used=planned_capacity,
                            ),
                            channel,
                        )
                        for channel in channels
                    ]
                    candidates = [pair for pair in candidates if pair[0]]
                    if not candidates:
                        break
                    offer, channel = max(
                        candidates, key=lambda pair: pair[0]["net"] / pair[0]["quantity"]
                    )
                    sold = offer["quantity"]
                    route = (item_id, quality, channel["id"])
                    routes[route] = routes.get(route, 0) + sold
                    planned_capacity[channel["id"]] = planned_capacity.get(channel["id"], 0) + sold
                    remaining -= sold

        return [
            {
                "item_id": item_id,
                "quantity": quantity,
                "channel_id": channel_id,
                "quality": quality,
            }
            for (item_id, quality, channel_id), quantity in routes.items()
        ]
