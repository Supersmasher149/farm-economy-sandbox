"""Cross-file configuration validation."""


def validate(crops: list, upgrades: list, world: dict) -> None:
    crop_ids = _unique_ids(crops, "crop")
    _unique_ids(upgrades, "upgrade")
    products = world["processing"].get("products", [])
    product_ids = _unique_ids(products, "product")
    item_ids = crop_ids | product_ids
    channel_ids = _unique_ids(world["markets"].get("channels", []), "market channel")
    if "spot" not in channel_ids:
        raise ValueError("markets.json must define a 'spot' channel")
    _unique_ids(world["processing"].get("recipes", []), "processing recipe")
    for recipe in world["processing"].get("recipes", []):
        if recipe["input_item_id"] not in item_ids or recipe["output_item_id"] not in item_ids:
            raise ValueError(f"Recipe '{recipe['id']}' references an unknown item")
    for buyer in world["buyers"]:
        unknown = set(buyer.get("items", [])) - item_ids
        if unknown:
            raise ValueError(f"Buyer '{buyer['id']}' references unknown items: {sorted(unknown)}")
    if world["processing"].get("base_capacity", 0) < 0:
        raise ValueError("Processing capacity cannot be negative")


def _unique_ids(values: list, label: str) -> set:
    identifiers = [value.get("id") for value in values]
    if any(not identifier for identifier in identifiers):
        raise ValueError(f"Every {label} requires a non-empty id")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"Duplicate {label} id")
    return set(identifiers)
