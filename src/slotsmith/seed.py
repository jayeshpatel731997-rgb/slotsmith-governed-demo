"""Reproducible synthetic warehouse, catalog, affinities, and drift scenarios."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from .domain import Location, SKU, Scenario

SEED = 240519


@dataclass(slots=True)
class WarehouseData:
    locations: dict[str, Location]
    skus: dict[str, SKU]
    assignments: dict[str, str]
    affinities: dict[tuple[str, str], float]
    orders: list[tuple[str, ...]]
    seasonal_reslot_due: bool = False


def generate_warehouse(scenario: Scenario, sku_count: int = 2_000) -> WarehouseData:
    """Create 3,200 bins and an ABC/Pareto-like synthetic SKU catalog."""
    rng = random.Random(SEED)
    locations: dict[str, Location] = {}
    for aisle in range(1, 21):
        for bay in range(1, 41):
            for level in range(4):
                lid = f"A{aisle:02d}-B{bay:02d}-L{level}"
                zone = "hazmat" if aisle >= 19 else ("chilled" if aisle >= 17 else "ambient")
                locations[lid] = Location(
                    id=lid, aisle=aisle, bay=bay, level=level, zone=zone,
                    capacity_cm3=90_000 if level == 0 else 60_000,
                    max_weight_kg=80.0 if level == 0 else 25.0,
                    golden=level in (1, 2) and bay <= 24,
                    congestion=0.0,
                )

    skus: dict[str, SKU] = {}
    for i in range(1, sku_count + 1):
        rank = i
        velocity = round(800 / (rank ** 0.72) + rng.uniform(0.1, 2.0), 3)
        zone = "hazmat" if i % 97 == 0 else ("chilled" if i % 31 == 0 else "ambient")
        weight = round(rng.uniform(1, 68 if i % 17 == 0 else 22), 2)
        skus[f"SKU-{i:04d}"] = SKU(
            id=f"SKU-{i:04d}", velocity=velocity,
            volume_cm3=rng.randint(1_000, 58_000), weight_kg=weight,
            zone=zone, hazmat=zone == "hazmat",
        )

    compatible = {
        zone: [loc.id for loc in locations.values() if loc.zone == zone]
        for zone in ("ambient", "chilled", "hazmat")
    }
    # Deliberately poor but feasible baseline: reverse-distance assignment.
    assignments: dict[str, str] = {}
    used: set[str] = set()
    for sku in skus.values():
        candidates = sorted(
            (locations[lid] for lid in compatible[sku.zone] if lid not in used
             and sku.volume_cm3 <= locations[lid].capacity_cm3
             and sku.weight_kg <= locations[lid].max_weight_kg
             and (sku.weight_kg <= 25 or locations[lid].level == 0)),
            key=lambda loc: loc.distance,
            reverse=True,
        )
        if candidates:
            assignments[sku.id] = candidates[0].id
            used.add(candidates[0].id)

    # Generate a reproducible order stream with Pareto-skewed demand and local
    # product-family co-picks, then derive affinity from observed pair counts.
    orders: list[tuple[str, ...]] = []
    pair_counts: dict[tuple[str, str], int] = {}
    for _ in range(10_000):
        anchor = min(sku_count, int((rng.random() ** 2.2) * sku_count) + 1)
        size = 1 + rng.randrange(4)
        lines = {f"SKU-{anchor:04d}"}
        for _line in range(size - 1):
            related = min(sku_count, max(1, anchor + rng.randint(-3, 3)))
            lines.add(f"SKU-{related:04d}")
        order = tuple(sorted(lines))
        orders.append(order)
        for left_index, left in enumerate(order):
            for right in order[left_index + 1:]:
                pair = (left, right)
                pair_counts[pair] = pair_counts.get(pair, 0) + 1
    affinities = {pair: round(count / 10, 3) for pair, count in pair_counts.items() if count >= 2}

    if scenario == Scenario.POST_PROMO:
        for i in range(900, 921):
            old = skus[f"SKU-{i:04d}"]
            skus[old.id] = SKU(old.id, old.velocity * 18, old.volume_cm3, old.weight_kg, old.zone, old.hazmat)
    elif scenario == Scenario.NEW_SKU:
        for i in range(1_981, 2_001):
            old = skus[f"SKU-{i:04d}"]
            skus[old.id] = SKU(old.id, 85 + (2_001 - i), old.volume_cm3, old.weight_kg, old.zone, old.hazmat, True)
            assignments.pop(old.id, None)
    elif scenario == Scenario.CONGESTED_AISLE:
        locations = {
            lid: (replace(loc, congestion=2.5) if loc.aisle == 1 else loc)
            for lid, loc in locations.items()
        }
        targets = [loc.id for loc in locations.values() if loc.aisle == 1 and loc.zone == "ambient"]
        for sku_id, target in zip((f"SKU-{i:04d}" for i in range(1, 61)), targets, strict=False):
            sku = skus[sku_id]
            if sku.zone == locations[target].zone and sku.volume_cm3 <= locations[target].capacity_cm3 and sku.weight_kg <= locations[target].max_weight_kg and (sku.weight_kg <= 25 or locations[target].level == 0):
                assignments[sku_id] = target

    return WarehouseData(locations, skus, assignments, affinities, orders, False)
