"""Run and print the full governed loop for all three synthetic scenarios."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from slotsmith.domain import Scenario
from slotsmith.service import SlotSmithService


def main() -> None:
    results = []
    with tempfile.TemporaryDirectory() as directory:
        for scenario in Scenario:
            service = SlotSmithService(Path(directory) / f"{scenario.value}.db")
            service.seed(scenario)
            triggers = service.detect()
            proposal = service.create_proposal()
            token = service.approve(proposal.id, "demo.operator", proposal.version)
            execution = service.execute(proposal.id, token, f"demo-{scenario.value}")
            outcome = service.observe(proposal.id)
            results.append({
                "scenario": scenario.value, "triggers": [item["kind"] for item in triggers],
                "moves": len(proposal.moves), "before": proposal.before_travel,
                "after": execution["projected_travel"], "reduction_pct": proposal.projected_reduction_pct,
                "throughput_gain_pct": execution["projected_throughput_gain_pct"],
                "measured_variance_pct": outcome["variance_pct"], "audit_events": len(service.audit_events()),
            })
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
