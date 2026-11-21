"""Pydantic response models for AI-powered migration (Layer 5)."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class PatchType(str, Enum):
    """Specifies the format/status of the code transformation returned by the LLM."""
    FULL_REPLACEMENT = "FULL_REPLACEMENT"
    UNIFIED_DIFF = "UNIFIED_DIFF"
    UNRESOLVED = "UNRESOLVED"


class TypeResolution(BaseModel):
    """Inferred or resolved type for an untyped parameter, property, or variable."""
    model_config = ConfigDict(extra="forbid")

    param_name: str = Field(
        ...,
        description="Name of the parameter, property, or variable (e.g. '$amount' or 'amount')",
    )
    inferred_type: str = Field(
        ...,
        description="Resolved strictly-typed RHP type (e.g. 'float', 'string', 'int[]', 'dict<string, mixed>')",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this type resolution between 0.0 and 1.0",
    )
    rationale: str = Field(
        ...,
        description="Explanation or AST evidence justifying the inferred type",
    )


class CompanionFile(BaseModel):
    """Represents an additional file created during migration (e.g. companion .rade.rhp template)."""
    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(
        ...,
        description="Relative destination file path (e.g. 'resources/views/partials/card.rade.rhp')",
    )
    content: str = Field(
        ...,
        description="Full content of the companion file (e.g. Rade template directives and HTML)",
    )
    description: str = Field(
        default="",
        description="Brief description of the companion file's purpose",
    )


class LLMMigrationResult(BaseModel):
    """Structured response output produced by the LLM for a single unit migration."""
    model_config = ConfigDict(extra="forbid")

    unit_identifier: str = Field(
        ...,
        description="Fully-qualified identifier of the migrated unit (e.g. 'App\\Services\\PaymentService::charge')",
    )
    patch_type: PatchType = Field(
        default=PatchType.FULL_REPLACEMENT,
        description="Type of patch applied: FULL_REPLACEMENT, UNIFIED_DIFF, or UNRESOLVED",
    )
    transformed_code: str = Field(
        ...,
        description="The transformed RHP code, unified diff patch, or original code if UNRESOLVED",
    )
    companion_files: list[CompanionFile] = Field(
        default_factory=list,
        description="Companion template or view files created alongside the migrated RHP code (e.g. .rade.rhp files)",
    )
    resolved_types: list[TypeResolution] = Field(
        default_factory=list,
        description="List of parameter and variable types resolved during migration",
    )
    applied_rules: list[str] = Field(
        default_factory=list,
        description="Rule IDs from rules.yaml applied during transformation (e.g. ['RHP-UNSUP-001'])",
    )
    needs_human_review: bool = Field(
        default=False,
        description="True if unresolved ambiguities or unsupported patterns require human intervention",
    )
    review_reason: Optional[str] = Field(
        default=None,
        description="Detailed explanation of what requires manual review when needs_human_review is True",
    )



class TokenUsage(BaseModel):
    """Tracks token consumption for an LLM interaction."""
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0, description="Tokens used in prompt input")
    completion_tokens: int = Field(default=0, ge=0, description="Tokens used in completion output")
    total_tokens: int = Field(default=0, ge=0, description="Total tokens consumed")

    def add(self, other: "TokenUsage") -> "TokenUsage":
        """Add another TokenUsage to this instance, returning a new TokenUsage."""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class CostEstimate(BaseModel):
    """Calculated cost in USD for token consumption."""
    model_config = ConfigDict(extra="forbid")

    prompt_cost_usd: float = Field(default=0.0, ge=0.0, description="Cost for prompt tokens in USD")
    completion_cost_usd: float = Field(default=0.0, ge=0.0, description="Cost for completion tokens in USD")
    total_cost_usd: float = Field(default=0.0, ge=0.0, description="Total estimated cost in USD")


class AIExecutionStats(BaseModel):
    """Execution telemetry including model, tokens, cost, and duration."""
    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., description="Name of the model invoked")
    token_usage: TokenUsage = Field(default_factory=TokenUsage, description="Token usage breakdown")
    cost: CostEstimate = Field(default_factory=CostEstimate, description="Estimated cost breakdown")
    duration_seconds: float = Field(default=0.0, ge=0.0, description="Execution duration in seconds")


class AIMigrationResponse(BaseModel):
    """Top-level container holding the migration result and execution statistics."""
    model_config = ConfigDict(extra="forbid")

    result: LLMMigrationResult = Field(..., description="The structured migration output")
    stats: AIExecutionStats = Field(..., description="Telemetry and cost statistics")
    raw_response: Optional[str] = Field(default=None, description="Raw model text before parsing")
