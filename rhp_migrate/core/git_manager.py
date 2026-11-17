"""Atomic Git branch management and per-unit commits for RHP migrations (Layer 7)."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import subprocess
from typing import Optional, Sequence, Union


class GitManagerError(Exception):
    """Raised when a Git command fails."""

    def __init__(
        self,
        message: str,
        cmd: Optional[Sequence[str]] = None,
        returncode: int = 1,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.cmd = list(cmd) if cmd else []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GitManager:
    """Manages migration branch creation and atomic per-unit commits."""

    def __init__(
        self,
        repo_path: Optional[Union[str, Path]] = None,
        git_binary: str = "git",
    ) -> None:
        self.target_path = (
            Path(repo_path).resolve() if repo_path else Path.cwd().resolve()
        )
        self.git_binary = git_binary
        self.active_branch: Optional[str] = None
        self._repo_root: Optional[Path] = None

    def _run_git(
        self,
        args: Sequence[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a git command within the repository root directory."""
        cwd = self.repo_path
        cmd = [self.git_binary, *args]
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitManagerError(
                f"git command failed: {' '.join(cmd)}\n{proc.stderr.strip() or proc.stdout.strip()}",
                cmd=cmd,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        return proc

    @property
    def repo_path(self) -> Path:
        """Locate root directory of git repository."""
        if self._repo_root is not None:
            return self._repo_root

        dir_to_check = (
            self.target_path
            if self.target_path.is_dir()
            else self.target_path.parent
        )
        proc = subprocess.run(
            [self.git_binary, "rev-parse", "--show-toplevel"],
            cwd=str(dir_to_check),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            self._repo_root = Path(proc.stdout.strip()).resolve()
            return self._repo_root

        self._repo_root = dir_to_check
        return self._repo_root

    def is_git_repo(self) -> bool:
        """Check whether target directory is inside a valid git repository."""
        dir_to_check = (
            self.target_path
            if self.target_path.is_dir()
            else self.target_path.parent
        )
        proc = subprocess.run(
            [self.git_binary, "rev-parse", "--is-inside-work-tree"],
            cwd=str(dir_to_check),
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def create_migration_branch(self, branch_name: Optional[str] = None) -> str:
        """Create and checkout a new migration branch.

        Format: `rhp-migration-<timestamp>`.

        Args:
            branch_name: Optional explicit branch name override.

        Returns:
            Name of the created branch.
        """
        if not branch_name:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            branch_name = f"rhp-migration-{timestamp}"

        self._run_git(["checkout", "-b", branch_name])
        self.active_branch = branch_name
        return branch_name

    def get_current_branch(self) -> str:
        """Retrieve current checked-out git branch name."""
        proc = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        return proc.stdout.strip()

    def commit_file(
        self,
        file_path: Union[str, Path],
        unit_name: Optional[str] = None,
        custom_message: Optional[str] = None,
    ) -> Optional[str]:
        """Atomically stage and commit changes for a verified file or unit.

        Commit message format: `migrate(rhp): migrate <unit> [auto]`.

        Args:
            file_path: Path to the modified file.
            unit_name: Fully qualified class or unit name (e.g. `App\\Models\\User`).
            custom_message: Optional explicit commit message override.

        Returns:
            Short commit SHA if committed, or None if no changes were staged.
        """
        path = Path(file_path).resolve()
        # Stage the target file
        self._run_git(["add", str(path)])

        # Check if changes were staged
        diff_proc = self._run_git(["diff", "--cached", "--quiet"], check=False)
        if diff_proc.returncode == 0:
            # 0 means no staged changes
            return None

        # Build commit message
        if custom_message:
            commit_msg = custom_message
        elif unit_name:
            commit_msg = f"migrate(rhp): migrate {unit_name} [auto]"
        else:
            try:
                rel = path.relative_to(self.repo_path)
            except ValueError:
                rel = path.name
            commit_msg = f"migrate(rhp): migrate {rel} [auto]"

        self._run_git(["commit", "-m", commit_msg])

        sha_proc = self._run_git(["rev-parse", "--short", "HEAD"])
        return sha_proc.stdout.strip()

    def get_last_commit_message(self) -> str:
        """Get commit subject of HEAD."""
        proc = self._run_git(["log", "-1", "--format=%s"])
        return proc.stdout.strip()
