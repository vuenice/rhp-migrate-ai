"""Verification and compiler-guided auto-healing loop (Layer 6).

Executes `rhp check --format=json` against migrated files. If compiler errors
or type diagnostic issues are flagged, extracts the failing code unit, re-prompts
the LLM with compiler diagnostic feedback, and iteratively applies fixes.
If verification continues to fail after `max_retries`, reverts to the last known
syntactically valid state and annotates the failing unit with
`// RHP-TODO(failed-verification): <error>`.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Optional, Sequence, Union

import tree_sitter

from rhp_migrate.ai.client import LLMRunner
from rhp_migrate.analyzer.parser import PHPParser
from rhp_migrate.engine.rhp_cli import CheckReport, CompilerDiagnostic, run_check


@dataclass
class CodeUnit:
    """Represents a targeted code block (method, function, class, or whole file)."""

    identifier: str
    code: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    node_type: str
    is_whole_file: bool = False


def clean_code_fence(code: str) -> str:
    """Strip markdown code block fences (e.g. ```php ... ```) if returned by the LLM."""
    stripped = code.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove opening fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return code


def _get_node_name(node: tree_sitter.Node) -> str:
    """Extract identifier text from a declaration node's child name node."""
    for child in node.children:
        if child.type == "name":
            return PHPParser.get_node_text(child)
    return "anonymous"


def _get_namespace_from_ast(root: tree_sitter.Node) -> str:
    """Extract the active namespace if defined in the root tree."""
    for child in root.children:
        if child.type == "namespace_definition":
            for sub in child.children:
                if sub.type == "namespace_name":
                    return PHPParser.get_node_text(sub)
    return ""


def find_code_unit(
    source: str,
    line_number: int,
    file_path: Optional[Union[str, Path]] = None,
) -> CodeUnit:
    """Locate the most specific code unit (method, function, or class) enclosing line_number.

    Args:
        source: Source code of the file.
        line_number: 1-indexed target line number.
        file_path: Optional path to the source file for naming fallback.

    Returns:
        CodeUnit representing the target function, method, class, or file.
    """
    source_bytes = source.encode("utf-8")
    source_lines = source.splitlines(keepends=True)
    total_lines = len(source_lines) or 1
    total_bytes = len(source_bytes)
    fallback_id = Path(file_path).name if file_path else "file"

    if not source.strip():
        return CodeUnit(
            identifier=fallback_id,
            code=source,
            start_line=1,
            end_line=total_lines,
            start_byte=0,
            end_byte=total_bytes,
            node_type="file",
            is_whole_file=True,
        )

    parser = PHPParser()
    try:
        tree = parser.parse(source_bytes)
    except Exception:
        return CodeUnit(
            identifier=fallback_id,
            code=source,
            start_line=1,
            end_line=total_lines,
            start_byte=0,
            end_byte=total_bytes,
            node_type="file",
            is_whole_file=True,
        )

    unit_types = {
        "method_declaration",
        "function_definition",
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
    }

    best_node: Optional[tree_sitter.Node] = None
    stack = [tree.root_node]

    while stack:
        curr = stack.pop()
        start_line = curr.start_point.row + 1
        end_line = curr.end_point.row + 1

        if start_line <= line_number <= end_line:
            if curr.type in unit_types:
                # Prefer tighter enclosing nodes (innermost unit)
                if best_node is None or (
                    curr.start_byte >= best_node.start_byte
                    and curr.end_byte <= best_node.end_byte
                ):
                    best_node = curr
            for child in curr.children:
                stack.append(child)

    if best_node is None:
        return CodeUnit(
            identifier=fallback_id,
            code=source,
            start_line=1,
            end_line=total_lines,
            start_byte=0,
            end_byte=total_bytes,
            node_type="file",
            is_whole_file=True,
        )

    start_byte = best_node.start_byte
    end_byte = best_node.end_byte
    unit_code = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")
    start_line = best_node.start_point.row + 1
    end_line = best_node.end_point.row + 1
    node_type = best_node.type

    # Determine qualified identifier
    namespace = _get_namespace_from_ast(tree.root_node)
    unit_name = _get_node_name(best_node)

    if node_type == "method_declaration":
        # Find enclosing class name
        parent = best_node.parent
        class_name = "Class"
        while parent is not None:
            if parent.type in ("class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"):
                class_name = _get_node_name(parent)
                break
            parent = parent.parent
        identifier = f"{class_name}::{unit_name}"
        if namespace:
            identifier = f"{namespace}\\{identifier}"
    elif node_type == "function_definition":
        identifier = f"{namespace}\\{unit_name}" if namespace else unit_name
    elif node_type in ("class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"):
        identifier = f"{namespace}\\{unit_name}" if namespace else unit_name
    else:
        identifier = unit_name

    return CodeUnit(
        identifier=identifier,
        code=unit_code,
        start_line=start_line,
        end_line=end_line,
        start_byte=start_byte,
        end_byte=end_byte,
        node_type=node_type,
        is_whole_file=False,
    )


def replace_unit_in_source(source: str, unit: CodeUnit, replacement: str) -> str:
    """Replace target CodeUnit within source with replacement code.

    Args:
        source: Full source code text.
        unit: Target CodeUnit to replace.
        replacement: New code for the unit.

    Returns:
        Updated full source code string.
    """
    if unit.is_whole_file:
        return replacement

    source_bytes = source.encode("utf-8")
    new_bytes = (
        source_bytes[: unit.start_byte]
        + replacement.encode("utf-8")
        + source_bytes[unit.end_byte :]
    )
    return new_bytes.decode("utf-8", errors="replace")


def insert_todo_above_unit(
    source: str,
    line_number: int,
    error_message: str,
    file_path: Optional[Union[str, Path]] = None,
) -> str:
    """Insert `// RHP-TODO(failed-verification): <error>` above the failing unit.

    Args:
        source: Source code of the file.
        line_number: 1-indexed line number of compiler diagnostic error.
        error_message: Diagnostic error text.
        file_path: Optional file path.

    Returns:
        Updated source code with TODO comment prepended to the failing unit.
    """
    unit = find_code_unit(source, line_number, file_path=file_path)
    lines = source.splitlines(keepends=True)
    if not lines:
        return f"// RHP-TODO(failed-verification): {error_message}\n"

    # Determine line index to insert above
    target_idx = max(0, min(unit.start_line - 1, len(lines) - 1))

    # For whole-file units, if first line is PHP opening tag, place comment on next line
    if unit.is_whole_file and lines and lines[0].strip().startswith("<?php"):
        target_idx = 1 if len(lines) > 1 else len(lines)

    # Avoid duplicate TODO insertion if identical tag is already present immediately above
    comment_tag = f"// RHP-TODO(failed-verification): {error_message}"
    if target_idx > 0 and comment_tag in lines[target_idx - 1]:
        return source
    if target_idx < len(lines) and comment_tag in lines[target_idx]:
        return source

    # Preserve indentation from target line
    indent = ""
    if target_idx < len(lines):
        match = re.match(r"^(\s*)", lines[target_idx])
        if match:
            indent = match.group(1)

    todo_line = f"{indent}// RHP-TODO(failed-verification): {error_message}\n"
    lines.insert(target_idx, todo_line)
    return "".join(lines)


def is_syntactically_valid(source: str) -> bool:
    """Return True if source parses without syntax errors using tree-sitter."""
    try:
        parser = PHPParser()
        tree = parser.parse(source)
        return not tree.root_node.has_error
    except Exception:
        return False


def verify_and_heal(
    file_path: Union[str, Path],
    max_retries: int = 3,
    *,
    llm_runner: Optional[LLMRunner] = None,
    bin_path: Optional[Union[str, Path]] = None,
    extra_instructions: Optional[str] = None,
) -> bool:
    """Verify a migrated file using `rhp check` and auto-heal compiler errors with LLM.

    Execution Flow:
    1. Run `rhp check <file_path> --format=json`.
    2. If 0 errors, return True.
    3. If compiler errors exist:
       - Extract error message, offending line numbers, and diagnostic code.
       - Re-prompt the LLM with the error diagnostic and the failing code unit.
       - Apply the LLM fix.
       - Repeat up to `max_retries`.
    4. If still failing after max retries:
       - Insert `// RHP-TODO(failed-verification): <error>` above the failing unit.
       - Revert file to last known syntactically valid state or tag unit for manual review.

    Args:
        file_path: Path to the target file to verify and heal.
        max_retries: Maximum number of compiler feedback and healing iterations (default: 3).
        llm_runner: Optional configured LLMRunner instance.
        bin_path: Optional custom path to the native rhp executable.
        extra_instructions: Optional additional guidance passed to the LLM during healing.

    Returns:
        True if the file passes `rhp check` with 0 errors, False if verification fails.

    Raises:
        FileNotFoundError: If target file_path does not exist.
        RhpNotFoundError: If rhp binary cannot be located.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Target file for verification does not exist: {path}")

    # 1. Run initial rhp check
    report = run_check(path, bin_path=bin_path)

    # 2. If 0 errors, file is verified
    if not report.has_errors:
        return True

    # 3. Compiler errors exist: Prepare auto-healing loop
    initial_content = path.read_text(encoding="utf-8")
    last_valid_content = initial_content
    runner = llm_runner or LLMRunner()

    if is_syntactically_valid(initial_content):
        last_valid_content = initial_content

    for attempt in range(max_retries):
        errors = report.error_diagnostics
        if not errors:
            break

        primary_diag = errors[0]
        offending_line = primary_diag.line

        # Extract current source and failing unit
        current_source = path.read_text(encoding="utf-8")
        unit = find_code_unit(current_source, offending_line, file_path=path)

        # Build compiler diagnostic error feedback
        diag_feedback: list[str] = []
        for d in errors:
            loc = f"line {d.line}:{d.column}"
            code_prefix = f"[{d.code}] " if d.code else ""
            diag_feedback.append(f"{code_prefix}{loc}: {d.message}")

        # Re-prompt LLM with the error diagnostic and failing code unit
        ai_resp = runner.migrate_unit(
            unit_identifier=unit.identifier,
            code=unit.code,
            compiler_errors=diag_feedback,
            extra_instructions=extra_instructions,
        )

        transformed = clean_code_fence(ai_resp.result.transformed_code)

        # Apply LLM fix to file
        new_source = replace_unit_in_source(current_source, unit, transformed)

        # Keep track of last known syntactically valid state
        if is_syntactically_valid(new_source):
            last_valid_content = new_source

        path.write_text(new_source, encoding="utf-8")

        # Re-check with compiler
        report = run_check(path, bin_path=bin_path)
        if not report.has_errors:
            return True

    # 4. If still failing after max retries:
    # Tag failing unit with TODO comment and revert to last known syntactically valid state
    failing_diag = report.error_diagnostics[0] if report.error_diagnostics else primary_diag
    error_desc = f"[{failing_diag.code}] {failing_diag.message}" if failing_diag.code else failing_diag.message

    reverted_and_tagged = insert_todo_above_unit(
        last_valid_content,
        line_number=failing_diag.line,
        error_message=error_desc,
        file_path=path,
    )
    path.write_text(reverted_and_tagged, encoding="utf-8")

    return False
