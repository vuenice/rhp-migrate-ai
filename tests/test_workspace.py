"""Comprehensive test suite for workspace ingestion, safe extraction, git cloning, and tree navigation."""

from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import time
import zipfile
import pytest
from fastapi.testclient import TestClient

from rhp_migrate.server.main import app
from rhp_migrate.server.workspace import (
    ZipSlipError,
    cleanup_old_workspaces,
    classify_php_file,
    get_workspaces_root,
    shallow_clone_repo,
    validate_and_extract_zip,
    workspace_manager,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_in_memory_zip(file_dict: dict[str, str]) -> bytes:
    """Create a valid zip archive in memory from a dict of relative_path -> content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in file_dict.items():
            zf.writestr(path, content)
    return buf.getvalue()


def create_zip_slip_archive(traversal_path: str = "../../evil.php") -> bytes:
    """Create an archive with an explicit Zip-Slip directory traversal member."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(traversal_path, "<?php echo 'pwned'; ?>")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Unit Tests: PHP Component Classification
# ---------------------------------------------------------------------------

def test_classify_php_components():
    """Verify classification of Controllers, Models, Services, Migrations, and Blade views."""
    # Blade views
    assert classify_php_file("resources/views/welcome.blade.php") == "Blade view"
    assert classify_php_file("views/auth/login.blade.php") == "Blade view"
    assert classify_php_file("resources/views/header.php") == "Blade view"
    assert (
        classify_php_file("custom/template.php", content="@extends('layout')\n@section('content')")
        == "Blade view"
    )

    # Migrations
    assert (
        classify_php_file("database/migrations/2024_01_01_000000_create_users_table.php")
        == "Migration"
    )
    assert (
        classify_php_file("database/migrations/add_column.php")
        == "Migration"
    )
    assert (
        classify_php_file(
            "custom/schema.php",
            content="<?php use Illuminate\\Database\\Migrations\\Migration;\nclass CreateUsersTable extends Migration {}",
        )
        == "Migration"
    )

    # Controllers
    assert classify_php_file("app/Http/Controllers/UserController.php") == "Controller"
    assert classify_php_file("app/Controllers/AuthController.php") == "Controller"
    assert (
        classify_php_file("legacy/handler.php", content="<?php class HandlerController extends BaseController {}")
        == "Controller"
    )

    # Services
    assert classify_php_file("app/Services/PaymentService.php") == "Service"
    assert (
        classify_php_file("app/Billing/StripeService.php", content="<?php class StripeService {}")
        == "Service"
    )

    # Models
    assert classify_php_file("app/Models/User.php") == "Model"
    assert classify_php_file("app/Models/Order.php") == "Model"
    assert (
        classify_php_file(
            "app/User.php",
            content="<?php namespace App;\nuse Illuminate\\Database\\Eloquent\\Model;\nclass User extends Model {}",
        )
        == "Model"
    )

    # Non-PHP or unclassified
    assert classify_php_file("package.json") is None
    assert classify_php_file("README.md") is None
    assert classify_php_file("routes/web.php", content="<?php Route::get('/', function() {});") is None


# ---------------------------------------------------------------------------
# Unit Tests: Zip Validation and Zip-Slip Prevention
# ---------------------------------------------------------------------------

def test_validate_and_extract_valid_zip(tmp_path):
    """Test extracting a standard valid zip archive."""
    zip_bytes = create_in_memory_zip({
        "app/Http/Controllers/UserController.php": "<?php class UserController {}",
        "config/app.php": "<?php return [];",
    })
    target_dir = tmp_path / "workspace_valid"
    extracted = validate_and_extract_zip(zip_bytes, target_dir)

    assert len(extracted) == 2
    assert (target_dir / "app/Http/Controllers/UserController.php").is_file()
    assert (target_dir / "config/app.php").is_file()


def test_zip_slip_traversal_detection(tmp_path):
    """Verify ZipSlipError is raised when an archive attempts directory traversal."""
    bad_zip = create_zip_slip_archive("../../evil.php")
    target_dir = tmp_path / "workspace_slip"

    with pytest.raises(ZipSlipError, match="Zip-slip detected|detected in zip member"):
        validate_and_extract_zip(bad_zip, target_dir)

    # Verify no files escaped to tmp_path or were created outside target_dir
    assert not (tmp_path / "evil.php").exists()


def test_zip_slip_absolute_path_detection(tmp_path):
    """Verify ZipSlipError is raised for absolute paths inside zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/tmp/escape.php", "<?php")
    bad_zip = buf.getvalue()

    target_dir = tmp_path / "workspace_abs"
    with pytest.raises(ZipSlipError, match="Absolute path"):
        validate_and_extract_zip(bad_zip, target_dir)


# ---------------------------------------------------------------------------
# API Tests: POST /api/workspace/upload-zip
# ---------------------------------------------------------------------------

def test_api_upload_zip_success(monkeypatch, tmp_path):
    """Test successful multipart zip upload and extraction via API."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir
    workspaces_dir.mkdir(parents=True, exist_ok=True)

    zip_bytes = create_in_memory_zip({
        "app/Http/Controllers/UserController.php": "<?php class UserController extends Controller {}",
        "app/Models/User.php": "<?php class User extends Model {}",
        "app/Services/UserService.php": "<?php class UserService {}",
        "database/migrations/2024_01_01_000000_create_users_table.php": "<?php class CreateUsersTable extends Migration {}",
        "resources/views/welcome.blade.php": "<h1>Welcome</h1>",
        "vendor/autoload.php": "<?php // vendor file",
        ".env": "APP_KEY=secret",
    })

    files = {"file": ("project.zip", zip_bytes, "application/zip")}
    resp = client.post("/api/workspace/upload-zip", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert "workspace_id" in data
    assert data["file_count"] >= 5
    assert "successfully unpacked" in data["message"].lower()

    ws_id = data["workspace_id"]
    ws_path = Path(data["workspace_path"])
    assert ws_path.is_dir()
    assert (ws_path / "app/Http/Controllers/UserController.php").is_file()


def test_api_upload_zip_slip_rejected(monkeypatch, tmp_path):
    """Test that a malicious zip with traversal is blocked by the API with HTTP 400."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir

    bad_zip = create_zip_slip_archive("../../evil.php")
    files = {"file": ("malicious.zip", bad_zip, "application/zip")}
    resp = client.post("/api/workspace/upload-zip", files=files)
    assert resp.status_code == 400
    assert "Zip-Slip" in resp.json()["detail"] or "Security error" in resp.json()["detail"]


def test_api_upload_zip_invalid_file(monkeypatch, tmp_path):
    """Test uploading a non-zip file returns 400."""
    files = {"file": ("test.txt", b"plain text content", "text/plain")}
    resp = client.post("/api/workspace/upload-zip", files=files)
    assert resp.status_code == 400
    assert "not a valid zip" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# API Tests: Tree Navigation & PHP Classification
# ---------------------------------------------------------------------------

def test_api_workspace_tree_and_classification(monkeypatch, tmp_path):
    """Verify tree response filters vendor/, node_modules/, .git/, .env and classifies components."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir

    zip_bytes = create_in_memory_zip({
        "app/Http/Controllers/UserController.php": "<?php class UserController extends Controller {}",
        "app/Models/User.php": "<?php class User extends Model {}",
        "app/Services/UserService.php": "<?php class UserService {}",
        "database/migrations/2024_01_01_000000_create_users_table.php": "<?php class CreateUsersTable extends Migration {}",
        "resources/views/welcome.blade.php": "<h1>Welcome</h1>",
        "vendor/autoload.php": "<?php // vendor",
        "node_modules/package/index.js": "// js",
        ".git/config": "[core]",
        ".env": "APP_SECRET=12345",
        ".env.example": "APP_SECRET=",
    })

    # 1. Upload
    upload_resp = client.post(
        "/api/workspace/upload-zip",
        files={"file": ("project.zip", zip_bytes, "application/zip")},
    )
    assert upload_resp.status_code == 200
    ws_id = upload_resp.json()["workspace_id"]

    # 2. Get Tree
    tree_resp = client.get(f"/api/workspace/{ws_id}/tree")
    assert tree_resp.status_code == 200
    tree_data = tree_resp.json()
    assert tree_data["workspace_id"] == ws_id
    assert tree_data["total_files"] == 5  # vendor, node_modules, .git, .env, .env.example excluded!

    # Helper to flatten tree into dict of path -> node
    flat_nodes = {}
    def collect_nodes(nodes):
        for n in nodes:
            flat_nodes[n["path"]] = n
            if n.get("children"):
                collect_nodes(n["children"])
    collect_nodes(tree_data["tree"])

    # Verify exclusions
    assert "vendor" not in flat_nodes
    assert "node_modules" not in flat_nodes
    assert ".git" not in flat_nodes
    assert ".env" not in flat_nodes
    assert ".env.example" not in flat_nodes

    # Verify PHP Classifications
    user_ctrl = flat_nodes["app/Http/Controllers/UserController.php"]
    assert user_ctrl["type"] == "file"
    assert user_ctrl["classification"] == "Controller"

    user_model = flat_nodes["app/Models/User.php"]
    assert user_model["type"] == "file"
    assert user_model["classification"] == "Model"

    user_svc = flat_nodes["app/Services/UserService.php"]
    assert user_svc["type"] == "file"
    assert user_svc["classification"] == "Service"

    migration = flat_nodes["database/migrations/2024_01_01_000000_create_users_table.php"]
    assert migration["type"] == "file"
    assert migration["classification"] == "Migration"

    blade_view = flat_nodes["resources/views/welcome.blade.php"]
    assert blade_view["type"] == "file"
    assert blade_view["classification"] == "Blade view"


def test_api_workspace_tree_not_found():
    """Verify 404 for a non-existent workspace ID."""
    resp = client.get("/api/workspace/nonexistent-uuid-1234/tree")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API Tests: GET /api/workspace/{workspace_id}/file
# ---------------------------------------------------------------------------

def test_api_workspace_get_file_content(monkeypatch, tmp_path):
    """Verify reading file contents and path traversal defenses."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir

    raw_php = "<?php\n\nnamespace App\\Http\\Controllers;\n\nclass UserController {}\n"
    zip_bytes = create_in_memory_zip({
        "app/Http/Controllers/UserController.php": raw_php,
    })

    upload_resp = client.post(
        "/api/workspace/upload-zip",
        files={"file": ("project.zip", zip_bytes, "application/zip")},
    )
    ws_id = upload_resp.json()["workspace_id"]

    # 1. Fetch file content (JSON)
    file_resp = client.get(
        f"/api/workspace/{ws_id}/file",
        params={"path": "app/Http/Controllers/UserController.php"},
    )
    assert file_resp.status_code == 200
    file_data = file_resp.json()
    assert file_data["workspace_id"] == ws_id
    assert file_data["path"] == "app/Http/Controllers/UserController.php"
    assert file_data["content"] == raw_php
    assert file_data["classification"] == "Controller"
    assert file_data["size"] == len(raw_php.encode("utf-8"))

    # 2. Fetch file content with text/plain Accept header
    plain_resp = client.get(
        f"/api/workspace/{ws_id}/file",
        params={"path": "app/Http/Controllers/UserController.php"},
        headers={"Accept": "text/plain"},
    )
    assert plain_resp.status_code == 200
    assert plain_resp.text == raw_php

    # 3. Path traversal attack attempt
    trav_resp = client.get(
        f"/api/workspace/{ws_id}/file",
        params={"path": "../../etc/passwd"},
    )
    assert trav_resp.status_code == 400
    assert "directory traversal" in trav_resp.json()["detail"].lower()

    # 4. Non-existent file
    missing_resp = client.get(
        f"/api/workspace/{ws_id}/file",
        params={"path": "does/not/exist.php"},
    )
    assert missing_resp.status_code == 404

    # 5. Directory path instead of file
    dir_resp = client.get(
        f"/api/workspace/{ws_id}/file",
        params={"path": "app/Http/Controllers"},
    )
    assert dir_resp.status_code == 400
    assert "directory" in dir_resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# API Tests: POST /api/workspace/clone-github
# ---------------------------------------------------------------------------

def test_api_clone_github_local_repo(monkeypatch, tmp_path):
    """Test shallow git cloning from a real local git repository."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir

    # Create a local source git repository
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(source_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(source_repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(source_repo), check=True)

    # Add sample files
    ctrl_dir = source_repo / "app" / "Http" / "Controllers"
    ctrl_dir.mkdir(parents=True)
    (ctrl_dir / "PostController.php").write_text("<?php class PostController extends Controller {}", encoding="utf-8")
    (source_repo / ".env").write_text("DB_PASS=secret", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=str(source_repo), check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(source_repo), check=True)

    # Clone via API using file:// protocol
    payload = {
        "repo_url": f"file://{source_repo.resolve()}",
    }
    resp = client.post("/api/workspace/clone-github", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "workspace_id" in data
    assert data["file_count"] == 1  # PostController.php (.env is filtered out)

    ws_id = data["workspace_id"]
    tree_resp = client.get(f"/api/workspace/{ws_id}/tree")
    assert tree_resp.status_code == 200
    assert tree_resp.json()["total_files"] == 1


def test_api_clone_github_invalid_url():
    """Verify that malformed or flag-injection URLs are rejected."""
    resp = client.post("/api/workspace/clone-github", json={"repo_url": "--upload-pack=evil"})
    assert resp.status_code == 400


def test_api_clone_github_token_sanitization(monkeypatch, tmp_path):
    """Verify that github_token is never leaked in error messages if clone fails."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir

    secret_token = "ghp_super_secret_personal_access_token_12345"
    payload = {
        "repo_url": "https://github.com/nonexistent-org-xyz-987/nonexistent-repo-12345.git",
        "github_token": secret_token,
    }
    resp = client.post("/api/workspace/clone-github", json=payload)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert secret_token not in detail
    assert "******" in detail or "failed" in detail.lower()


# ---------------------------------------------------------------------------
# Unit & API Tests: Cleanup & TTL Lifecycle
# ---------------------------------------------------------------------------

def test_workspace_ttl_cleanup(monkeypatch, tmp_path):
    """Verify workspaces older than 2 hours are pruned, and newer ones are kept."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir
    workspaces_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create a "stale" workspace (older than 2 hours = 7200 seconds)
    stale_dir = workspaces_dir / "stale_ws_123"
    stale_dir.mkdir()
    (stale_dir / "test.php").write_text("<?php", encoding="utf-8")
    stale_meta = {"workspace_id": "stale_ws_123", "created_at": time.time() - 8000}
    (stale_dir / ".workspace_meta.json").write_text(str(stale_meta).replace("'", '"'))

    # 2. Create a "fresh" workspace (< 2 hours)
    fresh_dir = workspaces_dir / "fresh_ws_456"
    fresh_dir.mkdir()
    (fresh_dir / "test.php").write_text("<?php", encoding="utf-8")
    fresh_meta = {"workspace_id": "fresh_ws_456", "created_at": time.time() - 100}
    (fresh_dir / ".workspace_meta.json").write_text(str(fresh_meta).replace("'", '"'))

    # Run cleanup with default TTL (7200s)
    cleaned = cleanup_old_workspaces(workspaces_dir, max_age_seconds=7200)
    assert cleaned == 1
    assert not stale_dir.exists()
    assert fresh_dir.exists()


def test_api_cleanup_endpoint(monkeypatch, tmp_path):
    """Verify POST /api/workspace/cleanup endpoint."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir
    workspaces_dir.mkdir(parents=True, exist_ok=True)

    old_dir = workspaces_dir / "old_ws"
    old_dir.mkdir()
    (old_dir / ".workspace_meta.json").write_text(f'{{"created_at": {time.time() - 10000}}}')

    resp = client.post("/api/workspace/cleanup", params={"max_age_seconds": 7200})
    assert resp.status_code == 200
    data = resp.json()
    assert data["cleaned_count"] >= 1
    assert not old_dir.exists()


def test_api_delete_workspace(monkeypatch, tmp_path):
    """Verify manual DELETE /api/workspace/{workspace_id} endpoint."""
    workspaces_dir = tmp_path / "rhp_workspaces"
    monkeypatch.setenv("RHP_WORKSPACES_DIR", str(workspaces_dir))
    workspace_manager.base_dir = workspaces_dir

    wid, wdir = workspace_manager.create_workspace()
    assert wdir.is_dir()

    resp = client.delete(f"/api/workspace/{wid}")
    assert resp.status_code == 200
    assert not wdir.exists()
