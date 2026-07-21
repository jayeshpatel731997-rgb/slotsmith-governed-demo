"""Release gates that do not require Docker on the development host."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_offline_runtime_bundle_is_complete_and_integrity_checked():
    wheel_dir = ROOT / "vendor" / "runtime"
    expected = {}
    for line in (wheel_dir / "SHA256SUMS").read_text().splitlines():
        digest, filename = line.split("  ", 1)
        expected[filename] = digest
    actual = {path.name for path in wheel_dir.glob("*.whl")}
    assert actual == set(expected)
    for filename, digest in expected.items():
        assert hashlib.sha256((wheel_dir / filename).read_bytes()).hexdigest() == digest

    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "--no-index" in dockerfile
    assert "sha256sum -c SHA256SUMS" in dockerfile
    assert "COPY frontend/dist/" in dockerfile
    assert (ROOT / "frontend" / "dist" / "index.html").is_file()


def test_public_artifacts_and_required_banner_exist():
    banner = (
        "This is a SYNTHETIC, NON-COMMERCIAL PORTFOLIO PROTOTYPE built for learning and "
        "demonstration only. Not operated as a business, takes no customers, uses only "
        "locally-generated synthetic data. All monetization/market content is analysis, not an offer."
    )
    assert (ROOT / "README.md").read_text().splitlines()[0] == banner
    screenshots = sorted((ROOT / "docs" / "screenshots").glob("*.png"))
    assert len(screenshots) == 8
    assert all(path.stat().st_size > 100_000 for path in screenshots)
