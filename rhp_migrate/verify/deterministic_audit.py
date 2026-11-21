"""Deterministic migration syntax validator and mistake auditor.

Assumes deterministic migration passes (native `rhp migrate` or AST fallback rules)
are not perfect and can produce broken syntax, invalid typing, or regressions.

Inspects deterministic output syntax, identifies:
1. Whether output syntax is valid
2. What is invalid (error codes, messages, offending lines)
3. Before syntax (original code snippet) vs After syntax (broken output snippet)
4. What could have been done better (root cause analysis and tooling recommendations)
5. Suggested valid RHP transformation
6. Records comprehensive JSON and Markdown audit reports in `.rhp/reports/deterministic_audit/`
7. Appends context file create/update suggestions to `.rhp/feedback/pending_context.md`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field
import tree_sitter

from rhp_migrate.ai.evaluation_models import CodeSample, MigrateMistake
from rhp_migrate.analyzer.parser import PHPParser
from rhp_migrate.engine.rhp_cli import CheckReport, CompilerDiagnostic, find_rhp_binary, run_check


class DeterministicSyntaxError(BaseModel):
    """Represents a specific syntax defect or compiler error in deterministic output."""

    model_config = ConfigDict(extra="ignore")

    line: int = Field(1, description="1-indexed line number in after_code")
    column: int = Field(1, description="1-indexed column number in after_code")
    error_code: Optional[str] = Field(None, description="Diagnostic error code if known (e.g. PARSE_ERR, COLON_TYPE)")
    message: str = Field(..., description="Explanation of what is invalid in the syntax")
    offending_snippet: str = Field(..., description="Code snippet containing the syntax error")
    severity: str = Field("breaking_syntax", description="breaking_syntax, bad_type, or warning")


class DeterministicMistakeRecord(BaseModel):
    """Complete audit record of deterministic migration mistakes on a file."""

    model_config = ConfigDict(extra="ignore")

    file_path: str = Field(..., description="Path to the file audited")
    is_valid: bool = Field(True, description="True if output syntax is 100% valid RHP")
    errors: list[DeterministicSyntaxError] = Field(
        default_factory=list,
        description="List of syntax errors or defects identified in deterministic output",
    )
    before_syntax: str = Field("", description="Original source code or excerpt before deterministic pass")
    after_syntax: str = Field("", description="Deterministic output code or excerpt")
    what_is_invalid: str = Field("", description="Summary description of what is invalid")
    what_could_have_been_done_better: str = Field(
        "",
        description="Engineering analysis of what the deterministic tool should have done instead",
    )
    suggested_fix: Optional[str] = Field(
        None,
        description="Suggested valid RHP code resolving the deterministic mistake",
    )
    context_suggestions: list[str] = Field(
        default_factory=list,
        description="Actionable Markdown blocks ready for pending_context.md or learnings.md",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 audit timestamp",
    )

    def to_critic_mistakes(self) -> list[MigrateMistake]:
        """Convert detected errors into MigrateMistake models for the MigrationCritic."""
        mistakes: list[MigrateMistake] = []
        for idx, err in enumerate(self.errors, start=1):
            mistakes.append(
                MigrateMistake(
                    title=f"Deterministic syntax mistake on line {err.line}: {err.message}",
                    severity=err.severity,
                    sample=CodeSample(
                        title=f"{Path(self.file_path).name}:{err.line} - {err.error_code or 'SyntaxError'}",
                        before=self.before_syntax or err.offending_snippet,
                        after=err.offending_snippet,
                        expected=self.suggested_fix,
                        explanation=f"{err.message}. {self.what_could_have_been_done_better}",
                    ),
                )
            )
        return mistakes

    def format_markdown_report(self) -> str:
        """Render a human-readable markdown report of this deterministic audit."""
        filename = Path(self.file_path).name
        status_badge = "✅ VALID" if self.is_valid else "❌ INVALID SYNTAX"
        lines = [
            f"# Deterministic Migration Audit: {filename}",
            f"- **Status:** {status_badge}",
            f"- **Target File:** `{self.file_path}`",
            f"- **Audit Timestamp:** {self.timestamp}",
            f"- **Syntax Errors Detected:** {len(self.errors)}",
            "",
        ]

        if self.is_valid:
            lines.extend([
                "## ✅ Verification Passed",
                "Deterministic migration produced syntactically valid RHP output.",
            ])
            return "\n".join(lines)

        lines.extend([
            "## ❌ What is Invalid",
            self.what_is_invalid,
            "",
            "### Specific Syntax Errors:",
        ])
        for idx, err in enumerate(self.errors, start=1):
            code_tag = f"[{err.error_code}] " if err.error_code else ""
            lines.extend([
                f"{idx}. **Line {err.line}:{err.column}** {code_tag}{err.message}",
                "   ```php",
                f"   {err.offending_snippet}",
                "   ```",
            ])

        lines.extend([
            "",
            "## 🔄 Before & After Syntax Comparison",
            "### Before (Original PHP / Input to Deterministic Step):",
            "```php",
            self.before_syntax.strip(),
            "```",
            "",
            "### After (Deterministic Output - Broken):",
            "```php",
            self.after_syntax.strip(),
            "```",
            "",
            "## 💡 What Could Have Been Done Better",
            self.what_could_have_been_done_better,
            "",
        ])

        if self.suggested_fix:
            lines.extend([
                "## 🛠️ Suggested Valid RHP Syntax",
                "```php",
                self.suggested_fix.strip(),
                "```",
                "",
            ])

        if self.context_suggestions:
            lines.extend([
                "## 📝 Context File Update Suggestions",
                "The following documentation rules were generated for addition to `pending_context.md` / `context.md`:",
                "",
            ])
            for sug in self.context_suggestions:
                lines.append(sug)
                lines.append("")

        return "\n".join(lines)


# Regex patterns detecting common deterministic syntax mistakes in RHP
COLON_TYPE_VAR_PATTERN = re.compile(
    r"\$([a-zA-Z_\x7f-\xff][a-zA-Z0-9_\x7f-\xff]*)\s*:\s*([a-zA-Z0-9_\\<>\[\]?]+)(\s*=|\s*;)",
)
DANGLING_CONCAT_PATTERN = re.compile(r"(\+\s*\+|(?<!\+)\+\s*;\s*$)")
DANGLING_ARROW_PATTERN = re.compile(r"->\s*;")


class DeterministicValidator:
    """Validates deterministic migration output syntax and records mistake audits."""

    def __init__(self, parser: Optional[PHPParser] = None) -> None:
        self.parser = parser or PHPParser()

    def validate(
        self,
        before_code: str,
        after_code: str,
        file_path: Union[str, Path] = "file.php",
        *,
        bin_path: Optional[Union[str, Path]] = None,
    ) -> DeterministicMistakeRecord:
        """Analyze before and after code from deterministic migration to detect syntax errors.

        Args:
            before_code: Source code before deterministic migration.
            after_code: Code produced by deterministic migration tool.
            file_path: Relative or absolute file path.
            bin_path: Optional path to rhp native binary for compiler check.

        Returns:
            DeterministicMistakeRecord detailing validity, defects, before/after, and suggestions.
        """
        path_str = str(file_path)
        errors: list[DeterministicSyntaxError] = []

        # 1. Check for known deterministic syntax traps (e.g. colon typing '$var: Type')
        colon_errors = self._check_colon_type_syntax(after_code)
        errors.extend(colon_errors)

        # 2. Check for operator concatenation or broken statement artifacts
        op_errors = self._check_broken_operator_syntax(after_code)
        errors.extend(op_errors)

        # 3. Check AST syntax validity using Tree-sitter
        ast_errors = self._check_tree_sitter_syntax(after_code)
        # Avoid duplicate error on same line if colon typing already flagged
        flagged_lines = {e.line for e in errors}
        for ae in ast_errors:
            if ae.line not in flagged_lines:
                errors.append(ae)

        # 4. If native rhp binary is available and target file exists, run rhp check
        if bin_path or find_rhp_binary():
            resolved = Path(file_path)
            if resolved.is_file():
                try:
                    rep = run_check(resolved, bin_path=bin_path)
                    for diag in rep.error_diagnostics:
                        if diag.line not in flagged_lines:
                            errors.append(
                                DeterministicSyntaxError(
                                    line=diag.line,
                                    column=diag.column,
                                    error_code=diag.code or "COMPILER_ERR",
                                    message=diag.message,
                                    offending_snippet=self._extract_line(after_code, diag.line),
                                    severity="breaking_syntax",
                                )
                            )
                except Exception:
                    pass

        # Sort errors by line and column
        errors.sort(key=lambda e: (e.line, e.column))

        is_valid = len(errors) == 0

        # Build analysis of what is invalid and what could have been done better
        what_is_invalid = ""
        what_could_have_been_done_better = ""
        suggested_fix = None
        context_suggestions: list[str] = []

        if not is_valid:
            what_is_invalid, what_could_have_been_done_better, suggested_fix = (
                self._synthesize_analysis(errors, before_code, after_code)
            )
            context_suggestions = self._build_context_suggestions(
                file_path=path_str,
                errors=errors,
                before_code=before_code,
                after_code=after_code,
                what_invalid=what_is_invalid,
                what_better=what_could_have_been_done_better,
                suggested_fix=suggested_fix,
            )

        return DeterministicMistakeRecord(
            file_path=path_str,
            is_valid=is_valid,
            errors=errors,
            before_syntax=before_code,
            after_syntax=after_code,
            what_is_invalid=what_is_invalid,
            what_could_have_been_done_better=what_could_have_been_done_better,
            suggested_fix=suggested_fix,
            context_suggestions=context_suggestions,
        )

    def _check_colon_type_syntax(self, code: str) -> list[DeterministicSyntaxError]:
        """Detect invalid colon typing ($var: Type) introduced by deterministic regex."""
        errors: list[DeterministicSyntaxError] = []
        for line_idx, line_text in enumerate(code.splitlines(), start=1):
            match = COLON_TYPE_VAR_PATTERN.search(line_text)
            if match:
                var_name = match.group(1)
                t_name = match.group(2)
                errors.append(
                    DeterministicSyntaxError(
                        line=line_idx,
                        column=match.start() + 1,
                        error_code="RHP_SYN_COLON_TYPE",
                        message=(
                            f"Colon type syntax '${var_name}: {t_name}' is forbidden in RHP. "
                            f"Use prefix type syntax '{t_name} ${var_name}' instead."
                        ),
                        offending_snippet=line_text.strip(),
                        severity="breaking_syntax",
                    )
                )
        return errors

    def _check_broken_operator_syntax(self, code: str) -> list[DeterministicSyntaxError]:
        """Detect broken operator artifacts produced by naive replacement (e.g. '++' for concat)."""
        errors: list[DeterministicSyntaxError] = []
        for line_idx, line_text in enumerate(code.splitlines(), start=1):
            if DANGLING_CONCAT_PATTERN.search(line_text) and "++" not in line_text:
                errors.append(
                    DeterministicSyntaxError(
                        line=line_idx,
                        column=1,
                        error_code="RHP_SYN_DANGLING_OP",
                        message="Malformed string concatenation operator artifact detected.",
                        offending_snippet=line_text.strip(),
                        severity="breaking_syntax",
                    )
                )
            if DANGLING_ARROW_PATTERN.search(line_text):
                errors.append(
                    DeterministicSyntaxError(
                        line=line_idx,
                        column=1,
                        error_code="RHP_SYN_DANGLING_ARROW",
                        message="Dangling method or property access arrow '->;' with missing identifier.",
                        offending_snippet=line_text.strip(),
                        severity="breaking_syntax",
                    )
                )
        return errors

    def _check_tree_sitter_syntax(self, code: str) -> list[DeterministicSyntaxError]:
        """Inspect Tree-sitter AST for ERROR and MISSING syntax nodes."""
        errors: list[DeterministicSyntaxError] = []
        try:
            tree = self.parser.parse(code)
            if not tree.root_node.has_error:
                return []

            # Traverse and find syntax error nodes
            for node in PHPParser.traverse(tree.root_node):
                if node.type in ("ERROR", "MISSING") or node.is_missing:
                    line = node.start_point.row + 1
                    col = node.start_point.column + 1
                    snippet = self._extract_line(code, line)
                    msg = (
                        f"Syntax parse error: unexpected token or malformed construct at line {line}:{col}"
                        if node.type == "ERROR"
                        else f"Syntax parse error: missing required token near line {line}:{col}"
                    )
                    errors.append(
                        DeterministicSyntaxError(
                            line=line,
                            column=col,
                            error_code="PARSE_ERR",
                            message=msg,
                            offending_snippet=snippet,
                            severity="breaking_syntax",
                        )
                    )
        except Exception as exc:
            errors.append(
                DeterministicSyntaxError(
                    line=1,
                    column=1,
                    error_code="PARSE_CRASH",
                    message=f"Parser threw exception while validating code: {exc}",
                    offending_snippet=code.splitlines()[0] if code.splitlines() else "",
                    severity="breaking_syntax",
                )
            )
        return errors

    def _extract_line(self, code: str, line_num: int) -> str:
        """Extract a single line from code text, 1-indexed."""
        lines = code.splitlines()
        idx = line_num - 1
        if 0 <= idx < len(lines):
            return lines[idx].strip()
        return ""

    def _synthesize_analysis(
        self,
        errors: list[DeterministicSyntaxError],
        before_code: str,
        after_code: str,
    ) -> tuple[str, str, Optional[str]]:
        """Synthesize 'what is invalid', 'what could have been done better', and suggested fix."""
        reasons: list[str] = []
        better_recs: list[str] = []
        suggested_code = after_code

        colon_errs = [e for e in errors if e.error_code == "RHP_SYN_COLON_TYPE"]
        if colon_errs:
            reasons.append(
                f"{len(colon_errs)} variable declaration(s) used invalid colon type syntax "
                "('$var: Type = ...') which is prohibited in RHP."
            )
            better_recs.append(
                "The deterministic migration pass or regex rule mistakenly assumed TypeScript/Rust syntax. "
                "In RHP, prefix type syntax is mandatory: 'Type $var = expr;'. The deterministic pass "
                "must format variables as 'Type $var' or leave typing to the AST/LLM stage."
            )
            # Fix colon typing in suggested code
            suggested_code = COLON_TYPE_VAR_PATTERN.sub(r"\2 $\1\3", suggested_code)

        op_errs = [e for e in errors if e.error_code in ("RHP_SYN_DANGLING_OP", "RHP_SYN_DANGLING_ARROW")]
        if op_errs:
            reasons.append("Naive string or operator replacement left dangling operators or broken syntax.")
            better_recs.append(
                "Use AST-aware node replacement for operator transformations rather than global string replaces, "
                "preventing partial expression collisions."
            )

        parse_errs = [e for e in errors if e.error_code == "PARSE_ERR"]
        if parse_errs:
            reasons.append(
                f"{len(parse_errs)} syntax parse error(s) detected where AST could not parse the generated tokens."
            )
            better_recs.append(
                "Ensure bracket, quote, and delimiter matching are balanced during statement transformations. "
                "Verify AST node boundaries before applying text splices."
            )

        what_invalid = " | ".join(reasons) if reasons else "Output syntax fails RHP compiler/parser check."
        what_better = "\n".join(better_recs) if better_recs else "Verify output syntax against compiler before committing."

        return what_invalid, what_better, suggested_code

    def _build_context_suggestions(
        self,
        file_path: str,
        errors: list[DeterministicSyntaxError],
        before_code: str,
        after_code: str,
        what_invalid: str,
        what_better: str,
        suggested_fix: Optional[str],
    ) -> list[str]:
        """Generate structured context file update markdown suggestions."""
        filename = Path(file_path).name
        suggestions: list[str] = []

        colon_errs = [e for e in errors if e.error_code == "RHP_SYN_COLON_TYPE"]
        if colon_errs:
            sample_err = colon_errs[0]
            suggestions.append(
                f"### [DETERMINISTIC-MISTAKE] Prefix Type Syntax vs Colon Typing\n"
                f"- **Context / Symptom:** Deterministic migration in `{filename}:{sample_err.line}` emitted `$var: Type`.\n"
                f"- **Root Cause:** Postfix colon typing is invalid in RHP; triggers `Colon type syntax '$var: Type' is not supported`.\n"
                f"- **Before Syntax (PHP):**\n"
                f"  ```php\n  {self._extract_line(before_code, sample_err.line) or '$var = expr;'}\n  ```\n"
                f"- **After Syntax (Broken Deterministic):**\n"
                f"  ```php\n  {sample_err.offending_snippet}\n  ```\n"
                f"- **What Could Have Been Done Better:** Always emit prefix type syntax `Type $var = expr;`.\n"
                f"- **Correct RHP Syntax:**\n"
                f"  ```php\n  {COLON_TYPE_VAR_PATTERN.sub(r'\\2 $\\1\\3', sample_err.offending_snippet)}\n  ```"
            )

        if not colon_errs and errors:
            sample_err = errors[0]
            suggestions.append(
                f"### [DETERMINISTIC-MISTAKE] Syntax Defect in `{filename}:{sample_err.line}`\n"
                f"- **Context / Symptom:** {sample_err.message}\n"
                f"- **Before Syntax (PHP):**\n"
                f"  ```php\n  {self._extract_line(before_code, sample_err.line) or before_code[:120]}\n  ```\n"
                f"- **After Syntax (Broken Deterministic):**\n"
                f"  ```php\n  {sample_err.offending_snippet}\n  ```\n"
                f"- **What Could Have Been Done Better:** {what_better}\n"
            )

        return suggestions

    def record_report(
        self,
        record: DeterministicMistakeRecord,
        base_dir: Union[str, Path] = Path("."),
    ) -> Path:
        """Persist audit report into `.rhp/reports/deterministic_audit/` and append suggestions.

        Args:
            record: DeterministicMistakeRecord instance to record.
            base_dir: Base workspace root directory.

        Returns:
            Path to the saved JSON audit report.
        """
        root = Path(base_dir).resolve()
        audit_dir = root / ".rhp" / "reports" / "deterministic_audit"
        audit_dir.mkdir(parents=True, exist_ok=True)

        raw_id = f"{record.file_path}:{record.timestamp}"
        digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]

        # 1. Save JSON report
        json_file = audit_dir / f"{digest}.json"
        json_file.write_text(record.model_dump_json(indent=2), encoding="utf-8")

        # 2. Append to aggregate audit log
        log_file = audit_dir / "audit_log.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

        # 3. Save human-readable markdown report
        md_file = audit_dir / f"{digest}.md"
        md_file.write_text(record.format_markdown_report(), encoding="utf-8")

        # 4. If mistakes found and context suggestions exist, append to pending_context.md
        if not record.is_valid and record.context_suggestions:
            feedback_dir = root / ".rhp" / "feedback"
            feedback_dir.mkdir(parents=True, exist_ok=True)
            pending_file = feedback_dir / "pending_context.md"

            header = f"\n<!-- DETERMINISTIC-AUDIT-START id={digest} file={record.file_path} -->\n"
            content = "\n\n".join(record.context_suggestions) + "\n"
            footer = f"<!-- DETERMINISTIC-AUDIT-END id={digest} -->\n---\n"

            with open(pending_file, "a", encoding="utf-8") as f:
                f.write(header + content + footer)

        return json_file
