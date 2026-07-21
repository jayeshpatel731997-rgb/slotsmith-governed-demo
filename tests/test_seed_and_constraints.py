from slotsmith.domain import Scenario
from slotsmith.engine import compatible, detect_slotting_triggers, validate_assignments
from slotsmith.seed import generate_warehouse


def test_fixed_seed_produces_full_synthetic_scale_and_valid_baseline():
    first = generate_warehouse(Scenario.POST_PROMO)
    second = generate_warehouse(Scenario.POST_PROMO)
    assert len(first.locations) == 3_200
    assert len(first.skus) == 2_000
    assert len(first.orders) == 10_000
    assert first.orders == second.orders
    assert first.affinities == second.affinities
    assert first.assignments == second.assignments
    assert first.skus == second.skus
    assert validate_assignments(first, first.assignments) == []


def test_constraint_model_rejects_zone_capacity_weight_and_non_floor_heavy():
    data = generate_warehouse(Scenario.POST_PROMO)
    hazmat = next(s for s in data.skus.values() if s.hazmat)
    ambient = next(loc for loc in data.locations.values() if loc.zone == "ambient")
    assert not compatible(hazmat, ambient)
    heavy = next(s for s in data.skus.values() if s.weight_kg > 25)
    upper = next(loc for loc in data.locations.values() if loc.zone == heavy.zone and loc.level > 0)
    assert not compatible(heavy, upper)


def test_scenarios_emit_expected_trigger():
    assert any(t.kind == "velocity_shift" for t in detect_slotting_triggers(generate_warehouse(Scenario.POST_PROMO)))
    assert any(t.kind == "unslotted_sku" for t in detect_slotting_triggers(generate_warehouse(Scenario.NEW_SKU)))
    assert any(t.kind == "aisle_congestion" for t in detect_slotting_triggers(generate_warehouse(Scenario.CONGESTED_AISLE)))
    seasonal = generate_warehouse(Scenario.POST_PROMO)
    seasonal.seasonal_reslot_due = True
    assert any(t.kind == "seasonal_reslot_due" for t in detect_slotting_triggers(seasonal))
