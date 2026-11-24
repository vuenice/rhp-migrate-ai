"""Unit and integration tests for RhpCli and native rhp binary wrapper."""

import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from rhp_migrate.engine.rhp_cli import (
    CheckReport,
    CompilerDiagnostic,
    DiagnosticPosition,
    DiagnosticSpan,
    MigrateFileResult,
    MigrateReport,
    RhpCli,
    RhpExecutionError,
    RhpManifestNotFoundError,
    RhpNotFoundError,
    create_default_rhp_toml,
    find_project_root,
    find_rhp_binary,
    get_rhp_binary,
    has_rhp_manifest,
    run_check,
    run_migrate,
)


# ---------------------------------------------------------------------------
# Fixtures and Sample Data
# ---------------------------------------------------------------------------

SAMPLE_MIGRATE_JSON = {
    "files_scanned": 2,
    "files_modified": 1,
    "results": [
        {
            "file_path": "/path/to/project/src/index.php",
            "modified": True,
            "added_lines": ["$_GET = Request::query();"],
            "cookie_set_count": 0,
            "global_replace_count": 1,
            "dot_concat_count": 4,
            "php_tag_removed": True,
            "curl_migrated_count": 0,
            "curl_multi_count": 0,
            "heredoc_count": 0,
            "declare_count": 1,
            "error_reporting_count": 1,
            "ini_set_count": 0,
            "cast_count": 2,
            "list_destructure_count": 1,
            "foreach_count": 1,
            "get_replace_count": 2,
            "post_replace_count": 0,
            "error_suppress_count": 1,
            "var_dump_count": 1,
            "strict_eq_count": 3,
            "byte_param_count": 0,
            "named_arg_count": 0,
            "param_order_count": 0,
            "void_return_count": 1,
            "warnings": [],
        },
        {
            "file_path": "/path/to/project/src/clean.php",
            "modified": False,
            "added_lines": [],
            "cookie_set_count": 0,
            "global_replace_count": 0,
            "dot_concat_count": 0,
            "php_tag_removed": False,
            "curl_migrated_count": 0,
            "curl_multi_count": 0,
            "heredoc_count": 0,
            "declare_count": 0,
            "error_reporting_count": 0,
            "ini_set_count": 0,
            "cast_count": 0,
            "list_destructure_count": 0,
            "foreach_count": 0,
            "get_replace_count": 0,
            "post_replace_count": 0,
            "error_suppress_count": 0,
            "var_dump_count": 0,
            "strict_eq_count": 0,
            "byte_param_count": 0,
            "named_arg_count": 0,
            "param_order_count": 0,
            "void_return_count": 0,
            "warnings": [],
        },
    ],
    "warnings": [],
}

SAMPLE_CHECK_NDJSON = """{"level":"error","code":"RHP0102","message":"syntax error, unexpected token '}'","span":{"file":"src/bad.rhp","start":{"line":12,"col":5,"byte":240},"end":{"line":12,"col":6,"byte":241}},"found":"}","expected":["expression",";"],"labels":[{"span":{"line":12,"col":5},"text":"unexpected closing brace","primary":true}],"snippet":[{"line":12,"text":"    }","marker":{"col":5,"len":1}}],"suggestions":[{"message":"remove unexpected brace","applicability":"machine_applicable","edits":[{"start":{"line":12,"col":5},"end":{"line":12,"col":6},"replacement":""}]}],"explain":"rhp explain RHP0102"}
{"level":"warning","code":"RHP0403","message":"deprecated C-style for loop","span":{"file":"src/bad.rhp","start":{"line":20,"col":1,"byte":350},"end":{"line":20,"col":28,"byte":377}},"labels":[],"snippet":[],"suggestions":[],"explain":"rhp explain RHP0403"}
{"level":"summary","errors":1,"warnings":1,"status":"error"}
"""

SAMPLE_CHECK_CLEAN_NDJSON = """{"level":"summary","errors":0,"warnings":0,"status":"ok"}
"""


# ---------------------------------------------------------------------------
# Binary Locator Tests
# ---------------------------------------------------------------------------

def test_find_rhp_binary_custom_path(tmp_path: Path):
    """Explicit custom binary path is honored if executable or existing."""
    fake_bin = tmp_path / "fake_rhp"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)

    found = find_rhp_binary(fake_bin)
    assert found == fake_bin.resolve()


def test_find_rhp_binary_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """RHP_BIN_PATH environment variable is used when set."""
    fake_bin = tmp_path / "env_rhp"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)

    monkeypatch.setenv("RHP_BIN_PATH", str(fake_bin))
    found = find_rhp_binary()
    assert found == fake_bin.resolve()


def test_find_rhp_binary_which(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """System PATH via shutil.which is checked."""
    monkeypatch.delenv("RHP_BIN_PATH", raising=False)
    fake_bin = tmp_path / "path_rhp"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")

    monkeypatch.setattr("shutil.which", lambda name: str(fake_bin) if name == "rhp" else None)
    found = find_rhp_binary()
    assert found == fake_bin.resolve()


def test_get_rhp_binary_not_found(monkeypatch: pytest.MonkeyPatch):
    """RhpNotFoundError is raised when rhp cannot be found anywhere."""
    monkeypatch.delenv("RHP_BIN_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    with pytest.raises(RhpNotFoundError) as exc_info:
        get_rhp_binary()
    assert "The native 'rhp' executable was not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# rhp migrate Tests
# ---------------------------------------------------------------------------

@patch("subprocess.run")
def test_run_migrate_success(mock_subproc: MagicMock, tmp_path: Path):
    """run_migrate executes rhp migrate --format=json and parses report."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    mock_subproc.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(SAMPLE_MIGRATE_JSON),
        stderr="",
    )

    target_dir = tmp_path / "src"
    report = run_migrate(target_dir, dry_run=False, php_version=7, bin_path=fake_bin)

    assert isinstance(report, MigrateReport)
    assert report.files_scanned == 2
    assert report.files_modified == 1
    assert len(report.results) == 2

    # Check command arguments
    mock_subproc.assert_called_once()
    cmd = mock_subproc.call_args[0][0]
    assert cmd[0] == str(fake_bin.resolve())
    assert cmd[1] == "migrate"
    assert cmd[2] == str(target_dir)
    assert "--format=json" in cmd
    assert "--php-version=7" in cmd
    assert "--dry-run" not in cmd

    # Verify per-file detail
    res1 = report.results[0]
    assert res1.modified is True
    assert res1.dot_concat_count == 4
    assert res1.php_tag_removed is True
    assert res1.added_lines == ["$_GET = Request::query();"]

    # Verify helper lookup
    found_res = report.get_file_result("/path/to/project/src/index.php")
    assert found_res is not None
    assert found_res.file_path == "/path/to/project/src/index.php"


@patch("subprocess.run")
def test_run_migrate_dry_run_and_php8(mock_subproc: MagicMock, tmp_path: Path):
    """run_migrate passes --dry-run and --php-version=8 appropriately."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    mock_subproc.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(SAMPLE_MIGRATE_JSON),
        stderr="",
    )

    run_migrate(tmp_path, dry_run=True, php_version=8, bin_path=fake_bin)

    cmd = mock_subproc.call_args[0][0]
    assert "--dry-run" in cmd
    assert "--php-version=8" in cmd


@patch("subprocess.run")
def test_run_migrate_execution_error(mock_subproc: MagicMock, tmp_path: Path):
    """RhpExecutionError is raised when rhp migrate fails with non-zero exit code."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    mock_subproc.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="Error: Target path 'non_existent' does not exist.",
    )

    with pytest.raises(RhpExecutionError) as exc_info:
        run_migrate(tmp_path / "non_existent", bin_path=fake_bin)

    assert exc_info.value.returncode == 1
    assert "does not exist" in exc_info.value.stderr


@patch("subprocess.run")
def test_run_migrate_malformed_json(mock_subproc: MagicMock, tmp_path: Path):
    """RhpExecutionError is raised if output is invalid JSON."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    mock_subproc.return_value = MagicMock(
        returncode=0,
        stdout="not valid json",
        stderr="",
    )

    with pytest.raises(RhpExecutionError) as exc_info:
        run_migrate(tmp_path, bin_path=fake_bin)

    assert "Failed to parse JSON" in str(exc_info.value)


@patch("subprocess.run")
def test_run_migrate_single_file_flag(mock_subproc: MagicMock, tmp_path: Path):
    """run_migrate uses --file=<path> when migrating an individual file."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()
    mock_subproc.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(SAMPLE_MIGRATE_JSON),
        stderr="",
    )

    test_file = tmp_path / "UserController.php"
    test_file.touch()

    # Create rhp.toml at root
    (tmp_path / "rhp.toml").write_text("[package]\nname = 'test'\n", encoding="utf-8")

    report = run_migrate(test_file, dry_run=True, php_version=7, bin_path=fake_bin)
    assert isinstance(report, MigrateReport)

    mock_subproc.assert_called_once()
    cmd = mock_subproc.call_args[0][0]
    assert cmd[1] == "migrate"
    assert f"--file={test_file}" in cmd
    assert "--dry-run" in cmd


def test_run_migrate_missing_manifest_error(tmp_path: Path):
    """run_migrate raises RhpManifestNotFoundError when require_manifest=True and rhp.toml is missing."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    test_file = tmp_path / "app" / "Services" / "Payment.php"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.touch()

    with pytest.raises(RhpManifestNotFoundError) as exc_info:
        run_migrate(test_file, bin_path=fake_bin, require_manifest=True)

    assert "rhp.toml" in str(exc_info.value)
    assert "--file" in str(exc_info.value)


def test_create_default_rhp_toml(tmp_path: Path):
    """create_default_rhp_toml generates a valid starter manifest."""
    # Write a mock composer.json
    composer = tmp_path / "composer.json"
    composer.write_text(
        json.dumps({
            "name": "vuenice/test-app",
            "autoload": {
                "psr-4": {
                    "App\\": "app/",
                    "Database\\": "database/"
                }
            }
        }),
        encoding="utf-8",
    )

    toml_path = create_default_rhp_toml(tmp_path)
    assert toml_path.is_file()
    assert has_rhp_manifest(tmp_path) is True

    content = toml_path.read_text(encoding="utf-8")
    assert 'name = "vuenice/test-app"' in content
    assert '"App\\\\" = "app/"' in content
    assert '"Database\\\\" = "database/"' in content
    assert "[server]" in content



# ---------------------------------------------------------------------------
# rhp check Tests
# ---------------------------------------------------------------------------

@patch("subprocess.run")
def test_run_check_clean(mock_subproc: MagicMock, tmp_path: Path):
    """run_check parses clean NDJSON stream with 0 errors and status=ok."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    mock_subproc.return_value = MagicMock(
        returncode=0,
        stdout=SAMPLE_CHECK_CLEAN_NDJSON,
        stderr="",
    )

    target_file = tmp_path / "src" / "good.rhp"
    report = run_check(target_file, bin_path=fake_bin)

    assert isinstance(report, CheckReport)
    assert report.status == "ok"
    assert report.errors == 0
    assert report.warnings == 0
    assert not report.has_errors
    assert not report.has_warnings
    assert len(report.diagnostics) == 0

    cmd = mock_subproc.call_args[0][0]
    assert cmd[1] == "check"
    assert cmd[2] == str(target_file)
    assert "--format=json" in cmd


@patch("subprocess.run")
def test_run_check_with_diagnostics(mock_subproc: MagicMock, tmp_path: Path):
    """run_check parses compiler errors, warnings, line spans, snippets, and suggestions."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    # Note: rhp check exits with code 1 when user code has syntax or type errors
    mock_subproc.return_value = MagicMock(
        returncode=1,
        stdout=SAMPLE_CHECK_NDJSON,
        stderr="",
    )

    target_file = tmp_path / "src" / "bad.rhp"
    report = run_check(target_file, bin_path=fake_bin)

    assert isinstance(report, CheckReport)
    assert report.status == "error"
    assert report.errors == 1
    assert report.warnings == 1
    assert report.has_errors is True
    assert report.has_warnings is True
    assert len(report.diagnostics) == 2

    # Diagnostic 1: Error
    err = report.error_diagnostics[0]
    assert err.code == "RHP0102"
    assert err.level == "error"
    assert "unexpected token" in err.message
    assert err.file == "src/bad.rhp"
    assert err.line == 12
    assert err.column == 5
    assert err.end_line == 12
    assert err.end_column == 6
    assert err.found == "}"
    assert err.expected == ["expression", ";"]
    assert len(err.labels) == 1
    assert err.labels[0].text == "unexpected closing brace"
    assert len(err.snippet) == 1
    assert err.snippet[0].line == 12
    assert len(err.suggestions) == 1
    assert err.suggestions[0].message == "remove unexpected brace"

    # Diagnostic 2: Warning
    warn = report.warning_diagnostics[0]
    assert warn.code == "RHP0403"
    assert warn.level == "warning"
    assert warn.line == 20
    assert warn.column == 1


@patch("subprocess.run")
def test_run_check_internal_compiler_panic(mock_subproc: MagicMock, tmp_path: Path):
    """RhpExecutionError is raised when compiler exits with crash code (>=2) and no diagnostics."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    mock_subproc.return_value = MagicMock(
        returncode=2,
        stdout="",
        stderr="panic: unexpected internal failure",
    )

    with pytest.raises(RhpExecutionError) as exc_info:
        run_check(tmp_path / "crash.rhp", bin_path=fake_bin)

    assert exc_info.value.returncode == 2
    assert "internal error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# RhpCli Wrapper Class Tests
# ---------------------------------------------------------------------------

@patch("subprocess.run")
def test_rhp_cli_object(mock_subproc: MagicMock, tmp_path: Path):
    """RhpCli wrapper delegates correctly to run_migrate and run_check."""
    fake_bin = tmp_path / "rhp"
    fake_bin.touch()

    cli = RhpCli(bin_path=fake_bin)
    assert cli.is_available() is True
    assert cli.bin_path == fake_bin.resolve()

    mock_subproc.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(SAMPLE_MIGRATE_JSON),
        stderr="",
    )
    migrate_rep = cli.migrate(tmp_path, dry_run=True, php_version=7)
    assert migrate_rep.files_scanned == 2

    mock_subproc.return_value = MagicMock(
        returncode=0,
        stdout=SAMPLE_CHECK_CLEAN_NDJSON,
        stderr="",
    )
    check_rep = cli.check(tmp_path)
    assert check_rep.status == "ok"


# ---------------------------------------------------------------------------
# Real Binary Integration Test (Executed only if rhp is actually available)
# ---------------------------------------------------------------------------

def test_real_rhp_binary_integration(tmp_path: Path):
    """Run real migration and check if rhp binary is locally available."""
    rhp_bin = find_rhp_binary()
    if rhp_bin is None or not rhp_bin.exists():
        pytest.skip("rhp native binary is not available in environment; skipping real binary test")

    # Create a small PHP test file
    test_php = tmp_path / "test.php"
    test_php.write_text(
        "<?php\n"
        "declare(strict_types=1);\n"
        "$greeting = 'Hello ' . 'World';\n"
        "if ($greeting === 'Hello World') {\n"
        "    echo $greeting;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "rhp.toml").write_text("[package]\nname = 'test'\n", encoding="utf-8")

    # Test run_migrate with dry_run=True
    report = run_migrate(test_php, dry_run=True, php_version=7, bin_path=rhp_bin)
    assert isinstance(report, MigrateReport)
    assert report.files_scanned >= 1


def test_subprocess_real_executable(tmp_path: Path):
    """Test actual subprocess execution end-to-end using an executable script fixture."""
    script = tmp_path / "rhp_mock_bin"
    # Shell script responding to `migrate` and `check` with JSON output
    script.write_text(
        "#!/bin/bash\n"
        "if [ \"$1\" = \"migrate\" ]; then\n"
        "  echo '{\"files_scanned\": 1, \"files_modified\": 1, \"results\": [{\"file_path\": \"'\"$2\"'\", \"modified\": true, \"dot_concat_count\": 2}], \"warnings\": []}'\n"
        "  exit 0\n"
        "elif [ \"$1\" = \"check\" ]; then\n"
        "  echo '{\"level\":\"error\",\"code\":\"RHP0101\",\"message\":\"parse error\",\"span\":{\"file\":\"'\"$2\"'\",\"start\":{\"line\":1,\"col\":1},\"end\":{\"line\":1,\"col\":5}}}'\n"
        "  echo '{\"level\":\"summary\",\"errors\":1,\"warnings\":0,\"status\":\"error\"}'\n"
        "  exit 1\n"
        "fi\n"
        "exit 1\n"
    )
    script.chmod(0o755)

    test_file = tmp_path / "example.php"
    test_file.touch()

    # 1. Real subprocess execution of migrate
    mig_report = run_migrate(test_file, dry_run=True, php_version=7, bin_path=script)
    assert mig_report.files_scanned == 1
    assert mig_report.files_modified == 1
    assert mig_report.results[0].dot_concat_count == 2

    # 2. Real subprocess execution of check
    check_report = run_check(test_file, bin_path=script)
    assert check_report.errors == 1
    assert check_report.has_errors is True
    assert check_report.diagnostics[0].code == "RHP0101"
    assert check_report.diagnostics[0].line == 1
    assert check_report.diagnostics[0].column == 1


def test_bin_rhp_enforces_project_and_file_semantics(tmp_path: Path):
    """Test bin/rhp enforces that single file requires --file and rhp.toml."""
    bin_rhp = Path(__file__).resolve().parent.parent / "bin" / "rhp"
    if not bin_rhp.is_file():
        pytest.skip("bin/rhp not found")

    test_php = tmp_path / "app" / "index.php"
    test_php.parent.mkdir(parents=True, exist_ok=True)
    test_php.write_text("<?php echo 'hi';", encoding="utf-8")

    # 1. Positional file without --file must fail
    res_pos = subprocess.run(
        [str(bin_rhp), "migrate", str(test_php)],
        capture_output=True,
        text=True,
    )
    assert res_pos.returncode == 1
    assert "only runs on a project" in res_pos.stderr
    assert "--file=" in res_pos.stderr

    # 2. --file without rhp.toml must fail
    res_no_toml = subprocess.run(
        [str(bin_rhp), "migrate", f"--file={test_php}"],
        capture_output=True,
        text=True,
    )
    assert res_no_toml.returncode == 1
    assert "rhp.toml" in res_no_toml.stderr

    # 3. --file with rhp.toml must succeed
    (tmp_path / "rhp.toml").write_text("[package]\nname = 'test'\n", encoding="utf-8")
    res_ok = subprocess.run(
        [str(bin_rhp), "migrate", f"--file={test_php}", "--format=json"],
        capture_output=True,
        text=True,
    )
    assert res_ok.returncode == 0
    data = json.loads(res_ok.stdout)
    assert data["files_scanned"] == 1


def test_rhp_cli_build_command(tmp_path: Path):
    """Test run_build and RhpCli.build integration with bin/rhp."""
    bin_rhp = Path(__file__).resolve().parent.parent / "bin" / "rhp"
    if not bin_rhp.is_file():
        pytest.skip("bin/rhp not found")

    from rhp_migrate.engine.rhp_cli import BuildReport, run_build

    # 1. Clean file build passes
    clean_file = tmp_path / "Clean.rhp"
    clean_file.write_text("class Clean { public int $id; }\n", encoding="utf-8")
    report = run_build(clean_file, bin_path=bin_rhp)
    assert isinstance(report, BuildReport)
    assert report.has_errors is False

    # 2. Syntax defect build returns errors
    broken_file = tmp_path / "Broken.rhp"
    broken_file.write_text("class Broken { public int $id = ; }\n", encoding="utf-8")
    err_report = run_build(broken_file, bin_path=bin_rhp)
    assert err_report.has_errors is True
    assert len(err_report.diagnostics) > 0

    # 3. RhpCli object wrapper build method
    cli = RhpCli(bin_path=bin_rhp)
    cli_report = cli.build(clean_file)
    assert isinstance(cli_report, BuildReport)
    assert cli_report.has_errors is False


