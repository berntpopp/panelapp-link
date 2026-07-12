"""Regression guard: the Docker build bootstraps uv from a digest-pinned image.

A floating ``pip install --upgrade pip uv`` pulls whatever installer version the
index happens to serve at build time -- an unpinned supply-chain input. The
builder must instead COPY the ``uv`` binary from a specific
``ghcr.io/astral-sh/uv`` image pinned by ``@sha256:`` so every build resolves the
exact same artifact (F-19).
"""

from __future__ import annotations

from pathlib import Path

_DOCKERFILE = Path(__file__).resolve().parent.parent / "docker" / "Dockerfile"

# The exact digest-pinned uv image shared fleet-wide (matches the router pin).
_UV_COPY = (
    "COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:"
    "1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab "
    "/uv /usr/local/bin/uv"
)


def test_dockerfile_pins_uv_and_has_no_floating_pip_upgrade() -> None:
    """No floating installer upgrade; uv comes from the digest-pinned COPY."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert "pip install --upgrade" not in text, "floating pip/uv upgrade must be removed"
    assert _UV_COPY in text, "uv must be COPYed from the digest-pinned ghcr.io/astral-sh/uv image"
