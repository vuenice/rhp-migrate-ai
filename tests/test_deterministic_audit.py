"""Unit and integration tests for DeterministicValidator and syntax mistake auditing."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner

from rhp_migrate.ai.evaluation_models import MigrateMistake
from rhp_migrate.ai.models import CompanionFile, LLMMigrationResult, PatchType
from rhp_migrate.cli import app
from rhp_migrate.core.feedback import FeedbackDispatcher
from rhp_migrate.rules.registry import RuleRegistry
from rhp_migrate.verify.deterministic_audit import (
    DeterministicMistakeRecord,
    DeterministicSyntaxError,
    DeterministicValidator,
)


runner = CliRunner()


# ============================================================================
# Task 1: DeterministicValidator Syntax Verification Tests
# ============================================================================

def test_deterministic_validator_valid_syntax():
    """Verify that syntactically valid RHP code passes audit with 0 errors."""
    validator = DeterministicValidator()
    before_code = (
        "<?php\n"
        "function greeting($name) {\n"
        "    return 'Hello ' . $name;\n"
        "}\n"
    )
    after_code = (
        "function greeting(string $name): string {\n"
        "    return 'Hello ' + $name;\n"
        "}\n"
    )

    record = validator.validate(before_code=before_code, after_code=after_code, file_path="greeting.php")
    assert record.is_valid is True
    assert len(record.errors) == 0
    assert "✅ VALID" in record.format_markdown_report()
    assert len(record.context_suggestions) == 0


def test_deterministic_validator_detects_colon_type_mistake():
    """Verify detection of colon type syntax '$var: Type = ...' with before/after and suggestions."""
    validator = DeterministicValidator()
    before_code = (
        "<?php\n"
        "$html = renderView();\n"
        "$count = 42;\n"
    )
    # Broken deterministic output using colon typing
    after_code = (
        "$html: string = renderView();\n"
        "$count: int = 42;\n"
    )

    record = validator.validate(before_code=before_code, after_code=after_code, file_path="view_loader.php")
    assert record.is_valid is False
    assert len(record.errors) == 2

    err1 = record.errors[0]
    assert err1.error_code == "RHP_SYN_COLON_TYPE"
    assert "Colon type syntax '$html: string' is forbidden" in err1.message
    assert err1.line == 1

    err2 = record.errors[1]
    assert err2.error_code == "RHP_SYN_COLON_TYPE"
    assert "Colon type syntax '$count: int' is forbidden" in err2.message
    assert err2.line == 2

    # Verify what is invalid and what could have been done better
    assert "variable declaration(s) used invalid colon type syntax" in record.what_is_invalid
    assert "prefix type syntax is mandatory: 'Type $var = expr;'" in record.what_could_have_been_done_better

    # Verify suggested fix
    assert record.suggested_fix is not None
    assert "string $html = renderView();" in record.suggested_fix
    assert "int $count = 42;" in record.suggested_fix

    # Verify context suggestions
    assert len(record.context_suggestions) >= 1
    sug = record.context_suggestions[0]
    assert "[DETERMINISTIC-MISTAKE]" in sug
    assert "Prefix Type Syntax vs Colon Typing" in sug
    assert "Before Syntax (PHP):" in sug
    assert "After Syntax (Broken Deterministic):" in sug
    assert "What Could Have Been Done Better:" in sug


def test_deterministic_validator_detects_broken_operators():
    """Verify detection of dangling or malformed operators from regex replacements."""
    validator = DeterministicValidator()
    before_code = "$user->profile->name;\n"
    after_code = "$user->;\n"

    record = validator.validate(before_code=before_code, after_code=after_code, file_path="user.php")
    assert record.is_valid is False
    assert any(e.error_code == "RHP_SYN_DANGLING_ARROW" for e in record.errors)
    assert "Dangling method or property access arrow" in record.errors[0].message


def test_deterministic_validator_detects_tree_sitter_parse_error():
    """Verify detection of unclosed brackets or syntax parse errors."""
    validator = DeterministicValidator()
    before_code = "function test() { echo 'ok'; }\n"
    after_code = "function test() { echo 'ok'; \n"  # missing closing brace

    record = validator.validate(before_code=before_code, after_code=after_code, file_path="syntax_err.php")
    assert record.is_valid is False
    assert len(record.errors) >= 1
    assert any(e.error_code in ("PARSE_ERR", "COMPILER_ERR") for e in record.errors)


# ============================================================================
# Task 2: Report Persistence & Context Suggestions Integration Tests
# ============================================================================

def test_record_report_saves_json_and_markdown(tmp_path: Path):
    """Verify report persistence into .rhp/reports/deterministic_audit/ and pending_context.md."""
    validator = DeterministicValidator()
    before_code = "$result = compute();\n"
    after_code = "$result: float = compute();\n"

    record = validator.validate(
        before_code=before_code,
        after_code=after_code,
        file_path=tmp_path / "calc.php",
    )

    saved_json = validator.record_report(record, base_dir=tmp_path)
    assert saved_json.is_file()
    assert saved_json.exists()

    # Read saved JSON
    data = json.loads(saved_json.read_text(encoding="utf-8"))
    assert data["is_valid"] is False
    assert len(data["errors"]) == 1
    assert data["errors"][0]["error_code"] == "RHP_SYN_COLON_TYPE"

    # Verify audit_log.jsonl
    log_file = tmp_path / ".rhp" / "reports" / "deterministic_audit" / "audit_log.jsonl"
    assert log_file.is_file()
    assert len(log_file.read_text(encoding="utf-8").strip().splitlines()) == 1

    # Verify Markdown report
    md_file = saved_json.with_suffix(".md")
    assert md_file.is_file()
    md_content = md_file.read_text(encoding="utf-8")
    assert "Deterministic Migration Audit: calc.php" in md_content
    assert "❌ INVALID SYNTAX" in md_content
    assert "Before & After Syntax Comparison" in md_content
    assert "What Could Have Been Done Better" in md_content

    # Verify pending_context.md was appended with context suggestions
    pending_file = tmp_path / ".rhp" / "feedback" / "pending_context.md"
    assert pending_file.is_file()
    pending_text = pending_file.read_text(encoding="utf-8")
    assert "DETERMINISTIC-AUDIT-START" in pending_text
    assert "Prefix Type Syntax vs Colon Typing" in pending_text


def test_record_to_critic_mistakes_conversion():
    """Verify conversion of DeterministicMistakeRecord into MigrationCritic MigrateMistakes."""
    validator = DeterministicValidator()
    before = "$items = getItems();\n"
    after = "$items: list<string> = getItems();\n"

    record = validator.validate(before, after, "items.php")
    mistakes = record.to_critic_mistakes()

    assert len(mistakes) == 1
    m = mistakes[0]
    assert isinstance(m, MigrateMistake)
    assert "Deterministic syntax mistake" in m.title
    assert m.sample.after.strip() == "$items: list<string> = getItems();"
    assert m.sample.expected is not None and m.sample.expected.strip() == "list<string> $items = getItems();"



def test_feedback_dispatcher_approves_deterministic_audit_blocks(tmp_path: Path):
    """Verify FeedbackDispatcher loads and merges deterministic audit proposals."""
    dispatcher = FeedbackDispatcher(base_dir=tmp_path)
    pending_file = tmp_path / ".rhp" / "feedback" / "pending_context.md"
    pending_file.parent.mkdir(parents=True, exist_ok=True)

    block = (
        "<!-- DETERMINISTIC-AUDIT-START id=abc12345 file=app/Test.php php=8.2 -->\n"
        "### [DETERMINISTIC-MISTAKE] Prefix Type Syntax vs Colon Typing\n"
        "- **Context:** Colon type syntax forbidden.\n"
        "<!-- DETERMINISTIC-AUDIT-END id=abc12345 -->\n"
        "---\n"
    )
    pending_file.write_text(block, encoding="utf-8")

    proposals = dispatcher.load_pending_contexts()
    assert len(proposals) == 1
    assert proposals[0]["id"] == "abc12345"
    assert proposals[0]["file"] == "app/Test.php"
    assert "Prefix Type Syntax vs Colon Typing" in proposals[0]["content"]

    target_context = tmp_path / "rules" / "context.md"
    count = dispatcher.approve_all_pending(target_context)
    assert count == 1
    assert target_context.is_file()
    assert "Prefix Type Syntax vs Colon Typing" in target_context.read_text(encoding="utf-8")
    assert pending_file.read_text(encoding="utf-8") == ""


# ============================================================================
# Task 3: CompanionFile and Rade Template Models Tests
# ============================================================================

def test_companion_file_and_migration_result_schema():
    """Verify LLMMigrationResult cleanly serializes companion_files for Rade templates."""
    companion = CompanionFile(
        file_path="resources/views/partials/card.rade.rhp",
        content=(
            "@if ($active)\n"
            "    <div class=\"card\">{{ $title }}</div>\n"
            "@endif\n"
        ),
        description="User card component extracted from output buffer",
    )

    result = LLMMigrationResult(
        unit_identifier="App\\Http\\Controllers\\CardController::render",
        patch_type=PatchType.FULL_REPLACEMENT,
        transformed_code="return Response::html(View::render('partials.card', ['active' => true, 'title' => $title]));",
        companion_files=[companion],
        applied_rules=["RHP-TEM-001", "RHP-UNSUP-002"],
    )

    json_str = result.model_dump_json()
    assert "resources/views/partials/card.rade.rhp" in json_str
    assert "@if ($active)" in json_str
    assert "RHP-TEM-001" in json_str

    deserialized = LLMMigrationResult.model_validate_json(json_str)
    assert len(deserialized.companion_files) == 1
    assert deserialized.companion_files[0].file_path == "resources/views/partials/card.rade.rhp"


def test_rules_registry_contains_new_templates_and_prefix_typing_rules():
    """Verify that RuleRegistry includes RHP-TEM-001 and RHP-SYN-007."""
    registry = RuleRegistry.default()
    assert "RHP-TEM-001" in registry
    assert "RHP-SYN-007" in registry

    r_tem = registry["RHP-TEM-001"]
    assert r_tem.id == "RHP-TEM-001"
    assert "Rade" in r_tem.title
    assert "View::render" in r_tem.rhp_replacement

    r_syn = registry["RHP-SYN-007"]
    assert r_syn.id == "RHP-SYN-007"
    assert "prefix type syntax" in r_syn.title.lower()
    assert "Type $var" in r_syn.rhp_replacement


# ============================================================================
# Task 4: CLI audit-deterministic Command Tests
# ============================================================================

def test_cli_audit_deterministic_command_valid(tmp_path: Path):
    """Test running 'rhp-migrate-ai audit-deterministic' on valid PHP file."""
    php_file = tmp_path / "valid.php"
    php_file.write_text("<?php function sum(int $a, int $b): int { return $a + $b; }", encoding="utf-8")

    res = runner.invoke(app, ["audit-deterministic", str(php_file)])
    assert res.exit_code == 0
    assert "Valid Syntax: valid.php" in res.output
    assert "0 files invalid" in res.output


def test_cli_audit_deterministic_command_invalid(tmp_path: Path):
    """Test running 'rhp-migrate-ai audit-deterministic' on a file that fails syntax check."""
    # Write code that triggers colon typing check
    php_file = tmp_path / "broken.php"
    php_file.write_text("$count: int = 10;", encoding="utf-8")

    res = runner.invoke(app, ["audit-deterministic", str(php_file)])
    assert res.exit_code == 0
    assert "Invalid Deterministic Syntax: broken.php" in res.output
    assert "1 files invalid" in res.output
    assert "What is invalid:" in res.output
    assert "Before & After Syntax Comparison" in res.output
