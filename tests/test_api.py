import asyncio

import httpx

from slotsmith import api
from slotsmith.domain import Scenario
from slotsmith.service import SlotSmithService


def test_api_exposes_typed_governed_loop(tmp_path, monkeypatch):
    local = SlotSmithService(tmp_path / "api.db")
    local.seed(Scenario.NEW_SKU)
    monkeypatch.setattr(api, "service", local)
    async def exercise() -> None:
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/health")).json()["mode"] == "synthetic-no-key"
            assert (await client.get("/api/triggers")).status_code == 200
            context = await client.post("/api/context", json={"sku_ids": ["SKU-0001"]})
            assert context.json()["skus"][0]["id"] == "SKU-0001"
            proposal = (await client.post("/api/proposals", json={"batch_size": 2})).json()
            denied = await client.post(
                f"/api/proposals/{proposal['id']}/execute",
                json={"approval_token": "bad", "idempotency_key": "k"},
            )
            assert denied.status_code == 400
            approval = await client.post(
                f"/api/proposals/{proposal['id']}/approve",
                json={"actor": "API Operator", "expected_version": 1},
            )
            executed = await client.post(
                f"/api/proposals/{proposal['id']}/execute",
                json={"approval_token": approval.json()["approval_token"], "idempotency_key": "api-k"},
            )
            assert executed.json()["moves_executed"] == 2

    asyncio.run(exercise())
