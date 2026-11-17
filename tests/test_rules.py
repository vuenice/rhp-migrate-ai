"""Unit tests for RHP migration rules schema, registry, and rules.yaml."""

from pathlib import Path
import pytest
from pydantic import ValidationError

from rhp_migrate.rules import (
    MigrationStrategy,
    Rule,
    RuleCategory,
    RuleExample,
    RuleRegistry,
)


def test_rule_schema_valid():
    """Test valid Rule construction."""
    rule = Rule(
        id="RHP-TEST-001",
        title="Test Rule",
        category=RuleCategory.SYNTAX,
        strategy=MigrationStrategy.DETERMINISTIC,
        php_pattern="test()",
        rhp_replacement="rhp_test()",
        unsupported=False,
        doc_url="https://example.com/doc",
        llm_prompt_guidance="Replace test with rhp_test.",
        examples=[
            RuleExample(
                before="test();",
                after="rhp_test();",
                explanation="Test replacement",
            )
        ],
    )
    assert rule.id == "RHP-TEST-001"
    assert rule.category == RuleCategory.SYNTAX
    assert rule.strategy == MigrationStrategy.DETERMINISTIC
    assert len(rule.examples) == 1
    assert rule.examples[0].before == "test();"
    assert not rule.unsupported


def test_rule_schema_invalid():
    """Test validation errors on missing or invalid fields."""
    with pytest.raises(ValidationError):
        # Missing required title, category, etc.
        Rule.model_validate({"id": "RHP-001"})

    with pytest.raises(ValidationError):
        # Invalid category
        Rule(
            id="RHP-001",
            title="Title",
            category="NON_EXISTENT_CATEGORY",  # type: ignore
            strategy=MigrationStrategy.DETERMINISTIC,
            php_pattern="pattern",
        )

    with pytest.raises(ValidationError):
        # Invalid strategy
        Rule(
            id="RHP-001",
            title="Title",
            category=RuleCategory.SYNTAX,
            strategy="NON_EXISTENT_STRATEGY",  # type: ignore
            php_pattern="pattern",
        )


def test_rule_format_prompt_block():
    """Test markdown formatting of a rule."""
    rule = Rule(
        id="RHP-TEST-002",
        title="Sample Rule",
        category=RuleCategory.HTTP_FACADE,
        strategy=MigrationStrategy.LLM_ASSIST,
        php_pattern="foo()",
        rhp_replacement="bar()",
        unsupported=False,
        llm_prompt_guidance="Convert foo to bar.",
        examples=[
            RuleExample(
                before="foo();",
                after="bar();",
                explanation="Simple conversion",
            )
        ],
    )
    block = rule.format_prompt_block()
    assert "### Rule [RHP-TEST-002]: Sample Rule" in block
    assert "- **Category**: HTTP_FACADE" in block
    assert "- **Strategy**: LLM_ASSIST" in block
    assert "- **PHP Pattern**: foo()" in block
    assert "- **RHP Replacement**: bar()" in block
    assert "- **AI Guidance**: Convert foo to bar." in block
    assert "Simple conversion" in block
    assert "// BEFORE:" in block
    assert "// AFTER:" in block


def test_default_rules_yaml_loads():
    """Test that default rules.yaml loads and validates successfully."""
    registry = RuleRegistry.default()
    assert len(registry) >= 11

    # Verify all expected rule IDs are present
    expected_ids = [
        "RHP-SYN-001",
        "RHP-SYN-002",
        "RHP-SYN-003",
        "RHP-SYN-004",
        "RHP-SYN-005",
        "RHP-SYN-006",
        "RHP-HTTP-001",
        "RHP-HTTP-002",
        "RHP-UNSUP-001",
        "RHP-UNSUP-002",
        "RHP-UNSUP-003",
    ]
    for rule_id in expected_ids:
        assert rule_id in registry, f"Missing expected rule: {rule_id}"
        rule = registry[rule_id]
        assert rule.id == rule_id
        assert rule.title
        assert isinstance(rule.category, RuleCategory)
        assert isinstance(rule.strategy, MigrationStrategy)
        assert rule.php_pattern
        assert len(rule.examples) >= 1
        for eg in rule.examples:
            assert eg.before
            assert eg.after
            assert eg.explanation


def test_required_rules_properties():
    """Verify specific properties for the 11 core required rules."""
    registry = RuleRegistry.default()

    # RHP-SYN-001: Remove <?php / ?> tags
    r1 = registry["RHP-SYN-001"]
    assert r1.category == RuleCategory.SYNTAX
    assert r1.strategy == MigrationStrategy.DETERMINISTIC
    assert not r1.unsupported

    # RHP-SYN-002: declare statements
    r2 = registry["RHP-SYN-002"]
    assert r2.category == RuleCategory.SYNTAX
    assert r2.strategy == MigrationStrategy.DETERMINISTIC
    assert not r2.unsupported

    # RHP-SYN-003: === / !== to == / !=
    r3 = registry["RHP-SYN-003"]
    assert r3.category == RuleCategory.SYNTAX
    assert r3.strategy == MigrationStrategy.DETERMINISTIC
    assert "==" in r3.rhp_replacement

    # RHP-SYN-004: String concatenation . / .= to + / +=
    r4 = registry["RHP-SYN-004"]
    assert r4.category == RuleCategory.SYNTAX
    assert r4.strategy == MigrationStrategy.DETERMINISTIC
    assert "+" in r4.rhp_replacement

    # RHP-SYN-005: list(...) destructuring to [...]
    r5 = registry["RHP-SYN-005"]
    assert r5.category == RuleCategory.SYNTAX
    assert r5.strategy == MigrationStrategy.DETERMINISTIC

    # RHP-SYN-006: foreach to for-in
    r6 = registry["RHP-SYN-006"]
    assert r6.category == RuleCategory.SYNTAX
    assert r6.strategy == MigrationStrategy.DETERMINISTIC

    # RHP-HTTP-001: curl_* to Http::
    r7 = registry["RHP-HTTP-001"]
    assert r7.category == RuleCategory.HTTP_FACADE
    assert r7.strategy == MigrationStrategy.LLM_ASSIST
    assert "Http::" in r7.rhp_replacement

    # RHP-HTTP-002: $_GET / $_POST to Request::
    r8 = registry["RHP-HTTP-002"]
    assert r8.category == RuleCategory.HTTP_FACADE
    assert "Request::" in r8.rhp_replacement

    # RHP-UNSUP-001: Reflection unsupported
    r9 = registry["RHP-UNSUP-001"]
    assert r9.category == RuleCategory.UNSUPPORTED
    assert r9.strategy == MigrationStrategy.MANUAL_REWRITE
    assert r9.unsupported is True

    # RHP-UNSUP-002: Output buffering unsupported
    r10 = registry["RHP-UNSUP-002"]
    assert r10.category == RuleCategory.UNSUPPORTED
    assert r10.strategy == MigrationStrategy.MANUAL_REWRITE
    assert r10.unsupported is True

    # RHP-UNSUP-003: Dynamic includes unsupported
    r11 = registry["RHP-UNSUP-003"]
    assert r11.category == RuleCategory.UNSUPPORTED
    assert r11.strategy == MigrationStrategy.MANUAL_REWRITE
    assert r11.unsupported is True


def test_registry_registration_and_lookup():
    """Test registry methods for registration, lookup, and duplicate handling."""
    reg = RuleRegistry()
    assert len(reg) == 0

    rule1 = Rule(
        id="RHP-A",
        title="Rule A",
        category=RuleCategory.SYNTAX,
        strategy=MigrationStrategy.DETERMINISTIC,
        php_pattern="a()",
    )
    reg.register(rule1)
    assert len(reg) == 1
    assert "RHP-A" in reg
    assert reg.get("RHP-A") == rule1
    assert reg["RHP-A"] == rule1

    # Missing item
    assert reg.get("NON-EXISTENT") is None
    with pytest.raises(KeyError):
        _ = reg["NON-EXISTENT"]

    # Duplicate without overwrite
    with pytest.raises(ValueError, match="already exists"):
        reg.register(rule1, overwrite=False)

    # Duplicate with overwrite
    updated_rule = Rule(
        id="RHP-A",
        title="Rule A Updated",
        category=RuleCategory.SYNTAX,
        strategy=MigrationStrategy.DETERMINISTIC,
        php_pattern="a_updated()",
    )
    reg.register(updated_rule, overwrite=True)
    assert reg["RHP-A"].title == "Rule A Updated"


def test_registry_filtering():
    """Test registry filtering by category, strategy, and unsupported flag."""
    reg = RuleRegistry.default()

    # Filter by category
    syntax_rules = reg.filter(category=RuleCategory.SYNTAX)
    assert len(syntax_rules) == 6
    assert all(r.category == RuleCategory.SYNTAX for r in syntax_rules)

    # Case-insensitive string filtering
    http_rules = reg.get_by_category("http_facade")
    assert len(http_rules) == 2
    assert all(r.category == RuleCategory.HTTP_FACADE for r in http_rules)

    # Filter by strategy
    manual_rules = reg.get_by_strategy(MigrationStrategy.MANUAL_REWRITE)
    assert len(manual_rules) >= 3
    assert all(r.strategy == MigrationStrategy.MANUAL_REWRITE for r in manual_rules)

    # Filter unsupported
    unsupported_rules = reg.get_unsupported()
    assert len(unsupported_rules) == 3
    assert all(r.unsupported for r in unsupported_rules)

    # Filter deterministic
    det_rules = reg.get_deterministic()
    assert all(r.strategy == MigrationStrategy.DETERMINISTIC for r in det_rules)


def test_registry_export_prompt_context():
    """Test exporting LLM prompt context."""
    reg = RuleRegistry.default()

    # Full export
    full_context = reg.export_prompt_context()
    assert "# RHP Migration Rules Reference" in full_context
    assert "RHP-SYN-001" in full_context
    assert "RHP-UNSUP-001" in full_context

    # Filtered export: only UNSUPPORTED
    unsup_context = reg.export_prompt_context(unsupported=True, include_header=False)
    assert "# RHP Migration Rules Reference" not in unsup_context
    assert "RHP-UNSUP-001" in unsup_context
    assert "RHP-SYN-001" not in unsup_context

    # Empty export
    empty_context = reg.export_prompt_context(category=RuleCategory.STANDARD_LIBRARY)
    assert "No migration rules matching criteria." in empty_context


def test_registry_yaml_parsing_variations(tmp_path: Path):
    """Test loading from custom YAML strings and files."""
    # Top-level list
    yaml_list = """
- id: RHP-CUSTOM-001
  title: Custom Rule
  category: SYNTAX
  strategy: DETERMINISTIC
  php_pattern: "custom()"
"""
    reg1 = RuleRegistry.from_yaml_string(yaml_list)
    assert len(reg1) == 1
    assert "RHP-CUSTOM-001" in reg1

    # Empty YAML
    reg_empty = RuleRegistry.from_yaml_string("")
    assert len(reg_empty) == 0

    # Malformed YAML schema
    bad_yaml = """
rules:
  - id: RHP-BAD-001
    title: Missing fields
"""
    with pytest.raises(ValueError, match="Validation failed for rule 'RHP-BAD-001'"):
        RuleRegistry.from_yaml_string(bad_yaml)

    # Invalid YAML mapping without 'rules'
    invalid_mapping = "foo: bar\n"
    with pytest.raises(ValueError, match="must contain a top-level 'rules' key"):
        RuleRegistry.from_yaml_string(invalid_mapping)

    # File loading
    custom_file = tmp_path / "custom_rules.yaml"
    custom_file.write_text(yaml_list, encoding="utf-8")
    reg_file = RuleRegistry.from_yaml_file(custom_file)
    assert len(reg_file) == 1
    assert "RHP-CUSTOM-001" in reg_file

    # File not found
    with pytest.raises(FileNotFoundError):
        RuleRegistry.from_yaml_file(tmp_path / "does_not_exist.yaml")
