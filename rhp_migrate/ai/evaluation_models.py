"""Pydantic models for AI continuous improvement evaluation and meta-critique."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class CodeSample(BaseModel):
    """Represents a code snippet before, after, and expected ideal transformation."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., description="Brief title of the snippet (e.g. 'curl_setopt CURLOPT_RETURNTRANSFER')")
    before: str = Field(..., description="Original PHP code snippet before transformation")
    after: str = Field(..., description="Actual output code produced by the tool/LLM")
    expected: Optional[str] = Field(None, description="Ideal expected RHP code")
    explanation: str = Field(..., description="Detailed explanation of the issue or difference")


class MigrateMistake(BaseModel):
    """Mistake or regression introduced by the `rhp migrate` deterministic tool."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., description="Short summary of the mistake")
    sample: CodeSample = Field(..., description="Code sample demonstrating the mistake")
    severity: str = Field("breaking_syntax", description="breaking_syntax, logic_error, or bad_type")


class MigrateOpportunity(BaseModel):
    """Pattern that could and should be migrated deterministically by `rhp migrate` but was not."""

    model_config = ConfigDict(extra="ignore")

    pattern_name: str = Field(..., description="Name of the deterministic pattern (e.g. array_key_exists, in_array)")
    suggested_rule_id: str = Field(..., description="Suggested rule ID like RHP-DET-045")
    sample: CodeSample = Field(..., description="Sample showing how this could be automated deterministically")
    reasoning: str = Field(..., description="Why this belongs in native 'rhp migrate' binary rather than needing LLM")


class LintMistake(BaseModel):
    """Mistake made by `rhp lint` (e.g. false positive warning, broken autofix)."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., description="Summary of the lint false positive or mistake")
    rule_id: Optional[str] = Field(None, description="Lint rule ID flagged")
    sample: CodeSample = Field(..., description="Code snippet flagged incorrectly")
    why_invalid: str = Field(..., description="Why rhp lint's flag or fix is incorrect")


class LintOpportunity(BaseModel):
    """Pattern that `rhp lint` should detect or auto-fix but currently misses."""

    model_config = ConfigDict(extra="ignore")

    rule_name: str = Field(..., description="Suggested lint rule name")
    sample: CodeSample = Field(..., description="Code sample illustrating the lint issue")
    recommendation: str = Field(..., description="What the linter should detect or auto-fix")


class LLMDifficulty(BaseModel):
    """Construct or problem where the LLM struggled or required multiple retries."""

    model_config = ConfigDict(extra="ignore")

    unit_identifier: str = Field(..., description="Function/class/method where LLM struggled")
    difficulty_type: str = Field(
        ...,
        description="dynamic_typing, reflection_elimination, framework_magic, circular_types, other",
    )
    sample: CodeSample = Field(..., description="Snippet where difficulty occurred")
    reasoning: str = Field(..., description="Why this was hard to resolve safely without developer domain knowledge")
    workaround_applied: Optional[str] = Field(None, description="Workaround applied or if marked TODO")


class RHPLanguageGap(BaseModel):
    """Language feature or standard library API missing from RHP compared to PHP."""

    model_config = ConfigDict(extra="ignore")

    feature_name: str = Field(..., description="Missing language feature or standard library API in RHP")
    php_equivalent: str = Field(..., description="PHP feature/API (e.g. array_filter with closure, union types string|int)")
    suggested_rhp_syntax: str = Field(..., description="Proposed syntax or standard library function in RHP")
    impact: str = Field("medium", description="high, medium, or low")
    example: CodeSample = Field(..., description="Code snippet demonstrating the missing capability")


class ContextMdProposal(BaseModel):
    """Markdown documentation block ready for human approval and merge into main repo context.md."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., description="Title of the pattern/rule for documentation")
    category: str = Field("general", description="syntax, types, framework, stdlib, architecture")
    markdown_content: str = Field(..., description="Formatted markdown documentation block ready for merge")


class MigrationCriticReport(BaseModel):
    """Complete evaluation report produced by the LLM Critic for a file or unit migration."""

    model_config = ConfigDict(extra="ignore")

    file_path: str = Field(..., description="Target file path")
    php_version: str = Field("unknown", description="Detected PHP target version")
    migrate_mistakes: list[MigrateMistake] = Field(
        default_factory=list,
        description="Mistakes made by 'rhp migrate'",
    )
    migrate_missed_opportunities: list[MigrateOpportunity] = Field(
        default_factory=list,
        description="What 'rhp migrate' could have done deterministically",
    )
    lint_mistakes: list[LintMistake] = Field(
        default_factory=list,
        description="Mistakes made by 'rhp lint'",
    )
    lint_missed_opportunities: list[LintOpportunity] = Field(
        default_factory=list,
        description="What 'rhp lint' could have caught/fixed",
    )
    llm_difficulties: list[LLMDifficulty] = Field(
        default_factory=list,
        description="Where LLM struggled to fix",
    )
    rhp_language_gaps: list[RHPLanguageGap] = Field(
        default_factory=list,
        description="Missing features/syntax in RHP language",
    )
    context_proposal: Optional[ContextMdProposal] = Field(
        None,
        description="Ready-to-approve markdown block for repo context.md",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 evaluation timestamp",
    )
