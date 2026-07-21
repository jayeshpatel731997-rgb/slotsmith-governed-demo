"""Governed orchestration and SQLite simulated-WMS persistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Iterator

from .domain import Location, Move, Proposal, ProposalStatus, SKU, Scenario
from .engine import MAX_BATCH_SIZE, InfeasibleError, detect_slotting_triggers, explain_move, optimize, simulate_moves, travel_kpi
from .seed import WarehouseData, generate_warehouse


class GovernanceError(RuntimeError):
    pass


class ConflictError(GovernanceError):
    pass


class SlotSmithService:
    def __init__(self, db_path: str | Path = "slotsmith.db") -> None:
        self.db_path = str(db_path)
        self._create_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _create_schema(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS warehouse_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    scenario TEXT NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS locations (
                    id TEXT PRIMARY KEY, aisle INTEGER NOT NULL, bay INTEGER NOT NULL, level INTEGER NOT NULL,
                    zone TEXT NOT NULL, capacity_cm3 INTEGER NOT NULL, max_weight_kg REAL NOT NULL,
                    golden INTEGER NOT NULL, congestion REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skus (
                    id TEXT PRIMARY KEY, velocity REAL NOT NULL, volume_cm3 INTEGER NOT NULL,
                    weight_kg REAL NOT NULL, zone TEXT NOT NULL, hazmat INTEGER NOT NULL, is_new INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    sku_id TEXT PRIMARY KEY REFERENCES skus(id),
                    location_id TEXT NOT NULL UNIQUE REFERENCES locations(id)
                );
                CREATE TABLE IF NOT EXISTS affinities (
                    sku_a TEXT NOT NULL REFERENCES skus(id), sku_b TEXT NOT NULL REFERENCES skus(id),
                    weight REAL NOT NULL, PRIMARY KEY(sku_a, sku_b)
                );
                CREATE TABLE IF NOT EXISTS synthetic_orders (
                    id INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS synthetic_order_lines (
                    order_id INTEGER NOT NULL REFERENCES synthetic_orders(id),
                    line_number INTEGER NOT NULL, sku_id TEXT NOT NULL REFERENCES skus(id),
                    PRIMARY KEY(order_id, line_number)
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    token TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL,
                    created_at TEXT NOT NULL, used_at TEXT, FOREIGN KEY(proposal_id) REFERENCES proposals(id)
                );
                CREATE TABLE IF NOT EXISTS executions (
                    idempotency_key TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
                    actor TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
            """)

    def seed(self, scenario: Scenario, force: bool = False) -> dict[str, int | str]:
        data = generate_warehouse(scenario)
        payload = self._data_to_json(data)
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM warehouse_state WHERE singleton=1").fetchone()
            if exists and not force:
                return self.summary()
            if exists:
                db.execute("DELETE FROM approvals")
                db.execute("DELETE FROM executions")
                db.execute("DELETE FROM proposals")
                db.execute("DELETE FROM synthetic_order_lines")
                db.execute("DELETE FROM synthetic_orders")
                db.execute("DELETE FROM affinities")
                db.execute("DELETE FROM assignments")
                db.execute("DELETE FROM skus")
                db.execute("DELETE FROM locations")
            db.execute(
                "INSERT INTO warehouse_state(singleton,scenario,payload,version) VALUES(1,?,?,1) "
                "ON CONFLICT(singleton) DO UPDATE SET scenario=excluded.scenario,payload=excluded.payload,version=warehouse_state.version+1",
                (scenario.value, payload),
            )
            db.executemany(
                "INSERT INTO locations VALUES(?,?,?,?,?,?,?,?,?)",
                [(loc.id, loc.aisle, loc.bay, loc.level, loc.zone, loc.capacity_cm3, loc.max_weight_kg, int(loc.golden), loc.congestion) for loc in data.locations.values()],
            )
            db.executemany(
                "INSERT INTO skus VALUES(?,?,?,?,?,?,?)",
                [(sku.id, sku.velocity, sku.volume_cm3, sku.weight_kg, sku.zone, int(sku.hazmat), int(sku.new)) for sku in data.skus.values()],
            )
            db.executemany("INSERT INTO assignments VALUES(?,?)", data.assignments.items())
            db.executemany("INSERT INTO affinities VALUES(?,?,?)", [(a, b, weight) for (a, b), weight in data.affinities.items()])
            db.executemany("INSERT INTO synthetic_orders(id) VALUES(?)", ((index,) for index in range(1, len(data.orders) + 1)))
            db.executemany(
                "INSERT INTO synthetic_order_lines VALUES(?,?,?)",
                ((order_id, line_number, sku_id) for order_id, order in enumerate(data.orders, 1) for line_number, sku_id in enumerate(order, 1)),
            )
            self._audit(db, "warehouse.seeded", "system", {"scenario": scenario.value, "seed": 240519})
        return self.summary()

    def summary(self) -> dict[str, int | str]:
        data, scenario, version = self.load_data()
        return {
            "scenario": scenario.value, "locations": len(data.locations), "skus": len(data.skus),
            "assignments": len(data.assignments), "version": version,
        }

    def load_data(self) -> tuple[WarehouseData, Scenario, int]:
        with self.connect() as db:
            row = db.execute("SELECT scenario,payload,version FROM warehouse_state WHERE singleton=1").fetchone()
        if not row:
            raise GovernanceError("Warehouse is not seeded")
        return self._data_from_json(row["payload"]), Scenario(row["scenario"]), row["version"]

    def detect(self) -> list[dict[str, object]]:
        data, _, _ = self.load_data()
        return [asdict(item) for item in detect_slotting_triggers(data)]

    def gather_context(self, sku_ids: list[str]) -> dict[str, object]:
        """Return typed optimizer inputs for a bounded set of known SKUs."""
        if len(sku_ids) > 100:
            raise GovernanceError("Context scope is bounded at 100 SKUs")
        data, scenario, version = self.load_data()
        unknown = sorted(set(sku_ids) - data.skus.keys())
        if unknown:
            raise GovernanceError(f"Unknown SKU IDs: {', '.join(unknown[:5])}")
        records = []
        for sku_id in sku_ids:
            sku = data.skus[sku_id]
            location_id = data.assignments.get(sku_id)
            records.append({**asdict(sku), "current_location": location_id})
        affinity = [
            {"sku_a": a, "sku_b": b, "weight": weight}
            for (a, b), weight in data.affinities.items() if a in sku_ids or b in sku_ids
        ]
        capacity = {
            zone: sum(1 for loc in data.locations.values() if loc.zone == zone and loc.id not in data.assignments.values())
            for zone in ("ambient", "chilled", "hazmat")
        }
        return {"scenario": scenario.value, "warehouse_version": version, "skus": records, "affinity": affinity, "empty_capacity_by_zone": capacity}

    def escalate(self, reason: str, actor: str = "orchestrator") -> dict[str, str]:
        if not reason.strip():
            raise GovernanceError("Escalation reason is required")
        escalation_id = f"esc_{uuid.uuid4().hex[:12]}"
        with self.connect() as db:
            self._audit(db, "optimization.escalated", actor, {"escalation_id": escalation_id, "reason": reason.strip()})
        return {"escalation_id": escalation_id, "status": "open"}

    def emit_audit(self, event_type: str, actor: str, payload: dict[str, object]) -> None:
        """Append a validated external tool event without permitting mutation."""
        if not event_type.strip() or not actor.strip():
            raise GovernanceError("Audit event type and actor are required")
        if len(event_type) > 100 or len(actor) > 80:
            raise GovernanceError("Audit event metadata exceeds its bound")
        with self.connect() as db:
            self._audit(db, event_type.strip(), actor.strip(), payload)

    def create_proposal(self, batch_size: int = MAX_BATCH_SIZE, sku_ids: list[str] | None = None) -> Proposal:
        data, scenario, _ = self.load_data()
        scope = set(sku_ids) if sku_ids else None
        if sku_ids is not None:
            if len(sku_ids) > 100:
                raise GovernanceError("Optimization scope is bounded at 100 SKUs")
            unknown = sorted(scope - data.skus.keys())
            if unknown:
                raise GovernanceError(f"Unknown SKU IDs: {', '.join(unknown[:5])}")
        try:
            moves, _ = optimize(data, batch_size, scope)
        except InfeasibleError as error:
            self.escalate(str(error), "optimizer")
            raise
        metrics = simulate_moves(data, moves)
        proposal = Proposal(
            id=f"prop_{uuid.uuid4().hex[:12]}", scenario=scenario, moves=moves,
            before_travel=metrics["before_travel"], projected_travel=metrics["projected_travel"],
            projected_reduction_pct=metrics["projected_reduction_pct"],
            explanations=[explain_move(move, data) for move in moves],
        )
        with self.connect() as db:
            db.execute("INSERT INTO proposals VALUES(?,?,?,?)", (proposal.id, json.dumps(proposal.to_dict()), proposal.status.value, proposal.version))
            self._audit(db, "proposal.created", "orchestrator", {"proposal_id": proposal.id, "moves": len(moves), **metrics})
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal:
        with self.connect() as db:
            row = db.execute("SELECT payload,status,version FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise GovernanceError("Unknown proposal")
        raw = json.loads(row["payload"])
        return Proposal(
            id=raw["id"], scenario=Scenario(raw["scenario"]), moves=[Move(**m) for m in raw["moves"]],
            before_travel=raw["before_travel"], projected_travel=raw["projected_travel"],
            projected_reduction_pct=raw["projected_reduction_pct"], status=ProposalStatus(row["status"]),
            version=row["version"], explanations=raw["explanations"],
        )

    def approve(self, proposal_id: str, actor: str, expected_version: int) -> str:
        if not actor.strip():
            raise GovernanceError("Approval actor is required")
        token = f"apr_{uuid.uuid4().hex}"
        with self.connect() as db:
            updated = db.execute(
                "UPDATE proposals SET status=?,version=version+1 WHERE id=? AND status=? AND version=?",
                (ProposalStatus.APPROVED.value, proposal_id, ProposalStatus.PROPOSED.value, expected_version),
            )
            if updated.rowcount != 1:
                raise ConflictError("Proposal changed or is no longer approvable")
            db.execute("INSERT INTO approvals VALUES(?,?,?,?,NULL)", (token, proposal_id, actor.strip(), self._now()))
            self._audit(db, "proposal.approved", actor.strip(), {"proposal_id": proposal_id, "expected_version": expected_version})
        return token

    def reject(self, proposal_id: str, actor: str, expected_version: int, reason: str) -> None:
        if not actor.strip():
            raise GovernanceError("Decision actor is required")
        with self.connect() as db:
            updated = db.execute(
                "UPDATE proposals SET status=?,version=version+1 WHERE id=? AND status=? AND version=?",
                (ProposalStatus.REJECTED.value, proposal_id, ProposalStatus.PROPOSED.value, expected_version),
            )
            if updated.rowcount != 1:
                raise ConflictError("Proposal changed or is no longer rejectable")
            self._audit(db, "proposal.rejected", actor.strip(), {"proposal_id": proposal_id, "reason": reason})

    def execute(self, proposal_id: str, approval_token: str, idempotency_key: str) -> dict[str, object]:
        if not idempotency_key.strip():
            raise GovernanceError("Idempotency key is required")
        proposal = self.get_proposal(proposal_id)
        if len(proposal.moves) > MAX_BATCH_SIZE:
            raise GovernanceError("Move batch exceeds governance bound")
        with self.connect() as db:
            prior = db.execute("SELECT result FROM executions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if prior:
                return json.loads(prior["result"])
            approval = db.execute(
                "SELECT actor,used_at FROM approvals WHERE token=? AND proposal_id=?", (approval_token, proposal_id)
            ).fetchone()
            if not approval:
                raise GovernanceError("A valid attributable approval token is required")
            if proposal.status != ProposalStatus.APPROVED:
                raise GovernanceError("Proposal is not approved")
            data, scenario, state_version = self.load_data()
            metrics = simulate_moves(data, proposal.moves)
            assignments = dict(data.assignments)
            for move in sorted(proposal.moves, key=lambda m: m.sequence):
                assignments[move.sku_id] = move.to_location
            data.assignments = assignments
            result = {
                "proposal_id": proposal_id, "status": "executed", "moves_executed": len(proposal.moves),
                "warehouse_version": state_version + 1, **metrics,
            }
            state_update = db.execute(
                "UPDATE warehouse_state SET payload=?,version=version+1 WHERE singleton=1 AND version=?",
                (self._data_to_json(data), state_version),
            )
            if state_update.rowcount != 1:
                raise ConflictError("Warehouse state changed; proposal must be re-simulated")
            for move in sorted(proposal.moves, key=lambda item: item.sequence):
                db.execute(
                    "INSERT INTO assignments(sku_id,location_id) VALUES(?,?) "
                    "ON CONFLICT(sku_id) DO UPDATE SET location_id=excluded.location_id",
                    (move.sku_id, move.to_location),
                )
            db.execute("UPDATE proposals SET status=?,version=version+1 WHERE id=?", (ProposalStatus.EXECUTED.value, proposal_id))
            db.execute("UPDATE approvals SET used_at=? WHERE token=?", (self._now(), approval_token))
            db.execute("INSERT INTO executions VALUES(?,?,?,?)", (idempotency_key, proposal_id, json.dumps(result), self._now()))
            self._audit(db, "moves.executed", approval["actor"], result)
        return result

    def observe(self, proposal_id: str) -> dict[str, object]:
        proposal = self.get_proposal(proposal_id)
        data, _, _ = self.load_data()
        measured = travel_kpi(data)
        variance_pct = round((measured - proposal.projected_travel) / proposal.projected_travel * 100, 3)
        daily_picks = sum(sku.velocity for sku in data.skus.values())
        projected_throughput = round(daily_picks * 60 / max(proposal.projected_travel, 1) * 1_000, 3)
        measured_throughput = round(daily_picks * 60 / max(measured, 1) * 1_000, 3)
        result = {
            "proposal_id": proposal_id, "projected": proposal.projected_travel, "measured": measured,
            "variance_pct": variance_pct, "projected_throughput_pph": projected_throughput,
            "measured_throughput_pph": measured_throughput, "drift": abs(variance_pct) > 5,
        }
        with self.connect() as db:
            self._audit(db, "outcome.observed", "orchestrator", result)
        return result

    def audit_events(self) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM audit ORDER BY seq").fetchall()
        return [dict(row) | {"payload": json.loads(row["payload"])} for row in rows]

    def _audit(self, db: sqlite3.Connection, event_type: str, actor: str, payload: dict[str, object]) -> None:
        db.execute("INSERT INTO audit(event_type,actor,payload,created_at) VALUES(?,?,?,?)", (event_type, actor, json.dumps(payload, sort_keys=True), self._now()))

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _data_to_json(data: WarehouseData) -> str:
        return json.dumps({
            "locations": [asdict(x) for x in data.locations.values()],
            "skus": [asdict(x) for x in data.skus.values()],
            "assignments": data.assignments,
            "affinities": [[a, b, value] for (a, b), value in data.affinities.items()],
            "orders": data.orders,
            "seasonal_reslot_due": data.seasonal_reslot_due,
        }, separators=(",", ":"))

    @staticmethod
    def _data_from_json(payload: str) -> WarehouseData:
        raw = json.loads(payload)
        return WarehouseData(
            locations={x["id"]: Location(**x) for x in raw["locations"]},
            skus={x["id"]: SKU(**x) for x in raw["skus"]}, assignments=raw["assignments"],
            affinities={(a, b): value for a, b, value in raw["affinities"]},
            orders=[tuple(order) for order in raw.get("orders", [])],
            seasonal_reslot_due=raw.get("seasonal_reslot_due", False),
        )
