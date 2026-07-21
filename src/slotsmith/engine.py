"""Deterministic constraints, KPI math, optimization, and move simulation."""

from __future__ import annotations

from dataclasses import replace

from .domain import Location, Move, SKU, Trigger
from .seed import WarehouseData

MAX_BATCH_SIZE = 25


class InfeasibleError(ValueError):
    """Raised when no constraint-safe bounded proposal can be built."""


def compatible(sku: SKU, loc: Location) -> bool:
    """Central constraint predicate: zone, volume, floor weight, and ergonomic golden zone."""
    return (
        sku.zone == loc.zone
        and sku.volume_cm3 <= loc.capacity_cm3
        and sku.weight_kg <= loc.max_weight_kg
        and (sku.weight_kg <= 25 or loc.level == 0)
    )


def travel_kpi(data: WarehouseData, assignments: dict[str, str] | None = None) -> float:
    """Expected weighted pick travel plus aisle congestion and co-pick separation."""
    assigned = assignments or data.assignments
    total = 0.0
    for sku_id, location_id in assigned.items():
        sku, loc = data.skus[sku_id], data.locations[location_id]
        total += sku.velocity * loc.distance * (1 + loc.congestion)
    # Receiving/staging is deliberately modeled as a long route so new SKUs are
    # comparable before and after assignment rather than disappearing from KPI math.
    for sku_id, sku in data.skus.items():
        if sku_id not in assigned:
            total += sku.velocity * 500
    for (a, b), affinity in data.affinities.items():
        if a in assigned and b in assigned:
            la, lb = data.locations[assigned[a]], data.locations[assigned[b]]
            total += affinity * (abs(la.aisle - lb.aisle) * 12 + abs(la.bay - lb.bay))
    return round(total, 3)


def validate_assignments(data: WarehouseData, assignments: dict[str, str]) -> list[str]:
    errors: list[str] = []
    occupied: dict[str, str] = {}
    for sku_id, loc_id in assignments.items():
        if sku_id not in data.skus or loc_id not in data.locations:
            errors.append(f"unknown assignment {sku_id}->{loc_id}")
            continue
        if loc_id in occupied:
            errors.append(f"double occupancy at {loc_id}: {occupied[loc_id]}, {sku_id}")
        occupied[loc_id] = sku_id
        if not compatible(data.skus[sku_id], data.locations[loc_id]):
            errors.append(f"constraint violation {sku_id}->{loc_id}")
    return errors


def detect_slotting_triggers(data: WarehouseData) -> list[Trigger]:
    unslotted = tuple(s.id for s in data.skus.values() if s.id not in data.assignments)
    triggers: list[Trigger] = []
    if unslotted:
        triggers.append(Trigger("unslotted_sku", unslotted, f"{len(unslotted)} synthetic SKUs need homes"))
    hot = tuple(
        sid for sid, lid in data.assignments.items()
        if data.skus[sid].velocity >= 80 and data.locations[lid].distance >= 300
    )
    if hot:
        triggers.append(Trigger("velocity_shift", hot[:100], "High-velocity SKUs are far from pack-out"))
    congested = tuple(
        sid for sid, lid in data.assignments.items() if data.locations[lid].congestion >= 1
    )
    if congested:
        triggers.append(Trigger("aisle_congestion", congested[:100], "Picks are concentrated in a hot aisle"))
    if data.seasonal_reslot_due:
        triggers.append(Trigger("seasonal_reslot_due", (), "Synthetic seasonal review date is due"))
    return triggers


def optimize(
    data: WarehouseData,
    batch_size: int = MAX_BATCH_SIZE,
    scope: set[str] | None = None,
) -> tuple[list[Move], dict[str, str]]:
    """Greedy + deterministic single-move local search into empty locations.

    Empty-first moves avoid cycles and guarantee that each intermediate state is
    executable without double occupancy. The bounded set is ranked by objective gain.
    """
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be 1..{MAX_BATCH_SIZE}")
    occupied = set(data.assignments.values())
    empty = [loc for loc in data.locations.values() if loc.id not in occupied]
    base = travel_kpi(data)
    candidates: list[tuple[float, str, str]] = []
    scoped_skus = (sku for sku in data.skus.values() if scope is None or sku.id in scope)
    for sku in scoped_skus:
        current_id = data.assignments.get(sku.id)
        current_cost = sku.velocity * data.locations[current_id].distance * (1 + data.locations[current_id].congestion) if current_id else float("inf")
        viable = [loc for loc in empty if compatible(sku, loc)]
        if not viable:
            continue
        viable.sort(key=lambda loc: (sku.velocity * loc.distance * (1 + loc.congestion), loc.id))
        for target in viable[:batch_size]:
            target_cost = sku.velocity * target.distance * (1 + target.congestion)
            gain = current_cost - target_cost if current_id else sku.velocity * 500
            # Golden-zone preference is deterministic and only used as a ranking bonus.
            if target.golden and sku.velocity >= 50:
                gain += sku.velocity * 4
            if gain > 0:
                candidates.append((gain, sku.id, target.id))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    chosen: list[tuple[float, str, str]] = []
    used_targets: set[str] = set()
    used_skus: set[str] = set()
    for item in candidates:
        _, sku_id, target_id = item
        if sku_id not in used_skus and target_id not in used_targets:
            chosen.append(item)
            used_skus.add(sku_id)
            used_targets.add(target_id)
        if len(chosen) == batch_size:
            break
    if not chosen:
        raise InfeasibleError("No improving constraint-safe move exists in the bounded scope")

    assignments = dict(data.assignments)
    moves: list[Move] = []
    for sequence, (_, sku_id, target_id) in enumerate(chosen, 1):
        old = assignments.get(sku_id)
        assignments[sku_id] = target_id
        moves.append(Move(sequence, sku_id, old, target_id, "Lower expected travel with all constraints satisfied"))
    errors = validate_assignments(data, assignments)
    if errors:
        raise InfeasibleError("; ".join(errors))
    if travel_kpi(data, assignments) >= base:
        raise InfeasibleError("Candidate batch does not improve the objective")
    return moves, assignments


def simulate_moves(data: WarehouseData, moves: list[Move]) -> dict[str, float]:
    assignments = dict(data.assignments)
    occupied = set(assignments.values())
    before = travel_kpi(data, assignments)
    for move in sorted(moves, key=lambda m: m.sequence):
        if move.to_location in occupied:
            raise InfeasibleError(f"sequence {move.sequence} targets occupied {move.to_location}")
        sku = data.skus[move.sku_id]
        loc = data.locations[move.to_location]
        if not compatible(sku, loc):
            raise InfeasibleError(f"sequence {move.sequence} violates constraints")
        if move.from_location:
            occupied.discard(move.from_location)
        occupied.add(move.to_location)
        assignments[move.sku_id] = move.to_location
    after = travel_kpi(data, assignments)
    # Synthetic throughput translates route burden into picks/hour. This is a
    # comparative digital-twin estimate, not a claim about a real operation.
    daily_picks = sum(sku.velocity for sku in data.skus.values())
    before_throughput = daily_picks * 60 / max(before, 1) * 1_000
    projected_throughput = daily_picks * 60 / max(after, 1) * 1_000
    return {
        "before_travel": before,
        "projected_travel": after,
        "projected_reduction_pct": round((before - after) / before * 100, 3),
        "before_throughput_pph": round(before_throughput, 3),
        "projected_throughput_pph": round(projected_throughput, 3),
        "projected_throughput_gain_pct": round((projected_throughput - before_throughput) / before_throughput * 100, 3),
    }


def explain_move(move: Move, data: WarehouseData) -> str:
    """No-key deterministic fallback; an optional AI adapter may replace only this text."""
    sku, target = data.skus[move.sku_id], data.locations[move.to_location]
    origin = data.locations.get(move.from_location) if move.from_location else None
    delta = (origin.distance * (1 + origin.congestion) - target.distance * (1 + target.congestion)) if origin else target.distance
    return (
        f"Move {sku.id} from {move.from_location or 'receiving'} to {target.id}. "
        f"At {sku.velocity:.1f} picks/day, its weighted route improves by {delta:.1f} distance units per pick; "
        f"zone, volume, weight, and ergonomic constraints were checked deterministically."
    )
