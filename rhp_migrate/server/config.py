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
    """Path to learnings.md in workspace root or configured via RHP_LEARNINGS_PATH."""
    env_path = os.environ.get("RHP_LEARNINGS_PATH")
    if env_path:
        return Path(env_path).resolve()
    return find_workspace_root() / "learnings.md"


def get_context_path() -> Path:
    """Path to context.md, checking RHP_CONTEXT_PATH, RHP_RULES_DIR, package, and workspace."""
    env_path = os.environ.get("RHP_CONTEXT_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path).resolve()
    env_dir = os.environ.get("RHP_RULES_DIR")
    if env_dir and (Path(env_dir) / "context.md").is_file():
        return (Path(env_dir) / "context.md").resolve()

    # Next check standard package path
    package_context = Path(__file__).resolve().parent.parent / "rules" / "context.md"
    if package_context.is_file():
        return package_context
    return find_workspace_root() / "rhp_migrate" / "rules" / "context.md"


def get_rules_yaml_path() -> Path:
    """Path to rules.yaml, checking RHP_RULES_YAML_PATH, RHP_RULES_DIR, package, and workspace."""
    env_path = os.environ.get("RHP_RULES_YAML_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path).resolve()
    env_dir = os.environ.get("RHP_RULES_DIR")
    if env_dir and (Path(env_dir) / "rules.yaml").is_file():
        return (Path(env_dir) / "rules.yaml").resolve()

    package_yaml = Path(__file__).resolve().parent.parent / "rules" / "rules.yaml"
    if package_yaml.is_file():
        return package_yaml
    return find_workspace_root() / "rhp_migrate" / "rules" / "rules.yaml"


def get_pending_context_path() -> Path:
    """Path to .rhp/feedback/pending_context.md."""
    return find_workspace_root() / ".rhp" / "feedback" / "pending_context.md"
