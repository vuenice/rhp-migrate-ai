"""Differential execution testing between PHP and RHP (Layer 6).

Executes the original PHP code using `php -d display_errors=1 -f <original_php_file>`
and the migrated RHP code using `rhp run <migrated_rhp_file>`. Compares STDOUT,
STDERR, and exit codes to ensure behavioral fidelity during migration.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field

from rhp_migrate.engine.rhp_cli import (
    RhpNotFoundError,
    find_rhp_binary,
    get_rhp_binary,
    run_script,
)

PHP_BIN_ENV_VAR = "PHP_BIN_PATH"


class PhpError(Exception):
    """Base exception for PHP execution errors."""


class PhpNotFoundError(PhpError, FileNotFoundError):
    """Raised when the php executable cannot be found."""


class DifferentialResult(BaseModel):
    """Result of differential execution comparing PHP and RHP."""

    model_config = ConfigDict(extra="ignore")

    matches: bool = Field(
        ...,
        description="True if STDOUT, STDERR, and exit code all match identically",
    )
    php_output: str = Field(
        ...,
        description="Captured standard output produced by PHP execution",
    )
    rhp_output: str = Field(
        ...,
        description="Captured standard output produced by RHP execution",
    )
    diff: str = Field(
        ...,
        description="Unified diff or explanation of differences if outputs do not match",
    )
    php_stdout: str = Field(
        "",
        description="Raw STDOUT from PHP process",
    )
    rhp_stdout: str = Field(
        "",
        description="Raw STDOUT from RHP process",
    )
    php_stderr: str = Field(
        "",
        description="Raw STDERR from PHP process",
    )
    rhp_stderr: str = Field(
        "",
        description="Raw STDERR from RHP process",
    )
    php_exit_code: int = Field(
        0,
        description="Exit status code from PHP process",
    )
    rhp_exit_code: int = Field(
        0,
        description="Exit status code from RHP process",
    )
    stdout_matches: bool = Field(
        True,
        description="True if STDOUT is identical",
    )
    stderr_matches: bool = Field(
        True,
        description="True if STDERR is identical",
    )
    exit_code_matches: bool = Field(
        True,
        description="True if exit codes match",
    )


def find_php_binary(custom_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """Locate the PHP binary on the host system.

    Resolution priority:
    1. Explicit custom_path argument.
    2. Environment variable `PHP_BIN_PATH`.
    3. System PATH lookup via `shutil.which('php')`.
    4. Common system installation locations (/usr/bin/php, /usr/local/bin/php, etc.).

    Args:
        custom_path: Optional custom binary path.

    Returns:
        Path to php executable, or None if not located.
    """
    if custom_path is not None:
        p = Path(custom_path).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p.resolve()
        if p.is_file():
            return p.resolve()

    env_bin = os.environ.get(PHP_BIN_ENV_VAR)
    if env_bin:
        p = Path(env_bin).expanduser()
        if p.is_file():
            return p.resolve()

    which_bin = shutil.which("php")
    if which_bin:
        p = Path(which_bin)
        try:
            resolved = p.resolve()
            if resolved.is_file():
                return resolved
        except (OSError, RuntimeError):
            pass

    common_paths = [
        Path("/usr/bin/php"),
        Path("/usr/local/bin/php"),
        Path("/opt/homebrew/bin/php"),
        Path.home() / ".config" / "herd" / "bin" / "php",
    ]
    for candidate in common_paths:
        try:
            if candidate.is_file():
                resolved = candidate.resolve()
                if resolved.is_file():
                    return resolved
        except (OSError, RuntimeError):
            continue

    return None


def get_php_binary(custom_path: Optional[Union[str, Path]] = None) -> Path:
    """Locate php binary or raise PhpNotFoundError.

    Args:
        custom_path: Optional explicit binary override.

    Returns:
        Path to php executable.

    Raises:
        PhpNotFoundError: If php binary cannot be located.
    """
    binary = find_php_binary(custom_path)
    if binary is None:
        raise PhpNotFoundError(
            "The PHP executable was not found. Please ensure PHP is installed, "
            f"in your $PATH, or set the ${PHP_BIN_ENV_VAR} environment variable."
        )
    return binary


def run_differential_test(
    original_php_file: Union[str, Path],
    migrated_rhp_file: Union[str, Path],
    *,
    php_bin: Optional[Union[str, Path]] = None,
    rhp_bin: Optional[Union[str, Path]] = None,
    args: Optional[Sequence[str]] = None,
    timeout: float = 30.0,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[dict[str, str]] = None,
    normalize_line_endings: bool = True,
) -> DifferentialResult:
    """Execute original PHP and migrated RHP files and compare outputs differentials.

    Flow:
    1. Execute original PHP: `php -d display_errors=1 -f <original_php_file> [args...]`.
    2. Execute migrated RHP: `rhp run <migrated_rhp_file> [args...]`.
    3. Compare STDOUT, STDERR, and exit code.
    4. Return DifferentialResult(matches=bool, php_output=str, rhp_output=str, diff=str).

    Args:
        original_php_file: Path to original PHP script.
        migrated_rhp_file: Path to migrated RHP script.
        php_bin: Optional path to php executable.
        rhp_bin: Optional path to rhp executable.
        args: Optional CLI arguments passed to both scripts.
        timeout: Execution timeout in seconds per command (default: 30.0).
        cwd: Optional working directory for subprocesses.
        env: Optional environment dictionary.
        normalize_line_endings: If True, normalizes CRLF to LF before comparing.

    Returns:
        DifferentialResult containing comparison metrics and output diffs.

    Raises:
        FileNotFoundError: If original_php_file or migrated_rhp_file does not exist.
        PhpNotFoundError: If php executable is not found.
        RhpNotFoundError: If rhp executable is not found.
    """
    php_path = Path(original_php_file).resolve()
    if not php_path.is_file():
        raise FileNotFoundError(f"Original PHP file not found: {php_path}")

    rhp_path = Path(migrated_rhp_file).resolve()
    if not rhp_path.is_file():
        raise FileNotFoundError(f"Migrated RHP file not found: {rhp_path}")

    resolved_php_bin = get_php_binary(php_bin)
    resolved_rhp_bin = get_rhp_binary(rhp_bin)

    # 1. Execute original PHP: php -d display_errors=1 -f <original_php_file>
    php_cmd = [
        str(resolved_php_bin),
        "-d",
        "display_errors=1",
        "-f",
        str(php_path),
    ]
    if args:
        php_cmd.extend(args)

    try:
        php_proc = subprocess.run(
            php_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return DifferentialResult(
            matches=False,
            php_output="",
            rhp_output="",
            diff=f"PHP execution timed out after {timeout} seconds.",
            php_stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
            rhp_stdout="",
            php_stderr=exc.stderr or "" if isinstance(exc.stderr, str) else "",
            rhp_stderr="",
            php_exit_code=-1,
            rhp_exit_code=0,
            stdout_matches=False,
            stderr_matches=False,
            exit_code_matches=False,
        )

    # 2. Execute migrated RHP: rhp run <migrated_rhp_file>
    try:
        rhp_proc = run_script(
            path=rhp_path,
            args=args,
            bin_path=resolved_rhp_bin,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return DifferentialResult(
            matches=False,
            php_output=php_proc.stdout,
            rhp_output="",
            diff=f"RHP execution timed out after {timeout} seconds.",
            php_stdout=php_proc.stdout,
            rhp_stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
            php_stderr=php_proc.stderr,
            rhp_stderr=exc.stderr or "" if isinstance(exc.stderr, str) else "",
            php_exit_code=php_proc.returncode,
            rhp_exit_code=-1,
            stdout_matches=False,
            stderr_matches=False,
            exit_code_matches=False,
        )

    # 3. Compare STDOUT, STDERR, and exit code
    php_stdout = php_proc.stdout
    rhp_stdout = rhp_proc.stdout
    php_stderr = php_proc.stderr
    rhp_stderr = rhp_proc.stderr

    norm_php_stdout = php_stdout.replace("\r\n", "\n") if normalize_line_endings else php_stdout
    norm_rhp_stdout = rhp_stdout.replace("\r\n", "\n") if normalize_line_endings else rhp_stdout
    norm_php_stderr = php_stderr.replace("\r\n", "\n") if normalize_line_endings else php_stderr
    norm_rhp_stderr = rhp_stderr.replace("\r\n", "\n") if normalize_line_endings else rhp_stderr

    stdout_matches = norm_php_stdout == norm_rhp_stdout
    stderr_matches = norm_php_stderr == norm_rhp_stderr
    exit_code_matches = php_proc.returncode == rhp_proc.returncode

    matches = stdout_matches and stderr_matches and exit_code_matches

    # 4. Generate Diff
    diff_str = ""
    if not matches:
        diff_sections: list[str] = []

        if not stdout_matches:
            stdout_diff = list(
                difflib.unified_diff(
                    norm_php_stdout.splitlines(keepends=True),
                    norm_rhp_stdout.splitlines(keepends=True),
                    fromfile=f"php:{php_path.name}",
                    tofile=f"rhp:{rhp_path.name}",
                )
            )
            diff_sections.append("--- STDOUT DIFF ---")
            diff_sections.append("".join(stdout_diff).rstrip())

        if not stderr_matches:
            stderr_diff = list(
                difflib.unified_diff(
                    norm_php_stderr.splitlines(keepends=True),
                    norm_rhp_stderr.splitlines(keepends=True),
                    fromfile="php:STDERR",
                    tofile="rhp:STDERR",
                )
            )
            diff_sections.append("--- STDERR DIFF ---")
            diff_sections.append("".join(stderr_diff).rstrip())

        if not exit_code_matches:
            diff_sections.append(
                f"--- EXIT CODE MISMATCH ---\n"
                f"PHP exit code: {php_proc.returncode}\n"
                f"RHP exit code: {rhp_proc.returncode}"
            )

        diff_str = "\n".join(diff_sections).strip()

    return DifferentialResult(
        matches=matches,
        php_output=php_stdout,
        rhp_output=rhp_stdout,
        diff=diff_str,
        php_stdout=php_stdout,
        rhp_stdout=rhp_stdout,
        php_stderr=php_stderr,
        rhp_stderr=rhp_stderr,
        php_exit_code=php_proc.returncode,
        rhp_exit_code=rhp_proc.returncode,
        stdout_matches=stdout_matches,
        stderr_matches=stderr_matches,
        exit_code_matches=exit_code_matches,
    )


run_differential = run_differential_test
execute_differential_test = run_differential_test


def run_differential_snippet(
    php_snippet: str,
    rhp_snippet: str,
    *,
    php_bin: Optional[Union[str, Path]] = None,
    rhp_bin: Optional[Union[str, Path]] = None,
    args: Optional[Sequence[str]] = None,
    timeout: float = 30.0,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[dict[str, str]] = None,
    normalize_line_endings: bool = True,
) -> DifferentialResult:
    """Run differential testing on source code snippets using temporary files.

    Args:
        php_snippet: PHP source code string.
        rhp_snippet: RHP source code string.
        php_bin: Optional custom php binary path.
        rhp_bin: Optional custom rhp binary path.
        args: Optional CLI arguments.
        timeout: Subprocess timeout in seconds.
        cwd: Working directory.
        env: Environment variables.
        normalize_line_endings: Normalize newlines before comparing.

    Returns:
        DifferentialResult for the snippets.
    """
    with tempfile.TemporaryDirectory(prefix="rhp_diff_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        php_file = tmp_path / "snippet.php"
        rhp_file = tmp_path / "snippet.rhp"

        php_file.write_text(php_snippet, encoding="utf-8")
        rhp_file.write_text(rhp_snippet, encoding="utf-8")

        return run_differential_test(
            original_php_file=php_file,
            migrated_rhp_file=rhp_file,
            php_bin=php_bin,
            rhp_bin=rhp_bin,
            args=args,
            timeout=timeout,
            cwd=cwd,
            env=env,
            normalize_line_endings=normalize_line_endings,
        )
