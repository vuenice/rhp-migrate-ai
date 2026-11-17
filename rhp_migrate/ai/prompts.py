"""Prompt generation and templating for Layer 5 AI migration."""

import json
from typing import Any, Iterable, Optional, Sequence, Union
from pydantic import BaseModel, ConfigDict, Field

from rhp_migrate.ai.models import PatchType
from rhp_migrate.rules.registry import RuleRegistry
from rhp_migrate.rules.schema import Rule


SYSTEM_PROMPT = """You are an expert compiler engineer and static analysis specialist migrating legacy PHP/Laravel code to RHP (a high-performance, strictly-typed PHP superset compiled ahead-of-time).

### Core Specification & Architectural Guidelines
1. **Strict Static Types Mandatory**:
   - ALL parameters must have explicit static types. Untyped parameters ($param) are prohibited.
   - Return types are mandatory for all functions and methods (: void, : string, : int, etc.).
   - Class properties must have explicit static types.
   - When parameter types are ambiguous, deduce the most specific type from AST context, caller signatures, and usage operations.

2. **No Reflection**:
   - Runtime reflection (ReflectionClass, ReflectionMethod, ReflectionProperty, ReflectionFunction, eval, variable variables $$var, and dynamic method dispatches) is unsupported in RHP.
   - Refactor reflection into static shims, constructor dependency injection, typed interfaces, or class registry lookups.

3. **Array Disambiguation**:
   - PHP's polymorphic array is split in RHP:
     - Use indexed sequential arrays/lists (e.g. `int[]`, `string[]`, `list<T>`) for sequential lists.
     - Use key-value maps/dicts (e.g. `dict<string, mixed>`, `map<string, T>`) for string-keyed associative tables.
   - Never leave arrays untyped where elements or keys can be disambiguated.

4. **Never Invent Non-Existent APIs**:
   - Only produce valid RHP syntax and standard PHP/framework APIs.
   - Do not hallucinate or invent imaginary helper functions, packages, or framework abstractions.

5. **Single-Unit Processing Scope**:
   - Process ONLY the exact target function, method, or class provided.
   - Do NOT emit whole files, opening PHP tags (<?php), closing tags (?>), or file-level wrappers unless the target unit is a standalone class definition.

6. **Strict Structured JSON Output**:
   - You MUST output ONLY a valid JSON object matching the LLMMigrationResult schema.
   - Do NOT include markdown code blocks (e.g. ```json ... ```), conversational commentary, introductory greetings, or postscript explanations.
   - If a construct cannot be resolved safely, mark `patch_type` as "UNRESOLVED", set `needs_human_review` to true, and provide a clear `review_reason`.
"""


def build_system_prompt() -> str:
    """Return the authoritative RHP migration system prompt."""
    return SYSTEM_PROMPT.strip()


def validate_single_unit(code: str, unit_identifier: str) -> None:
    """Validate that the input code represents a single unit, not a whole multi-unit file.

    Args:
        code: The PHP code snippet to inspect.
        unit_identifier: Identifier of the unit (e.g. 'App\\Services\\PaymentService::charge').

    Raises:
        ValueError: If code appears to be a whole file with multiple class definitions.
    """
    cleaned = code.strip()
    if not cleaned:
        raise ValueError(f"Target code for '{unit_identifier}' cannot be empty.")

    # Check for multiple top-level class declarations
    lines = cleaned.splitlines()
    class_count = sum(
        1 for line in lines
        if line.strip().startswith("class ") or line.strip().startswith("final class ") or line.strip().startswith("abstract class ")
    )
    if class_count > 1:
        raise ValueError(
            f"Whole files with multiple classes cannot be sent to the AI stage. "
            f"Found {class_count} classes in target '{unit_identifier}'. "
            "Send exactly one function, method, or class at a time."
        )


def format_rules_for_prompt(
    rules: Optional[Union[Sequence[Union[Rule, str]], RuleRegistry, str]] = None
) -> str:
    """Format migration rules and examples from rules.yaml into a prompt section.

    Args:
        rules: List of Rule instances, rule ID strings, a RuleRegistry, or a pre-formatted string.

    Returns:
        Formatted markdown block containing rules and before/after examples.
    """
    if rules is None:
        return "No specific rules provided. Adhere to general RHP specification guidelines."

    if isinstance(rules, str):
        return rules.strip()

    if isinstance(rules, RuleRegistry):
        target_rules = rules.all_rules()
    else:
        target_rules = []
        default_registry: Optional[RuleRegistry] = None
        for item in rules:
            if isinstance(item, Rule):
                target_rules.append(item)
            elif isinstance(item, str):
                if default_registry is None:
                    try:
                        default_registry = RuleRegistry.default()
                    except Exception:
                        default_registry = None
                if default_registry and item in default_registry:
                    target_rules.append(default_registry[item])
                else:
                    # Synthetic minimal rule placeholder if not found in yaml
                    target_rules.append(
                        Rule(
                            id=item,
                            title=f"Rule {item}",
                            category="UNSUPPORTED",  # type: ignore[arg-type]
                            strategy="LLM_ASSIST",  # type: ignore[arg-type]
                            php_pattern=f"Pattern governed by {item}",
                            unsupported=True,
                            llm_prompt_guidance=f"Apply migration guidance for rule {item}.",
                        )
                    )

    if not target_rules:
        return "No specific rules provided. Adhere to general RHP specification guidelines."

    blocks = []
    for r in target_rules:
        blocks.append(r.format_prompt_block())
    return "\n\n".join(blocks)


def format_ast_context(ast_context: Any) -> str:
    """Format AST context (e.g. node type, parameter list, return type, calls) into prompt text."""
    if ast_context is None:
        return "No additional AST context provided."

    if isinstance(ast_context, str):
        return ast_context.strip()

    if isinstance(ast_context, dict):
        return json.dumps(ast_context, indent=2)

    # Check for symbols from analyzer (PHPMethod, PHPFunction, PHPClass, etc.)
    lines = []
    if hasattr(ast_context, "name"):
        lines.append(f"- **Name**: {getattr(ast_context, 'name')}")
    if hasattr(ast_context, "visibility"):
        lines.append(f"- **Visibility**: {getattr(ast_context, 'visibility')}")
    if hasattr(ast_context, "return_type"):
        ret = getattr(ast_context, "return_type")
        lines.append(f"- **Declared Return Type**: {ret if ret is not None else 'None (untyped)'}")
    if hasattr(ast_context, "parameters"):
        params = getattr(ast_context, "parameters", [])
        if params:
            lines.append("- **Parameters**:")
            for p in params:
                p_name = getattr(p, "name", str(p))
                p_type = getattr(p, "type_hint", None)
                p_default = getattr(p, "default_value", None)
                desc = f"  - `{p_name}`: declared type `{p_type or 'untyped'}`"
                if p_default is not None:
                    desc += f" (default: `{p_default}`)"
                lines.append(desc)
        else:
            lines.append("- **Parameters**: None")
    if hasattr(ast_context, "calls") and getattr(ast_context, "calls"):
        lines.append(f"- **Calls Detected in Body**: {', '.join(getattr(ast_context, 'calls'))}")
    if hasattr(ast_context, "parent_class") and getattr(ast_context, "parent_class"):
        lines.append(f"- **Extends**: {getattr(ast_context, 'parent_class')}")
    if hasattr(ast_context, "interfaces") and getattr(ast_context, "interfaces"):
        lines.append(f"- **Implements**: {', '.join(getattr(ast_context, 'interfaces'))}")
    if hasattr(ast_context, "properties") and getattr(ast_context, "properties"):
        props = getattr(ast_context, "properties")
        lines.append("- **Class Properties**:")
        for prop in props:
            pr_name = getattr(prop, "name", str(prop))
            pr_type = getattr(prop, "type_hint", "untyped")
            pr_vis = getattr(prop, "visibility", "public")
            lines.append(f"  - `{pr_vis} {pr_type} {pr_name}`")

    return "\n".join(lines) if lines else str(ast_context)


def format_caller_signatures(
    caller_signatures: Optional[Union[Sequence[Union[str, dict[str, Any]]], str]] = None
) -> str:
    """Format extracted caller signatures and call site argument types."""
    if not caller_signatures:
        return "No external caller signatures or call-site invocations observed."

    if isinstance(caller_signatures, str):
        return caller_signatures.strip()

    lines = []
    for idx, sig in enumerate(caller_signatures, start=1):
        if isinstance(sig, dict):
            caller = sig.get("caller", "unknown")
            args = sig.get("inferred_arguments", sig.get("args", ""))
            line_num = sig.get("line")
            loc = f" (line {line_num})" if line_num else ""
            lines.append(f"{idx}. In `{caller}`{loc}: called with arguments `({args})`")
        else:
            lines.append(f"{idx}. {sig}")
    return "\n".join(lines)


def format_class_properties(
    class_properties: Optional[Union[Sequence[Any], str]] = None
) -> str:
    """Format class property and injected dependency context."""
    if not class_properties:
        return "No class properties or dependency injections provided."

    if isinstance(class_properties, str):
        return class_properties.strip()

    lines = []
    for prop in class_properties:
        if isinstance(prop, str):
            lines.append(f"- {prop}")
        elif isinstance(prop, dict):
            name = prop.get("name", "")
            p_type = prop.get("type", prop.get("type_hint", "mixed"))
            vis = prop.get("visibility", "private")
            lines.append(f"- `{vis} {p_type} {name}`")
        elif hasattr(prop, "name"):
            name = getattr(prop, "name")
            p_type = getattr(prop, "type_hint", "mixed") or "mixed"
            vis = getattr(prop, "visibility", "private")
            lines.append(f"- `{vis} {p_type} {name}`")
        else:
            lines.append(f"- {prop}")
    return "\n".join(lines)


def format_compiler_errors(
    compiler_errors: Optional[Union[Sequence[str], str]] = None
) -> str:
    """Format compiler / PHPStan / RHP errors from previous migration attempts."""
    if not compiler_errors:
        return "None (first attempt)."

    if isinstance(compiler_errors, str):
        return compiler_errors.strip()

    lines = []
    for idx, err in enumerate(compiler_errors, start=1):
        lines.append(f"{idx}. {err}")
    return "\n".join(lines)


def build_user_prompt(
    unit_identifier: str,
    code: str,
    *,
    ast_context: Optional[Any] = None,
    caller_signatures: Optional[Union[Sequence[Union[str, dict[str, Any]]], str]] = None,
    class_properties: Optional[Union[Sequence[Any], str]] = None,
    rules: Optional[Union[Sequence[Union[Rule, str]], RuleRegistry, str]] = None,
    compiler_errors: Optional[Union[Sequence[str], str]] = None,
    patch_type: Optional[PatchType] = None,
    extra_instructions: Optional[str] = None,
    enforce_single_unit: bool = True,
) -> str:
    """Build a comprehensive, strictly-formatted user prompt for migrating a single unit.

    Args:
        unit_identifier: Fully-qualified unit name (e.g. 'App\\Services\\PaymentService::charge').
        code: PHP source code of the single function, method, or class.
        ast_context: AST information (parameters, return type, calls, etc.).
        caller_signatures: Call-site usages and parameter types from other parts of the codebase.
        class_properties: Fields and dependencies belonging to the enclosing class.
        rules: Relevant machine-readable rules from rules.yaml.
        compiler_errors: Errors from previous compilation/type-checking attempts (for retries).
        patch_type: Preferred patch type (FULL_REPLACEMENT or UNIFIED_DIFF).
        extra_instructions: Optional additional guidance for this migration.
        enforce_single_unit: If True, validates that code is not a whole multi-unit file.

    Returns:
        Structured user prompt markdown string.
    """
    if enforce_single_unit:
        validate_single_unit(code, unit_identifier)

    rules_section = format_rules_for_prompt(rules)
    ast_section = format_ast_context(ast_context)
    callers_section = format_caller_signatures(caller_signatures)
    properties_section = format_class_properties(class_properties)
    errors_section = format_compiler_errors(compiler_errors)

    preferred_patch_desc = (
        f"Preferred patch format: `{patch_type.value}`."
        if patch_type
        else "Preferred patch format: `FULL_REPLACEMENT`."
    )

    prompt_parts = [
        f"# Target Migration Unit: `{unit_identifier}`",
        f"{preferred_patch_desc}",
        "",
        "## Source Code to Migrate",
        "```php",
        code.strip(),
        "```",
        "",
        "## AST Symbol Context",
        ast_section,
        "",
        "## Caller Signatures & Call-Site Evidence",
        callers_section,
        "",
        "## Enclosing Class Context & Properties",
        properties_section,
        "",
        "## Applicable RHP Migration Rules & Examples",
        rules_section,
        "",
        "## Previous Compiler Errors (Retry Feedback)",
        errors_section,
    ]

    if extra_instructions:
        prompt_parts.extend([
            "",
            "## Extra Migration Instructions",
            extra_instructions.strip(),
        ])

    prompt_parts.extend([
        "",
        "## Strict Output Instructions",
        "Respond with a single JSON object conforming strictly to the LLMMigrationResult schema.",
        "Required keys:",
        '- `unit_identifier`: string (must match "' + unit_identifier + '")',
        '- `patch_type`: "FULL_REPLACEMENT" | "UNIFIED_DIFF" | "UNRESOLVED"',
        "- `transformed_code`: string (the RHP replacement code or unified diff)",
        "- `resolved_types`: list of objects with {param_name, inferred_type, confidence, rationale}",
        "- `applied_rules`: list of applied rule IDs (e.g. ['RHP-UNSUP-001'])",
        "- `needs_human_review`: boolean (true if ambiguous or manual rewrite required)",
        "- `review_reason`: string or null (explanation if needs_human_review is true)",
        "NO markdown code block wrappers (do NOT wrap in ```json), NO conversational intro or outro.",
    ])

    return "\n".join(prompt_parts)


class MigrationPromptContext(BaseModel):
    """Encapsulates all context necessary to generate system and user prompts."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    unit_identifier: str = Field(..., description="Fully-qualified unit identifier")
    code: str = Field(..., description="Single function, method, or class code")
    ast_context: Optional[Any] = Field(default=None, description="AST symbol context")
    caller_signatures: list[Union[str, dict[str, Any]]] = Field(
        default_factory=list, description="Caller signatures and argument types"
    )
    class_properties: list[Any] = Field(
        default_factory=list, description="Class properties and dependencies"
    )
    rules: list[Union[Rule, str]] = Field(
        default_factory=list, description="Rules applied or referenced"
    )
    compiler_errors: list[str] = Field(
        default_factory=list, description="Compiler or linter error messages"
    )
    patch_type: PatchType = Field(
        default=PatchType.FULL_REPLACEMENT, description="Desired patch type"
    )
    extra_instructions: Optional[str] = Field(
        default=None, description="Additional custom instructions"
    )

    def render_system_prompt(self) -> str:
        """Render the system prompt."""
        return build_system_prompt()

    def render_user_prompt(self, enforce_single_unit: bool = True) -> str:
        """Render the user prompt using this context."""
        return build_user_prompt(
            unit_identifier=self.unit_identifier,
            code=self.code,
            ast_context=self.ast_context,
            caller_signatures=self.caller_signatures,
            class_properties=self.class_properties,
            rules=self.rules,
            compiler_errors=self.compiler_errors,
            patch_type=self.patch_type,
            extra_instructions=self.extra_instructions,
            enforce_single_unit=enforce_single_unit,
        )
