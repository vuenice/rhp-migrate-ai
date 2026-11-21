"""Pydantic request and response schemas for FastAPI endpoints."""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# BYOK (Bring-Your-Own-Key) Models
# ---------------------------------------------------------------------------

class BYOKCredentials(BaseModel):
    """Encapsulates user-supplied API credentials from HTTP headers."""
    model_config = ConfigDict(extra="ignore")

    provider: Optional[str] = Field(
        None,
        description="LLM provider name: 'openai', 'anthropic', 'gemini', 'deepseek', 'ollama', 'mock'",
    )
    api_key: Optional[str] = Field(
        None,
        description="User's secret API key",
    )
    model_name: Optional[str] = Field(
        None,
        description="Model override identifier (e.g. 'gemini-2.5-pro', 'claude-3-7-sonnet', 'gpt-4o')",
    )
    api_base: Optional[str] = Field(
        None,
        description="Optional custom API base URL (for local Ollama, vLLM, or self-hosted proxy)",
    )
    temperature: float = Field(
        0.0,
        description="Sampling temperature",
    )


# ---------------------------------------------------------------------------
# Analysis Models
# ---------------------------------------------------------------------------

class AnalyzeJsonRequest(BaseModel):
    """Payload for POST /api/analyze when submitted via application/json."""
    model_config = ConfigDict(extra="forbid")

    project_path: Optional[str] = Field(
        None,
        description="Filesystem path to project directory to analyze",
    )
    code: Optional[str] = Field(
        None,
        description="PHP source code snippet for single-file analysis",
    )
    file_name: Optional[str] = Field(
        "source.php",
        description="Logical filename when analyzing a code snippet",
    )
    files: Optional[dict[str, str]] = Field(
        None,
        description="Dictionary of {relative_file_path: php_code} for in-memory batch analysis",
    )


class FileDiagnosticItem(BaseModel):
    """Diagnostic flagged during AST or compiler checks."""
    model_config = ConfigDict(extra="ignore")

    line: int
    column: int
    severity: str
    rule_id: Optional[str] = None
    message: str
    code_snippet: Optional[str] = None


class FileReportSummary(BaseModel):
    """Summary of readiness analysis for a single file."""
    model_config = ConfigDict(extra="ignore")

    file_path: str
    readiness_percentage: float
    unsupported_count: int
    untyped_param_count: int
    is_migration_ready: bool
    classes: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    diagnostics: list[FileDiagnosticItem] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    """Response returned by POST /api/analyze."""
    model_config = ConfigDict(extra="ignore")

    total_files: int
    overall_readiness: float
    total_unsupported_count: int
    total_untyped_param_count: int
    unsupported_rule_counts: dict[str, int] = Field(default_factory=dict)
    unsupported_rule_references: dict[str, str] = Field(default_factory=dict)
    migration_order: list[str] = Field(default_factory=list)
    file_reports: dict[str, FileReportSummary] = Field(default_factory=dict)
    summary_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Migration Models
# ---------------------------------------------------------------------------

class CompanionFileItem(BaseModel):
    """Companion file generated during migration (e.g. .rade.rhp template)."""
    model_config = ConfigDict(extra="ignore")

    file_path: str
    content: str


class MigrateFileRequest(BaseModel):
    """Payload for POST /api/migrate/file."""
    model_config = ConfigDict(extra="forbid")

    file_path: Optional[str] = Field(
        "input.php",
        description="Path or filename of the target PHP file",
    )
    code: str = Field(
        ...,
        description="Source PHP code to migrate to RHP",
    )
    php_version: Optional[str] = Field(
        "8.2",
        description="Source PHP version (e.g. '8.2', '8.1', '7.4')",
    )
    run_critic: bool = Field(
        True,
        description="Whether to run the AI Continuous Improvement Critic evaluation",
    )
    max_retries: int = Field(
        2,
        ge=0,
        le=5,
        description="Maximum compiler-healing retry attempts",
    )
    dry_run: bool = Field(
        False,
        description="Run migration without side effects",
    )


class MigrateFileResponse(BaseModel):
    """Response returned by POST /api/migrate/file."""
    model_config = ConfigDict(extra="ignore")

    file_path: str
    original_code: str
    migrated_code: str
    final_code: str
    is_valid: bool
    deterministic_edits: int = 0
    applied_rules: list[str] = Field(default_factory=list)
    todos_marked: int = 0
    companion_files: list[CompanionFileItem] = Field(default_factory=list)
    compiler_errors: list[str] = Field(default_factory=list)
    critic_report: Optional[dict[str, Any]] = None
    stats: Optional[dict[str, Any]] = None


class MigrateStreamRequest(BaseModel):
    """Payload for POST /api/migrate/stream."""
    model_config = ConfigDict(extra="ignore")

    file_path: Optional[str] = Field(
        "input.php",
        description="Path or filename of the target PHP file",
    )
    code: str = Field(
        ...,
        description="Source PHP code to stream migration for",
    )
    user_instruction: Optional[str] = Field(
        None,
        description="Optional custom user instruction or re-prompt for Critic loop",
    )
    php_version: Optional[str] = Field(
        "8.2",
        description="Source PHP version",
    )
    run_critic: bool = Field(
        True,
        description="Whether to run AI critic reflection stage",
    )
    max_retries: int = Field(
        2,
        ge=0,
        le=5,
        description="Maximum compiler healing retries",
    )


class SSEDiagnosticError(BaseModel):
    """Diagnostic compiler error emitted in SSE stream."""
    model_config = ConfigDict(extra="ignore")

    line: int
    col: int
    message: str


class StreamProgressEvent(BaseModel):
    """Server-Sent Event schema emitted during streaming migration."""
    model_config = ConfigDict(extra="ignore")

    stage: Literal[
        "ast_parsing",
        "initial_rhp_draft",
        "compiler_check",
        "critic_reflection",
        "final_code",
        "done",
        "error",
    ]
    status: Literal["started", "in_progress", "completed", "failed", "success"]
    data: Optional[dict[str, Any]] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Rules Models
# ---------------------------------------------------------------------------

class RuleFileResponse(BaseModel):
    """Response containing rule/learning file contents and metadata."""
    model_config = ConfigDict(extra="ignore")

    file_name: str
    file_path: str
    content: str
    exists: bool
    size_bytes: int


class RuleFileUpdateRequest(BaseModel):
    """Request to overwrite a rules/learnings file."""
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., description="New complete markdown content for the file")


class RuleFileAppendRequest(BaseModel):
    """Request to append an entry or section to a rules/learnings file."""
    model_config = ConfigDict(extra="ignore")

    content: Optional[str] = Field(None, description="Content or markdown block to append")
    title: Optional[str] = Field(None, description="Optional title or header for the appended block")
    context_symptom: Optional[str] = Field(None, description="Context / Symptom for structured learning fix")
    root_cause: Optional[str] = Field(None, description="Root cause analysis")
    working_solution: Optional[str] = Field(None, description="Verified working solution or code pattern")
    verification: Optional[str] = Field(None, description="Verification command or test result")
    key_heuristic: Optional[str] = Field(None, description="Actionable 1-2 sentence heuristic for future operations")


class ActiveRulesRequest(BaseModel):
    """Request payload for fetching AST-matched rules for a file."""
    model_config = ConfigDict(extra="ignore")

    file_path: Optional[str] = Field(None, description="Relative or absolute file path")
    code: Optional[str] = Field(None, description="PHP code snippet to analyze directly")
    workspace_id: Optional[str] = Field(None, description="Optional workspace ID to locate the file")


class ActiveRuleItem(BaseModel):
    """Summarized rule matching the target file AST."""
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    category: str
    strategy: str
    php_pattern: str
    rhp_replacement: Optional[str] = None
    unsupported: bool = False
    doc_url: Optional[str] = None
    llm_prompt_guidance: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    examples: list[dict[str, str]] = Field(default_factory=list)


class ActiveRulesResponse(BaseModel):
    """Response containing AST tags, matched rules, and active heuristics."""
    model_config = ConfigDict(extra="ignore")

    file_path: Optional[str] = None
    detected_tags: list[str] = Field(default_factory=list)
    rules: list[ActiveRuleItem] = Field(default_factory=list)
    heuristics: list[str] = Field(default_factory=list)
    rules_count: int = 0
    heuristics_count: int = 0
    system_prompt_preview: Optional[str] = None


class RuleActionResponse(BaseModel):
    """Generic action confirmation response for rules endpoints."""
    model_config = ConfigDict(extra="ignore")

    success: bool
    message: str
    file_path: str
    size_bytes: int


class PendingProposalItem(BaseModel):
    """Pending context proposal awaiting review/approval."""
    model_config = ConfigDict(extra="ignore")

    id: str
    file: str
    php: str
    content: str


class PendingProposalsResponse(BaseModel):
    """List of pending context proposals."""
    model_config = ConfigDict(extra="ignore")

    count: int
    pending_file: str
    proposals: list[PendingProposalItem]


# ---------------------------------------------------------------------------
# Workspace Ingestion Models
# ---------------------------------------------------------------------------

class CloneGithubRequest(BaseModel):
    """Payload for POST /api/workspace/clone-github."""
    model_config = ConfigDict(extra="forbid")

    repo_url: str = Field(..., description="GitHub repository URL (https or ssh)")
    branch: Optional[str] = Field(None, description="Optional branch or tag to clone")
    github_token: Optional[str] = Field(None, description="Optional GitHub personal access token for private repositories")


class WorkspaceResponse(BaseModel):
    """Response returned upon workspace creation (upload-zip or clone-github)."""
    model_config = ConfigDict(extra="ignore")

    workspace_id: str = Field(..., description="Unique workspace identifier")
    workspace_path: str = Field(..., description="Sandboxed workspace directory path")
    file_count: int = Field(0, description="Total number of files extracted or cloned")
    message: str = Field("Workspace ready", description="Status message")


class FileTreeNode(BaseModel):
    """Hierarchical node in a workspace file tree."""
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="File or directory name")
    path: str = Field(..., description="Relative path from workspace root")
    type: Literal["file", "directory"] = Field("file", description="Node type: 'file' or 'directory'")
    size: Optional[int] = Field(None, description="File size in bytes (None for directories)")
    classification: Optional[str] = Field(None, description="PHP file classification: Controller, Model, Service, Migration, Blade view")
    children: Optional[list[FileTreeNode]] = Field(None, description="Child nodes for directories")


FileTreeNode.model_rebuild()


class WorkspaceTreeResponse(BaseModel):
    """Response returned by GET /api/workspace/{workspace_id}/tree."""
    model_config = ConfigDict(extra="ignore")

    workspace_id: str = Field(..., description="Unique workspace identifier")
    total_files: int = Field(..., description="Total non-filtered files in workspace")
    tree: list[FileTreeNode] = Field(default_factory=list, description="Root directory tree structure")


class WorkspaceFileContentResponse(BaseModel):
    """Response returned by GET /api/workspace/{workspace_id}/file."""
    model_config = ConfigDict(extra="ignore")

    workspace_id: str = Field(..., description="Unique workspace identifier")
    path: str = Field(..., description="Relative path within the workspace")
    content: str = Field(..., description="Raw text content of the file")
    size: int = Field(..., description="Size of the file in bytes")
    classification: Optional[str] = Field(None, description="PHP file classification")


class WorkspaceCleanupResponse(BaseModel):
    """Response returned by workspace cleanup actions."""
    model_config = ConfigDict(extra="ignore")

    cleaned_count: int = Field(..., description="Number of expired workspaces deleted")
    message: str = Field(..., description="Status message")

