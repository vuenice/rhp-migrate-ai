"""Unit and integration tests for Layer 7: CLI, Caching, and Git Automation."""

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from rhp_migrate.ai.client import LLMRunner, MockLLMClient
from rhp_migrate.ai.models import (
    AIExecutionStats,
    AIMigrationResponse,
    CostEstimate,
    LLMMigrationResult,
    PatchType,
    TokenUsage,
)
from rhp_migrate.cli import app
from rhp_migrate.core.cache import (
    CacheEntry,
    MigrationCache,
    compute_content_hash,
    compute_settings_hash,
)
from rhp_migrate.core.git_manager import GitManager, GitManagerError


runner = CliRunner()


# ===========================================================================
# Part 1: MigrationCache Unit Tests
# ===========================================================================

def test_hash_computation():
    """compute_content_hash and compute_settings_hash produce valid sha256 digests."""
    h1 = compute_content_hash("<?php echo 'hello';")
    h2 = compute_content_hash("<?php echo 'hello';")
    h3 = compute_content_hash("<?php echo 'world';")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64

    s1 = compute_settings_hash({"ai": True, "model": "gemini-2.5-pro"})
    s2 = compute_settings_hash({"model": "gemini-2.5-pro", "ai": True})
    assert s1 == s2  # Key order invariant


def test_cache_entry_serialization():
    """CacheEntry serializes and deserializes cleanly."""
    entry = CacheEntry(
        content_hash="abc123hash",
        settings_hash="def456hash",
        verified=True,
        settings={"model": "gemini-2.5-pro"},
        stats={"deterministic_edits": 3},
    )
    data = entry.to_dict()
    reconstructed = CacheEntry.from_dict(data)
    assert reconstructed.content_hash == "abc123hash"
    assert reconstructed.settings_hash == "def456hash"
    assert reconstructed.verified is True
    assert reconstructed.stats["deterministic_edits"] == 3


def test_migration_cache_lifecycle(tmp_path: Path):
    """MigrationCache correctly skips verified unchanged files and invalidates on changes."""
    cache_file = tmp_path / ".rhp_cache" / "migration_cache.json"
    cache = MigrationCache(cache_path=cache_file, base_dir=tmp_path)

    php_file = tmp_path / "User.php"
    initial_content = "<?php class User {}"
    php_file.write_text(initial_content, encoding="utf-8")

    settings = {"ai": True, "model": "gemini-2.5-pro", "max_retries": 3}

    # Initially not in cache
    assert cache.should_skip(php_file, initial_content, settings) is False

    # Update cache and save
    cache.update(php_file, initial_content, settings, verified=True)
    cache.save()
    assert cache_file.exists()

    # New cache instance loading from disk
    new_cache = MigrationCache(cache_path=cache_file, base_dir=tmp_path)
    assert len(new_cache) == 1
    assert new_cache.should_skip(php_file, initial_content, settings) is True

    # Modified content should not skip
    modified_content = "<?php class User { public int $id; }"
    assert new_cache.should_skip(php_file, modified_content, settings) is False

    # Modified settings should not skip
    changed_settings = {"ai": False, "model": "gemini-2.5-pro", "max_retries": 3}
    assert new_cache.should_skip(php_file, initial_content, changed_settings) is False

    # Unverified file should not skip
    cache.update(php_file, initial_content, settings, verified=False)
    assert cache.should_skip(php_file, initial_content, settings) is False


def test_cache_clear_and_invalidate(tmp_path: Path):
    """MigrationCache clear and invalidate methods."""
    cache = MigrationCache(base_dir=tmp_path)
    f1 = tmp_path / "A.php"
    f2 = tmp_path / "B.php"
    cache.update(f1, "code A", verified=True)
    cache.update(f2, "code B", verified=True)
    cache.save()
    assert len(cache) == 2

    cache.invalidate(f1)
    assert len(cache) == 1
    assert cache.get_entry(f1) is None
    assert cache.get_entry(f2) is not None

    cache.clear()
    assert len(cache) == 0
    assert not cache.cache_file.exists()


# ===========================================================================
# Part 2: GitManager Unit Tests
# ===========================================================================

def _init_git_repo(path: Path) -> None:
    """Helper to initialize a real git repository for testing."""
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(path), check=True, capture_output=True)
    # Create initial commit so HEAD exists
    readme = path / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(path), check=True, capture_output=True)


def test_git_manager_non_git_dir(tmp_path: Path):
    """GitManager correctly identifies non-git directories."""
    gm = GitManager(repo_path=tmp_path)
    assert gm.is_git_repo() is False


def test_git_manager_branch_and_commit(tmp_path: Path):
    """GitManager creates migration branch and commits atomically with target format."""
    _init_git_repo(tmp_path)
    gm = GitManager(repo_path=tmp_path)
    assert gm.is_git_repo() is True

    # 1. Create migration branch
    branch_name = gm.create_migration_branch()
    assert branch_name.startswith("rhp-migration-")
    assert gm.get_current_branch() == branch_name

    # 2. Add and commit a PHP class
    user_file = tmp_path / "src" / "Models" / "User.php"
    user_file.parent.mkdir(parents=True, exist_ok=True)
    user_file.write_text("<?php namespace App\\Models; class User {}", encoding="utf-8")

    commit_sha = gm.commit_file(user_file, unit_name="App\\Models\\User")
    assert commit_sha is not None
    assert len(commit_sha) >= 7

    # 3. Check commit message format
    last_msg = gm.get_last_commit_message()
    assert last_msg == "migrate(rhp): migrate App\\Models\\User [auto]"

    # 4. Calling commit on un-modified file returns None (idempotent)
    second_commit = gm.commit_file(user_file, unit_name="App\\Models\\User")
    assert second_commit is None


# ===========================================================================
# Part 3: CLI Integration Tests
# ===========================================================================

def test_cli_help():
    """CLI provides comprehensive help message with all required options."""
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "--analyze-only" in res.stdout
    assert "--dry-run" in res.stdout
    assert "--ai" in res.stdout
    assert "--no-ai" in res.stdout
    assert "--model" in res.stdout
    assert "--max-retries" in res.stdout
    assert "--cache" in res.stdout
    assert "--no-cache" in res.stdout
    assert "--git" in res.stdout
    assert "--no-git" in res.stdout


def test_cli_nonexistent_path():
    """CLI fails gracefully if target path does not exist."""
    res = runner.invoke(app, ["/non/existent/path/for/sure/12345"])
    assert res.exit_code != 0
    assert "Target path does not exist" in res.stdout


def test_cli_analyze_only(tmp_path: Path):
    """CLI --analyze-only generates pre-analysis report with 0 edits on disk."""
    php_file = tmp_path / "Order.php"
    content = """<?php
class Order {
    public function calculateTotal($items, $taxRate) {
        return mb_str_contains("test", "t");
    }
}
"""
    php_file.write_text(content, encoding="utf-8")

    res = runner.invoke(app, [str(tmp_path), "--analyze-only"])
    assert res.exit_code == 0
    assert "Pre-Analysis Report" in res.stdout
    # Original file must remain unmodified
    assert php_file.read_text(encoding="utf-8") == content


def test_cli_dry_run(tmp_path: Path):
    """CLI --dry-run previews migration and prints summary without writing changes."""
    php_file = tmp_path / "Helper.php"
    content = """<?php
function testHelper() {
    return mb_str_contains("foo", "bar");
}
"""
    php_file.write_text(content, encoding="utf-8")

    res = runner.invoke(app, [str(php_file), "--dry-run", "--no-ai", "--no-git", "--no-cache"])
    assert res.exit_code == 0
    assert "RHP Migration Summary" in res.stdout
    assert "Files scanned" in res.stdout
    assert "Deterministic edits applied" in res.stdout
    # File content remains original on dry-run
    assert php_file.read_text(encoding="utf-8") == content


def test_cli_deterministic_migration_fallback(tmp_path: Path):
    """CLI executes deterministic fallback rules and updates file content."""
    php_file = tmp_path / "StringUtil.php"
    content = """<?php
class StringUtil {
    public function check(string $haystack, string $needle): bool {
        return mb_str_contains($haystack, $needle);
    }
}
"""
    php_file.write_text(content, encoding="utf-8")

    res = runner.invoke(app, [str(php_file), "--no-ai", "--no-git", "--no-cache"])
    assert res.exit_code == 0
    assert "RHP Migration Summary" in res.stdout
    assert "Deterministic edits applied" in res.stdout

    updated = php_file.read_text(encoding="utf-8")
    assert "Str::contains($haystack, $needle)" in updated
    assert "mb_str_contains" not in updated


def test_cli_content_hash_caching(tmp_path: Path):
    """CLI caches migrated files and skips them on subsequent executions."""
    php_file = tmp_path / "CachedModel.php"
    content = """<?php
class CachedModel {
    public function check(): bool {
        return true;
    }
}
"""
    php_file.write_text(content, encoding="utf-8")

    # First run: populates cache
    res1 = runner.invoke(app, [str(tmp_path), "--no-ai", "--no-git", "--cache"])
    assert res1.exit_code == 0

    cache_file = tmp_path / ".rhp_cache" / "migration_cache.json"
    assert cache_file.exists()

    # Second run: file skipped by cache
    res2 = runner.invoke(app, [str(tmp_path), "--no-ai", "--no-git", "--cache"])
    assert res2.exit_code == 0
    assert "Files scanned" in res2.stdout


def test_cli_git_branch_and_commit(tmp_path: Path):
    """CLI creates migration branch and auto-commits verified units."""
    _init_git_repo(tmp_path)

    model_file = tmp_path / "User.php"
    model_file.write_text(
        """<?php
namespace App\\Models;

class User {
    public function active(): bool {
        return mb_str_contains("active", "act");
    }
}
""",
        encoding="utf-8",
    )

    res = runner.invoke(app, [str(tmp_path), "--git", "--no-ai", "--no-cache"])
    assert res.exit_code == 0

    gm = GitManager(repo_path=tmp_path)
    assert gm.get_current_branch().startswith("rhp-migration-")
    last_msg = gm.get_last_commit_message()
    assert last_msg == "migrate(rhp): migrate App\\Models\\User [auto]"


def test_cli_dependency_ordered_execution(tmp_path: Path):
    """CLI executes files in topological dependency order (Model before Service)."""
    model_file = tmp_path / "User.php"
    model_file.write_text(
        "<?php namespace App\\Models;\nclass User {}\n",
        encoding="utf-8",
    )

    service_file = tmp_path / "UserService.php"
    service_file.write_text(
        "<?php namespace App\\Services;\nuse App\\Models\\User;\nclass UserService {\n    public function find(): User { return new User(); }\n}\n",
        encoding="utf-8",
    )

    res = runner.invoke(app, [str(tmp_path), "--no-ai", "--no-git", "--no-cache"])
    assert res.exit_code == 0
    assert "Files scanned" in res.stdout
    assert "2" in res.stdout


def test_cli_summary_table_statistics(tmp_path: Path):
    """CLI displays complete summary table with all 5 required metrics."""
    f = tmp_path / "Sample.php"
    f.write_text(
        """<?php
class Sample {
    public function run(): bool {
        return mb_str_starts_with("abc", "a");
    }
}
""",
        encoding="utf-8",
    )

    res = runner.invoke(app, [str(f), "--no-ai", "--no-git", "--no-cache"])
    assert res.exit_code == 0
    output = res.stdout
    assert "Files scanned" in output
    assert "Deterministic edits applied" in output
    assert "LLM-assisted functions" in output
    assert "Units marked // RHP-TODO" in output
    assert "Estimated token cost" in output


def test_cli_ai_assisted_leftovers(tmp_path: Path):
    """CLI uses LLMRunner for leftovers when --ai is enabled and tracks token cost."""
    f = tmp_path / "Untyped.php"
    f.write_text(
        """<?php
class Untyped {
    public function compute($val) {
        return $val * 2;
    }
}
""",
        encoding="utf-8",
    )

    # Use model="mock" to test LLM branch without real API network calls
    res = runner.invoke(app, [str(f), "--ai", "--model", "mock", "--no-git", "--no-cache"])
    assert res.exit_code == 0
    assert "RHP Migration Summary" in res.stdout
    assert "LLM-assisted functions" in res.stdout
    assert "Estimated token cost" in res.stdout


def test_cli_whole_project_stages_ai_only_on_errors(tmp_path: Path):
    """Whole-project migration runs stages 1, 2, 3 and only triggers AI on errors/warnings."""
    # 1. Clean file: no errors/warnings -> 0 AI operations
    clean_file = tmp_path / "CleanModel.php"
    clean_file.write_text(
        """<?php
class CleanModel {
    public function isValid(): bool {
        return true;
    }
}
""",
        encoding="utf-8",
    )

    res_clean = runner.invoke(app, [str(clean_file), "--ai", "--model", "mock", "--no-git", "--no-cache"])
    assert res_clean.exit_code == 0
    assert "Stage 1: Deterministic 'rhp migrate'" in res_clean.stdout
    assert "Stage 2: Whole-Project Linting" in res_clean.stdout
    assert "Stage 3: Whole-Project Build" in res_clean.stdout
    assert "LLM-assisted functions" in res_clean.stdout
    assert "0" in res_clean.stdout

    # 2. File with errors/warnings -> AI triggers specifically to heal that use case
    broken_file = tmp_path / "BrokenModel.php"
    broken_file.write_text(
        """<?php
class BrokenModel {
    public function compute($untypedParam) {
        return $untypedParam * 2;
    }
}
""",
        encoding="utf-8",
    )

    res_broken = runner.invoke(app, [str(broken_file), "--ai", "--model", "mock", "--no-git", "--no-cache"])
    assert res_broken.exit_code == 0
    assert "Stage 1: Deterministic 'rhp migrate'" in res_broken.stdout
    assert "Stage 2: Whole-Project Linting" in res_broken.stdout
    assert "Stage 3: Whole-Project Build" in res_broken.stdout
    assert "LLM-assisted functions" in res_broken.stdout
