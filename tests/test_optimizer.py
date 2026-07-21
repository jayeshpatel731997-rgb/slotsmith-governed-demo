import pytest

from slotsmith.domain import Scenario
from slotsmith.engine import MAX_BATCH_SIZE, InfeasibleError, optimize, simulate_moves, validate_assignments
from slotsmith.seed import generate_warehouse


@pytest.mark.parametrize("scenario", list(Scenario))
def test_every_demo_has_bounded_valid_measurable_improvement(scenario):
    data = generate_warehouse(scenario)
    moves, assignments = optimize(data)
    metrics = simulate_moves(data, moves)
    assert 1 <= len(moves) <= MAX_BATCH_SIZE
    assert len({m.to_location for m in moves}) == len(moves)
    assert validate_assignments(data, assignments) == []
    assert metrics["projected_reduction_pct"] > 0
    assert metrics["projected_throughput_gain_pct"] > 0


def test_optimizer_and_kpi_are_deterministic():
    data = generate_warehouse(Scenario.POST_PROMO)
    first, _ = optimize(data, 10)
    second, _ = optimize(data, 10)
    assert first == second
    assert simulate_moves(data, first) == simulate_moves(data, second)


def test_simulator_detects_double_occupancy():
    data = generate_warehouse(Scenario.POST_PROMO)
    moves, _ = optimize(data, 1)
    occupied = next(iter(data.assignments.values()))
    bad = [type(moves[0])(1, moves[0].sku_id, moves[0].from_location, occupied, "bad")]
    with pytest.raises(InfeasibleError, match="occupied"):
        simulate_moves(data, bad)
