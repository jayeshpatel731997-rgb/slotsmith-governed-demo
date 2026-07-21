"""Typed domain objects shared by the optimizer, WMS, and API."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Scenario(StrEnum):
    POST_PROMO = "post-promo"
    NEW_SKU = "new-sku"
    CONGESTED_AISLE = "congested-aisle"


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


@dataclass(frozen=True, slots=True)
class Location:
    id: str
    aisle: int
    bay: int
    level: int
    zone: str
    capacity_cm3: int
    max_weight_kg: float
    golden: bool
    congestion: float = 0.0

    @property
    def distance(self) -> float:
        # Pack-out is beside aisle 1/bay 1; vertical reaches carry a labor cost.
        return (self.aisle - 1) * 24 + (self.bay - 1) * 2 + self.level * 6


@dataclass(frozen=True, slots=True)
class SKU:
    id: str
    velocity: float
    volume_cm3: int
    weight_kg: float
    zone: str
    hazmat: bool = False
    new: bool = False


@dataclass(frozen=True, slots=True)
class Move:
    sequence: int
    sku_id: str
    from_location: str | None
    to_location: str
    reason: str


@dataclass(slots=True)
class Proposal:
    id: str
    scenario: Scenario
    moves: list[Move]
    before_travel: float
    projected_travel: float
    projected_reduction_pct: float
    status: ProposalStatus = ProposalStatus.PROPOSED
    version: int = 1
    explanations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["scenario"] = self.scenario.value
        result["status"] = self.status.value
        return result


@dataclass(frozen=True, slots=True)
class Trigger:
    kind: str
    sku_ids: tuple[str, ...]
    detail: str
