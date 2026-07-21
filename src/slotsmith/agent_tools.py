"""Exact bounded tool façade used by the single SlotSmith orchestrator.

Every consequential method delegates to the governed service; no tool can
bypass approval, idempotency, optimistic concurrency, or audit persistence.
"""

from __future__ import annotations

from typing import Any

from .domain import Move, Proposal
from .engine import MAX_BATCH_SIZE, explain_move as deterministic_explanation, simulate_moves as digital_twin
from .service import GovernanceError, SlotSmithService

SUPPORTED_OBJECTIVES = frozenset({"pick_travel", "congestion", "co_pick_affinity"})
SUPPORTED_CONSTRAINTS = frozenset({"capacity", "weight_on_ground", "hazmat_zoning", "golden_zone"})


class SlotSmithTools:
    """Typed tool collection for one deterministic governed orchestrator."""

    def __init__(self, service: SlotSmithService) -> None:
        self.service = service

    def detect_slotting_triggers(self) -> list[dict[str, object]]:
        return self.service.detect()

    def gather_slotting_context(self, sku_ids: list[str]) -> dict[str, object]:
        return self.service.gather_context(sku_ids)

    def run_slotting_optimization(
        self,
        scope: list[str] | None = None,
        objectives: set[str] | None = None,
        constraints: set[str] | None = None,
        batch_size: int = MAX_BATCH_SIZE,
    ) -> Proposal:
        requested_objectives = objectives or set(SUPPORTED_OBJECTIVES)
        requested_constraints = constraints or set(SUPPORTED_CONSTRAINTS)
        unsupported = (requested_objectives - SUPPORTED_OBJECTIVES) | (requested_constraints - SUPPORTED_CONSTRAINTS)
        if unsupported:
            raise GovernanceError(f"Unsupported deterministic policy: {', '.join(sorted(unsupported))}")
        return self.service.create_proposal(batch_size, scope)

    @staticmethod
    def build_move_list(proposal: Proposal) -> list[Move]:
        moves = sorted(proposal.moves, key=lambda move: move.sequence)
        if len(moves) > MAX_BATCH_SIZE or [move.sequence for move in moves] != list(range(1, len(moves) + 1)):
            raise GovernanceError("Move list is unbounded or not dependency ordered")
        return moves

    def simulate_moves(self, move_list: list[Move]) -> dict[str, float]:
        data, _, _ = self.service.load_data()
        return digital_twin(data, move_list)

    def explain_move(self, move: Move) -> str:
        data, _, _ = self.service.load_data()
        return deterministic_explanation(move, data)

    def request_approval(self, proposal_id: str, actor: str, expected_version: int) -> str:
        return self.service.approve(proposal_id, actor, expected_version)

    def execute_moves(
        self, proposal_id: str, approval_token: str, idempotency_key: str
    ) -> dict[str, object]:
        return self.service.execute(proposal_id, approval_token, idempotency_key)

    def observe_outcome(self, proposal_id: str) -> dict[str, object]:
        return self.service.observe(proposal_id)

    def escalate(self, reason: str, actor: str = "orchestrator") -> dict[str, str]:
        return self.service.escalate(reason, actor)

    def emit_audit(self, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        self.service.emit_audit(event_type, actor, payload)
