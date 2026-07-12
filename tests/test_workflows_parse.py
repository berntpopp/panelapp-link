"""Guards for the GitHub Actions security workflows (F-18).

Asserts every workflow file is valid YAML and that the added ``security.yml``
wires a CodeQL analysis job plus a *blocking* dependency-review job (no
``continue-on-error``; a high-severity finding fails the check).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} is not a mapping"
    return data


def test_all_workflows_are_valid_yaml() -> None:
    """Every ``.github/workflows`` file parses (Actions loads .yml AND .yaml)."""
    files = [*_WORKFLOWS.glob("*.yml"), *_WORKFLOWS.glob("*.yaml")]
    assert files, "no workflow files found"
    for path in files:
        _load(path)  # must not raise


def test_security_workflow_has_codeql_and_blocking_dependency_review() -> None:
    """security.yml runs CodeQL + a blocking high-severity dependency review."""
    security = _WORKFLOWS / "security.yml"
    assert security.exists(), "security.yml (CodeQL + dependency-review) is missing"
    doc = _load(security)

    jobs = doc.get("jobs", {})
    assert "codeql" in jobs, "codeql job missing"
    assert "dependency-review" in jobs, "dependency-review job missing"

    text = security.read_text(encoding="utf-8")
    # dependency-review must be BLOCKING: no continue-on-error escape hatch, and a
    # high-severity finding fails the check.
    assert "continue-on-error" not in text, "dependency-review must not be non-blocking"
    assert "fail-on-severity: high" in text, "dependency-review must fail on high severity"

    # CodeQL + dependency-review actions must be SHA-pinned (40-hex commit refs),
    # not floating tags.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("uses:"):
            ref = stripped.split("@", 1)[1].split()[0] if "@" in stripped else ""
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
                f"action not SHA-pinned: {stripped}"
            )
