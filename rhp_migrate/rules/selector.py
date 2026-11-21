"""AST-targeted rule retrieval and file-based working memory connector.

Extracts key AST language constructs from PHP files and selects corresponding
migration rules from rules.yaml, while injecting active heuristics from learnings.md
into LLM system prompts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any, Iterable, Optional, Sequence, TYPE_CHECKING, Union

from pydantic import BaseModel, ConfigDict, Field
import tree_sitter

from rhp_migrate.ai.models import PatchType
from rhp_migrate.analyzer.parser import PHPParser
from rhp_migrate.rules.registry import RuleRegistry
from rhp_migrate.rules.schema import Rule

if TYPE_CHECKING:
    from rhp_migrate.ai.prompts import MigrationPromptContext

# ---------------------------------------------------------------------------
# Default mappings between detected AST tags and rule IDs
# ---------------------------------------------------------------------------
DEFAULT_TAG_TO_RULE_IDS: dict[str, list[str]] = {
    "has_php_tags": ["RHP-SYN-001"],
    "has_declare_statements": ["RHP-SYN-002"],
    "has_strict_equality": ["RHP-SYN-003"],
    "has_string_concat": ["RHP-SYN-004"],
    "has_list_destructure": ["RHP-SYN-005"],
    "has_foreach": ["RHP-SYN-006"],
    "has_curl": ["RHP-HTTP-001"],
    "has_superglobals": ["RHP-HTTP-002"],
    "has_dynamic_arrays": ["RHP-SYN-007"],
    "has_prefix_types": ["RHP-SYN-007"],
    "has_typed_vars": ["RHP-SYN-007"],
    "has_echo_or_html": ["RHP-TEM-001"],
    "has_output_buffering": ["RHP-UNSUP-002", "RHP-TEM-001"],
    "has_reflection": ["RHP-UNSUP-001"],
    "has_dynamic_includes": ["RHP-UNSUP-003"],
    "has_traits": ["RHP-TRAIT-001"],
    "has_exceptions": ["RHP-EXC-001"],
    "uses_eloquent_scopes": ["RHP-ELOQ-001"],
}

SUPERGLOBALS = {
    "$_GET",
    "$_POST",
    "$_REQUEST",
    "$_SERVER",
    "$_COOKIE",
    "$_SESSION",
    "$_FILES",
    "$_ENV",
    "$GLOBALS",
}

ARRAY_FUNCTIONS = {
    "array_merge",
    "array_push",
    "array_pop",
    "array_shift",
    "array_unshift",
    "array_keys",
    "array_values",
    "array_map",
    "array_filter",
    "array_reduce",
    "in_array",
    "array_key_exists",
    "is_array",
    "array_slice",
    "array_splice",
    "array_combine",
}

REFLECTION_SYMBOLS = {
    "ReflectionClass",
    "ReflectionMethod",
    "ReflectionProperty",
    "ReflectionFunction",
    "ReflectionParameter",
}


# ---------------------------------------------------------------------------
# Learning Memory Model & Parser
# ---------------------------------------------------------------------------

class LearningEntry(BaseModel):
    """Structured entry parsed from learnings.md persistent working memory."""
    model_config = ConfigDict(extra="ignore")

    date: str = Field(..., description="Date entry logged (YYYY-MM-DD)")
    title: str = Field(..., description="Title of the learning")
    context_symptom: Optional[str] = Field(None, description="Context / Symptom")
    root_cause: Optional[str] = Field(None, description="Root cause analysis")
    working_solution: Optional[str] = Field(None, description="Verified working solution")
    verification: Optional[str] = Field(None, description="How solution was verified")
    key_heuristic: Optional[str] = Field(None, description="Actionable 1-2 sentence heuristic")
    is_graduated: bool = Field(False, description="Whether entry has graduated to core rules")
    raw_markdown: Optional[str] = Field(None, description="Full raw markdown snippet")


def find_learnings_file() -> Path:
    """Locate learnings.md by searching parent directories starting from current file or cwd."""
    env_path = os.environ.get("RHP_LEARNINGS_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path).resolve()
    candidates = [
        Path.cwd() / "learnings.md",
        Path(__file__).resolve().parent.parent.parent / "learnings.md",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return candidates[0]


def extract_learnings_from_markdown(content: str) -> list[LearningEntry]:
    """Parse structured markdown entries from learnings.md content."""
    entry_pattern = re.compile(
        r"###\s+\[(?P<date>\d{4}-\d{2}-\d{2})\]\s+(?P<title>[^\n]+)\n(?P<body>(?:(?!###|\Z)[\s\S])+)",
        re.MULTILINE,
    )

    entries: list[LearningEntry] = []
    # Identify graduation cutoff if present
    graduation_pos = content.find("## 🔄 Graduation Pipeline")

    for match in entry_pattern.finditer(content):
        date = match.group("date").strip()
        title = match.group("title").strip()
        body = match.group("body")
        raw_snippet = match.group(0).strip()
        is_graduated = (graduation_pos != -1 and match.start() > graduation_pos) or "graduated" in title.lower()

        def extract_field(label: str) -> Optional[str]:
            field_pat = re.compile(
                rf"-\s+\*\*{re.escape(label)}\*\*:\s*(?P<val>.+?)(?=\n\s*-\s+\*\*|\Z)",
                re.DOTALL,
            )
            f_match = field_pat.search(body)
            return f_match.group("val").strip() if f_match else None

        entries.append(
            LearningEntry(
                date=date,
                title=title,
                context_symptom=extract_field("Context / Symptom"),
                root_cause=extract_field("Root Cause"),
                working_solution=extract_field("Working Solution"),
                verification=extract_field("Verification"),
                key_heuristic=extract_field("Key Heuristic"),
                is_graduated=is_graduated,
                raw_markdown=raw_snippet,
            )
        )

    return entries


def load_active_learnings(learnings_path: Optional[Union[str, Path]] = None) -> list[LearningEntry]:
    """Load active (non-graduated or core actionable) learnings from learnings.md."""
    path = Path(learnings_path) if learnings_path else find_learnings_file()
    if not path.is_file():
        return []
    content = path.read_text(encoding="utf-8")
    return extract_learnings_from_markdown(content)


def extract_actionable_heuristics(
    learnings_path: Optional[Union[str, Path]] = None,
    content: Optional[str] = None,
) -> list[str]:
    """Extract actionable heuristic statements formatted as bullet items for system prompts."""
    if content is None:
        path = Path(learnings_path) if learnings_path else find_learnings_file()
        if not path.is_file():
            return []
        content = path.read_text(encoding="utf-8")

    entries = extract_learnings_from_markdown(content)
    heuristics: list[str] = []
    for e in entries:
        if e.key_heuristic:
            # Clean title removing any graduation notes
            clean_title = re.sub(r"\*\(.*?\)\*", "", e.title).strip()
            heuristics.append(f"**{clean_title}**: {e.key_heuristic}")
        elif e.working_solution:
            clean_title = re.sub(r"\*\(.*?\)\*", "", e.title).strip()
            first_sentence = e.working_solution.split("\n")[0].strip("- ")
            heuristics.append(f"**{clean_title}**: {first_sentence}")

    return heuristics


# ---------------------------------------------------------------------------
# AST Tag Detection Service
# ---------------------------------------------------------------------------

def detect_ast_tags(
    source_or_tree: Union[str, bytes, tree_sitter.Tree, Path],
    parser: Optional[PHPParser] = None,
) -> set[str]:
    """Parse PHP source code and extract AST language constructs / tags.

    Tags detected:
      - `has_superglobals`: $_GET, $_POST, $_REQUEST, etc.
      - `has_dynamic_arrays`: array(), [], $a[] = ..., array_* functions
      - `has_traits`: trait Foo { ... }, use TraitName inside class
      - `has_exceptions`: try, catch, throw, Exception types
      - `uses_eloquent_scopes`: scopeActive($query), Eloquent Scope usage
      - `has_curl`: curl_* function calls
      - `has_strict_equality`: ===, !==
      - `has_string_concat`: ., .=
      - `has_list_destructure`: list(...) or [$a, $b] assignment
      - `has_foreach`: foreach (...)
      - `has_php_tags`: <?php, <?=, ?>
      - `has_declare_statements`: declare(...)
      - `has_reflection`: ReflectionClass, ReflectionMethod, eval, $$var
      - `has_output_buffering`: ob_start, ob_get_clean, etc.
      - `has_dynamic_includes`: include, require, include_once, require_once
      - `has_echo_or_html`: echo ..., inline HTML text
      - `has_prefix_types`: Typed properties or prefix variable declarations
    """
    p = parser or PHPParser()

    if isinstance(source_or_tree, Path):
        tree = p.parse_file(source_or_tree)
        source_text = source_or_tree.read_text(encoding="utf-8", errors="replace")
    elif isinstance(source_or_tree, (str, bytes)):
        source_text = source_or_tree if isinstance(source_or_tree, str) else source_or_tree.decode("utf-8", errors="replace")
        tree = p.parse(source_or_tree)
    elif isinstance(source_or_tree, tree_sitter.Tree):
        tree = source_or_tree
        source_text = p.get_node_text(tree.root_node)
    else:
        raise TypeError(f"Unsupported source type: {type(source_or_tree)}")

    tags: set[str] = set()

    # Fast source check for PHP delimiters
    if "<?" in source_text or "?>" in source_text:
        tags.add("has_php_tags")

    # Traverse AST nodes
    for node in p.traverse(tree.root_node):
        ntype = node.type

        # 1. Superglobals
        if ntype == "variable_name":
            var_text = p.get_node_text(node)
            if var_text in SUPERGLOBALS:
                tags.add("has_superglobals")
        elif ntype == "name":
            # Sometimes name inside variable_expression without leading $
            name_text = p.get_node_text(node)
            if f"${name_text}" in SUPERGLOBALS or name_text in {"_GET", "_POST", "_REQUEST", "_SERVER", "_SESSION", "_COOKIE"}:
                tags.add("has_superglobals")

        # 2. Dynamic arrays
        elif ntype == "array_creation_expression":
            tags.add("has_dynamic_arrays")
        elif ntype == "subscript_expression":
            # Check for appending $x[] = ...
            child_types = [c.type for c in node.children]
            if child_types == ["variable_name", "[", "]"] or (
                "[" in child_types and "]" in child_types and len(child_types) <= 3
            ):
                tags.add("has_dynamic_arrays")

        # 3. Traits
        elif ntype == "trait_declaration":
            tags.add("has_traits")
        elif ntype == "use_declaration":
            # Check if this use statement is inside a class declaration body (trait use)
            curr = node.parent
            while curr is not None:
                if curr.type in ("class_declaration", "enum_declaration"):
                    tags.add("has_traits")
                    break
                curr = curr.parent

        # 4. Exceptions
        elif ntype in ("throw_expression", "try_statement", "catch_clause"):
            tags.add("has_exceptions")

        # 5. Eloquent scopes
        elif ntype == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                m_name = p.get_node_text(name_node)
                if m_name.startswith("scope") and len(m_name) > 5 and m_name[5].isupper():
                    tags.add("uses_eloquent_scopes")

        # 6. Strict equality
        elif ntype == "binary_expression":
            op_node = None
            for child in node.children:
                if child.type in ("===", "!=="):
                    tags.add("has_strict_equality")
                elif child.type == ".":
                    tags.add("has_string_concat")

        # 7. String concatenation assignment (.=)
        elif ntype == "augmented_assignment_expression":
            for child in node.children:
                if child.type == ".=":
                    tags.add("has_string_concat")

        # 8. List destructuring
        elif ntype == "list_literal":
            tags.add("has_list_destructure")

        # 9. Foreach loop
        elif ntype == "foreach_statement":
            tags.add("has_foreach")

        # 10. Declare statements
        elif ntype == "declare_statement":
            tags.add("has_declare_statements")

        # 11. Dynamic file includes
        elif ntype in ("include_expression", "require_expression", "include_once_expression", "require_once_expression"):
            tags.add("has_dynamic_includes")

        # 12. Echo / Output / Inline text
        elif ntype in ("echo_statement", "inline_text", "text_interpolation"):
            tags.add("has_echo_or_html")

        # 13. Calls: curl_*, ob_*, array_*, Reflection
        elif ntype == "function_call_expression":
            func_node = node.child_by_field_name("function")
            if func_node:
                f_name = p.get_node_text(func_node).lower()
                if f_name.startswith("curl_"):
                    tags.add("has_curl")
                elif f_name.startswith("ob_"):
                    tags.add("has_output_buffering")
                elif f_name in ARRAY_FUNCTIONS:
                    tags.add("has_dynamic_arrays")
                elif f_name in ("eval",):
                    tags.add("has_reflection")

        # 14. Reflection object creations (new ReflectionClass(...))
        elif ntype == "object_creation_expression":
            class_node = node.child_by_field_name("class") or (node.children[1] if len(node.children) > 1 else None)
            if class_node:
                c_name = p.get_node_text(class_node)
                if any(sym in c_name for sym in REFLECTION_SYMBOLS):
                    tags.add("has_reflection")

        # 15. Property / Typed variable declarations
        elif ntype == "property_declaration":
            tags.add("has_typed_vars")

    return tags


# ---------------------------------------------------------------------------
# Rule Selector Service
# ---------------------------------------------------------------------------

def select_rules(
    source_or_tree: Union[str, bytes, tree_sitter.Tree, Path],
    registry: Optional[RuleRegistry] = None,
    detected_tags: Optional[set[str]] = None,
    parser: Optional[PHPParser] = None,
) -> list[Rule]:
    """Filter rules.yaml and select only the rules matching detected AST tags.

    Args:
        source_or_tree: PHP source code, AST Tree, or file Path.
        registry: RuleRegistry instance (defaults to RuleRegistry.default()).
        detected_tags: Pre-computed tag set, or None to detect automatically.
        parser: Optional custom PHPParser.

    Returns:
        List of Rule objects matching the detected AST constructs, in registry order.
    """
    reg = registry or RuleRegistry.default()
    tags = detected_tags if detected_tags is not None else detect_ast_tags(source_or_tree, parser=parser)

    if not tags:
        return []

    # Identify rule IDs triggered by default tag mappings
    mapped_rule_ids: set[str] = set()
    for tag in tags:
        for rule_id in DEFAULT_TAG_TO_RULE_IDS.get(tag, []):
            mapped_rule_ids.add(rule_id)

    selected: list[Rule] = []
    for rule in reg.all_rules():
        # 1. Match if rule has explicit tags intersecting detected tags
        if rule.tags and any(t in tags for t in rule.tags):
            selected.append(rule)
            continue

        # 2. Match if rule ID is mapped to any detected tag
        if rule.id in mapped_rule_ids:
            selected.append(rule)

    return selected


# ---------------------------------------------------------------------------
# System Prompt Builder with Injected Learnings & Working Memory
# ---------------------------------------------------------------------------

def build_system_prompt_with_learnings(
    rules: Optional[Sequence[Rule]] = None,
    heuristics: Optional[Sequence[str]] = None,
    base_prompt: Optional[str] = None,
    include_memory: bool = True,
    learnings_path: Optional[Union[str, Path]] = None,
) -> str:
    """Build the authoritative system prompt with injected rules and working memory heuristics.

    Args:
        rules: Target AST-matched Rule objects to inject into prompt context.
        heuristics: Actionable heuristics from learnings.md. If None and include_memory=True,
                    they are loaded automatically.
        base_prompt: Optional custom base prompt string. Defaults to SYSTEM_PROMPT.
        include_memory: Whether to inject learnings.md active working heuristics.
        learnings_path: Optional path override for learnings.md.

    Returns:
        Augmented system prompt markdown string.
    """
    if base_prompt is None:
        from rhp_migrate.ai.prompts import SYSTEM_PROMPT
        base_prompt = SYSTEM_PROMPT
    prompt = base_prompt.strip()
    sections: list[str] = [prompt]

    # 1. Inject Working Memory Heuristics from learnings.md
    if include_memory:
        active_heuristics = list(heuristics) if heuristics is not None else extract_actionable_heuristics(learnings_path=learnings_path)
        if active_heuristics:
            sections.append(
                "\n\n### Persistent Working Memory & Proven Heuristics (from learnings.md)\n"
                "Adhere strictly to these operational insights and verified transformation patterns "
                "discovered during previous migration turns:\n"
            )
            for idx, h in enumerate(active_heuristics, start=1):
                sections.append(f"{idx}. {h}")

    # 2. Inject AST-Targeted Migration Rules
    if rules:
        sections.append(
            "\n\n### AST-Targeted RHP Migration Rules\n"
            "The following migration rules specifically apply to language constructs detected in the target code:\n"
        )
        for rule in rules:
            sections.append(rule.format_prompt_block())
            sections.append("")

    return "\n".join(sections).strip()


# ---------------------------------------------------------------------------
# Learned Fix Formatter & Appender
# ---------------------------------------------------------------------------

def format_learned_fix_entry(
    title: str,
    *,
    context_symptom: Optional[str] = None,
    root_cause: Optional[str] = None,
    working_solution: Optional[str] = None,
    verification: Optional[str] = None,
    key_heuristic: Optional[str] = None,
    date: Optional[str] = None,
) -> str:
    """Format a structured fix entry conforming to the learnings.md schema."""
    entry_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"### [{entry_date}] {title.strip()}",
        f"- **Context / Symptom**: {context_symptom.strip() if context_symptom else 'Manual code fix during workbench migration.'}",
        f"- **Root Cause**: {root_cause.strip() if root_cause else 'Unresolved PHP-to-RHP type divergence or syntax mismatch.'}",
        f"- **Working Solution**: {working_solution.strip() if working_solution else 'Applied verified replacement pattern in workbench.'}",
        f"- **Verification**: {verification.strip() if verification else 'Verified via compiler check and differential audit.'}",
        f"- **Key Heuristic**: {key_heuristic.strip() if key_heuristic else 'Apply this pattern when similar compiler errors emerge.'}",
    ]
    return "\n".join(lines)


def append_learned_fix(
    learnings_path: Optional[Union[str, Path]] = None,
    entry_markdown: Optional[str] = None,
    *,
    title: Optional[str] = None,
    context_symptom: Optional[str] = None,
    root_cause: Optional[str] = None,
    working_solution: Optional[str] = None,
    verification: Optional[str] = None,
    key_heuristic: Optional[str] = None,
) -> Path:
    """Append a structured learning entry to learnings.md under Active Working Learnings.

    Args:
        learnings_path: Path to learnings.md.
        entry_markdown: Pre-formatted markdown block, or None to generate from keyword args.
        title: Title of learning entry.
        context_symptom: Context / Symptom description.
        root_cause: Root cause explanation.
        working_solution: Working code solution.
        verification: Verification method.
        key_heuristic: Key heuristic rule-of-thumb.

    Returns:
        Path to the modified learnings.md file.
    """
    path = Path(learnings_path) if learnings_path else find_learnings_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    block = entry_markdown or format_learned_fix_entry(
        title=title or "Workbench Migration Fix",
        context_symptom=context_symptom,
        root_cause=root_cause,
        working_solution=working_solution,
        verification=verification,
        key_heuristic=key_heuristic,
    )

    if not path.is_file():
        # Initialize learnings.md if it doesn't exist
        header = (
            "# System Memory & Working Learnings (`learnings.md`)\n\n"
            "## 🧠 Active Working Learnings\n\n"
        )
        path.write_text(f"{header}{block}\n", encoding="utf-8")
        return path

    content = path.read_text(encoding="utf-8")

    # Insert right before Graduation Pipeline if section exists, else append
    grad_marker = "## 🔄 Graduation Pipeline"
    if grad_marker in content:
        parts = content.split(grad_marker, 1)
        new_content = f"{parts[0].rstrip()}\n\n{block}\n\n---\n\n{grad_marker}{parts[1]}"
    else:
        new_content = f"{content.rstrip()}\n\n{block}\n"

    path.write_text(new_content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# High-Level RuleSelector Service Class
# ---------------------------------------------------------------------------

class RuleSelector:
    """Unified service for AST tag detection, rule filtering, and prompt augmentation."""

    def __init__(
        self,
        registry: Optional[RuleRegistry] = None,
        learnings_path: Optional[Union[str, Path]] = None,
        parser: Optional[PHPParser] = None,
    ) -> None:
        self.registry = registry or RuleRegistry.default()
        self.learnings_path = Path(learnings_path) if learnings_path else find_learnings_file()
        self.parser = parser or PHPParser()

    def detect_tags(self, source_or_tree: Union[str, bytes, tree_sitter.Tree, Path]) -> set[str]:
        """Detect language construct tags from PHP code or AST."""
        return detect_ast_tags(source_or_tree, parser=self.parser)

    def select_rules(self, source_or_tree: Union[str, bytes, tree_sitter.Tree, Path]) -> list[Rule]:
        """Retrieve all rules from registry matching AST constructs in source."""
        return select_rules(source_or_tree, registry=self.registry, parser=self.parser)

    def get_active_learnings(self) -> list[LearningEntry]:
        """Load structured entries from learnings.md."""
        return load_active_learnings(self.learnings_path)

    def get_active_heuristics(self) -> list[str]:
        """Load actionable heuristic statements from learnings.md."""
        return extract_actionable_heuristics(self.learnings_path)

    def build_system_prompt(
        self,
        source_or_tree: Optional[Union[str, bytes, tree_sitter.Tree, Path]] = None,
        rules: Optional[Sequence[Rule]] = None,
        heuristics: Optional[Sequence[str]] = None,
    ) -> str:
        """Build the system prompt with AST-matched rules and working memory heuristics."""
        target_rules = rules
        if target_rules is None and source_or_tree is not None:
            target_rules = self.select_rules(source_or_tree)

        active_heuristics = heuristics or self.get_active_heuristics()

        return build_system_prompt_with_learnings(
            rules=target_rules,
            heuristics=active_heuristics,
            learnings_path=self.learnings_path,
        )

    def build_prompt_context(
        self,
        source: str,
        unit_identifier: str,
        *,
        ast_context: Optional[Any] = None,
        caller_signatures: Optional[Union[Sequence[Union[str, dict[str, Any]]], str]] = None,
        class_properties: Optional[Union[Sequence[Any], str]] = None,
        compiler_errors: Optional[Union[Sequence[str], str]] = None,
        patch_type: Optional[PatchType] = None,
        extra_instructions: Optional[str] = None,
    ) -> MigrationPromptContext:
        """Create a full MigrationPromptContext with AST-selected rules."""
        from rhp_migrate.ai.prompts import MigrationPromptContext
        matched_rules = self.select_rules(source)
        return MigrationPromptContext(
            unit_identifier=unit_identifier,
            code=source,
            ast_context=ast_context,
            caller_signatures=list(caller_signatures) if isinstance(caller_signatures, (list, tuple)) else [],
            class_properties=list(class_properties) if isinstance(class_properties, (list, tuple)) else [],
            rules=matched_rules,
            compiler_errors=list(compiler_errors) if isinstance(compiler_errors, (list, tuple)) else [],
            patch_type=patch_type or PatchType.FULL_REPLACEMENT,
            extra_instructions=extra_instructions,
        )
