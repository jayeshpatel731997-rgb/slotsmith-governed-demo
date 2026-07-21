# Phase review checkpoints

Each checkpoint adds tests and records a concrete defect search. These are engineering review gates, not claims of production readiness.

## Phase 1 — synthetic WMS and constraints

Evidence: `test_seed_and_constraints.py` reproduces 3,200 bins, 2,000 SKUs, and 10,000 synthetic orders from seed `240519`, validates every baseline assignment, confirms order-derived affinity determinism, and directly challenges zone and floor-weight constraints.

Defect hunted: **constraint leak**. The congested-aisle injector initially placed a chilled SKU into an ambient hot-aisle bin. The baseline-wide constraint test caught it; the injector now checks zone, volume, maximum weight, and floor-only heavy-item rules before changing an assignment.

## Phase 2 — optimizer and digital twin

Evidence: `test_optimizer.py` runs every scenario, asserts positive travel reduction and throughput gain, validates the resulting full assignment, caps the batch, confirms deterministic output, and injects an occupied destination into the simulator.

Defect hunted: **capacity-violating/unsafe sequence**. The simulator rejects occupied destinations at the exact move sequence. The optimizer targets currently empty locations and reserves each destination once, making the list executable from first move to last without a swap cycle.

## Phase 3 — governed execution

Evidence: `test_governance.py` covers missing approval, attributable execution, stale approval versions, retry idempotency, immutable audit triggers, automatic infeasibility escalation, public append-only event emission, and measured-versus-projected outcomes.

Defect hunted: **non-idempotent write and resource leak**. Replaying an idempotency key is asserted to create one WMS write/audit event. The all-scenario script additionally found SQLite connections left open on Windows; the repository context manager now closes every connection and the script completes temporary-directory cleanup.

## Phase 4 — API, console, and explanations

Evidence: `App.test.tsx` verifies the synthetic status and approval boundary, TypeScript production compilation checks the UI contract, and the eight captured screens cover loading-ready, proposal, executed, scenario, audit, and responsive states.

Defect hunted: **AI decision creep**. `explain_move()` accepts an already-decided `Move` and returns only a string. It has no path to mutate assignments or call execution, and its no-key template is exercised in every proposal.

## Phase 5 — packaging and story

Evidence: GitHub Actions pins Python 3.12, runs pytest plus the frontend test/build, then builds and health-checks Docker Compose. The README starts with the required status banner, reports generated scenario output, documents limitations, and includes the interview narrative.

Defect hunted: **reproducibility and offline-build drift**. The first image definition fetched npm and PyPI dependencies during `docker compose up --build`, contradicting the offline requirement. The corrected image uses a committed reproducible console build plus checksum-verified CPython 3.12/manylinux wheels; CI builds with `--network=none` after obtaining the base image. `pip-audit` and `npm audit` report zero known vulnerabilities at checkpoint time. A security review also caught and replaced the original FastAPI/Starlette pin after current Starlette advisories were detected.
