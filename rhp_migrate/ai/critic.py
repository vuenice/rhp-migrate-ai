"""AI Continuous Improvement Critic (Layer 5B).

Evaluates the migration results of `rhp migrate`, `rhp lint`, `rhp check`, and LLM healing.
Produces structured diagnostic critiques on:
1. Mistakes made by `rhp migrate`
2. Missed deterministic opportunities for `rhp migrate`
3. Mistakes made by `rhp lint`
4. Missed linting checks for `rhp lint`
5. Where the LLM struggled to fix issues
6. Missing features/syntax in RHP language
7. A ready-to-approve markdown snippet for repo's context.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence, Union

from rhp_migrate.ai.client import LLMRunner
from rhp_migrate.ai.evaluation_models import (
    CodeSample,
    ContextMdProposal,
    LintMistake,
    LintOpportunity,
    LLMDifficulty,
    MigrateMistake,
    MigrateOpportunity,
    MigrationCriticReport,
    RHPLanguageGap,
)
from rhp_migrate.engine.rhp_cli import CheckReport, LintReport, MigrateReport


CRITIC_SYSTEM_PROMPT = """You are a Principal Compiler Architect and Language Tooling Critic for RHP (a high-performance, strictly-typed PHP superset).

Your objective is NOT to write code, but to CRITIQUE the automated migration pipeline that just ran on a PHP source file.

You must critically inspect:
1. **`rhp migrate` Mistakes**: Did the native deterministic `rhp migrate` tool introduce broken syntax, incorrect regex/AST replacements, or regressions?
2. **`rhp migrate` Missed Opportunities**: What transformations were left for the LLM that COULD and SHOULD have been performed deterministically by the native `rhp migrate` CLI binary (e.g. array_key_exists -> isset, in_array -> binary search or dict check, removing unused imports, strict casts)?
3. **`rhp lint` Mistakes**: Did `rhp lint` report false alarms, bad warnings, or incorrect auto-fixes?
4. **`rhp lint` Missed Opportunities**: What code quality, naming, or formatting issues should `rhp lint` have flagged or auto-fixed?
5. **LLM Difficulties**: Which functions or classes were hard for the AI to fix? Why (e.g. untyped dynamic dispatch, reflection, complex framework macros)?
6. **RHP Language Gaps**: What syntax or standard library functions are missing in RHP itself compared to PHP that forced awkward workarounds?
7. **Context Markdown Proposal**: Formulate an actionable, clean Markdown documentation section (`context.md` format) containing:
   - Rule title and category
   - Problem description
   - Clear Before/After code example
   - Guidance for future migrations
   This proposal will be human-reviewed and merged directly into the central repo's context!

Output ONLY a valid JSON object matching the `MigrationCriticReport` schema.
"""


class MigrationCritic:
    """Orchestrates LLM meta-evaluation and generates continuous-improvement reports."""

    def __init__(self, runner: Optional[LLMRunner] = None) -> None:
        self.runner = runner or LLMRunner()

    def build_user_prompt(
        self,
        file_path: Union[str, Path],
        php_version: str,
        original_code: str,
        migrated_code: str,
        final_code: str,
        migrate_report: Optional[MigrateReport] = None,
        lint_report: Optional[LintReport] = None,
        check_report: Optional[CheckReport] = None,
        healing_log: Optional[Sequence[str]] = None,
    ) -> str:
        """Render the complete context prompt for the Critic."""
        parts = [
            f"# Target File: {Path(file_path).name}",
            f"**Detected PHP Version:** {php_version}",
            f"**Full Path:** `{file_path}`",
            "",
            "## 1. Original PHP Code (Before any tool ran)",
            "```php",
            original_code.strip(),
            "```",
            "",
            "## 2. Code Produced by Deterministic `rhp migrate`",
            "```php",
            migrated_code.strip(),
            "```",
        ]

        if migrate_report:
            file_res = migrate_report.get_file_result(file_path)
            parts.extend([
                "",
                "### `rhp migrate` Metrics:",
                f"- Modified: {file_res.modified if file_res else False}",
                f"- Warnings: {file_res.warnings if file_res else []}",
            ])

        parts.extend([
            "",
            "## 3. `rhp lint` Diagnostics",
        ])
        if lint_report and lint_report.diagnostics:
            for d in lint_report.diagnostics:
                parts.append(f"- Line {d.line}:{d.column} [{d.rule_id}] ({d.severity}): {d.message}")
        else:
            parts.append("- No lint diagnostics reported.")

        parts.extend([
            "",
            "## 4. `rhp check` Compiler Diagnostics",
        ])
        if check_report and check_report.diagnostics:
            for d in check_report.diagnostics:
                parts.append(f"- Line {d.line}:{d.column} [{d.code or 'ERR'}]: {d.message}")
        else:
            parts.append("- Clean: 0 compiler errors.")

        if healing_log:
            parts.extend([
                "",
                "## 5. LLM Healing History (Errors resolved during retries)",
            ])
            for log_entry in healing_log:
                parts.append(f"- {log_entry}")

        parts.extend([
            "",
            "## 6. Final Migrated Code (Current State)",
            "```php",
            final_code.strip(),
            "```",
            "",
            "Now produce the structured `MigrationCriticReport` evaluating all 7 areas.",
        ])

        return "\n".join(parts)

    def analyze(
        self,
        file_path: Union[str, Path],
        php_version: str,
        original_code: str,
        migrated_code: str,
        final_code: str,
        migrate_report: Optional[MigrateReport] = None,
        lint_report: Optional[LintReport] = None,
        check_report: Optional[CheckReport] = None,
        healing_log: Optional[Sequence[str]] = None,
    ) -> MigrationCriticReport:
        """Run the LLM critic analysis and return a structured MigrationCriticReport."""
        user_prompt = self.build_user_prompt(
            file_path=file_path,
            php_version=php_version,
            original_code=original_code,
            migrated_code=migrated_code,
            final_code=final_code,
            migrate_report=migrate_report,
            lint_report=lint_report,
            check_report=check_report,
            healing_log=healing_log,
        )

        parsed, usage, _ = self.runner.client.generate_structured(
            system_prompt=CRITIC_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=MigrationCriticReport,
        )

        # Ensure file_path and php_version are populated
        parsed.file_path = str(file_path)
        parsed.php_version = php_version
        return parsed
