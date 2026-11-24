"""Workspace manager service and FastAPI endpoints for project ingestion.

Handles:
- Safe archive unpacking with Zip-Slip traversal prevention.
- Shallow git cloning from GitHub with optional branch and token authentication.
- File tree generation with directory filtering and PHP component classification.
- Raw file content fetching with directory traversal protection.
- Background and on-demand TTL workspace cleanup (older than 2 hours).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Optional, Sequence, Union
import csv
import sys
import urllib.parse
import uuid
import zipfile

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import PlainTextResponse

from rhp_migrate.engine import create_default_rhp_toml, has_rhp_manifest
from rhp_migrate.server.models import (
    CloneGithubRequest,
    CsvStatusResponse,
    CsvStatusSummary,
    FileTreeNode,
    LastSessionResponse,
    ManifestStatusResponse,
    ProjectListResponse,
    ProjectSummaryItem,
    SoftwareInfoResponse,
    WorkspaceCleanupResponse,
    WorkspaceFileContentResponse,
    WorkspaceResponse,
    WorkspaceSaveFileRequest,
    WorkspaceSaveFileResponse,
    WorkspaceStateResponse,
    WorkspaceStateSaveRequest,
    WorkspaceTreeResponse,
)

logger = logging.getLogger(__name__)

# Default base directory for sandboxed workspaces
DEFAULT_WORKSPACES_ROOT = None

# Default TTL: 2 hours (in seconds)
WORKSPACE_TTL_SECONDS = 7200

# Directories and files to exclude from tree navigation
IGNORED_DIRS = {"vendor", "node_modules", ".git"}
IGNORED_FILES = {".env", ".workspace_meta.json", ".migration_state.json", "migration_status.csv"}



# Allowed pattern for workspace IDs to prevent traversal in path parameters
WORKSPACE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class ZipSlipError(ValueError):
    """Raised when an archive member contains directory traversal paths."""
    pass


class GitCloneError(Exception):
    """Raised when git shallow clone fails."""
    pass


def get_software_data_dir() -> Path:
    """Return the persistent software directory for rhp-migrate.

    Following conventions of desktop/CLI tools like VSCode (~/.config/Code on Linux,
    %APPDATA%/Code on Windows) and Antigravity (~/.gemini/antigravity).
    Configurable via the RHP_APP_DIR environment variable.
    """
    env_dir = os.environ.get("RHP_APP_DIR")
    if env_dir:
        try:
            root = Path(env_dir).resolve()
            root.mkdir(parents=True, exist_ok=True)
            (root / "projects").mkdir(parents=True, exist_ok=True)
            return root
        except Exception:
            pass

    # If RHP_WORKSPACES_DIR is set (e.g. in tests), co-locate app data there
    ws_env = os.environ.get("RHP_WORKSPACES_DIR")
    if ws_env:
        try:
            root = Path(ws_env).resolve().parent / ".rhp-software-data"
            root.mkdir(parents=True, exist_ok=True)
            (root / "projects").mkdir(parents=True, exist_ok=True)
            return root
        except Exception:
            pass

    candidate_paths: list[Path] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidate_paths.append(Path(appdata) / "rhp-migrate")
        candidate_paths.append(Path.home() / ".rhp-migrate")
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            candidate_paths.append(Path(xdg_data) / "rhp-migrate")
        candidate_paths.append(Path.home() / ".rhp-migrate")

    # Local fallback within project workspace or tmp if home is read-only
    candidate_paths.append(Path.cwd() / ".rhp-data")
    candidate_paths.append(Path("/tmp/rhp-migrate"))

    for p in candidate_paths:
        try:
            p.mkdir(parents=True, exist_ok=True)
            test_file = p / ".test_write"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            (p / "projects").mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            continue

    fallback = Path("/tmp/rhp-migrate")
    fallback.mkdir(parents=True, exist_ok=True)
    (fallback / "projects").mkdir(parents=True, exist_ok=True)
    return fallback



def get_workspaces_root() -> Path:
    """Return the base directory for project workspaces.

    Configurable via the RHP_WORKSPACES_DIR environment variable.
    Defaults to the persistent 'projects' folder inside the software data directory.
    """
    env_dir = os.environ.get("RHP_WORKSPACES_DIR")
    if env_dir:
        root = Path(env_dir).resolve()
    else:
        root = get_software_data_dir() / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root



def classify_php_file(file_path: Union[str, Path], content: Optional[str] = None) -> Optional[str]:
    """Classify a PHP file into one of the 5 canonical categories:

    - 'Blade view'
    - 'Migration'
    - 'Controller'
    - 'Model'
    - 'Service'

    Returns None if the file is not a recognized PHP component or not a PHP file.
    """
    p = Path(file_path)
    name = p.name
    name_lower = name.lower()
    path_str = str(file_path).replace("\\", "/")
    path_lower = path_str.lower()

    # Only classify PHP or Blade files
    if not (name_lower.endswith(".php") or ".blade.php" in name_lower):
        return None

    # 1. Blade view
    if name_lower.endswith(".blade.php") or "/views/" in path_lower or path_lower.startswith("views/"):
        return "Blade view"

    # 2. Migration
    if "/migrations/" in path_lower or path_lower.startswith("migrations/"):
        return "Migration"
    if re.search(r"^\d{4}_\d{2}_\d{2}_\d{6}_.*\.php$", name):
        return "Migration"
    if "_create_" in name_lower and "_table.php" in name_lower:
        return "Migration"

    # 3. Controller
    if name_lower.endswith("controller.php") or "/controllers/" in path_lower:
        return "Controller"

    # 4. Service
    if name_lower.endswith("service.php") or "/services/" in path_lower:
        return "Service"

    # 5. Model
    if "/models/" in path_lower or path_lower.startswith("models/"):
        return "Model"

    # If filename and path alone were inconclusive, inspect source content if available
    snippet = content
    if snippet is None and p.is_file():
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                snippet = f.read(4096)
        except Exception:
            snippet = None

    if snippet:
        # Check Blade syntax
        if any(directive in snippet for directive in ("@extends", "@section", "@yield", "@component", "{{", "{!!")):
            return "Blade view"

        # Check Migration
        if "extends Migration" in snippet or "Illuminate\\Database\\Migrations\\Migration" in snippet or "Schema::create" in snippet:
            return "Migration"

        # Check Controller
        if "extends Controller" in snippet or re.search(r"\bclass\s+\w+Controller\b", snippet):
            return "Controller"

        # Check Service
        if re.search(r"\bclass\s+\w+Service\b", snippet):
            return "Service"

        # Check Model
        if (
            "extends Model" in snippet
            or "extends Authenticatable" in snippet
            or "Illuminate\\Database\\Eloquent\\Model" in snippet
            or "protected $table" in snippet
            or "protected $fillable" in snippet
        ):
            return "Model"

        # In Laravel 5-7, app/User.php was a Model directly under app/
        if re.search(r"^app/[^/]+\.php$", path_lower) and re.search(r"\bclass\s+\w+\b", snippet):
            if "Controller" not in name and "Service" not in name:
                return "Model"

    return None


def validate_and_extract_zip(
    zip_source: Union[bytes, io.BytesIO, Path, str, UploadFile],
    dest_dir: Path,
) -> list[str]:
    """Validate archive against Zip-Slip / directory traversal and unpack into dest_dir.

    Raises ZipSlipError if any member would unpack outside dest_dir.
    Returns list of extracted relative file paths.
    """
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Prepare ZipFile object
    if isinstance(zip_source, bytes):
        zip_obj = zipfile.ZipFile(io.BytesIO(zip_source))
    elif isinstance(zip_source, io.BytesIO):
        zip_obj = zipfile.ZipFile(zip_source)
    elif isinstance(zip_source, (str, Path)):
        zip_obj = zipfile.ZipFile(zip_source)
    elif hasattr(zip_source, "file"):
        zip_obj = zipfile.ZipFile(zip_source.file)
    else:
        raise ValueError(f"Unsupported zip source type: {type(zip_source)}")

    logger.info("Inspecting zip archive for extraction into %s", dest_dir)
    with zip_obj as zf:
        infolist = zf.infolist()
        if not infolist:
            logger.warning("Zip archive has no entries (empty archive)")
            return []

        logger.info("Validating %d archive members for Zip-Slip / path traversal", len(infolist))
        # 1. Validation pass: strictly check all members before extracting anything
        for member in infolist:
            filename = member.filename
            if not filename:
                continue

            # Reject absolute paths (POSIX / or Windows C:\)
            if filename.startswith(("/", "\\")) or (len(filename) > 1 and filename[1] == ":"):
                logger.warning("Rejected zip member with absolute path: %s", filename)
                raise ZipSlipError(f"Absolute path in zip member forbidden: {filename}")

            # Reject explicit parent directory traversal segments
            parts = Path(filename).parts
            if ".." in parts:
                logger.warning("Rejected zip member with path traversal '..': %s", filename)
                raise ZipSlipError(f"Path traversal '..' detected in zip member: {filename}")

            # Verify resolved target is within destination
            target_path = (dest_dir / filename).resolve()
            try:
                target_path.relative_to(dest_dir)
            except ValueError:
                logger.warning("Zip-Slip detected for member '%s' escaping %s", filename, dest_dir)
                raise ZipSlipError(f"Zip-slip detected: member '{filename}' escapes target workspace")

            # Check for symlink escaping target
            mode = member.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                link_content = zf.read(member).decode("utf-8", errors="replace").strip()
                if link_content.startswith(("/", "\\")) or (len(link_content) > 1 and link_content[1] == ":"):
                    logger.warning("Forbidden absolute symlink target '%s' in member '%s'", link_content, filename)
                    raise ZipSlipError(f"Absolute symlink target in zip forbidden: {link_content}")
                resolved_link = (target_path.parent / link_content).resolve()
                try:
                    resolved_link.relative_to(dest_dir)
                except ValueError:
                    logger.warning("Symlink '%s' -> '%s' escapes target workspace %s", filename, link_content, dest_dir)
                    raise ZipSlipError(f"Symlink '{filename}' -> '{link_content}' escapes target workspace")

        # 2. Safe extraction pass
        logger.info("All %d members validated safely; unpacking archive into %s", len(infolist), dest_dir)
        zf.extractall(dest_dir)

    extracted_files: list[str] = []
    for root, _, files in os.walk(dest_dir):
        for f in files:
            full_path = Path(root) / f
            try:
                rel = str(full_path.relative_to(dest_dir)).replace("\\", "/")
                extracted_files.append(rel)
            except ValueError:
                pass

    logger.info("Successfully unpacked %d files into %s", len(extracted_files), dest_dir)
    return extracted_files


def shallow_clone_repo(
    repo_url: str,
    dest_dir: Path,
    branch: Optional[str] = None,
    github_token: Optional[str] = None,
    timeout: int = 120,
) -> None:
    """Perform a shallow clone (--depth 1) of a GitHub repository into dest_dir.

    Sanitizes credentials so tokens are never exposed in exceptions or logs.
    """
    dest_dir = dest_dir.resolve()
    # Git clone requires destination to either not exist or be an empty directory
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)

    clean_url = repo_url.strip()
    if clean_url.startswith("-"):
        logger.warning("Rejected repository URL starting with dash: %s", clean_url)
        raise GitCloneError("Invalid repository URL: cannot start with a dash")

    logger.info("Executing shallow git clone for '%s' (branch: %s) into %s", clean_url, branch or "default", dest_dir)

    # Inject token into HTTPS URL if provided
    clone_url = clean_url
    if github_token:
        token = github_token.strip()
        if clean_url.startswith("git@github.com:"):
            repo_path = clean_url.removeprefix("git@github.com:")
            clone_url = f"https://x-access-token:{token}@github.com/{repo_path}"
        elif "://" in clean_url:
            parsed = urllib.parse.urlsplit(clean_url)
            netloc = f"x-access-token:{token}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            clone_url = urllib.parse.urlunsplit(
                (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
            )

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch.strip()])
    cmd.extend([clone_url, str(dest_dir)])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("Git clone timed out after %ds for repository '%s'", timeout, clean_url)
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise GitCloneError(f"Git clone timed out after {timeout} seconds") from exc
    except Exception as exc:
        logger.exception("Failed to spawn git clone process for '%s': %s", clean_url, exc)
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise GitCloneError(f"Failed to execute git clone: {exc}") from exc

    if proc.returncode != 0:
        error_msg = proc.stderr.strip() or proc.stdout.strip()
        if github_token:
            error_msg = error_msg.replace(github_token, "******")
        logger.error("Git clone failed (exit %d) for '%s': %s", proc.returncode, clean_url, error_msg)
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise GitCloneError(f"Git clone failed (exit {proc.returncode}): {error_msg}")

    logger.info("Git clone successfully cloned '%s' into %s", clean_url, dest_dir)

    # Write metadata after successful clone
    meta = {
        "workspace_id": dest_dir.name,
        "created_at": time.time(),
        "repo_url": clean_url,
        "branch": branch,
    }
    (dest_dir / ".workspace_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def build_file_tree(
    root_dir: Path,
    current_dir: Optional[Path] = None,
) -> tuple[list[FileTreeNode], int]:
    """Recursively build a hierarchical FileTreeNode tree, filtering ignored files/dirs.

    Filters out:
    - vendor/
    - node_modules/
    - .git/
    - .env and .env.*

    Returns:
    - (nodes_list, total_file_count)
    """
    if current_dir is None:
        current_dir = root_dir

    nodes: list[FileTreeNode] = []
    total_files = 0

    try:
        entries = sorted(os.scandir(current_dir), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (FileNotFoundError, PermissionError):
        return [], 0

    for entry in entries:
        name = entry.name

        # Filtering logic
        if entry.is_dir():
            if name in IGNORED_DIRS:
                continue
            child_dir = Path(entry.path)
            rel_path = str(child_dir.relative_to(root_dir)).replace("\\", "/")
            children, sub_file_count = build_file_tree(root_dir, child_dir)
            total_files += sub_file_count
            nodes.append(
                FileTreeNode(
                    name=name,
                    path=rel_path,
                    type="directory",
                    size=None,
                    classification=None,
                    children=children,
                )
            )
        elif entry.is_file():
            if name in IGNORED_FILES or name.startswith(".env.") or name.startswith(".workspace_meta"):
                continue

            file_path = Path(entry.path)
            rel_path = str(file_path.relative_to(root_dir)).replace("\\", "/")
            total_files += 1

            size: Optional[int] = None
            try:
                size = entry.stat().st_size
            except Exception:
                pass

            classification = classify_php_file(file_path)
            nodes.append(
                FileTreeNode(
                    name=name,
                    path=rel_path,
                    type="file",
                    size=size,
                    classification=classification,
                    children=None,
                )
            )

    return nodes, total_files


def cleanup_old_workspaces(
    base_dir: Optional[Path] = None,
    max_age_seconds: int = WORKSPACE_TTL_SECONDS,
) -> int:
    """Remove workspace directories older than max_age_seconds (default 2 hours).

    Returns the number of cleaned workspaces.
    """
    root = (base_dir or get_workspaces_root()).resolve()
    if not root.is_dir():
        return 0

    cleaned = 0
    now = time.time()

    for item in list(root.iterdir()):
        if not item.is_dir():
            continue

        # Check metadata file or directory mtime
        meta_file = item / ".workspace_meta.json"
        created_at: Optional[float] = None
        if meta_file.is_file():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                created_at = float(data.get("created_at", 0))
            except Exception:
                pass

        if created_at is None or created_at <= 0:
            try:
                created_at = item.stat().st_mtime
            except Exception:
                continue

        if (now - created_at) > max_age_seconds:
            try:
                shutil.rmtree(item, ignore_errors=True)
                cleaned += 1
                logger.info("Cleaned up expired workspace: %s", item.name)
            except Exception as exc:
                logger.warning("Failed to remove expired workspace %s: %exc", item.name, exc)

    return cleaned


async def periodic_workspace_cleanup_task(
    interval_seconds: int = 1800,
    max_age_seconds: int = WORKSPACE_TTL_SECONDS,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Continuous background loop for purging stale workspaces."""
    while True:
        try:
            cleanup_old_workspaces(max_age_seconds=max_age_seconds)
        except Exception as exc:
            logger.error("Error during periodic workspace cleanup: %s", exc)

        if stop_event:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                break
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(interval_seconds)


def record_last_session(workspace_id: str, ws_dir: Path, project_name: Optional[str] = None) -> None:
    """Record the last opened workspace in the persistent software directory."""
    software_dir = get_software_data_dir()
    session_file = software_dir / "last_session.json"
    resolved_name = project_name
    if not resolved_name:
        meta_file = ws_dir / ".workspace_meta.json"
        if meta_file.is_file():
            try:
                mdata = json.loads(meta_file.read_text(encoding="utf-8"))
                resolved_name = mdata.get("name") or mdata.get("repo_url")
            except Exception:
                pass
    if not resolved_name:
        resolved_name = ws_dir.name

    file_count = 0
    try:
        _, file_count = build_file_tree(ws_dir)
    except Exception:
        pass

    session_data = {
        "workspace_id": workspace_id,
        "workspace_path": str(ws_dir),
        "project_name": resolved_name,
        "last_opened_at": datetime.now(timezone.utc).isoformat(),
        "file_count": file_count,
    }
    try:
        session_file.write_text(json.dumps(session_data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to record last session: %s", exc)


def get_last_session() -> LastSessionResponse:
    """Retrieve last opened workspace information."""
    software_dir = get_software_data_dir()
    session_file = software_dir / "last_session.json"
    if session_file.is_file():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            wid = data.get("workspace_id")
            wpath_str = data.get("workspace_path", "")
            wpath = Path(wpath_str) if wpath_str else None
            if wid and wpath and wpath.is_dir():
                return LastSessionResponse(
                    workspace_id=wid,
                    workspace_path=str(wpath),
                    project_name=data.get("project_name", wid),
                    last_opened_at=data.get("last_opened_at"),
                    file_count=data.get("file_count", 0),
                )
        except Exception as exc:
            logger.warning("Failed to read last session: %s", exc)
    return LastSessionResponse(workspace_id=None)


def generate_workspace_csv(ws_dir: Path) -> tuple[Path, str, CsvStatusSummary]:
    """Generate or update migration_status.csv containing all project file information.

    Records:
    - file_path
    - file_name
    - classification
    - status ('completed', 'needs_review', 'pending')
    - needs_review ('Yes' / 'No')
    - review_reason
    - readiness_percentage
    - unsupported_features
    - untyped_params
    - rhp_file_path
    - last_updated
    """
    csv_path = ws_dir / "migration_status.csv"
    state_file = ws_dir / ".migration_state.json"
    state: dict[str, Any] = {}
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    migrated_files: dict[str, str] = state.get("migrated_files", {})
    file_statuses: dict[str, str] = state.get("file_statuses", {})
    scorecards: dict[str, Any] = state.get("scorecards", {})

    rows: list[list[str]] = []
    headers = [
        "file_path",
        "file_name",
        "classification",
        "status",
        "needs_review",
        "review_reason",
        "readiness_percentage",
        "unsupported_features",
        "untyped_params",
        "rhp_file_path",
        "last_updated",
    ]

    total_files = 0
    completed_count = 0
    needs_review_count = 0
    pending_count = 0

    now_iso = datetime.now(timezone.utc).isoformat()

    for root, dirs, files in os.walk(ws_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in sorted(files):
            if f in IGNORED_FILES or f.startswith(".env.") or f == "migration_status.csv":
                continue

            full_path = Path(root) / f
            try:
                rel_path = str(full_path.relative_to(ws_dir)).replace("\\", "/")
            except ValueError:
                continue

            # Skip standalone .rhp if companion to .php exists
            if rel_path.endswith(".rhp"):
                php_counterpart = ws_dir / (rel_path[:-4] + ".php")
                if php_counterpart.is_file():
                    continue

            total_files += 1
            classification = classify_php_file(full_path) or "Source"

            recorded_status = file_statuses.get(rel_path, "")
            is_migrated_in_state = rel_path in migrated_files or recorded_status == "migrated"
            rhp_companion = ws_dir / (rel_path[:-4] + ".rhp") if rel_path.endswith(".php") else None
            has_rhp = (rhp_companion and rhp_companion.is_file()) or rel_path.endswith(".rhp")

            file_code = migrated_files.get(rel_path, "")
            if not file_code and full_path.is_file():
                try:
                    file_code = full_path.read_text(encoding="utf-8", errors="replace")[:4096]
                except Exception:
                    file_code = ""

            scorecard_data = scorecards.get(rel_path, {})
            readiness = scorecard_data.get("readinessPercentage", "")
            unsupported = scorecard_data.get("unsupportedCount", 0)
            untyped = scorecard_data.get("untypedParamCount", 0)

            has_todos = "TODO_RHP_TYPE" in file_code or "needs_human_review" in file_code

            if recorded_status == "needs_review" or (has_todos and is_migrated_in_state) or recorded_status == "warning":
                status_str = "needs_review"
                needs_review_str = "Yes"
                review_reason = "Contains unresolved type annotations or compiler warnings" if has_todos else "Manual review flagged"
                needs_review_count += 1
            elif is_migrated_in_state or has_rhp or recorded_status == "migrated":
                status_str = "completed"
                needs_review_str = "No"
                review_reason = "Migration complete & verified"
                completed_count += 1
                if not readiness:
                    readiness = "100"
            else:
                status_str = "pending"
                needs_review_str = "No"
                review_reason = "Pending migration"
                pending_count += 1

            rhp_rel = str(rhp_companion.relative_to(ws_dir)).replace("\\", "/") if (rhp_companion and rhp_companion.is_file()) else ""

            rows.append([
                rel_path,
                f,
                classification,
                status_str,
                needs_review_str,
                review_reason,
                f"{readiness}%" if readiness != "" else "N/A",
                str(unsupported),
                str(untyped),
                rhp_rel,
                now_iso,
            ])

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    csv_content = buf.getvalue()

    csv_path.write_text(csv_content, encoding="utf-8")
    summary = CsvStatusSummary(
        total_files=total_files,
        completed_count=completed_count,
        needs_review_count=needs_review_count,
        pending_count=pending_count,
    )
    return csv_path, csv_content, summary


def seed_demo_workspace(demo_dir: Path) -> None:
    """Seed the demo workspace in the persistent software projects directory."""
    demo_dir.mkdir(parents=True, exist_ok=True)
    services_dir = demo_dir / "app" / "Services"
    models_dir = demo_dir / "app" / "Models"
    services_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    # 1. PaymentService.php
    (services_dir / "PaymentService.php").write_text("""<?php

namespace App\\Services;

use App\\Models\\Order;
use Illuminate\\Support\\Facades\\Http;

class PaymentService
{
    private string $gatewayUrl;
    private $logger; // Untyped property

    public function __construct(string $gatewayUrl, $logger = null)
    {
        $this->gatewayUrl = $gatewayUrl;
        $this->logger = $logger;
    }

    /**
     * Process customer order charge
     */
    public function charge(Order $order, $amount, array $options = []): array
    {
        // Dynamic associative array in PHP
        $payload = [
            'order_id' => $order->id,
            'amount' => (int) ($amount * 100),
            'currency' => $options['currency'] ?? 'USD',
            'metadata' => $options['metadata'] ?? [],
        ];

        $response = Http::timeout(10)->post($this->gatewayUrl . '/v1/charges', $payload);

        if (!$response->successful()) {
            throw new \\RuntimeException("Charge failed: " . $response->body());
        }

        return $response->json();
    }
}
""", encoding="utf-8")

    # 2. TaxCalculator.php
    (services_dir / "TaxCalculator.php").write_text("""<?php

namespace App\\Services;

class TaxCalculator
{
    public function calculate(float $subtotal, float $taxRate): float
    {
        return $subtotal * $taxRate;
    }
}
""", encoding="utf-8")

    # 3. Order.php
    (models_dir / "Order.php").write_text("""<?php

namespace App\\Models;

use Illuminate\\Database\\Eloquent\\Model;

class Order extends Model
{
    protected $table = 'orders';
    protected $fillable = ['id', 'user_id', 'total_cents', 'status'];

    public function getFormattedTotal(): string
    {
        return sprintf("$%.2f", $this->total_cents / 100);
    }
}
""", encoding="utf-8")

    # 4. User.php
    (models_dir / "User.php").write_text("""<?php

namespace App\\Models;

use Illuminate\\Database\\Eloquent\\Model;

class User extends Model
{
    protected $table = 'users';
    protected $fillable = ['id', 'name', 'email'];

    public function getDisplayName(): string
    {
        return $this->name . ' <' . $this->email . '>';
    }
}
""", encoding="utf-8")

    # 5. rhp.toml
    (demo_dir / "rhp.toml").write_text("""[package]
name = "rhp-demo-project"
version = "0.1.0"
edition = "2026"

[dependencies]
rhp-stdlib = "0.1"

[target.strict]
type_shapes = true
null_safety = true
array_specialization = true
""", encoding="utf-8")

    # 6. .workspace_meta.json
    meta = {
        "workspace_id": "demo",
        "name": "Demo E-Commerce Service",
        "created_at": time.time(),
        "created_iso": datetime.now(timezone.utc).isoformat(),
    }
    (demo_dir / ".workspace_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # 7. Initial .migration_state.json
    state = {
        "workspace_id": "demo",
        "active_file": "app/Services/PaymentService.php",
        "migrated_files": {
            "app/Services/TaxCalculator.php": """<?rhp
namespace App\\Services;

final class TaxCalculator
{
    public function calculate(float $subtotal, float $taxRate): float
    {
        return $subtotal * $taxRate;
    }
}
""",
            "app/Models/User.php": """<?rhp
namespace App\\Models;

final class User
{
    public int $id;
    public string $name;
    public string $email;

    public function getDisplayName(): string
    {
        return $this->name . ' <' . $this->email . '>';
    }
}
""",
        },
        "file_statuses": {
            "app/Services/PaymentService.php": "warning",
            "app/Services/TaxCalculator.php": "migrated",
            "app/Models/Order.php": "pending",
            "app/Models/User.php": "migrated",
        },
        "scorecards": {
            "app/Services/PaymentService.php": {
                "readinessPercentage": 74,
                "unsupportedCount": 1,
                "untypedParamCount": 2,
                "isMigrationReady": False,
                "classes": ["PaymentService"],
                "functions": ["__construct", "charge"],
            },
            "app/Services/TaxCalculator.php": {
                "readinessPercentage": 100,
                "unsupportedCount": 0,
                "untypedParamCount": 0,
                "isMigrationReady": True,
                "classes": ["TaxCalculator"],
                "functions": ["calculate"],
            },
        },
        "compiler_logs": [
            {
                "id": "1",
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "level": "info",
                "message": "RHP Language Engine initialized in software workspace.",
            },
            {
                "id": "2",
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "level": "warn",
                "message": "Untyped parameters detected in PaymentService::charge(). Manual hint recommended.",
            },
        ],
        "chat_messages": [
            {
                "id": "m1",
                "role": "assistant",
                "content": "Welcome to the RHP Migration Workbench! I have inspected PaymentService.php. It has an AST readiness score of 74%. You can instruct me to specialize arrays or convert types using the chat below.",
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            }
        ],
    }
    (demo_dir / ".migration_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    generate_workspace_csv(demo_dir)


class WorkspaceManager:
    """Manages creation, navigation, and lifecycle for sandboxed project workspaces."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._custom_base_dir = base_dir.resolve() if base_dir else None

    @property
    def base_dir(self) -> Path:
        if self._custom_base_dir:
            return self._custom_base_dir.resolve()
        return get_workspaces_root()

    @base_dir.setter
    def base_dir(self, value: Path) -> None:
        self._custom_base_dir = value.resolve() if value else None

    def create_workspace(self, workspace_id: Optional[str] = None) -> tuple[str, Path]:
        """Create a new unique workspace directory and record metadata."""
        wid = workspace_id or uuid.uuid4().hex
        if not WORKSPACE_ID_PATTERN.match(wid):
            raise ValueError(f"Invalid workspace ID format: '{wid}'")

        target = (self.base_dir / wid).resolve()
        if not target.is_relative_to(self.base_dir):
            raise ValueError(f"Workspace path escapes root: '{wid}'")

        target.mkdir(parents=True, exist_ok=True)

        # Write workspace metadata
        meta = {
            "workspace_id": wid,
            "created_at": time.time(),
            "created_iso": datetime.now(timezone.utc).isoformat(),
        }
        (target / ".workspace_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        record_last_session(wid, target)

        return wid, target

    def get_workspace_path(self, workspace_id: str) -> Path:
        """Validate and resolve path for an existing workspace."""
        if not WORKSPACE_ID_PATTERN.match(workspace_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid workspace ID format: '{workspace_id}'",
            )

        ws_dir = (self.base_dir / workspace_id).resolve()
        if not ws_dir.is_relative_to(self.base_dir):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Directory traversal detected in workspace ID",
            )

        # Auto-seed demo workspace if accessed
        if workspace_id == "demo" and not ws_dir.is_dir():
            seed_demo_workspace(ws_dir)

        if not ws_dir.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workspace '{workspace_id}' not found",
            )

        return ws_dir

    def unpack_zip(
        self,
        workspace_id: str,
        zip_source: Union[bytes, io.BytesIO, Path, str, UploadFile],
    ) -> list[str]:
        """Unpack a zip file into a workspace with safety checks."""
        ws_dir = self.get_workspace_path(workspace_id)
        files = validate_and_extract_zip(zip_source, ws_dir)
        generate_workspace_csv(ws_dir)
        record_last_session(workspace_id, ws_dir)
        return files

    def clone_repository(
        self,
        workspace_id: str,
        repo_url: str,
        branch: Optional[str] = None,
        github_token: Optional[str] = None,
    ) -> None:
        """Shallow clone a git repository into a workspace."""
        ws_dir = self.get_workspace_path(workspace_id)
        shallow_clone_repo(
            repo_url=repo_url,
            dest_dir=ws_dir,
            branch=branch,
            github_token=github_token,
        )
        meta_file = ws_dir / ".workspace_meta.json"
        meta = {
            "workspace_id": workspace_id,
            "created_at": time.time(),
            "repo_url": repo_url,
            "branch": branch,
        }
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        generate_workspace_csv(ws_dir)
        record_last_session(workspace_id, ws_dir)

    def get_tree(self, workspace_id: str) -> WorkspaceTreeResponse:
        """Generate the filtered file tree for the workspace."""
        ws_dir = self.get_workspace_path(workspace_id)
        tree, total_files = build_file_tree(ws_dir)
        try:
            generate_workspace_csv(ws_dir)
        except Exception:
            pass
        record_last_session(workspace_id, ws_dir)
        return WorkspaceTreeResponse(
            workspace_id=workspace_id,
            total_files=total_files,
            tree=tree,
        )

    def get_file_content(self, workspace_id: str, rel_path: str) -> WorkspaceFileContentResponse:
        """Fetch raw contents of a file within the workspace."""
        ws_dir = self.get_workspace_path(workspace_id)

        clean_path = rel_path.lstrip("/\\")
        if ".." in Path(clean_path).parts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid path: directory traversal attempt",
            )

        target = (ws_dir / clean_path).resolve()
        if not target.is_relative_to(ws_dir):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid path: escapes workspace sandbox",
            )

        if not target.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: '{rel_path}'",
            )

        if target.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path is a directory, not a file: '{rel_path}'",
            )

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read file content: {exc}",
            )

        classification = classify_php_file(target, content=content)

        return WorkspaceFileContentResponse(
            workspace_id=workspace_id,
            path=str(target.relative_to(ws_dir)).replace("\\", "/"),
            content=content,
            size=len(content.encode("utf-8")),
            classification=classification,
        )

    def save_file(
        self,
        workspace_id: str,
        rel_path: str,
        content: str,
        status: Optional[str] = "migrated",
        is_rhp: bool = False,
    ) -> tuple[Path, Optional[Path], int]:
        """Save updated file content into workspace sandbox and generate/update .rhp companion."""
        ws_dir = self.get_workspace_path(workspace_id)
        clean_path = rel_path.lstrip("/\\")
        if ".." in Path(clean_path).parts:
            raise HTTPException(
                status_code=400,
                detail="Invalid path: directory traversal attempt",
            )
        target = (ws_dir / clean_path).resolve()
        if not target.is_relative_to(ws_dir):
            raise HTTPException(
                status_code=400,
                detail="Invalid path: escapes workspace sandbox",
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        # Companion RHP write
        rhp_target: Optional[Path] = None
        if clean_path.endswith(".php") or is_rhp:
            rhp_rel = clean_path if clean_path.endswith(".rhp") else (clean_path[:-4] + ".rhp")
            rhp_target = (ws_dir / rhp_rel).resolve()
            if rhp_target.is_relative_to(ws_dir):
                rhp_target.parent.mkdir(parents=True, exist_ok=True)
                rhp_target.write_text(content, encoding="utf-8")

        # Update .migration_state.json
        state_file = ws_dir / ".migration_state.json"
        current_state: dict[str, Any] = {}
        if state_file.is_file():
            try:
                current_state = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        current_state.setdefault("migrated_files", {})[clean_path] = content
        if status:
            current_state.setdefault("file_statuses", {})[clean_path] = status
        current_state["last_updated"] = datetime.now(timezone.utc).isoformat()
        state_file.write_text(json.dumps(current_state, indent=2), encoding="utf-8")

        generate_workspace_csv(ws_dir)
        record_last_session(workspace_id, ws_dir)

        return target, rhp_target, len(content.encode("utf-8"))

    def get_state(self, workspace_id: str) -> dict[str, Any]:
        """Load saved migration state, ensuring CSV exists and recording session."""
        ws_dir = self.get_workspace_path(workspace_id)
        state_file = ws_dir / ".migration_state.json"
        state: dict[str, Any] = {
            "workspace_id": workspace_id,
            "active_file": None,
            "migrated_files": {},
            "file_statuses": {},
            "scorecards": {},
            "compiler_logs": [],
            "chat_messages": [],
        }
        if state_file.is_file():
            try:
                loaded = json.loads(state_file.read_text(encoding="utf-8"))
                state.update(loaded)
            except Exception as exc:
                logger.warning("Error parsing state file: %s", exc)

        csv_path, _, summary = generate_workspace_csv(ws_dir)
        record_last_session(workspace_id, ws_dir)

        return {
            "workspace_id": workspace_id,
            "active_file": state.get("active_file"),
            "migrated_files": state.get("migrated_files", {}),
            "file_statuses": state.get("file_statuses", {}),
            "scorecards": state.get("scorecards", {}),
            "compiler_logs": state.get("compiler_logs", []),
            "chat_messages": state.get("chat_messages", []),
            "csv_path": str(csv_path),
            "completed_count": summary.completed_count,
            "needs_review_count": summary.needs_review_count,
            "pending_count": summary.pending_count,
            "last_updated": state.get("last_updated"),
        }

    def save_state(self, workspace_id: str, state_req: WorkspaceStateSaveRequest) -> dict[str, Any]:
        """Save migration state, regenerate CSV, and record session."""
        ws_dir = self.get_workspace_path(workspace_id)
        state_file = ws_dir / ".migration_state.json"
        current_state: dict[str, Any] = {}
        if state_file.is_file():
            try:
                current_state = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        if state_req.active_file is not None:
            current_state["active_file"] = state_req.active_file
        if state_req.migrated_files:
            current_state.setdefault("migrated_files", {}).update(state_req.migrated_files)
        if state_req.file_statuses:
            current_state.setdefault("file_statuses", {}).update(state_req.file_statuses)
        if state_req.scorecards:
            current_state.setdefault("scorecards", {}).update(state_req.scorecards)
        if state_req.compiler_logs is not None:
            current_state["compiler_logs"] = state_req.compiler_logs
        if state_req.chat_messages is not None:
            current_state["chat_messages"] = state_req.chat_messages

        current_state["last_updated"] = datetime.now(timezone.utc).isoformat()
        state_file.write_text(json.dumps(current_state, indent=2), encoding="utf-8")

        csv_path, _, summary = generate_workspace_csv(ws_dir)
        record_last_session(workspace_id, ws_dir)

        return {
            "workspace_id": workspace_id,
            "active_file": current_state.get("active_file"),
            "migrated_files": current_state.get("migrated_files", {}),
            "file_statuses": current_state.get("file_statuses", {}),
            "scorecards": current_state.get("scorecards", {}),
            "compiler_logs": current_state.get("compiler_logs", []),
            "chat_messages": current_state.get("chat_messages", []),
            "csv_path": str(csv_path),
            "completed_count": summary.completed_count,
            "needs_review_count": summary.needs_review_count,
            "pending_count": summary.pending_count,
            "last_updated": current_state.get("last_updated"),
        }

    def list_all_projects(self) -> list[ProjectSummaryItem]:
        """List all projects kept in the persistent software directory."""
        projects: list[ProjectSummaryItem] = []
        if not self.base_dir.is_dir():
            return projects

        for item in sorted(self.base_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if not item.is_dir():
                continue
            wid = item.name
            meta_file = item / ".workspace_meta.json"
            name = wid
            created_at = None
            if meta_file.is_file():
                try:
                    mdata = json.loads(meta_file.read_text(encoding="utf-8"))
                    name = mdata.get("name") or mdata.get("repo_url") or wid
                    created_at = mdata.get("created_at")
                except Exception:
                    pass

            csv_path = item / "migration_status.csv"
            completed = 0
            needs_review = 0
            file_count = 0
            if csv_path.is_file():
                try:
                    with open(csv_path, mode="r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        for r in reader:
                            file_count += 1
                            st = r.get("status", "")
                            if st == "completed":
                                completed += 1
                            elif st == "needs_review":
                                needs_review += 1
                except Exception:
                    pass
            else:
                try:
                    _, file_count = build_file_tree(item)
                except Exception:
                    pass

            projects.append(
                ProjectSummaryItem(
                    workspace_id=wid,
                    project_name=name,
                    workspace_path=str(item),
                    created_at=created_at,
                    last_opened_at=item.stat().st_mtime if item.exists() else None,
                    file_count=file_count,
                    completed_count=completed,
                    needs_review_count=needs_review,
                )
            )
        return projects

    def delete_workspace(self, workspace_id: str) -> bool:
        """Explicitly delete a workspace directory."""
        ws_dir = self.get_workspace_path(workspace_id)
        shutil.rmtree(ws_dir, ignore_errors=True)
        return True


# Global default manager instance
workspace_manager = WorkspaceManager()


# ---------------------------------------------------------------------------
# FastAPI Router Definitions
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/workspace", tags=["Workspace"])


@router.post("/upload-zip", response_model=WorkspaceResponse)
async def upload_zip_endpoint(
    request: Request,
    file: Optional[UploadFile] = File(None),
    archive: Optional[UploadFile] = File(None),
) -> WorkspaceResponse:
    """Accept a .zip file upload, validate against Zip-Slip, and unpack into sandbox."""
    upload = file or archive

    # Fallback to inspect form fields if named differently
    if upload is None:
        try:
            form = await request.form()
            for value in form.values():
                if hasattr(value, "filename") and hasattr(value, "read"):
                    upload = value
                    break
        except Exception as exc:
            logger.warning("Error inspecting multipart form fields: %s", exc)

    if upload is None:
        logger.warning("Zip upload rejected: no archive file detected in multipart request")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No archive file uploaded. Please provide a multipart .zip file.",
        )

    filename = getattr(upload, "filename", "unnamed.zip")
    logger.info("Processing zip upload: filename='%s'", filename)

    content_bytes = await upload.read()
    if not content_bytes:
        logger.warning("Zip upload rejected: '%s' is empty (0 bytes)", filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    logger.info("Read %d bytes for archive '%s'", len(content_bytes), filename)

    # Validate that it is a valid zip archive
    if not zipfile.is_zipfile(io.BytesIO(content_bytes)):
        logger.warning("Zip upload rejected: '%s' is not a valid zip archive magic header", filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid zip archive.",
        )

    # Create isolated workspace
    try:
        workspace_id, workspace_path = workspace_manager.create_workspace()
        logger.info("Allocated sandbox workspace '%s' at %s for upload '%s'", workspace_id, workspace_path, filename)
    except Exception as exc:
        logger.exception("Failed to allocate sandbox workspace for upload '%s': %s", filename, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize workspace: {exc}",
        )

    # Unpack with Zip-Slip protection
    try:
        extracted_files = workspace_manager.unpack_zip(workspace_id, content_bytes)
        logger.info("Unpacked %d files from '%s' into workspace '%s'", len(extracted_files), filename, workspace_id)
    except ZipSlipError as exc:
        logger.warning("Zip-Slip security rejection for '%s' in workspace '%s': %s", filename, workspace_id, exc)
        # Clean up workspace immediately on security violation
        shutil.rmtree(workspace_path, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security error (Zip-Slip detected): {exc}",
        )
    except Exception as exc:
        logger.exception("Unexpected error extracting '%s' into workspace '%s': %s", filename, workspace_id, exc)
        shutil.rmtree(workspace_path, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract archive: {exc}",
        )

    return WorkspaceResponse(
        workspace_id=workspace_id,
        workspace_path=str(workspace_path),
        file_count=len(extracted_files),
        message=f"Archive successfully unpacked into workspace ({len(extracted_files)} files).",
    )


@router.post("/clone-github", response_model=WorkspaceResponse)
async def clone_github_endpoint(
    req: CloneGithubRequest,
) -> WorkspaceResponse:
    """Accept repo_url, branch, and optional github_token, and perform a shallow clone."""
    if not req.repo_url or not req.repo_url.strip():
        logger.warning("GitHub clone rejected: empty repo_url")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="repo_url cannot be empty.",
        )

    logger.info(
        "Processing GitHub clone request: repo_url='%s', branch='%s', auth=%s",
        req.repo_url,
        req.branch or "default",
        "token_provided" if req.github_token else "none",
    )

    try:
        workspace_id, workspace_path = workspace_manager.create_workspace()
        logger.info("Allocated sandbox workspace '%s' at %s for clone", workspace_id, workspace_path)
    except Exception as exc:
        logger.exception("Failed to allocate workspace for clone: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize workspace: {exc}",
        )

    try:
        workspace_manager.clone_repository(
            workspace_id=workspace_id,
            repo_url=req.repo_url,
            branch=req.branch,
            github_token=req.github_token,
        )
        logger.info("Successfully cloned '%s' into workspace '%s'", req.repo_url, workspace_id)
    except GitCloneError as exc:
        logger.warning("Git clone failed for repo '%s' in workspace '%s': %s", req.repo_url, workspace_id, exc)
        shutil.rmtree(workspace_path, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected error during git clone for '%s' in workspace '%s': %s", req.repo_url, workspace_id, exc)
        shutil.rmtree(workspace_path, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during clone: {exc}",
        )

    # Count files in cloned workspace
    _, file_count = build_file_tree(workspace_path)
    logger.info("Workspace '%s' ready with %d files", workspace_id, file_count)

    return WorkspaceResponse(
        workspace_id=workspace_id,
        workspace_path=str(workspace_path),
        file_count=file_count,
        message="Repository cloned successfully into sandbox workspace.",
    )


@router.get("/{workspace_id}/tree", response_model=WorkspaceTreeResponse)
async def get_workspace_tree_endpoint(
    workspace_id: str,
) -> WorkspaceTreeResponse:
    """Return the file hierarchy with vendor/, node_modules/, .git/, .env filtered out."""
    logger.info("Fetching file tree for workspace '%s'", workspace_id)
    tree_resp = workspace_manager.get_tree(workspace_id)
    logger.info("Workspace '%s' tree returned %d files", workspace_id, tree_resp.total_files)
    return tree_resp


@router.get("/{workspace_id}/file", response_model=WorkspaceFileContentResponse)
async def get_workspace_file_endpoint(
    workspace_id: str,
    path: str = Query(..., description="Relative path of file inside workspace"),
    request: Request = None,
) -> Any:
    """Return the raw contents of a specific legacy PHP file for rendering in the editor."""
    logger.info("Reading workspace file: workspace_id='%s', path='%s'", workspace_id, path)
    file_info = workspace_manager.get_file_content(workspace_id, path)
    logger.info("Successfully read file '%s' (%d bytes) for workspace '%s'", path, file_info.size, workspace_id)

    # Support raw text response if requested via Accept header
    if request and "text/plain" in request.headers.get("accept", ""):
        return PlainTextResponse(content=file_info.content, media_type="text/plain")

    return file_info


@router.post("/cleanup", response_model=WorkspaceCleanupResponse)
async def cleanup_workspaces_endpoint(
    max_age_seconds: int = Query(
        WORKSPACE_TTL_SECONDS,
        description="Threshold age in seconds for workspace eviction (default 7200 = 2 hours)",
    ),
) -> WorkspaceCleanupResponse:
    """Trigger on-demand cleanup of stale workspaces older than TTL."""
    cleaned = cleanup_old_workspaces(max_age_seconds=max_age_seconds)
    return WorkspaceCleanupResponse(
        cleaned_count=cleaned,
        message=f"Cleaned up {cleaned} expired workspace(s).",
    )


@router.delete("/{workspace_id}", response_model=WorkspaceCleanupResponse)
async def delete_workspace_endpoint(
    workspace_id: str,
) -> WorkspaceCleanupResponse:
    """Manually delete a specific workspace."""
    workspace_manager.delete_workspace(workspace_id)
    return WorkspaceCleanupResponse(
        cleaned_count=1,
        message=f"Workspace '{workspace_id}' deleted successfully.",
    )


@router.get("/{workspace_id}/manifest", response_model=ManifestStatusResponse)
async def get_workspace_manifest_endpoint(workspace_id: str) -> ManifestStatusResponse:
    """Check if rhp.toml exists at workspace root and inspect its configuration."""
    ws_dir = workspace_manager.get_workspace_path(workspace_id)
    manifest_path = ws_dir / "rhp.toml"
    has_manifest_flag = manifest_path.is_file()
    content: Optional[str] = None
    package_name: Optional[str] = None
    psr4: dict[str, str] = {}

    if has_manifest_flag:
        content = manifest_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line_str = line.strip()
            if line_str.startswith("name ="):
                package_name = line_str.split("=", 1)[1].strip().strip('"\'')
    else:
        comp_path = ws_dir / "composer.json"
        if comp_path.is_file():
            try:
                cdata = json.loads(comp_path.read_text(encoding="utf-8"))
                package_name = cdata.get("name")
                if "autoload" in cdata and "psr-4" in cdata["autoload"]:
                    psr4 = cdata["autoload"]["psr-4"]
            except Exception:
                pass

    return ManifestStatusResponse(
        workspace_id=workspace_id,
        has_manifest=has_manifest_flag,
        manifest_path=str(manifest_path),
        content=content,
        package_name=package_name or ws_dir.name or "app",
        psr4=psr4,
    )


@router.post("/{workspace_id}/manifest/init", response_model=ManifestStatusResponse)
async def init_workspace_manifest_endpoint(
    workspace_id: str,
    package_name: Optional[str] = Query(None, description="Optional package name override"),
) -> ManifestStatusResponse:
    """Initialize a standard starter rhp.toml manifest at workspace root."""
    ws_dir = workspace_manager.get_workspace_path(workspace_id)
    manifest_path = create_default_rhp_toml(ws_dir, package_name=package_name)
    content = manifest_path.read_text(encoding="utf-8")

    resolved_pkg_name = package_name
    for line in content.splitlines():
        line_str = line.strip()
        if line_str.startswith("name ="):
            resolved_pkg_name = line_str.split("=", 1)[1].strip().strip('"\'')

    return ManifestStatusResponse(
        workspace_id=workspace_id,
        has_manifest=True,
        manifest_path=str(manifest_path),
        content=content,
        package_name=resolved_pkg_name or ws_dir.name or "app",
    )


@router.get("/software-info", response_model=SoftwareInfoResponse)
async def get_software_info_endpoint() -> SoftwareInfoResponse:
    """Return software directory paths and platform information."""
    s_dir = get_software_data_dir()
    p_dir = get_workspaces_root()
    return SoftwareInfoResponse(
        software_dir=str(s_dir),
        projects_dir=str(p_dir),
        platform=sys.platform,
        description="Persistent software workspace storage directory for rhp-migrate",
    )


@router.get("/last-opened", response_model=LastSessionResponse)
async def get_last_opened_endpoint() -> LastSessionResponse:
    """Return the last opened project workspace information to restore open session."""
    return get_last_session()


@router.post("/last-opened", response_model=LastSessionResponse)
async def set_last_opened_endpoint(payload: dict[str, str]) -> LastSessionResponse:
    """Explicitly update the last opened workspace."""
    wid = payload.get("workspace_id", "")
    if wid:
        ws_dir = workspace_manager.get_workspace_path(wid)
        record_last_session(wid, ws_dir, payload.get("project_name"))
    return get_last_session()


@router.get("/list", response_model=ProjectListResponse)
async def list_projects_endpoint() -> ProjectListResponse:
    """Return list of all existing projects saved in the software directory."""
    projects = workspace_manager.list_all_projects()
    return ProjectListResponse(projects=projects, total_projects=len(projects))


@router.get("/{workspace_id}/state", response_model=WorkspaceStateResponse)
async def get_workspace_state_endpoint(workspace_id: str) -> WorkspaceStateResponse:
    """Load complete saved migration state for the workspace."""
    state = workspace_manager.get_state(workspace_id)
    return WorkspaceStateResponse(**state)


@router.post("/{workspace_id}/state", response_model=WorkspaceStateResponse)
async def save_workspace_state_endpoint(
    workspace_id: str,
    req: WorkspaceStateSaveRequest,
) -> WorkspaceStateResponse:
    """Persist complete migration state for the workspace."""
    state = workspace_manager.save_state(workspace_id, req)
    return WorkspaceStateResponse(**state)


@router.post("/{workspace_id}/file", response_model=WorkspaceSaveFileResponse)
async def save_workspace_file_endpoint(
    workspace_id: str,
    req: WorkspaceSaveFileRequest,
) -> WorkspaceSaveFileResponse:
    """Save updated/migrated file content to the project directory and regenerate CSV."""
    target, rhp_target, size = workspace_manager.save_file(
        workspace_id=workspace_id,
        rel_path=req.path,
        content=req.content,
        status=req.status,
        is_rhp=req.is_rhp,
    )
    ws_dir = workspace_manager.get_workspace_path(workspace_id)
    rel_path = str(target.relative_to(ws_dir)).replace("\\", "/")
    rhp_rel = str(rhp_target.relative_to(ws_dir)).replace("\\", "/") if rhp_target else None

    return WorkspaceSaveFileResponse(
        success=True,
        path=rel_path,
        rhp_path=rhp_rel,
        size=size,
        message=f"File '{rel_path}' saved successfully to project software directory.",
    )


@router.get("/{workspace_id}/csv")
async def get_workspace_csv_endpoint(
    workspace_id: str,
    download: bool = Query(False, description="Set true to download file as attachment"),
    request: Request = None,
) -> Any:
    """Fetch or download the migration_status.csv containing all project file information."""
    ws_dir = workspace_manager.get_workspace_path(workspace_id)
    csv_path, csv_content, summary = generate_workspace_csv(ws_dir)

    is_csv_requested = download or (request and "text/csv" in request.headers.get("accept", ""))
    if is_csv_requested:
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="migration_status_{workspace_id}.csv"',
                "Content-Type": "text/csv; charset=utf-8",
            },
        )

    return CsvStatusResponse(
        workspace_id=workspace_id,
        csv_path=str(csv_path),
        csv_content=csv_content,
        summary=summary,
    )


@router.post("/{workspace_id}/csv/generate", response_model=CsvStatusResponse)
async def generate_workspace_csv_endpoint(workspace_id: str) -> CsvStatusResponse:
    """Regenerate and return the migration_status.csv for the workspace."""
    ws_dir = workspace_manager.get_workspace_path(workspace_id)
    csv_path, csv_content, summary = generate_workspace_csv(ws_dir)
    return CsvStatusResponse(
        workspace_id=workspace_id,
        csv_path=str(csv_path),
        csv_content=csv_content,
        summary=summary,
    )


