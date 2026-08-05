"""Capacity-limited value-added processing jobs."""
from simulation import inventory
from simulation.state import InventoryLot, ProcessingJob


def start_job(player, recipe: dict, quantity_batches: int, capacity: int) -> bool:
    if quantity_batches <= 0 or len(player.processing_jobs) + quantity_batches > capacity:
        return False
    total_input = recipe["input_quantity"] * quantity_batches
    total_cost = recipe.get("cost", 0.0) * quantity_batches
    if player.money < total_cost:
        return False
    if inventory.available_quantity(player, recipe["input_item_id"], recipe.get("min_quality", "processing")) < total_input:
        return False
    consumed, input_cost = inventory.consume(
        player, recipe["input_item_id"], total_input, recipe.get("min_quality", "processing")
    )
    if consumed != total_input:
        return False
    player.money -= total_cost
    player.record_expense("processing", total_cost)
    for _ in range(quantity_batches):
        output_quantity = recipe["output_quantity"]
        player.processing_jobs.append(ProcessingJob(
            recipe_id=recipe["id"],
            output_item_id=recipe["output_item_id"],
            output_quantity=output_quantity,
            completion_day=player.day + recipe.get("processing_days", 1),
            shelf_life_days=recipe.get("shelf_life_days", 30),
            unit_cost=(input_cost / quantity_batches + recipe.get("cost", 0.0)) / output_quantity,
        ))
    return True


def complete_jobs(player) -> int:
    completed = 0
    remaining = []
    for job in player.processing_jobs:
        if player.day < job.completion_day:
            remaining.append(job)
            continue
        player.inventory_lots.append(InventoryLot(
            item_id=job.output_item_id,
            quantity=job.output_quantity,
            quality="standard",
            produced_day=player.day,
            shelf_life_days=job.shelf_life_days,
            unit_cost=job.unit_cost,
            item_type="product",
        ))
        completed += job.output_quantity
    player.processing_jobs = remaining
    player.total_processed += completed
    return completed
