"""Guard the split between the deployed overlay and the released Compose files.

The fleet's deploy contract wants every service in the deployed NPM overlay to declare a
numeric non-root `user` so the controller's runtime observer can prove the effective uid
from /proc; the shared release gate forbids `user` in the release Compose files entirely,
so this test also asserts it never creeps back in there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_NUMERIC_USER = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_tagged(loader: _ComposeLoader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_ComposeLoader.add_multi_constructor("!", _construct_tagged)


def _load_compose(relative_path: str) -> dict[str, Any]:
    text = (REPO_ROOT / relative_path).read_text()
    return yaml.load(text, Loader=_ComposeLoader)  # noqa: S506


def test_npm_overlay_services_declare_numeric_user() -> None:
    compose = _load_compose("docker/docker-compose.npm.yml")

    for name, service in compose["services"].items():
        user = service.get("user")
        assert user is not None, f"service {name!r} is missing a numeric user"
        assert _NUMERIC_USER.match(str(user)), (
            f"service {name!r} user={user!r} is not numeric non-root (uid:gid)"
        )


def test_release_compose_files_never_declare_user() -> None:
    declared = json.loads((REPO_ROOT / "container-release.json").read_text())
    compose_files = declared["service"]["compose_files"]

    for relative_path in compose_files:
        compose = _load_compose(relative_path)
        for name, service in compose["services"].items():
            assert "user" not in service, (
                f"{relative_path}: service {name!r} declares user, but the release gate forbids it"
            )
