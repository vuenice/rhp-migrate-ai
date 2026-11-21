"""Comprehensive test suite for rhp-migrate-ai FastAPI backend with BYOK LiteLLM support."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import zipfile
import pytest
from fastapi.testclient import TestClient

from rhp_migrate.server.byok import (
    BYOKCredentials,
    create_runner_from_byok,
    resolve_litellm_model,
)
from rhp_migrate.server.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# BYOK LiteLLM Unit Tests
# ---------------------------------------------------------------------------

def test_resolve_litellm_model():
    """Verify provider prefixing conventions for LiteLLM."""
    assert resolve_litellm_model("gemini", "gemini-2.5-pro") == "gemini/gemini-2.5-pro"
    assert resolve_litellm_model("gemini", "") == "gemini/gemini-2.5-pro"
    assert resolve_litellm_model("anthropic", "claude-3-7-sonnet") == "anthropic/claude-3-7-sonnet"
    assert resolve_litellm_model("openai", "gpt-4o") == "gpt-4o"
    assert resolve_litellm_model("deepseek", "deepseek-chat") == "deepseek/deepseek-chat"
    assert resolve_litellm_model("ollama", "llama3") == "ollama/llama3"
    # Pre-formatted model name preserves original format
    assert resolve_litellm_model("gemini", "gemini/gemini-2.5-flash") == "gemini/gemini-2.5-flash"


def test_create_runner_from_byok_mock():
    """Verify creating an LLMRunner with mock BYOK credentials."""
    creds = BYOKCredentials(provider="mock", model_name="mock-model")
    runner = create_runner_from_byok(creds)
    assert runner.client.provider_name == "mock"

    res = runner.migrate_unit(unit_identifier="test", code="<?php function a() {}")
    assert res.result.unit_identifier == "MockUnit"


# ---------------------------------------------------------------------------
# Health & Root Endpoints
# ---------------------------------------------------------------------------

def test_root_endpoint():
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["name"] == "rhp-migrate-ai API"


def test_health_check_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "rhp-migrate-ai"


# ---------------------------------------------------------------------------
# POST /api/analyze Endpoints
# ---------------------------------------------------------------------------

def test_analyze_code_snippet():
    """Test AST readiness analysis on a raw PHP snippet."""
    payload = {
        "code": "<?php function add($a, $b) { return $a + $b; }",
        "file_name": "Calculator.php",
    }
    resp = client.post("/api/analyze", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files"] == 1
    assert data["overall_readiness"] > 0
    assert "Calculator.php" in data["file_reports"]
    rep = data["file_reports"]["Calculator.php"]
    assert rep["untyped_param_count"] == 2
    assert "add" in rep["functions"]


def test_analyze_in_memory_files():
    """Test AST readiness analysis across a dictionary of in-memory files."""
    payload = {
        "files": {
            "app/Services/UserService.php": "<?php namespace App\\Services; class UserService { public function find(int $id): array { return []; } }",
            "app/Controllers/UserController.php": "<?php namespace App\\Controllers; use App\\Services\\UserService; class UserController { private UserService $service; }",
        }
    }
    resp = client.post("/api/analyze", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files"] == 2
    assert len(data["migration_order"]) == 2
    # UserService has no dependencies, so it should come first in topological order
    assert "UserService.php" in data["migration_order"][0]


def test_analyze_zip_upload():
    """Test AST readiness analysis on an uploaded ZIP archive."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("app/Models/User.php", "<?php class User { public string $name; }")
        z.writestr("app/Helpers/str.php", "<?php function str_upper(string $s): string { return strtoupper($s); }")

    zip_buffer.seek(0)
    files = {"file": ("project.zip", zip_buffer, "application/zip")}
    resp = client.post("/api/analyze", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files"] == 2
    assert data["overall_readiness"] >= 0.9


def test_analyze_empty_request_fails():
    """Verify bad request when no input is provided."""
    resp = client.post("/api/analyze", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/migrate/file Endpoint
# ---------------------------------------------------------------------------

def test_migrate_file_byok_mock():
    """Test synchronous file migration with BYOK mock headers."""
    payload = {
        "file_path": "app/Services/PaymentService.php",
        "code": "<?php\n\nfunction charge($amount, $token) {\n    return true;\n}\n",
        "php_version": "8.2",
        "run_critic": True,
    }
    headers = {
        "x-provider": "mock",
        "x-model-name": "mock-model",
        "x-api-key": "test-key-123",
    }
    resp = client.post("/api/migrate/file", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_path"] == "app/Services/PaymentService.php"
    assert "final_code" in data
    assert data["is_valid"] is True
    assert data["critic_report"] is not None
    assert "stats" in data
    assert data["stats"]["model"] == "mock-model"


# ---------------------------------------------------------------------------
# POST /api/migrate/stream SSE Endpoint
# ---------------------------------------------------------------------------

def test_migrate_stream_sse_events():
    """Test Server-Sent Events (SSE) streaming through all 5 migration stages."""
    payload = {
        "file_path": "app/Utils/Math.php",
        "code": "<?php\nfunction multiply($x, $y) {\n    return $x * $y;\n}\n",
        "run_critic": True,
    }
    headers = {
        "x-provider": "mock",
        "x-model-name": "mock-model",
        "x-api-key": "sk-mock-key",
    }

    with client.stream("POST", "/api/migrate/stream", json=payload, headers=headers) as stream:
        assert stream.status_code == 200
        assert "text/event-stream" in stream.headers["content-type"]

        events: list[tuple[str, dict]] = []
        current_event = None

        for line in stream.iter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("event: "):
                current_event = line.replace("event: ", "").strip()
            elif line.startswith("data: ") and current_event:
                raw_json = line.replace("data: ", "").strip()
                parsed_data = json.loads(raw_json)
                events.append((current_event, parsed_data))
                current_event = None

        event_names = [ev[0] for ev in events]

        # Verify all required stages were emitted in sequence:
        # AST parsing -> initial RHP draft -> compiler check -> critic reflection -> final verified RHP code
        assert "ast_parsing" in event_names
        assert "initial_rhp_draft" in event_names
        assert "compiler_check" in event_names
        assert "critic_reflection" in event_names
        assert "final_code" in event_names
        assert "done" in event_names

        # Check final_code event payload
        final_code_events = [data for name, data in events if name == "final_code"]
        assert len(final_code_events) > 0
        final_data = final_code_events[0]["data"]
        assert "final_code" in final_data
        assert final_data["is_verified"] is True


# ---------------------------------------------------------------------------
# Rules & Learnings Endpoints
# ---------------------------------------------------------------------------

def test_rules_learnings_crud(monkeypatch, tmp_path):
    """Test reading, updating, and appending to learnings.md."""
    test_learnings = tmp_path / "learnings.md"
    test_learnings.write_text("# Test Learnings\nInitial content\n", encoding="utf-8")

    monkeypatch.setattr("rhp_migrate.server.routes.rules.get_learnings_path", lambda: test_learnings)

    # 1. GET
    resp = client.get("/api/rules/learnings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert "Initial content" in data["content"]

    # 2. PUT (Overwrite)
    new_content = "# Overwritten Learnings\nUpdated text\n"
    put_resp = client.put("/api/rules/learnings", json={"content": new_content})
    assert put_resp.status_code == 200
    assert test_learnings.read_text(encoding="utf-8") == new_content

    # 3. POST (Append structured entry)
    post_resp = client.post(
        "/api/rules/learnings",
        json={"title": "Test Insight", "content": "- **Finding**: Working as expected"},
    )
    assert post_resp.status_code == 200
    content_after = test_learnings.read_text(encoding="utf-8")
    assert "### [" in content_after
    assert "Test Insight" in content_after
    assert "Working as expected" in content_after


def test_rules_context_crud(monkeypatch, tmp_path):
    """Test reading, updating, and appending to context.md."""
    test_context = tmp_path / "context.md"
    test_context.write_text("# RHP Context\nRules here\n", encoding="utf-8")

    monkeypatch.setattr("rhp_migrate.server.routes.rules.get_context_path", lambda: test_context)

    # 1. GET
    resp = client.get("/api/rules/context")
    assert resp.status_code == 200
    data = resp.json()
    assert "Rules here" in data["content"]

    # 2. PUT
    new_content = "# Updated Context\nStrict types everywhere\n"
    put_resp = client.put("/api/rules/context", json={"content": new_content})
    assert put_resp.status_code == 200
    assert test_context.read_text(encoding="utf-8") == new_content

    # 3. POST (Append section)
    post_resp = client.post(
        "/api/rules/context",
        json={"title": "Array Conversions", "content": "Always convert to dict or list"},
    )
    assert post_resp.status_code == 200
    after = test_context.read_text(encoding="utf-8")
    assert "## Array Conversions" in after
    assert "Always convert to dict or list" in after


def test_pending_proposals_and_approve(monkeypatch, tmp_path):
    """Test fetching and approving pending context proposals."""
    feedback_dir = tmp_path / ".rhp" / "feedback"
    feedback_dir.mkdir(parents=True)
    pending_file = feedback_dir / "pending_context.md"
    pending_content = (
        "<!-- PENDING-CONTEXT-START id=prop123 file=app/User.php php=8.2 -->\n"
        "### [PROPOSAL] Array to Dict\nUse dict<string, mixed>\n"
        "<!-- PENDING-CONTEXT-END id=prop123 -->\n"
    )
    pending_file.write_text(pending_content, encoding="utf-8")

    test_context = tmp_path / "context.md"
    test_context.write_text("# Initial Context\n", encoding="utf-8")

    monkeypatch.setattr("rhp_migrate.server.routes.rules.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("rhp_migrate.server.routes.rules.get_context_path", lambda: test_context)

    # GET pending
    resp = client.get("/api/rules/pending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["proposals"][0]["id"] == "prop123"

    # POST approve
    approve_resp = client.post("/api/rules/context/approve")
    assert approve_resp.status_code == 200
    assert "Successfully approved and merged 1 proposal" in approve_resp.json()["message"]

    # Pending file should now be empty and context.md updated
    assert pending_file.read_text(encoding="utf-8") == ""
    assert "Array to Dict" in test_context.read_text(encoding="utf-8")
