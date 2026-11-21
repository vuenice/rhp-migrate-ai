"""Server configuration and workspace path resolvers."""

from __future__ import annotations

import os
from pathlib import Path


def find_workspace_root() -> Path:
    """Locate the root directory of the workspace.

    Checks RHP_WORKSPACE_ROOT env var, or traverses upward from this file
    until finding pyproject.toml or learnings.md.
    """
    env_root = os.environ.get("RHP_WORKSPACE_ROOT")
    if env_root and Path(env_root).is_dir():
        return Path(env_root).resolve()

    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").is_file() or (parent / "learnings.md").is_file():
            return parent

    return Path.cwd().resolve()


def get_learnings_path() -> Path:
    """Path to learnings.md in workspace root."""
    return find_workspace_root() / "learnings.md"


def get_context_path() -> Path:
    """Path to rhp_migrate/rules/context.md."""
    # First check standard package path
    package_context = Path(__file__).resolve().parent.parent / "rules" / "context.md"
    if package_context.is_file():
        return package_context
    return find_workspace_root() / "rhp_migrate" / "rules" / "context.md"


def get_rules_yaml_path() -> Path:
    """Path to rhp_migrate/rules/rules.yaml."""
    package_yaml = Path(__file__).resolve().parent.parent / "rules" / "rules.yaml"
    if package_yaml.is_file():
        return package_yaml
    return find_workspace_root() / "rhp_migrate" / "rules" / "rules.yaml"


def get_pending_context_path() -> Path:
    """Path to .rhp/feedback/pending_context.md."""
    return find_workspace_root() / ".rhp" / "feedback" / "pending_context.md"
