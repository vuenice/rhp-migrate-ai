"""Unit tests for AST-targeted rule selector, learnings extractor, and active rules API."""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from rhp_migrate.rules.registry import RuleRegistry
from rhp_migrate.rules.selector import (
    RuleSelector,
    append_learned_fix,
    build_system_prompt_with_learnings,
    detect_ast_tags,
    extract_actionable_heuristics,
    extract_learnings_from_markdown,
    format_learned_fix_entry,
    load_active_learnings,
    select_rules,
)
from rhp_migrate.server.main import app


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app)


def test_detect_ast_tags_constructs():
    """Verify detection of key PHP language constructs using Tree-Sitter AST."""
    # 1. Superglobals & Dynamic arrays
    code_sg = """<?php
    $x = $_GET['search'];
    $data = ['name' => 'Alice'];
    $data[] = 'Bob';
    """
    tags_sg = detect_ast_tags(code_sg)
    assert "has_superglobals" in tags_sg
    assert "has_dynamic_arrays" in tags_sg
    assert "has_php_tags" in tags_sg

    # 2. Traits & Eloquent Scopes
    code_traits = """<?php
    class Product extends Model {
        use HasFactory, SoftDeletes;

        public function scopePublished($query) {
            return $query->where('is_published', 1);
        }
    }
    """
    tags_traits = detect_ast_tags(code_traits)
    assert "has_traits" in tags_traits
    assert "uses_eloquent_scopes" in tags_traits

    # 3. Exceptions & Try/Catch
    code_exc = """<?php
    try {
        throw new RuntimeException("Invalid state");
    } catch (Exception $e) {
        log_error($e->getMessage());
    }
    """
    tags_exc = detect_ast_tags(code_exc)
    assert "has_exceptions" in tags_exc

    # 4. cURL procedural calls
    code_curl = """<?php
    $ch = curl_init("https://api.example.com");
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    $res = curl_exec($ch);
    """
    tags_curl = detect_ast_tags(code_curl)
    assert "has_curl" in tags_curl

    # 5. Strict equality & String concatenation
    code_syntax = """<?php
    if ($a === $b) {
        $msg = $first . " " . $last;
        $msg .= "!";
    }
    """
    tags_syntax = detect_ast_tags(code_syntax)
    assert "has_strict_equality" in tags_syntax
    assert "has_string_concat" in tags_syntax

    # 6. Reflection & Output buffering & Dynamic includes
    code_unsupported = """<?php
    ob_start();
    require 'header.php';
    $ref = new ReflectionClass(Foo::class);
    """
    tags_unsup = detect_ast_tags(code_unsupported)
    assert "has_output_buffering" in tags_unsup
    assert "has_dynamic_includes" in tags_unsup
    assert "has_reflection" in tags_unsup


def test_select_rules_filtering():
    """Verify select_rules selects only rules matching detected AST tags."""
    registry = RuleRegistry.default()

    # Superglobals file should select RHP-HTTP-002
    sg_code = "<?php $val = $_POST['item'];"
    rules = select_rules(sg_code, registry=registry)
    rule_ids = [r.id for r in rules]
    assert "RHP-HTTP-002" in rule_ids
    assert "RHP-SYN-001" in rule_ids  # php tags
    assert "RHP-HTTP-001" not in rule_ids  # no curl

    # Eloquent scope file should select RHP-ELOQ-001
    eloq_code = """<?php
    class User {
        use Notifiable;
        public function scopeActive($query) {
            return $query->where('status', 'active');
        }
    }
    """
    eloq_rules = select_rules(eloq_code, registry=registry)
    eloq_rule_ids = [r.id for r in eloq_rules]
    assert "RHP-ELOQ-001" in eloq_rule_ids
    assert "RHP-TRAIT-001" in eloq_rule_ids


def test_learnings_markdown_parsing():
    """Verify structured parsing of learnings.md entries and actionable heuristics."""
    markdown_content = """# System Memory & Working Learnings (`learnings.md`)

## 🧠 Active Working Learnings

### [2026-11-20] Strict Return Types on Controllers
- **Context / Symptom**: Controller actions missing return types fail `rhp check`.
- **Root Cause**: PHP allows untyped action returns.
- **Working Solution**: Explicitly annotate actions with `: Response`.
- **Verification**: `rhp check` passes with 0 errors.
- **Key Heuristic**: Always add `: Response` return types to HTTP controller methods.

### [2026-11-21] Prefix Typing Rule
- **Context / Symptom**: Colon typing fails compiler.
- **Root Cause**: RHP requires prefix typing.
- **Working Solution**: Write `Type $name = value`.
- **Verification**: Passed tests.
- **Key Heuristic**: Always use prefix typing (`Type name = expr;`) in RHP.

## 🔄 Graduation Pipeline
### Graduated Entries
- **Prefix-Typed Variable Declaration Syntax**
"""
    entries = extract_learnings_from_markdown(markdown_content)
    assert len(entries) >= 2
    assert entries[0].date == "2026-11-20"
    assert "Strict Return Types" in entries[0].title
    assert "Always add `: Response`" in entries[0].key_heuristic

    heuristics = extract_actionable_heuristics(content=markdown_content)
    assert len(heuristics) == 2
    assert any("Always add `: Response`" in h for h in heuristics)
    assert any("Always use prefix typing" in h for h in heuristics)


def test_build_system_prompt_with_learnings():
    """Verify system prompt builder injects working memory heuristics and selected rules."""
    registry = RuleRegistry.default()
    target_rule = registry["RHP-HTTP-002"]

    custom_heuristics = [
        "**Type System Rule**: Always use prefix typing.",
        "**cURL Rule**: Replace curl_* with Http facade.",
    ]

    prompt = build_system_prompt_with_learnings(
        rules=[target_rule],
        heuristics=custom_heuristics,
        include_memory=True,
    )

    assert "Persistent Working Memory & Proven Heuristics" in prompt
    assert "**Type System Rule**: Always use prefix typing." in prompt
    assert "**cURL Rule**: Replace curl_* with Http facade." in prompt
    assert "AST-Targeted RHP Migration Rules" in prompt
    assert "### Rule [RHP-HTTP-002]: Replace superglobals with Request facade" in prompt


def test_format_and_append_learned_fix(tmp_path: Path):
    """Verify appending structured fix entries to a learnings.md file."""
    learnings_file = tmp_path / "learnings.md"
    initial_content = """# Memory
## 🧠 Active Working Learnings

### [2026-11-19] First Learning
- **Context / Symptom**: Initial symptom.
- **Root Cause**: Initial cause.
- **Working Solution**: Initial solution.
- **Verification**: Tests.
- **Key Heuristic**: First heuristic.

## 🔄 Graduation Pipeline
### Graduated Entries
"""
    learnings_file.write_text(initial_content, encoding="utf-8")

    # Append new fix
    append_learned_fix(
        learnings_path=learnings_file,
        title="Dynamic Array Fix",
        context_symptom="Type error on untyped array push",
        root_cause="RHP needs typed list<T>",
        working_solution="Convert to list<string> $items = []",
        verification="rhp check",
        key_heuristic="Use list<T> for indexed collections.",
    )

    updated_text = learnings_file.read_text(encoding="utf-8")
    assert "Dynamic Array Fix" in updated_text
    assert "- **Key Heuristic**: Use list<T> for indexed collections." in updated_text
    # Verify placed before graduation pipeline
    fix_idx = updated_text.find("Dynamic Array Fix")
    grad_idx = updated_text.find("## 🔄 Graduation Pipeline")
    assert fix_idx < grad_idx


def test_rule_selector_class():
    """Verify RuleSelector unified service methods."""
    selector = RuleSelector()
    code = "<?php $res = curl_init();"
    tags = selector.detect_tags(code)
    assert "has_curl" in tags

    rules = selector.select_rules(code)
    rule_ids = [r.id for r in rules]
    assert "RHP-HTTP-001" in rule_ids

    heuristics = selector.get_active_heuristics()
    assert len(heuristics) > 0

    system_prompt = selector.build_system_prompt(source_or_tree=code)
    assert "Persistent Working Memory & Proven Heuristics" in system_prompt
    assert "RHP-HTTP-001" in system_prompt


def test_api_active_rules_endpoint(test_client: TestClient):
    """Test POST and GET /api/rules/active API endpoints."""
    # POST with code
    req_payload = {
        "file_path": "app/Http/Controllers/OrderController.php",
        "code": "<?php $query = $_GET['q']; $items = ['a' => 1];",
    }
    res = test_client.post("/api/rules/active", json=req_payload)
    assert res.status_code == 200
    data = res.json()

    assert "has_superglobals" in data["detected_tags"]
    assert "has_dynamic_arrays" in data["detected_tags"]
    assert data["rules_count"] >= 2
    rule_ids = [r["id"] for r in data["rules"]]
    assert "RHP-HTTP-002" in rule_ids
    assert data["heuristics_count"] > 0
    assert "Persistent Working Memory" in data["system_prompt_preview"]

    # GET endpoint
    get_res = test_client.get("/api/rules/active")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert "heuristics" in get_data


def test_api_append_learning_structured(test_client: TestClient, tmp_path: Path, monkeypatch):
    """Test POST /api/rules/learnings with structured fix payload."""
    test_learnings = tmp_path / "learnings.md"
    test_learnings.write_text("# Working Memory\n## 🧠 Active Working Learnings\n", encoding="utf-8")

    monkeypatch.setattr(
        "rhp_migrate.server.routes.rules.get_learnings_path",
        lambda: test_learnings,
    )

    fix_payload = {
        "title": "Loose Comparison in Auth Middleware",
        "context_symptom": "Loose comparison === triggers type mismatch warning",
        "root_cause": "RHP strict typing requires standard ==",
        "working_solution": "Replaced === with ==",
        "verification": "uv run pytest tests/test_verification.py",
        "key_heuristic": "Use standard == / != operators for value comparisons in RHP.",
    }

    res = test_client.post("/api/rules/learnings", json=fix_payload)
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["success"] is True

    # Verify written content
    content = test_learnings.read_text(encoding="utf-8")
    assert "Loose Comparison in Auth Middleware" in content
    assert "Use standard == / != operators" in content
