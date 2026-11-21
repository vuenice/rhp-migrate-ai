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
import urllib.parse
import uuid
import zipfile

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import PlainTextResponse

from rhp_migrate.server.models import (
    CloneGithubRequest,
    FileTreeNode,
    WorkspaceCleanupResponse,
    WorkspaceFileContentResponse,
    WorkspaceResponse,
    WorkspaceTreeResponse,
)

logger = logging.getLogger(__name__)

# Default base directory for sandboxed workspaces
DEFAULT_WORKSPACES_ROOT = Path("/tmp/rhp_workspaces")

# Default TTL: 2 hours (in seconds)
WORKSPACE_TTL_SECONDS = 7200

# Directories and files to exclude from tree navigation
IGNORED_DIRS = {"vendor", "node_modules", ".git"}
IGNORED_FILES = {".env", ".workspace_meta.json"}


# Allowed pattern for workspace IDs to prevent traversal in path parameters
WORKSPACE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class ZipSlipError(ValueError):
    """Raised when an archive member contains directory traversal paths."""
    pass


class GitCloneError(Exception):
    """Raised when git shallow clone fails."""
    pass


def get_workspaces_root() -> Path:
    """Return the base directory for sandboxed workspaces.

    Configurable via the RHP_WORKSPACES_DIR environment variable.
    """
    env_dir = os.environ.get("RHP_WORKSPACES_DIR")
    if env_dir:
        root = Path(env_dir).resolve()
    else:
        root = DEFAULT_WORKSPACES_ROOT.resolve()
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

    with zip_obj as zf:
        infolist = zf.infolist()
        if not infolist:
            return []

        # 1. Validation pass: strictly check all members before extracting anything
        for member in infolist:
            filename = member.filename
            if not filename:
                continue

            # Reject absolute paths (POSIX / or Windows C:\)
            if filename.startswith(("/", "\\")) or (len(filename) > 1 and filename[1] == ":"):
                raise ZipSlipError(f"Absolute path in zip member forbidden: {filename}")

            # Reject explicit parent directory traversal segments
            parts = Path(filename).parts
            if ".." in parts:
                raise ZipSlipError(f"Path traversal '..' detected in zip member: {filename}")

            # Verify resolved target is within destination
            target_path = (dest_dir / filename).resolve()
            try:
                target_path.relative_to(dest_dir)
            except ValueError:
                raise ZipSlipError(f"Zip-slip detected: member '{filename}' escapes target workspace")

            # Check for symlink escaping target
            mode = member.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                link_content = zf.read(member).decode("utf-8", errors="replace").strip()
                if link_content.startswith(("/", "\\")) or (len(link_content) > 1 and link_content[1] == ":"):
                    raise ZipSlipError(f"Absolute symlink target in zip forbidden: {link_content}")
                resolved_link = (target_path.parent / link_content).resolve()
                try:
                    resolved_link.relative_to(dest_dir)
                except ValueError:
                    raise ZipSlipError(f"Symlink '{filename}' -> '{link_content}' escapes target workspace")

        # 2. Safe extraction pass
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
        raise GitCloneError("Invalid repository URL: cannot start with a dash")

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
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise GitCloneError(f"Git clone timed out after {timeout} seconds") from exc
    except Exception as exc:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise GitCloneError(f"Failed to execute git clone: {exc}") from exc

    if proc.returncode != 0:
        error_msg = proc.stderr.strip() or proc.stdout.strip()
        if github_token:
            error_msg = error_msg.replace(github_token, "******")
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise GitCloneError(f"Git clone failed (exit {proc.returncode}): {error_msg}")

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


class WorkspaceManager:
    """Manages creation, navigation, and lifecycle for sandboxed project workspaces."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = (base_dir or get_workspaces_root()).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, workspace_id: Optional[str] = None) -> tuple[str, Path]:
        """Create a new unique workspace directory and record metadata."""
        # Opportunistic cleanup of stale workspaces
        try:
            cleanup_old_workspaces(self.base_dir)
        except Exception:
            pass

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
        return validate_and_extract_zip(zip_source, ws_dir)

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
        # Update metadata timestamp after clone
        meta_file = ws_dir / ".workspace_meta.json"
        meta = {
            "workspace_id": workspace_id,
            "created_at": time.time(),
            "repo_url": repo_url,
            "branch": branch,
        }
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def get_tree(self, workspace_id: str) -> WorkspaceTreeResponse:
        """Generate the filtered file tree for the workspace."""
        ws_dir = self.get_workspace_path(workspace_id)
        tree, total_files = build_file_tree(ws_dir)
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
        except Exception:
            pass

    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No archive file uploaded. Please provide a multipart .zip file.",
        )

    content_bytes = await upload.read()
    if not content_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # Validate that it is a valid zip archive
    if not zipfile.is_zipfile(io.BytesIO(content_bytes)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid zip archive.",
        )

    # Create isolated workspace
    try:
        workspace_id, workspace_path = workspace_manager.create_workspace()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize workspace: {exc}",
        )

    # Unpack with Zip-Slip protection
    try:
        extracted_files = workspace_manager.unpack_zip(workspace_id, content_bytes)
    except ZipSlipError as exc:
        # Clean up workspace immediately on security violation
        shutil.rmtree(workspace_path, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security error (Zip-Slip detected): {exc}",
        )
    except Exception as exc:
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="repo_url cannot be empty.",
        )

    try:
        workspace_id, workspace_path = workspace_manager.create_workspace()
    except Exception as exc:
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
    except GitCloneError as exc:
        shutil.rmtree(workspace_path, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        shutil.rmtree(workspace_path, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during clone: {exc}",
        )

    # Count files in cloned workspace
    _, file_count = build_file_tree(workspace_path)

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
    return workspace_manager.get_tree(workspace_id)


@router.get("/{workspace_id}/file", response_model=WorkspaceFileContentResponse)
async def get_workspace_file_endpoint(
    workspace_id: str,
    path: str = Query(..., description="Relative path of file inside workspace"),
    request: Request = None,
) -> Any:
    """Return the raw contents of a specific legacy PHP file for rendering in the editor."""
    file_info = workspace_manager.get_file_content(workspace_id, path)

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
