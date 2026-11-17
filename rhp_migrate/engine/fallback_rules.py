"""AST-based deterministic fallback transforms using tree-sitter and end-to-start string splicing.

Handles patterns where native `rhp migrate` does not yet execute rewrites, including:
- Multibyte string function mappings (mb_* -> Str::*, Rune::*)
- Laravel facade calls (\\Illuminate\\Support\\Facades\\* -> Facade::*)
- Redundant Laravel facade use declarations (use Illuminate\\Support\\Facades\\*;)
- Facade method standardizations (File::get -> File::read, File::put -> File::write)
- Laravel global helpers (now(), today(), logger(), response()->json(), request())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union
import tree_sitter

from rhp_migrate.analyzer.parser import PHPParser


@dataclass
class TextEdit:
    """Represents a text replacement within exact byte bounds of a source file."""

    start_byte: int
    end_byte: int
    replacement: str
    rule_id: str = ""
    description: str = ""


@dataclass
class FallbackTransformResult:
    """Outcome of running fallback transforms on source code."""

    original_code: str
    transformed_code: str
    modified: bool
    edits: list[TextEdit] = field(default_factory=list)
    rules_applied: list[str] = field(default_factory=list)


def apply_text_edits(source: str, edits: Sequence[TextEdit]) -> str:
    """Apply a collection of TextEdits by splicing the source from end-to-start.

    Splicing in descending order of start_byte ensures that earlier byte offsets
    remain completely valid and unaffected by replacements later in the file.
    Overlapping edits are filtered out, keeping the outermost/first edit.

    Args:
        source: Original source text.
        edits: Collection of TextEdits.

    Returns:
        Modified source string.
    """
    if not edits:
        return source

    source_bytes = bytearray(source.encode("utf-8"))

    # Sort ascending by start_byte; for ties, larger span first (longer span)
    sorted_forward = sorted(
        edits,
        key=lambda e: (e.start_byte, -(e.end_byte - e.start_byte)),
    )

    non_overlapping: list[TextEdit] = []
    current_end = -1

    for edit in sorted_forward:
        if edit.start_byte >= current_end:
            non_overlapping.append(edit)
            current_end = edit.end_byte

    # Apply in reverse order (end-to-start) so byte offsets remain strictly valid
    for edit in reversed(non_overlapping):
        repl_bytes = edit.replacement.encode("utf-8")
        source_bytes[edit.start_byte : edit.end_byte] = repl_bytes

    return source_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# Multibyte (mb_*) Function Transforms
# ---------------------------------------------------------------------------

# Direct 1-to-1 function name mappings (when arguments format matches directly)
_MB_DIRECT_FUNCTION_MAP = {
    "mb_str_contains": ("Str::contains", "RHP-FALLBACK-MB-CONTAINS"),
    "mb_str_starts_with": ("Str::startsWith", "RHP-FALLBACK-MB-STARTS-WITH"),
    "mb_str_ends_with": ("Str::endsWith", "RHP-FALLBACK-MB-ENDS-WITH"),
}


def transform_mb_functions(
    source: str,
    root_node: tree_sitter.Node,
) -> list[TextEdit]:
    """Transform legacy mb_* multibyte string function calls to Str::* and Rune::*.

    RHP strings are strictly UTF-8 natively; redundant charset/encoding parameters
    (e.g. 'UTF-8') are safely dropped.
    """
    edits: list[TextEdit] = []

    for node in PHPParser.traverse(root_node):
        if node.type != "function_call_expression":
            continue

        fn_node = node.child_by_field_name("function")
        if fn_node is None:
            continue

        fn_name = PHPParser.get_node_text(fn_node).strip().lower()
        if not fn_name.startswith("mb_"):
            continue

        args_node = node.child_by_field_name("arguments")
        if args_node is None:
            continue

        arg_nodes = [c for c in args_node.children if c.type == "argument"]
        arg_texts = [PHPParser.get_node_text(a).strip() for a in arg_nodes]

        # 1. mb_strlen($str, $encoding = null) -> Str::length($str)
        if fn_name == "mb_strlen":
            if len(arg_texts) >= 1:
                replacement = f"Str::length({arg_texts[0]})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-STRLEN",
                        description=f"Rewrite mb_strlen to Str::length",
                    )
                )

        # 2. mb_substr($str, $start, $length = null, $encoding = null) -> Str::substr(...)
        elif fn_name == "mb_substr":
            if len(arg_texts) >= 2:
                # Up to 3 parameters kept (str, start, length); 4th (encoding) dropped
                kept_args = arg_texts[: min(3, len(arg_texts))]
                replacement = f"Str::substr({', '.join(kept_args)})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-SUBSTR",
                        description=f"Rewrite mb_substr to Str::substr",
                    )
                )

        # 3. mb_strpos($haystack, $needle, $offset = 0, $encoding = null) -> Str::find(...)
        elif fn_name == "mb_strpos":
            if len(arg_texts) >= 2:
                # Up to 3 parameters kept (haystack, needle, offset); 4th (encoding) dropped
                kept_args = arg_texts[: min(3, len(arg_texts))]
                replacement = f"Str::find({', '.join(kept_args)})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-STRPOS",
                        description=f"Rewrite mb_strpos to Str::find",
                    )
                )

        # 4. mb_strrpos($haystack, $needle, $offset = 0, $encoding = null) -> Str::fromLast(...)
        elif fn_name == "mb_strrpos":
            if len(arg_texts) >= 2:
                kept_args = arg_texts[: min(3, len(arg_texts))]
                replacement = f"Str::fromLast({', '.join(kept_args)})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-STRRPOS",
                        description=f"Rewrite mb_strrpos to Str::fromLast",
                    )
                )

        # 5. mb_strtolower($str, $encoding = null) -> Str::lowerCase($str)
        elif fn_name == "mb_strtolower":
            if len(arg_texts) >= 1:
                replacement = f"Str::lowerCase({arg_texts[0]})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-LOWERCASE",
                        description=f"Rewrite mb_strtolower to Str::lowerCase",
                    )
                )

        # 6. mb_strtoupper($str, $encoding = null) -> Str::upperCase($str)
        elif fn_name == "mb_strtoupper":
            if len(arg_texts) >= 1:
                replacement = f"Str::upperCase({arg_texts[0]})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-UPPERCASE",
                        description=f"Rewrite mb_strtoupper to Str::upperCase",
                    )
                )

        # 7. mb_ord($char, $encoding = null) -> Rune::ofFirst($char)
        elif fn_name == "mb_ord":
            if len(arg_texts) >= 1:
                replacement = f"Rune::ofFirst({arg_texts[0]})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-ORD",
                        description=f"Rewrite mb_ord to Rune::ofFirst",
                    )
                )

        # 8. mb_chr($cp, $encoding = null) -> Rune::from($cp)
        elif fn_name == "mb_chr":
            if len(arg_texts) >= 1:
                replacement = f"Rune::from({arg_texts[0]})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-CHR",
                        description=f"Rewrite mb_chr to Rune::from",
                    )
                )

        # 9. mb_strwidth($char, $encoding = null) -> Rune::width($char)
        elif fn_name == "mb_strwidth":
            if len(arg_texts) >= 1:
                replacement = f"Rune::width({arg_texts[0]})"
                edits.append(
                    TextEdit(
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        replacement=replacement,
                        rule_id="RHP-FALLBACK-MB-STRWIDTH",
                        description=f"Rewrite mb_strwidth to Rune::width",
                    )
                )

        # 10. mb_str_split($str, $split_length = 1, $encoding = null) -> Str::split($str, $split_length)
        elif fn_name == "mb_str_split":
            if len(arg_texts) == 1:
                replacement = f"Str::split('', {arg_texts[0]})"
            else:
                replacement = f"Str::split({', '.join(arg_texts[:2])})"
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement=replacement,
                    rule_id="RHP-FALLBACK-MB-STR-SPLIT",
                    description=f"Rewrite mb_str_split to Str::split",
                )
            )

        # 11. mb_check_encoding($var = null, $encoding = null) -> true
        elif fn_name == "mb_check_encoding":
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement="true",
                    rule_id="RHP-FALLBACK-MB-CHECK-ENCODING",
                    description=f"Rewrite mb_check_encoding to true (RHP strictly enforces UTF-8)",
                )
            )

        # 12. Direct 1-to-1 mappings
        elif fn_name in _MB_DIRECT_FUNCTION_MAP:
            target_fn, rule_id = _MB_DIRECT_FUNCTION_MAP[fn_name]
            replacement = f"{target_fn}({', '.join(arg_texts)})"
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement=replacement,
                    rule_id=rule_id,
                    description=f"Rewrite {fn_name} to {target_fn}",
                )
            )

    return edits


# ---------------------------------------------------------------------------
# Laravel Facade Transforms
# ---------------------------------------------------------------------------

# Canonical Laravel facades that map directly to native RHP Tier 1/2 facades
KNOWN_LARAVEL_FACADES = {
    "Log",
    "DB",
    "Http",
    "Request",
    "Response",
    "Session",
    "Cookie",
    "File",
    "App",
    "Cache",
    "Crypt",
    "Hash",
    "Route",
    "Schema",
    "Config",
    "Str",
    "Arr",
    "Time",
    "Carbon",
}


def _extract_facade_name(scope_text: str) -> Optional[str]:
    """Extract simple facade name from fully-qualified or namespace-qualified scope."""
    clean = scope_text.lstrip("\\")
    if clean.startswith("Illuminate\\Support\\Facades\\"):
        return clean.split("\\")[-1]
    if clean.startswith("Illuminate\\Support\\"):
        return clean.split("\\")[-1]
    if clean in KNOWN_LARAVEL_FACADES:
        return clean
    return None


def transform_laravel_facades(
    source: str,
    root_node: tree_sitter.Node,
) -> list[TextEdit]:
    """Transform Laravel facade scoped calls to native RHP facades and method names.

    E.g.:
    - \\Illuminate\\Support\\Facades\\Log::info(...) -> Log::info(...)
    - File::get(...) -> File::read(...)
    - File::put(...) -> File::write(...)
    - Hash::make(...) -> Crypt::hash(...)
    - Hash::check(...) -> Crypt::verify(...)
    """
    edits: list[TextEdit] = []

    for node in PHPParser.traverse(root_node):
        if node.type != "scoped_call_expression":
            continue

        scope_node = node.child_by_field_name("scope")
        name_node = node.child_by_field_name("name")
        args_node = node.child_by_field_name("arguments")

        if scope_node is None or name_node is None:
            continue

        scope_text = PHPParser.get_node_text(scope_node).strip()
        method_name = PHPParser.get_node_text(name_node).strip()
        facade_name = _extract_facade_name(scope_text)

        if facade_name is None:
            continue

        target_facade = facade_name
        target_method = method_name
        rule_id = "RHP-FALLBACK-LARAVEL-FACADE"

        # Specific method renames
        if facade_name == "File":
            if method_name == "get":
                target_method = "read"
                rule_id = "RHP-FALLBACK-FILE-READ"
            elif method_name == "put":
                target_method = "write"
                rule_id = "RHP-FALLBACK-FILE-WRITE"
        elif facade_name == "Hash":
            target_facade = "Crypt"
            if method_name == "make":
                target_method = "hash"
                rule_id = "RHP-FALLBACK-HASH-MAKE"
            elif method_name == "check":
                target_method = "verify"
                rule_id = "RHP-FALLBACK-HASH-CHECK"

        # If scope or method changed, replace the call head (scope::method)
        if target_facade != scope_text or target_method != method_name:
            call_head_start = scope_node.start_byte
            call_head_end = name_node.end_byte
            replacement = f"{target_facade}::{target_method}"

            edits.append(
                TextEdit(
                    start_byte=call_head_start,
                    end_byte=call_head_end,
                    replacement=replacement,
                    rule_id=rule_id,
                    description=f"Rewrite {scope_text}::{method_name} to {replacement}",
                )
            )

    return edits


def transform_laravel_use_statements(
    source: str,
    root_node: tree_sitter.Node,
) -> list[TextEdit]:
    """Strip redundant use statements for built-in RHP native facades.

    RHP provides Tier 1 facades (Log, DB, Http, Str, Arr, File, Request, Response,
    Session, Cookie, App, Crypt) globally in scope without import requirements.
    """
    edits: list[TextEdit] = []
    source_bytes = source.encode("utf-8")

    for node in PHPParser.traverse(root_node):
        if node.type != "namespace_use_declaration":
            continue

        text = PHPParser.get_node_text(node).strip()
        # Matches `use Illuminate\Support\Facades\...;` or `use Illuminate\Support\Str;` etc.
        if "Illuminate\\Support\\Facades\\" in text or (
            "Illuminate\\Support\\" in text
            and any(f"Illuminate\\Support\\{f}" in text for f in ("Str", "Arr"))
        ):
            # Also consume trailing newline so blank lines don't accumulate
            end_byte = node.end_byte
            if end_byte < len(source_bytes) and source_bytes[end_byte : end_byte + 1] == b"\n":
                end_byte += 1
            elif (
                end_byte + 1 < len(source_bytes)
                and source_bytes[end_byte : end_byte + 2] == b"\r\n"
            ):
                end_byte += 2

            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=end_byte,
                    replacement="",
                    rule_id="RHP-FALLBACK-STRIP-FACADE-USE",
                    description=f"Strip redundant Laravel facade import: {text}",
                )
            )

    return edits


# ---------------------------------------------------------------------------
# Laravel Global Helper Transforms
# ---------------------------------------------------------------------------

def transform_laravel_helpers(
    source: str,
    root_node: tree_sitter.Node,
) -> list[TextEdit]:
    """Transform common Laravel global helper functions into RHP facade calls.

    Patterns:
    - now() -> Time::now()
    - today() -> Time::today()
    - logger('msg') -> Log::info('msg')
    - logger()->info(...) -> Log::info(...)
    - info('msg') -> Log::info('msg')
    - response()->json(...) -> Response::json(...)
    - response()->make(...) -> Response::make(...)
    - request()->get(...) -> Request::get(...)
    - request('key') -> Request::get('key')
    - session('key') -> Session::get('key')
    - cookie('key', 'val') -> Cookie::set('key', 'val')
    """
    edits: list[TextEdit] = []
    # Set of function_call_expression nodes that are part of member calls handled separately
    handled_inner_calls: set[int] = set()

    # Pass 1: Handle chained helper member calls (e.g. logger()->info(...), response()->json(...))
    for node in PHPParser.traverse(root_node):
        if node.type != "member_call_expression":
            continue

        obj_node = node.child_by_field_name("object")
        method_node = node.child_by_field_name("name")
        args_node = node.child_by_field_name("arguments")

        if obj_node is None or method_node is None or args_node is None:
            continue

        if obj_node.type != "function_call_expression":
            continue

        inner_fn_node = obj_node.child_by_field_name("function")
        if inner_fn_node is None:
            continue

        helper_name = PHPParser.get_node_text(inner_fn_node).strip().lower()
        method_name = PHPParser.get_node_text(method_node).strip()
        args_text = PHPParser.get_node_text(args_node).strip()

        facade_map = {
            "logger": "Log",
            "response": "Response",
            "request": "Request",
            "session": "Session",
            "cookie": "Cookie",
        }

        if helper_name in facade_map:
            facade = facade_map[helper_name]
            handled_inner_calls.add(obj_node.start_byte)
            replacement = f"{facade}::{method_name}{args_text}"
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement=replacement,
                    rule_id=f"RHP-FALLBACK-HELPER-{helper_name.upper()}-CHAIN",
                    description=f"Rewrite {helper_name}()->{method_name} to {facade}::{method_name}",
                )
            )

    # Pass 2: Standalone helper function calls
    for node in PHPParser.traverse(root_node):
        if node.type != "function_call_expression":
            continue

        if node.start_byte in handled_inner_calls:
            continue

        fn_node = node.child_by_field_name("function")
        if fn_node is None:
            continue

        fn_name = PHPParser.get_node_text(fn_node).strip().lower()
        args_node = node.child_by_field_name("arguments")
        if args_node is None:
            continue

        arg_nodes = [c for c in args_node.children if c.type == "argument"]
        arg_texts = [PHPParser.get_node_text(a).strip() for a in arg_nodes]

        # now() -> Time::now()
        if fn_name == "now" and len(arg_texts) == 0:
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement="Time::now()",
                    rule_id="RHP-FALLBACK-HELPER-NOW",
                    description="Rewrite now() to Time::now()",
                )
            )

        # today() -> Time::today()
        elif fn_name == "today" and len(arg_texts) == 0:
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement="Time::today()",
                    rule_id="RHP-FALLBACK-HELPER-TODAY",
                    description="Rewrite today() to Time::today()",
                )
            )

        # logger('msg') or info('msg') -> Log::info('msg')
        elif fn_name in ("logger", "info") and len(arg_texts) >= 1:
            replacement = f"Log::info({', '.join(arg_texts)})"
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement=replacement,
                    rule_id="RHP-FALLBACK-HELPER-LOG-INFO",
                    description=f"Rewrite {fn_name}() to Log::info()",
                )
            )

        # request('key') -> Request::get('key')
        elif fn_name == "request" and len(arg_texts) == 1:
            replacement = f"Request::get({arg_texts[0]})"
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement=replacement,
                    rule_id="RHP-FALLBACK-HELPER-REQUEST",
                    description="Rewrite request($key) to Request::get($key)",
                )
            )

        # session('key') -> Session::get('key')
        elif fn_name == "session" and len(arg_texts) == 1:
            replacement = f"Session::get({arg_texts[0]})"
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement=replacement,
                    rule_id="RHP-FALLBACK-HELPER-SESSION",
                    description="Rewrite session($key) to Session::get($key)",
                )
            )

        # cookie('key', 'val') -> Cookie::set('key', 'val')
        elif fn_name == "cookie" and len(arg_texts) >= 2:
            replacement = f"Cookie::set({', '.join(arg_texts)})"
            edits.append(
                TextEdit(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    replacement=replacement,
                    rule_id="RHP-FALLBACK-HELPER-COOKIE",
                    description="Rewrite cookie(...) to Cookie::set(...)",
                )
            )

    return edits


# ---------------------------------------------------------------------------
# Fallback Pipeline Orchestrator
# ---------------------------------------------------------------------------

def apply_fallback_rules(
    source: Union[str, bytes],
    rules: Optional[Sequence[str]] = None,
) -> FallbackTransformResult:
    """Run deterministic AST fallback rules on PHP source code.

    Transforms include:
    - Laravel facade imports removal
    - Laravel facade scoped call rewriting
    - Multibyte mb_* function rewrites
    - Laravel helper function rewrites

    Args:
        source: PHP source code as string or bytes.
        rules: Optional list of rule IDs to filter execution (runs all if None).

    Returns:
        FallbackTransformResult with original and transformed code, edits, and applied rules.
    """
    source_str = source.decode("utf-8") if isinstance(source, bytes) else source
    parser = PHPParser()
    tree = parser.parse(source_str)

    all_edits: list[TextEdit] = []

    # Collect edits across all fallback transforms
    all_edits.extend(transform_laravel_use_statements(source_str, tree.root_node))
    all_edits.extend(transform_laravel_facades(source_str, tree.root_node))
    all_edits.extend(transform_mb_functions(source_str, tree.root_node))
    all_edits.extend(transform_laravel_helpers(source_str, tree.root_node))

    # Filter by requested rule IDs if specified
    if rules is not None:
        rule_set = set(rules)
        all_edits = [e for e in all_edits if e.rule_id in rule_set]

    if not all_edits:
        return FallbackTransformResult(
            original_code=source_str,
            transformed_code=source_str,
            modified=False,
            edits=[],
            rules_applied=[],
        )

    transformed = apply_text_edits(source_str, all_edits)
    applied_rule_ids = list(dict.fromkeys(e.rule_id for e in all_edits))

    return FallbackTransformResult(
        original_code=source_str,
        transformed_code=transformed,
        modified=transformed != source_str,
        edits=all_edits,
        rules_applied=applied_rule_ids,
    )


def apply_fallback_rules_to_file(
    path: Union[str, Path],
    in_place: bool = False,
    rules: Optional[Sequence[str]] = None,
) -> FallbackTransformResult:
    """Read a PHP file, apply fallback rules, and optionally save in place.

    Args:
        path: Path to the PHP file.
        in_place: If True, writes the transformed code back to the file.
        rules: Optional list of rule IDs to filter execution.

    Returns:
        FallbackTransformResult.
    """
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    result = apply_fallback_rules(content, rules=rules)

    if in_place and result.modified:
        file_path.write_text(result.transformed_code, encoding="utf-8")

    return result
