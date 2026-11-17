"""Layer 5: AI-powered migration module for ambiguous leftovers and complex constructs."""

from rhp_migrate.ai.models import (
    AIExecutionStats,
    AIMigrationResponse,
    CostEstimate,
    LLMMigrationResult,
    PatchType,
    TokenUsage,
    TypeResolution,
)
from rhp_migrate.ai.prompts import (
    MigrationPromptContext,
    build_system_prompt,
    build_user_prompt,
    format_ast_context,
    format_caller_signatures,
    format_class_properties,
    format_compiler_errors,
    format_rules_for_prompt,
    validate_single_unit,
)
from rhp_migrate.ai.client import (
    AnthropicClient,
    BaseLLMClient,
    GeminiClient,
    LLMRunner,
    MockLLMClient,
    OpenAIClient,
    calculate_cost,
    extract_json_payload,
    get_model_pricing,
    parse_structured_output,
)

__all__ = [
    # Models
    "PatchType",
    "TypeResolution",
    "LLMMigrationResult",
    "TokenUsage",
    "CostEstimate",
    "AIExecutionStats",
    "AIMigrationResponse",
    # Prompts
    "build_system_prompt",
    "build_user_prompt",
    "format_ast_context",
    "format_caller_signatures",
    "format_class_properties",
    "format_compiler_errors",
    "format_rules_for_prompt",
    "validate_single_unit",
    "MigrationPromptContext",
    # Clients & Runner
    "BaseLLMClient",
    "GeminiClient",
    "AnthropicClient",
    "OpenAIClient",
    "MockLLMClient",
    "LLMRunner",
    "calculate_cost",
    "get_model_pricing",
    "extract_json_payload",
    "parse_structured_output",
]
