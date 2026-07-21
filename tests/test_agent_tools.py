import pytest

from slotsmith.agent_tools import SlotSmithTools
from slotsmith.domain import Scenario
from slotsmith.service import GovernanceError, SlotSmithService


def test_exact_tool_facade_runs_scoped_governed_loop(tmp_path):
    service = SlotSmithService(tmp_path / "tools.db")
    service.seed(Scenario.NEW_SKU)
    tools = SlotSmithTools(service)
    triggers = tools.detect_slotting_triggers()
    unslotted = next(item for item in triggers if item["kind"] == "unslotted_sku")["sku_ids"]
    scope = list(unslotted)
    assert len(tools.gather_slotting_context(scope)["skus"]) == 20
    proposal = tools.run_slotting_optimization(scope=scope, batch_size=10)
    moves = tools.build_move_list(proposal)
    assert tools.simulate_moves(moves)["projected_reduction_pct"] > 0
    assert "checked deterministically" in tools.explain_move(moves[0])
    with pytest.raises(GovernanceError, match="approval"):
        tools.execute_moves(proposal.id, "missing", "tool-key")
    token = tools.request_approval(proposal.id, "Tool Operator", proposal.version)
    assert tools.execute_moves(proposal.id, token, "tool-key")["moves_executed"] == 10
    assert tools.observe_outcome(proposal.id)["drift"] is False


def test_tool_facade_rejects_unsupported_policy(tmp_path):
    service = SlotSmithService(tmp_path / "tools.db")
    service.seed(Scenario.POST_PROMO)
    with pytest.raises(GovernanceError, match="Unsupported"):
        SlotSmithTools(service).run_slotting_optimization(objectives={"llm_decides_assignment"})
