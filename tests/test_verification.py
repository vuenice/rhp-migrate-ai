"""Unit and integration tests for Layer 6: Verification and Differential Testing."""

import difflib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from rhp_migrate.ai.client import LLMRunner, MockLLMClient
from rhp_migrate.ai.models import LLMMigrationResult
from rhp_migrate.engine.rhp_cli import (
    CheckReport,
    CompilerDiagnostic,
    DiagnosticPosition,
    DiagnosticSpan,
    RhpNotFoundError,
)
from rhp_migrate.verify.compiler_loop import (
    CodeUnit,
    clean_code_fence,
    find_code_unit,
    insert_todo_above_unit,
    is_syntactically_valid,
    replace_unit_in_source,
    verify_and_heal,
)
from rhp_migrate.verify.differential import (
    DifferentialResult,
    PhpNotFoundError,
    find_php_binary,
    get_php_binary,
    run_differential,
    run_differential_snippet,
    run_differential_test,
)


# ===========================================================================
# Part 1: Compiler Loop & CodeUnit Tests
# ===========================================================================

def test_clean_code_fence():
    """clean_code_fence strips markdown wrappers."""
    assert clean_code_fence("```php\nfunction foo(): void {}\n```") == "function foo(): void {}"
    assert clean_code_fence("```rhp\nfunction foo(): void {}\n```") == "function foo(): void {}"
    assert clean_code_fence("```\nfunction foo(): void {}\n```") == "function foo(): void {}"
    assert clean_code_fence("function foo(): void {}") == "function foo(): void {}"


def test_find_code_unit_method():
    """find_code_unit locates method inside a class with enclosing identifier."""
    code = (
        "<?php\n"
        "namespace App\\Services;\n\n"
        "class OrderService {\n"
        "    public function process(int $orderId): bool {\n"
        "        return true;\n"
        "    }\n"
        "}\n"
    )
    # Line 5 is inside process()
    unit = find_code_unit(code, line_number=5)
    assert unit.node_type == "method_declaration"
    assert "OrderService::process" in unit.identifier
    assert "public function process" in unit.code
    assert unit.start_line == 5
    assert unit.end_line == 7
    assert unit.is_whole_file is False


def test_find_code_unit_function():
    """find_code_unit locates standalone function."""
    code = (
        "<?php\n"
        "function calculateTotal(int $a, int $b): int {\n"
        "    return $a + $b;\n"
        "}\n"
    )
    unit = find_code_unit(code, line_number=3)
    assert unit.node_type == "function_definition"
    assert unit.identifier == "calculateTotal"
    assert "function calculateTotal" in unit.code
    assert unit.start_line == 2
    assert unit.end_line == 4
    assert unit.is_whole_file is False


def test_find_code_unit_class():
    """find_code_unit locates class declaration when error is at class level."""
    code = (
        "<?php\n"
        "class Config {\n"
        "    public int $version = 1;\n"
        "}\n"
    )
    unit = find_code_unit(code, line_number=2)
    assert unit.node_type == "class_declaration"
    assert unit.identifier == "Config"
    assert "class Config" in unit.code
    assert unit.is_whole_file is False


def test_find_code_unit_top_level_fallback():
    """find_code_unit falls back to whole file for procedural scripts."""
    code = (
        "<?php\n"
        "$name = 'Alice';\n"
        "echo 'Hello ' . $name;\n"
    )
    unit = find_code_unit(code, line_number=3, file_path="script.rhp")
    assert unit.is_whole_file is True
    assert unit.identifier == "script.rhp"
    assert unit.code == code


def test_insert_todo_above_unit():
    """insert_todo_above_unit inserts comment with matching indentation."""
    code = (
        "<?php\n"
        "class Service {\n"
        "    public function run(): void {\n"
        "        echo 'ok';\n"
        "    }\n"
        "}\n"
    )
    # Target line 4 (inside run) -> comment placed above line 3 (public function run)
    tagged = insert_todo_above_unit(code, line_number=4, error_message="[RHP0101] parse error")
    lines = tagged.splitlines()

    assert "    // RHP-TODO(failed-verification): [RHP0101] parse error" in lines
    idx = lines.index("    // RHP-TODO(failed-verification): [RHP0101] parse error")
    assert lines[idx + 1].strip() == "public function run(): void {"

    # Calling again does not duplicate the comment
    tagged_again = insert_todo_above_unit(tagged, line_number=4, error_message="[RHP0101] parse error")
    assert tagged_again == tagged


def test_is_syntactically_valid():
    """is_syntactically_valid correctly classifies valid and invalid syntax."""
    assert is_syntactically_valid("<?php function ok(): int { return 1; }") is True
    assert is_syntactically_valid("<?php function bad(): int { return + ; }") is False


# ---------------------------------------------------------------------------
# verify_and_heal Tests
# ---------------------------------------------------------------------------

def test_verify_and_heal_file_not_found(tmp_path: Path):
    """verify_and_heal raises FileNotFoundError if file does not exist."""
    missing_file = tmp_path / "non_existent.rhp"
    with pytest.raises(FileNotFoundError):
        verify_and_heal(missing_file)


@patch("rhp_migrate.verify.compiler_loop.run_check")
def test_verify_and_heal_clean_file(mock_run_check: MagicMock, tmp_path: Path):
    """If initial rhp check reports 0 errors, immediately return True."""
    target_file = tmp_path / "clean.rhp"
    target_file.write_text("<?php function add(int $a, int $b): int { return $a + $b; }", encoding="utf-8")

    mock_run_check.return_value = CheckReport(
        target_path=str(target_file),
        status="ok",
        errors=0,
        warnings=0,
        diagnostics=[],
    )

    mock_client = MockLLMClient()
    runner = LLMRunner(provider=mock_client)

    success = verify_and_heal(target_file, max_retries=3, llm_runner=runner)

    assert success is True
    mock_run_check.assert_called_once()
    assert len(mock_client.call_history) == 0


@patch("rhp_migrate.verify.compiler_loop.run_check")
def test_verify_and_heal_fixes_on_first_retry(mock_run_check: MagicMock, tmp_path: Path):
    """File with compiler error is healed by LLM on 1st retry turn."""
    target_file = tmp_path / "service.rhp"
    bad_code = (
        "<?php\n"
        "class Service {\n"
        "    public function compute(int $x): int {\n"
        "        return $x + ;\n"
        "    }\n"
        "}\n"
    )
    target_file.write_text(bad_code, encoding="utf-8")

    # 1st check: error at line 4
    diag = CompilerDiagnostic(
        level="error",
        code="RHP0102",
        message="syntax error, unexpected ';'",
        span=DiagnosticSpan(
            file=str(target_file),
            start=DiagnosticPosition(line=4, col=21),
            end=DiagnosticPosition(line=4, col=22),
        ),
    )
    first_report = CheckReport(
        target_path=str(target_file),
        status="error",
        errors=1,
        warnings=0,
        diagnostics=[diag],
    )

    # 2nd check (after LLM fix): clean!
    clean_report = CheckReport(
        target_path=str(target_file),
        status="ok",
        errors=0,
        warnings=0,
        diagnostics=[],
    )

    mock_run_check.side_effect = [first_report, clean_report]

    fixed_method = (
        "    public function compute(int $x): int {\n"
        "        return $x + 10;\n"
        "    }"
    )
    mock_client = MockLLMClient(
        responses=[
            LLMMigrationResult(
                unit_identifier="Service::compute",
                transformed_code=fixed_method,
            )
        ]
    )
    runner = LLMRunner(provider=mock_client)

    success = verify_and_heal(target_file, max_retries=3, llm_runner=runner)

    assert success is True
    assert mock_run_check.call_count == 2
    assert len(mock_client.call_history) == 1

    # Verify that the LLM user prompt received the compiler error diagnostic
    prompt = mock_client.call_history[0]["user_prompt"]
    assert "RHP0102" in prompt
    assert "syntax error" in prompt

    # Verify file content was updated with the fix
    content = target_file.read_text(encoding="utf-8")
    assert "return $x + 10;" in content
    assert "return $x + ;" not in content


@patch("rhp_migrate.verify.compiler_loop.run_check")
def test_verify_and_heal_fixes_on_second_retry(mock_run_check: MagicMock, tmp_path: Path):
    """File failing 1st retry heals successfully on 2nd retry turn."""
    target_file = tmp_path / "calc.rhp"
    bad_code = "<?php\nfunction calc(): int {\n    return missing();\n}\n"
    target_file.write_text(bad_code, encoding="utf-8")

    err_report1 = CheckReport(
        target_path=str(target_file),
        status="error",
        errors=1,
        diagnostics=[
            CompilerDiagnostic(
                level="error",
                code="RHP0200",
                message="call to undefined function missing()",
                span=DiagnosticSpan(
                    file=str(target_file),
                    start=DiagnosticPosition(line=3, col=12),
                    end=DiagnosticPosition(line=3, col=21),
                ),
            )
        ],
    )
    err_report2 = CheckReport(
        target_path=str(target_file),
        status="error",
        errors=1,
        diagnostics=[
            CompilerDiagnostic(
                level="error",
                code="RHP0105",
                message="type mismatch, expected int got string",
                span=DiagnosticSpan(
                    file=str(target_file),
                    start=DiagnosticPosition(line=3, col=12),
                    end=DiagnosticPosition(line=3, col=15),
                ),
            )
        ],
    )
    clean_report = CheckReport(
        target_path=str(target_file),
        status="ok",
        errors=0,
        diagnostics=[],
    )

    mock_run_check.side_effect = [err_report1, err_report2, clean_report]

    mock_client = MockLLMClient(
        responses=[
            LLMMigrationResult(
                unit_identifier="calc",
                transformed_code="function calc(): int {\n    return 'string';\n}",
            ),
            LLMMigrationResult(
                unit_identifier="calc",
                transformed_code="function calc(): int {\n    return 42;\n}",
            ),
        ]
    )
    runner = LLMRunner(provider=mock_client)

    success = verify_and_heal(target_file, max_retries=3, llm_runner=runner)

    assert success is True
    assert mock_run_check.call_count == 3
    assert len(mock_client.call_history) == 2
    assert "return 42;" in target_file.read_text(encoding="utf-8")


@patch("rhp_migrate.verify.compiler_loop.run_check")
def test_verify_and_heal_reverts_and_annotates_on_failure(mock_run_check: MagicMock, tmp_path: Path):
    """When retries are exhausted without fix, revert to valid state and add TODO comment."""
    target_file = tmp_path / "broken.rhp"
    initial_valid_code = (
        "<?php\n"
        "class Order {\n"
        "    public function submit(): void {\n"
        "        $x = 1;\n"
        "    }\n"
        "}\n"
    )
    target_file.write_text(initial_valid_code, encoding="utf-8")

    persistent_error = CheckReport(
        target_path=str(target_file),
        status="error",
        errors=1,
        diagnostics=[
            CompilerDiagnostic(
                level="error",
                code="RHP0301",
                message="cannot resolve dynamic method call",
                span=DiagnosticSpan(
                    file=str(target_file),
                    start=DiagnosticPosition(line=4, col=9),
                    end=DiagnosticPosition(line=4, col=15),
                ),
            )
        ],
    )

    # Initial check + 2 retry checks all return persistent_error
    mock_run_check.return_value = persistent_error

    mock_client = MockLLMClient(
        responses=[
            LLMMigrationResult(
                unit_identifier="Order::submit",
                transformed_code="    public function submit(): void {\n        $this->stillBroken();\n    }",
            ),
            LLMMigrationResult(
                unit_identifier="Order::submit",
                transformed_code="    public function submit(): void {\n        $this->reallyBroken();\n    }",
            ),
        ]
    )
    runner = LLMRunner(provider=mock_client)

    # Run with max_retries=2
    success = verify_and_heal(target_file, max_retries=2, llm_runner=runner)

    assert success is False
    assert mock_run_check.call_count == 3  # Initial + 2 retries

    # Check final file state
    final_content = target_file.read_text(encoding="utf-8")
    assert "// RHP-TODO(failed-verification): [RHP0301] cannot resolve dynamic method call" in final_content
    assert "public function submit(): void {" in final_content


# ===========================================================================
# Part 2: Differential Execution Tests
# ===========================================================================

def test_find_php_binary_custom_path(tmp_path: Path):
    """find_php_binary honors custom_path override."""
    fake_php = tmp_path / "fake_php"
    fake_php.write_text("#!/bin/sh\nexit 0\n")
    fake_php.chmod(0o755)

    found = find_php_binary(fake_php)
    assert found == fake_php.resolve()


def test_find_php_binary_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """find_php_binary uses PHP_BIN_PATH when set."""
    fake_php = tmp_path / "env_php"
    fake_php.touch()

    monkeypatch.setenv("PHP_BIN_PATH", str(fake_php))
    assert find_php_binary() == fake_php.resolve()


def test_get_php_binary_not_found(monkeypatch: pytest.MonkeyPatch):
    """get_php_binary raises PhpNotFoundError when binary is nowhere to be found."""
    monkeypatch.delenv("PHP_BIN_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    with pytest.raises(PhpNotFoundError) as exc_info:
        get_php_binary()
    assert "The PHP executable was not found" in str(exc_info.value)


def test_run_differential_missing_files(tmp_path: Path):
    """run_differential_test raises FileNotFoundError if files do not exist."""
    missing_php = tmp_path / "missing.php"
    real_rhp = tmp_path / "real.rhp"
    real_rhp.touch()

    with pytest.raises(FileNotFoundError):
        run_differential_test(missing_php, real_rhp)

    real_php = tmp_path / "real.php"
    real_php.touch()
    missing_rhp = tmp_path / "missing.rhp"

    with pytest.raises(FileNotFoundError):
        run_differential_test(real_php, missing_rhp)


@patch("subprocess.run")
def test_differential_identical_output(mock_subproc: MagicMock, tmp_path: Path):
    """PHP and RHP outputs match identically -> matches=True, diff=''."""
    fake_php_bin = tmp_path / "php"
    fake_rhp_bin = tmp_path / "rhp"
    fake_php_bin.touch()
    fake_rhp_bin.touch()

    php_file = tmp_path / "index.php"
    rhp_file = tmp_path / "index.rhp"
    php_file.touch()
    rhp_file.touch()

    # PHP execution mock
    php_mock = MagicMock(returncode=0, stdout="Hello World!\nSum: 42\n", stderr="")
    # RHP execution mock
    rhp_mock = MagicMock(returncode=0, stdout="Hello World!\nSum: 42\n", stderr="")

    mock_subproc.side_effect = [php_mock, rhp_mock]

    result = run_differential_test(
        php_file,
        rhp_file,
        php_bin=fake_php_bin,
        rhp_bin=fake_rhp_bin,
    )

    assert isinstance(result, DifferentialResult)
    assert result.matches is True
    assert result.php_output == "Hello World!\nSum: 42\n"
    assert result.rhp_output == "Hello World!\nSum: 42\n"
    assert result.diff == ""
    assert result.stdout_matches is True
    assert result.stderr_matches is True
    assert result.exit_code_matches is True

    # Verify execution commands
    assert mock_subproc.call_count == 2
    cmd1 = mock_subproc.call_args_list[0][0][0]
    cmd2 = mock_subproc.call_args_list[1][0][0]

    # Command 1: php -d display_errors=1 -f <php_file>
    assert cmd1[0] == str(fake_php_bin.resolve())
    assert "-d" in cmd1
    assert "display_errors=1" in cmd1
    assert "-f" in cmd1
    assert str(php_file.resolve()) in cmd1

    # Command 2: rhp run <rhp_file>
    assert cmd2[0] == str(fake_rhp_bin.resolve())
    assert cmd2[1] == "run"
    assert str(rhp_file.resolve()) in cmd2


@patch("subprocess.run")
def test_differential_stdout_mismatch(mock_subproc: MagicMock, tmp_path: Path):
    """Mismatched STDOUT produces matches=False and unified diff."""
    fake_php_bin = tmp_path / "php"
    fake_rhp_bin = tmp_path / "rhp"
    fake_php_bin.touch()
    fake_rhp_bin.touch()

    php_file = tmp_path / "test.php"
    rhp_file = tmp_path / "test.rhp"
    php_file.touch()
    rhp_file.touch()

    mock_subproc.side_effect = [
        MagicMock(returncode=0, stdout="Value: 100\nStatus: OK\n", stderr=""),
        MagicMock(returncode=0, stdout="Value: 200\nStatus: OK\n", stderr=""),
    ]

    result = run_differential_test(
        php_file,
        rhp_file,
        php_bin=fake_php_bin,
        rhp_bin=fake_rhp_bin,
    )

    assert result.matches is False
    assert result.stdout_matches is False
    assert result.stderr_matches is True
    assert result.exit_code_matches is True
    assert "--- STDOUT DIFF ---" in result.diff
    assert "-Value: 100" in result.diff
    assert "+Value: 200" in result.diff


@patch("subprocess.run")
def test_differential_stderr_mismatch(mock_subproc: MagicMock, tmp_path: Path):
    """Mismatched STDERR produces matches=False and stderr diff."""
    fake_php_bin = tmp_path / "php"
    fake_rhp_bin = tmp_path / "rhp"
    fake_php_bin.touch()
    fake_rhp_bin.touch()

    php_file = tmp_path / "test.php"
    rhp_file = tmp_path / "test.rhp"
    php_file.touch()
    rhp_file.touch()

    mock_subproc.side_effect = [
        MagicMock(returncode=0, stdout="Output\n", stderr="PHP Warning: Undefined variable $foo\n"),
        MagicMock(returncode=0, stdout="Output\n", stderr=""),
    ]

    result = run_differential_test(
        php_file,
        rhp_file,
        php_bin=fake_php_bin,
        rhp_bin=fake_rhp_bin,
    )

    assert result.matches is False
    assert result.stdout_matches is True
    assert result.stderr_matches is False
    assert "--- STDERR DIFF ---" in result.diff
    assert "PHP Warning: Undefined variable $foo" in result.diff


@patch("subprocess.run")
def test_differential_exit_code_mismatch(mock_subproc: MagicMock, tmp_path: Path):
    """Mismatched exit codes flag matches=False and record exit mismatch."""
    fake_php_bin = tmp_path / "php"
    fake_rhp_bin = tmp_path / "rhp"
    fake_php_bin.touch()
    fake_rhp_bin.touch()

    php_file = tmp_path / "test.php"
    rhp_file = tmp_path / "test.rhp"
    php_file.touch()
    rhp_file.touch()

    mock_subproc.side_effect = [
        MagicMock(returncode=0, stdout="Complete\n", stderr=""),
        MagicMock(returncode=1, stdout="Complete\n", stderr=""),
    ]

    result = run_differential_test(
        php_file,
        rhp_file,
        php_bin=fake_php_bin,
        rhp_bin=fake_rhp_bin,
    )

    assert result.matches is False
    assert result.exit_code_matches is False
    assert "--- EXIT CODE MISMATCH ---" in result.diff
    assert "PHP exit code: 0" in result.diff
    assert "RHP exit code: 1" in result.diff


@patch("subprocess.run")
def test_differential_timeout_handling(mock_subproc: MagicMock, tmp_path: Path):
    """Timeout during PHP or RHP execution is captured safely."""
    fake_php_bin = tmp_path / "php"
    fake_rhp_bin = tmp_path / "rhp"
    fake_php_bin.touch()
    fake_rhp_bin.touch()

    php_file = tmp_path / "hang.php"
    rhp_file = tmp_path / "hang.rhp"
    php_file.touch()
    rhp_file.touch()

    mock_subproc.side_effect = subprocess.TimeoutExpired(cmd=["php"], timeout=5.0)

    result = run_differential_test(
        php_file,
        rhp_file,
        php_bin=fake_php_bin,
        rhp_bin=fake_rhp_bin,
        timeout=5.0,
    )

    assert result.matches is False
    assert "timed out after 5.0 seconds" in result.diff


@patch("subprocess.run")
def test_run_differential_snippet(mock_subproc: MagicMock, tmp_path: Path):
    """run_differential_snippet tests pure logic snippets via temporary files."""
    fake_php_bin = tmp_path / "php"
    fake_rhp_bin = tmp_path / "rhp"
    fake_php_bin.touch()
    fake_rhp_bin.touch()

    mock_subproc.side_effect = [
        MagicMock(returncode=0, stdout="Result: 120\n", stderr=""),
        MagicMock(returncode=0, stdout="Result: 120\n", stderr=""),
    ]

    result = run_differential_snippet(
        php_snippet="<?php echo 'Result: ' . (10 * 12) . \"\\n\";",
        rhp_snippet="echo 'Result: ' . (10 * 12) . \"\\n\";",
        php_bin=fake_php_bin,
        rhp_bin=fake_rhp_bin,
    )

    assert result.matches is True
    assert result.php_output == "Result: 120\n"
    assert result.rhp_output == "Result: 120\n"


# ---------------------------------------------------------------------------
# End-to-End Subprocess Test with Mock Binaries
# ---------------------------------------------------------------------------

def test_real_subprocess_differential_execution(tmp_path: Path):
    """Test actual subprocess execution using executable shell scripts as php and rhp binaries."""
    # 1. Mock PHP executable script
    php_script = tmp_path / "bin_php"
    php_script.write_text(
        "#!/bin/bash\n"
        "# Expects: -d display_errors=1 -f <file>\n"
        "echo 'Result: 42'\n"
        "exit 0\n"
    )
    php_script.chmod(0o755)

    # 2. Mock RHP executable script
    rhp_script = tmp_path / "bin_rhp"
    rhp_script.write_text(
        "#!/bin/bash\n"
        "# Expects: run <file>\n"
        "echo 'Result: 42'\n"
        "exit 0\n"
    )
    rhp_script.chmod(0o755)

    file_php = tmp_path / "script.php"
    file_php.write_text("<?php echo 42;\n")
    file_rhp = tmp_path / "script.rhp"
    file_rhp.write_text("echo 42;\n")

    res = run_differential_test(
        file_php,
        file_rhp,
        php_bin=php_script,
        rhp_bin=rhp_script,
    )

    assert res.matches is True
    assert "42" in res.php_output
    assert res.php_output == res.rhp_output
    assert res.diff == ""
