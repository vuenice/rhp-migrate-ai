"""Deterministic bridge invoking the native RHP binary via CLI subprocess."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field

RHP_BIN_ENV_VAR = "RHP_BIN_PATH"


class RhpError(Exception):
    """Base exception for RHP engine errors."""


class RhpNotFoundError(RhpError, FileNotFoundError):
    """Raised when the native rhp executable cannot be located."""


class RhpExecutionError(RhpError):
    """Raised when the rhp subprocess command terminates unexpectedly."""

    def __init__(
        self,
        message: str,
        returncode: int,
        cmd: Sequence[str],
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.cmd = list(cmd)
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# Pydantic models for `rhp migrate` output
# ---------------------------------------------------------------------------

class MigrateFileResult(BaseModel):
    """Per-file migration metrics and diagnostics from rhp migrate."""

    model_config = ConfigDict(extra="ignore")

    file_path: str = Field(..., description="Target file path")
    modified: bool = Field(False, description="True if modifications were made")
    added_lines: list[str] = Field(default_factory=list, description="Lines added during migration")
    cookie_set_count: int = Field(0, description="Number of cookie set upgrades")
    global_replace_count: int = Field(0, description="Number of global/superglobal replacements")
    dot_concat_count: int = Field(0, description="Number of string concatenation operator upgrades")
    php_tag_removed: bool = Field(False, description="True if PHP opening/closing tags were removed")
    curl_migrated_count: int = Field(0, description="Number of cURL calls upgraded to Http facade")
    curl_multi_count: int = Field(0, description="Number of curl_multi calls flagged")
    heredoc_count: int = Field(0, description="Number of heredoc constructs migrated")
    declare_count: int = Field(0, description="Number of declare() directives removed")
    error_reporting_count: int = Field(0, description="Number of error_reporting() calls removed")
    ini_set_count: int = Field(0, description="Number of ini_set() calls commented out")
    cast_count: int = Field(0, description="Number of type casts converted to functional form")
    list_destructure_count: int = Field(0, description="Number of list() destructuring constructs converted")
    foreach_count: int = Field(0, description="Number of foreach loops converted to for-in")
    get_replace_count: int = Field(0, description="Number of $_GET access replacements")
    post_replace_count: int = Field(0, description="Number of $_POST access replacements")
    error_suppress_count: int = Field(0, description="Number of @ error suppression operators removed")
    var_dump_count: int = Field(0, description="Number of var_dump/var_export calls replaced with dd()")
    strict_eq_count: int = Field(0, description="Number of strict equality operators replaced")
    byte_param_count: int = Field(0, description="Number of binary parameter types inferred as byte")
    named_arg_count: int = Field(0, description="Number of named arguments converted to positional")
    param_order_count: int = Field(0, description="Number of parameter order declarations fixed")
    void_return_count: int = Field(0, description="Number of void return types stripped")
    warnings: list[str] = Field(default_factory=list, description="File-level warnings")


class MigrateReport(BaseModel):
    """Aggregate migration report produced by `rhp migrate --format=json`."""

    model_config = ConfigDict(extra="ignore")

    files_scanned: int = Field(0, description="Total number of files inspected")
    files_modified: int = Field(0, description="Number of files modified or needing modification")
    results: list[MigrateFileResult] = Field(default_factory=list, description="Per-file migration results")
    warnings: list[str] = Field(default_factory=list, description="Top-level migration warnings")

    @property
    def success(self) -> bool:
        """True if migration report is present and valid."""
        return True

    def get_file_result(self, file_path: Union[str, Path]) -> Optional[MigrateFileResult]:
        """Lookup result for a specific file path."""
        target = str(file_path)
        resolved_target = Path(file_path).resolve() if Path(file_path).exists() else None
        for res in self.results:
            if res.file_path == target:
                return res
            if resolved_target and Path(res.file_path).exists():
                if Path(res.file_path).resolve() == resolved_target:
                    return res
        return None


# ---------------------------------------------------------------------------
# Pydantic models for `rhp check` output
# ---------------------------------------------------------------------------

class DiagnosticPosition(BaseModel):
    """1-based line/col position and 0-based byte offset."""

    model_config = ConfigDict(extra="ignore")

    line: int = Field(1, description="1-indexed line number")
    col: int = Field(1, description="1-indexed column number")
    byte: Optional[int] = Field(None, description="0-indexed byte offset")


class DiagnosticSpan(BaseModel):
    """Source file span."""

    model_config = ConfigDict(extra="ignore")

    file: str = Field("", description="File path")
    start: DiagnosticPosition = Field(default_factory=DiagnosticPosition, description="Start position")
    end: DiagnosticPosition = Field(default_factory=DiagnosticPosition, description="End position")


class DiagnosticLabel(BaseModel):
    """Visual span label for diagnostic rendering."""

    model_config = ConfigDict(extra="ignore")

    span: dict[str, Any] = Field(default_factory=dict, description="Span coordinates")
    text: str = Field("", description="Label text")
    primary: bool = Field(False, description="True if primary error site")


class SnippetMarker(BaseModel):
    """Column span marker."""

    model_config = ConfigDict(extra="ignore")

    col: int = Field(1, description="Marker column")
    len: int = Field(1, description="Marker length")


class SnippetLine(BaseModel):
    """Source code snippet line."""

    model_config = ConfigDict(extra="ignore")

    line: int = Field(1, description="Line number")
    text: str = Field("", description="Line text")
    marker: Optional[SnippetMarker] = Field(None, description="Underline marker")


class DiagnosticEdit(BaseModel):
    """Text replacement edit for automatic fix suggestions."""

    model_config = ConfigDict(extra="ignore")

    start: dict[str, Any] = Field(default_factory=dict)
    end: dict[str, Any] = Field(default_factory=dict)
    replacement: str = Field("")


class DiagnosticSuggestion(BaseModel):
    """Suggested code fix or guidance."""

    model_config = ConfigDict(extra="ignore")

    message: str = Field("", description="Suggestion description")
    applicability: Optional[str] = Field(None, description="machine_applicable, maybe_incorrect, etc.")
    edits: list[DiagnosticEdit] = Field(default_factory=list, description="Replacement edits")


class CompilerDiagnostic(BaseModel):
    """Single compiler error, warning, or diagnostic emitted by `rhp check`."""

    model_config = ConfigDict(extra="ignore")

    level: str = Field("error", description="Diagnostic severity: error, warning, internal_error, note, help")
    code: str = Field("", description="Diagnostic code (e.g. RHP0102)")
    message: str = Field("", description="Primary diagnostic message")
    span: Optional[DiagnosticSpan] = Field(None, description="Source span")
    found: Optional[str] = Field(None, description="Unexpected token found")
    expected: Optional[list[str]] = Field(None, description="Expected token types")
    labels: list[DiagnosticLabel] = Field(default_factory=list, description="Secondary annotations")
    snippet: list[SnippetLine] = Field(default_factory=list, description="Source code snippet")
    suggestions: list[DiagnosticSuggestion] = Field(default_factory=list, description="Suggested fixes")
    explain: Optional[str] = Field(None, description="rhp explain command")
    rendered: Optional[str] = Field(None, description="Human formatted diagnostic message")

    @property
    def file(self) -> str:
        """Source file containing the diagnostic."""
        return self.span.file if self.span else ""

    @property
    def line(self) -> int:
        """1-indexed starting line number."""
        return self.span.start.line if self.span else 1

    @property
    def column(self) -> int:
        """1-indexed starting column number."""
        return self.span.start.col if self.span else 1

    @property
    def end_line(self) -> int:
        """1-indexed ending line number."""
        return self.span.end.line if self.span else 1

    @property
    def end_column(self) -> int:
        """1-indexed ending column number."""
        return self.span.end.col if self.span else 1


class CheckReport(BaseModel):
    """Aggregate check report produced by `rhp check --format=json`."""

    model_config = ConfigDict(extra="ignore")

    target_path: str = Field("", description="Path checked")
    status: str = Field("ok", description="Overall check status (ok or error)")
    errors: int = Field(0, description="Total compiler errors")
    warnings: int = Field(0, description="Total compiler warnings")
    diagnostics: list[CompilerDiagnostic] = Field(default_factory=list, description="Parsed compiler diagnostics")

    @property
    def has_errors(self) -> bool:
        """True if any error or internal error diagnostics were found."""
        return self.errors > 0 or any(
            d.level in ("error", "internal_error") for d in self.diagnostics
        )

    @property
    def has_warnings(self) -> bool:
        """True if any warnings were found."""
        return self.warnings > 0 or any(d.level == "warning" for d in self.diagnostics)

    @property
    def error_diagnostics(self) -> list[CompilerDiagnostic]:
        """Filter diagnostics for errors only."""
        return [d for d in self.diagnostics if d.level in ("error", "internal_error")]

    @property
    def warning_diagnostics(self) -> list[CompilerDiagnostic]:
        """Filter diagnostics for warnings only."""
        return [d for d in self.diagnostics if d.level == "warning"]


# ---------------------------------------------------------------------------
# Binary Locator
# ---------------------------------------------------------------------------

def find_rhp_binary(custom_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """Auto-locate the rhp binary.

    Lookup resolution priority:
    1. Explicit custom_path argument (if provided and valid).
    2. Environment variable RHP_BIN_PATH.
    3. System PATH lookup via shutil.which('rhp').
    4. Common fallback user/system locations (~/.cargo/bin/rhp, /usr/local/bin/rhp, /usr/bin/rhp).

    Args:
        custom_path: Optional explicit binary path override.

    Returns:
        Path to existing rhp executable, or None if not found.
    """
    if custom_path is not None:
        p = Path(custom_path).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p.resolve()
        if p.exists() and p.is_file():
            return p.resolve()

    # 2. Check RHP_BIN_PATH environment variable
    env_bin = os.environ.get(RHP_BIN_ENV_VAR)
    if env_bin:
        p = Path(env_bin).expanduser()
        if p.is_file():
            return p.resolve()

    # 3. Check system PATH
    which_bin = shutil.which("rhp")
    if which_bin:
        p = Path(which_bin)
        try:
            resolved = p.resolve()
            if resolved.is_file():
                return resolved
        except (OSError, RuntimeError):
            pass

    # 4. Check common locations
    candidate_paths = [
        Path.home() / ".cargo" / "bin" / "rhp",
        Path("/usr/local/bin/rhp"),
        Path("/usr/bin/rhp"),
    ]
    for candidate in candidate_paths:
        try:
            if candidate.is_file():
                resolved = candidate.resolve()
                if resolved.is_file():
                    return resolved
        except (OSError, RuntimeError):
            continue

    return None


def get_rhp_binary(custom_path: Optional[Union[str, Path]] = None) -> Path:
    """Locate rhp executable or raise RhpNotFoundError.

    Args:
        custom_path: Optional explicit binary path override.

    Returns:
        Path to rhp executable.

    Raises:
        RhpNotFoundError: When rhp cannot be located.
    """
    binary = find_rhp_binary(custom_path)
    if binary is None:
        raise RhpNotFoundError(
            "The native 'rhp' executable was not found. Please install RHP, ensure it is in your "
            f"$PATH, or set the ${RHP_BIN_ENV_VAR} environment variable."
        )
    return binary


# ---------------------------------------------------------------------------
# Execution Functions
# ---------------------------------------------------------------------------

def run_migrate(
    path: Union[str, Path],
    dry_run: bool = False,
    php_version: int = 7,
    bin_path: Optional[Union[str, Path]] = None,
) -> MigrateReport:
    """Execute `rhp migrate <path> --format=json` and parse the output.

    Args:
        path: Target file or directory to migrate.
        dry_run: If True, executes dry run (--dry-run) without writing changes to disk.
        php_version: Source PHP version (7 or 8). Default is 7.
        bin_path: Optional custom path to the rhp binary.

    Returns:
        MigrateReport containing files scanned, modified, and per-file transform details.

    Raises:
        RhpNotFoundError: If rhp binary cannot be found.
        RhpExecutionError: If rhp returns a non-zero exit code and produces no valid report.
    """
    rhp_bin = get_rhp_binary(bin_path)
    target = Path(path)

    cmd = [
        str(rhp_bin),
        "migrate",
        str(target),
        "--format=json",
        f"--php-version={php_version}",
    ]
    if dry_run:
        cmd.append("--dry-run")

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    stdout = proc.stdout.strip()
    if not stdout:
        if proc.returncode != 0:
            raise RhpExecutionError(
                f"rhp migrate failed with exit code {proc.returncode}: {proc.stderr}",
                returncode=proc.returncode,
                cmd=cmd,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        return MigrateReport(files_scanned=0, files_modified=0)

    try:
        data = json.loads(stdout)
        return MigrateReport.model_validate(data)
    except json.JSONDecodeError as exc:
        if proc.returncode != 0:
            raise RhpExecutionError(
                f"rhp migrate failed with exit code {proc.returncode}: {proc.stderr or stdout}",
                returncode=proc.returncode,
                cmd=cmd,
                stdout=proc.stdout,
                stderr=proc.stderr,
            ) from exc
        raise RhpExecutionError(
            f"Failed to parse JSON output from rhp migrate: {exc}\nOutput: {stdout[:200]}",
            returncode=proc.returncode,
            cmd=cmd,
            stdout=proc.stdout,
            stderr=proc.stderr,
        ) from exc


def run_check(
    path: Union[str, Path],
    bin_path: Optional[Union[str, Path]] = None,
) -> CheckReport:
    """Execute `rhp check <path> --format=json` and parse the compiler diagnostics.

    Args:
        path: Target file or directory to check.
        bin_path: Optional custom path to the rhp binary.

    Returns:
        CheckReport containing compiler errors, warnings, and line spans.

    Raises:
        RhpNotFoundError: If rhp binary cannot be found.
        RhpExecutionError: If rhp crashes or terminates with internal error code (2).
    """
    rhp_bin = get_rhp_binary(bin_path)
    target = Path(path)

    cmd = [
        str(rhp_bin),
        "check",
        str(target),
        "--format=json",
    ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    # Note: Exit code 1 signifies syntax/type check errors in checked files, which is expected.
    # Exit code 2 signifies internal compiler panic / file read failure.
    stdout = proc.stdout.strip()
    if proc.returncode >= 2 and not stdout:
        raise RhpExecutionError(
            f"rhp check internal error (exit code {proc.returncode}): {proc.stderr}",
            returncode=proc.returncode,
            cmd=cmd,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    diagnostics: list[CompilerDiagnostic] = []
    errors = 0
    warnings = 0
    status = "ok"

    if stdout:
        # rhp check --format=json outputs NDJSON (newline-delimited JSON)
        for line in stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            try:
                item = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            if not isinstance(item, dict):
                continue

            # Summary terminator record
            if item.get("level") == "summary":
                errors = item.get("errors", errors)
                warnings = item.get("warnings", warnings)
                status = item.get("status", status)
                continue

            try:
                diag = CompilerDiagnostic.model_validate(item)
                diagnostics.append(diag)
            except Exception:
                continue

    # Fallback counts if summary record was omitted
    if errors == 0 and warnings == 0 and diagnostics:
        errors = sum(1 for d in diagnostics if d.level in ("error", "internal_error"))
        warnings = sum(1 for d in diagnostics if d.level == "warning")
        status = "error" if errors > 0 else "ok"

    return CheckReport(
        target_path=str(target),
        status=status,
        errors=errors,
        warnings=warnings,
        diagnostics=diagnostics,
    )


class RhpCli:
    """Object-oriented wrapper around the native RHP command-line interface."""

    def __init__(self, bin_path: Optional[Union[str, Path]] = None) -> None:
        self._custom_bin = bin_path

    @property
    def bin_path(self) -> Optional[Path]:
        """Resolved path to the rhp binary if found, or None."""
        return find_rhp_binary(self._custom_bin)

    def is_available(self) -> bool:
        """Check if the native rhp executable is installed and available."""
        return self.bin_path is not None

    def migrate(
        self,
        path: Union[str, Path],
        dry_run: bool = False,
        php_version: int = 7,
    ) -> MigrateReport:
        """Execute `rhp migrate`."""
        return run_migrate(
            path=path,
            dry_run=dry_run,
            php_version=php_version,
            bin_path=self._custom_bin,
        )

    def check(self, path: Union[str, Path]) -> CheckReport:
        """Execute `rhp check`."""
        return run_check(
            path=path,
            bin_path=self._custom_bin,
        )
