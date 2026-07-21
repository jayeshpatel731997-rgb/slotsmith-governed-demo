"""FastAPI surface for the bounded SlotSmith orchestrator tools."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .domain import Scenario
from .service import ConflictError, GovernanceError, SlotSmithService

DB_PATH = os.getenv("SLOTSMITH_DB", "/data/slotsmith.db" if Path("/data").exists() else "slotsmith.db")
service = SlotSmithService(DB_PATH)
try:
    service.summary()
except GovernanceError:
    service.seed(Scenario.POST_PROMO)

app = FastAPI(title="SlotSmith", version="0.1.0", description="Governed deterministic warehouse slotting")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


class SeedRequest(BaseModel):
    scenario: Scenario


class ProposalRequest(BaseModel):
    batch_size: int = Field(default=25, ge=1, le=25)
    sku_ids: list[str] | None = Field(default=None, max_length=100)


class ContextRequest(BaseModel):
    sku_ids: list[str] = Field(max_length=100)


class EscalationRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    actor: str = Field(default="orchestrator", min_length=1, max_length=80)


class DecisionRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=80)
    expected_version: int = Field(ge=1)
    reason: str = Field(default="", max_length=500)


class ExecuteRequest(BaseModel):
    approval_token: str
    idempotency_key: str = Field(min_length=1, max_length=100)


def handle_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=409 if isinstance(error, ConflictError) else 400, detail=str(error))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "synthetic-no-key"}


@app.get("/api/warehouse")
def warehouse() -> dict[str, object]:
    data, scenario, version = service.load_data()
    aisles = []
    for aisle in range(1, 21):
        sku_ids = [sid for sid, lid in data.assignments.items() if data.locations[lid].aisle == aisle]
        aisles.append({
            "aisle": aisle, "occupancy": len(sku_ids),
            "velocity": round(sum(data.skus[sid].velocity for sid in sku_ids), 1),
            "congestion": max((data.locations[lid].congestion for lid in data.assignments.values() if data.locations[lid].aisle == aisle), default=0),
        })
    return {**service.summary(), "scenario": scenario.value, "version": version, "aisles": aisles}


@app.post("/api/seed")
def seed(request: SeedRequest) -> dict[str, object]:
    return service.seed(request.scenario, force=True)


@app.get("/api/triggers")
def triggers() -> list[dict[str, object]]:
    return service.detect()


@app.post("/api/context")
def context(request: ContextRequest) -> dict[str, object]:
    try:
        return service.gather_context(request.sku_ids)
    except GovernanceError as error:
        raise handle_error(error) from error


@app.post("/api/escalations")
def escalate(request: EscalationRequest) -> dict[str, str]:
    try:
        return service.escalate(request.reason, request.actor)
    except GovernanceError as error:
        raise handle_error(error) from error


@app.post("/api/proposals")
def propose(request: ProposalRequest) -> dict[str, object]:
    try:
        return service.create_proposal(request.batch_size, request.sku_ids).to_dict()
    except (GovernanceError, ValueError) as error:
        raise handle_error(error) from error


@app.get("/api/proposals/{proposal_id}")
def get_proposal(proposal_id: str) -> dict[str, object]:
    try:
        return service.get_proposal(proposal_id).to_dict()
    except GovernanceError as error:
        raise handle_error(error) from error


@app.post("/api/proposals/{proposal_id}/approve")
def approve(proposal_id: str, request: DecisionRequest) -> dict[str, str]:
    try:
        return {"approval_token": service.approve(proposal_id, request.actor, request.expected_version)}
    except GovernanceError as error:
        raise handle_error(error) from error


@app.post("/api/proposals/{proposal_id}/reject")
def reject(proposal_id: str, request: DecisionRequest) -> dict[str, str]:
    try:
        service.reject(proposal_id, request.actor, request.expected_version, request.reason)
        return {"status": "rejected"}
    except GovernanceError as error:
        raise handle_error(error) from error


@app.post("/api/proposals/{proposal_id}/execute")
def execute(proposal_id: str, request: ExecuteRequest) -> dict[str, object]:
    try:
        return service.execute(proposal_id, request.approval_token, request.idempotency_key)
    except GovernanceError as error:
        raise handle_error(error) from error


@app.get("/api/proposals/{proposal_id}/outcome")
def outcome(proposal_id: str) -> dict[str, object]:
    try:
        return service.observe(proposal_id)
    except GovernanceError as error:
        raise handle_error(error) from error


@app.get("/api/audit")
def audit() -> list[dict[str, object]]:
    return service.audit_events()


STATIC = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        return FileResponse(STATIC / "index.html")
