"""Schema definitions for RHP migration rules."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class RuleCategory(str, Enum):
    """Categories classifying RHP migration rules."""
    SYNTAX = "SYNTAX"
    TYPE_SYSTEM = "TYPE_SYSTEM"
    HTTP_FACADE = "HTTP_FACADE"
    UNSUPPORTED = "UNSUPPORTED"
    STANDARD_LIBRARY = "STANDARD_LIBRARY"
    TEMPLATES = "TEMPLATES"


class MigrationStrategy(str, Enum):
    """Strategies indicating how a migration rule should be executed."""
    DETERMINISTIC = "DETERMINISTIC"
    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    LLM_ASSIST = "LLM_ASSIST"
    MANUAL_REWRITE = "MANUAL_REWRITE"


class RuleExample(BaseModel):
    """Code example demonstrating before/after states for a migration rule."""
    model_config = ConfigDict(extra="forbid")

    before: str = Field(..., description="PHP code snippet before migration")
    after: str = Field(..., description="RHP code snippet after migration")
    explanation: str = Field(..., description="Explanation of the migration logic")


class Rule(BaseModel):
    """Defines an atomic migration rule from PHP/Laravel to RHP."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique rule identifier (e.g. RHP-SYN-001)")
    title: str = Field(..., description="Human-readable title describing the rule")
    category: RuleCategory = Field(..., description="Category of the rule")
    strategy: MigrationStrategy = Field(..., description="Execution strategy for applying the rule")
    php_pattern: str = Field(..., description="Description or AST selector of the PHP pattern")
    rhp_replacement: Optional[str] = Field(None, description="Replacement pattern or RHP equivalent")
    unsupported: bool = Field(False, description="True if rejected by RHP (e.g. Reflection, Output Buffering)")
    doc_url: Optional[str] = Field(None, description="URL pointing to documentation or reference")
    llm_prompt_guidance: Optional[str] = Field(None, description="Guidance for the LLM when executing this migration")
    examples: list[RuleExample] = Field(default_factory=list, description="Code examples for before/after migration")
    tags: list[str] = Field(default_factory=list, description="Tags for AST matching and targeted retrieval")

    def format_prompt_block(self) -> str:
        """Format this rule as a markdown block for inclusion in LLM prompts."""
        lines = [
            f"### Rule [{self.id}]: {self.title}",
            f"- **Category**: {self.category.value}",
            f"- **Strategy**: {self.strategy.value}",
            f"- **Unsupported Feature**: {'Yes' if self.unsupported else 'No'}",
            f"- **PHP Pattern**: {self.php_pattern}",
        ]
        if self.rhp_replacement:
            lines.append(f"- **RHP Replacement**: {self.rhp_replacement}")
        if self.llm_prompt_guidance:
            lines.append(f"- **AI Guidance**: {self.llm_prompt_guidance}")
        if self.doc_url:
            lines.append(f"- **Documentation**: {self.doc_url}")

        if self.examples:
            lines.append("- **Examples**:")
            for idx, eg in enumerate(self.examples, start=1):
                lines.append(f"  *Example {idx}* ({eg.explanation}):")
                lines.append("  ```php")
                lines.append("  // BEFORE:")
                for l in eg.before.strip().splitlines():
                    lines.append(f"  {l}")
                lines.append("  // AFTER:")
                for l in eg.after.strip().splitlines():
                    lines.append(f"  {l}")
                lines.append("  ```")

        return "\n".join(lines)
