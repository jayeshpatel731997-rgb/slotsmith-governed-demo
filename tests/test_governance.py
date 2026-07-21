import sqlite3

import pytest

from slotsmith.domain import Scenario
from slotsmith.engine import InfeasibleError
from slotsmith.service import ConflictError, GovernanceError, SlotSmithService


@pytest.fixture()
def service(tmp_path):
    result = SlotSmithService(tmp_path / "wms.db")
    result.seed(Scenario.POST_PROMO)
    return result


def test_execution_requires_attributable_approval(service):
    proposal = service.create_proposal(5)
    with pytest.raises(GovernanceError, match="approval"):
        service.execute(proposal.id, "invalid", "attempt-1")
    token = service.approve(proposal.id, "Jordan Lee", proposal.version)
    result = service.execute(proposal.id, token, "attempt-2")
    assert result["moves_executed"] == 5
    assert any(e["event_type"] == "moves.executed" and e["actor"] == "Jordan Lee" for e in service.audit_events())


def test_wms_seed_is_normalized_and_execution_updates_assignment_rows(service):
    with service.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 3_200
        assert db.execute("SELECT COUNT(*) FROM skus").fetchone()[0] == 2_000
        assert db.execute("SELECT COUNT(*) FROM synthetic_orders").fetchone()[0] == 10_000
    proposal = service.create_proposal(1)
    move = proposal.moves[0]
    token = service.approve(proposal.id, "WMS Operator", proposal.version)
    service.execute(proposal.id, token, "normalized-write")
    with service.connect() as db:
        assert db.execute("SELECT location_id FROM assignments WHERE sku_id=?", (move.sku_id,)).fetchone()[0] == move.to_location


def test_execute_is_idempotent(service):
    proposal = service.create_proposal(3)
    token = service.approve(proposal.id, "Casey", proposal.version)
    first = service.execute(proposal.id, token, "same-key")
    second = service.execute(proposal.id, token, "same-key")
    assert first == second
    assert sum(e["event_type"] == "moves.executed" for e in service.audit_events()) == 1


def test_optimistic_concurrency_rejects_stale_decision(service):
    proposal = service.create_proposal(2)
    service.approve(proposal.id, "Casey", proposal.version)
    with pytest.raises(ConflictError):
        service.approve(proposal.id, "Other", proposal.version)


def test_audit_table_is_append_only(service):
    with service.connect() as db, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM audit")


def test_observed_outcome_matches_deterministic_projection(service):
    proposal = service.create_proposal(4)
    token = service.approve(proposal.id, "Taylor", proposal.version)
    service.execute(proposal.id, token, "observe-key")
    outcome = service.observe(proposal.id)
    assert outcome["variance_pct"] == 0
    assert outcome["measured_throughput_pph"] == outcome["projected_throughput_pph"]
    assert outcome["drift"] is False


def test_context_is_bounded_and_escalation_is_audited(service):
    context = service.gather_context(["SKU-0001", "SKU-0002"])
    assert len(context["skus"]) == 2
    assert set(context["empty_capacity_by_zone"]) == {"ambient", "chilled", "hazmat"}
    with pytest.raises(GovernanceError, match="bounded"):
        service.gather_context(["SKU-0001"] * 101)
    escalation = service.escalate("No feasible hazmat capacity", "optimizer")
    assert escalation["status"] == "open"
    assert service.audit_events()[-1]["event_type"] == "optimization.escalated"


def test_infeasible_optimizer_escalates_and_public_audit_is_append_only(service, monkeypatch):
    def impossible(*_args, **_kwargs):
        raise InfeasibleError("No constraint-safe capacity")

    monkeypatch.setattr("slotsmith.service.optimize", impossible)
    with pytest.raises(InfeasibleError):
        service.create_proposal()
    assert service.audit_events()[-1]["event_type"] == "optimization.escalated"
    service.emit_audit("tool.completed", "test.tool", {"ok": True})
    assert service.audit_events()[-1]["payload"] == {"ok": True}
